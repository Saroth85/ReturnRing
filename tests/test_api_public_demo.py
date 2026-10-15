"""The public demo after the review of 14 Oct 2026 (docs/review-backlog.md: "A judge following
the README" and "Security of the public deployment"; spec 11; rules 11 and 13).

- /readyz shows "Precomputed on AMD MI300X" only when the demo package's MANIFEST.json says the
  drafts were made on the MI300X and every cache entry is the model the manifest names; a
  rehearsal label otherwise, and no other public text claims the MI300X then (second review);
- the MI300X batch refuses a server that does not serve GEMMA_MODEL (a fake vLLM), records the
  device it ran on, and replaces the drafts of another model; the export refuses a CPU batch;
- the public read-only agent counts its questions per client before per person, so one anonymous
  client cannot lock the agent for every visitor, and a 429 says when to retry (Retry-After);
- the rate limiters forget idle clients and count an IPv6 client by its /64;
- the access log cannot be forged by an encoded newline in the path;
- no VLLM_BASE_URL reaches a public body;
- the portfolio matrix is built and encoded off the event loop;
- the reset waits for a database that is still starting;
- on the read-only cached demo the label stands beside the frozen demo clock (ADR-15 sections 19
  and 20), as the cached readyz example shows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from apps.api import batch_generate
from apps.api import schemas as s
from apps.api.app import create_app
from apps.api.batch_generate import batch
from apps.api.ratelimit import RateLimiter
from apps.api.reset import wait_for_database
from apps.api.roles import client_key
from apps.api.serving import Serving
from bench.fake_vllm import FakeVllm
from llm.client import MockClient
from pipeline.generator.golden import GIULIA
from scripts import demo_export
from tests.api_harness import Api, golden_return, open_api, settings_for, validate_body

EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples" / "api"
GPU = "AMD Instinct MI300X (AMD Developer Cloud)"
GEMMA = "google/gemma-4-31B-it"
PLAN_B = "google/gemma-4-26B-A4B-it"  # the plan-B model of .env.example
MI300X_MANIFEST: dict[str, Any] = {
    "format": "returnring.demo_export.v1",
    "llm_cache": {"rehearsal": False, "generated_on": GPU, "gemma_model": GEMMA},
}


# ---- The cached label (rules 11 and 13) ----------------------------------------------------------


def _package(
    serving_dir: Path, root: Path, *, models: tuple[str, ...], manifest: dict[str, Any] | None
) -> Path:
    """A demo package laid out as demo-cache/: serving/ with llm_cache/, MANIFEST.json beside."""
    serving = root / "demo-cache" / "serving"
    shutil.copytree(serving_dir, serving)
    cache = serving / "llm_cache"
    cache.mkdir(exist_ok=True)
    for n, model in enumerate(models):
        entry = {"format": "returnring.llm_cache.v1", "input_digest": f"d{n}", "model": model}
        (cache / f"d{n}.json").write_text(json.dumps(entry), encoding="utf-8")
    if manifest is not None:
        text = json.dumps(manifest)
        (serving.parent / "MANIFEST.json").write_text(text, encoding="utf-8")
    return serving


def _readyz(serving: Serving, package: Path, tmp_path: Path) -> dict[str, Any]:
    settings = settings_for(package, tmp_path / "none.sqlite", demo_mode="cached")
    assert settings.gemma_model == GEMMA  # the demo server's GEMMA_MODEL, as .env.example sets it
    for api in open_api(serving, settings, tmp_path / "none.sqlite"):
        body: dict[str, Any] = api.client.get("/readyz").json()
        validate_body("readyz_response", body)
        assert str(tmp_path) not in json.dumps(body)  # no server path in a public body (H26)
        telemetry = api.client.get("/api/telemetry").json()
        validate_body("telemetry", telemetry)
        body["telemetry_note"] = telemetry["note"]
        return body
    raise AssertionError("no app")


def _claims_mi300x(body: dict[str, Any]) -> bool:
    """Whether the telemetry check of /readyz or the note of GET /api/telemetry says where the
    precomputed outputs were made: only the label may, and the rehearsal label says they are
    not verified on the MI300X."""
    telemetry = next(c for c in body["checks"] if c["component"] == "telemetry")
    return any("MI300X" in (text or "") for text in (telemetry["detail"], body["telemetry_note"]))


def _cache_check(body: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in body["checks"] if c["component"] == "demo_cache")


def test_the_mi300x_label_needs_the_package_to_say_so(
    serving: Serving, serving_dir: Path, tmp_path: Path
) -> None:
    package = _package(serving_dir, tmp_path, models=(GEMMA, GEMMA), manifest=MI300X_MANIFEST)
    body = _readyz(serving, package, tmp_path)
    assert body["label"] == s.CACHED_LABEL == "Precomputed on AMD MI300X"
    assert _cache_check(body)["status"] == "ok"


def test_a_plan_b_package_made_on_the_mi300x_keeps_its_label(
    serving: Serving, serving_dir: Path, tmp_path: Path
) -> None:
    """The drafts are checked against the model the package names (the export checked them
    against GEMMA_MODEL of the droplet), not against GEMMA_MODEL of the demo server, which
    calls no model: the runbook's demo server keeps the .env.example default."""
    manifest = {**MI300X_MANIFEST, "llm_cache": {**MI300X_MANIFEST["llm_cache"]}}
    manifest["llm_cache"]["gemma_model"] = PLAN_B
    package = _package(serving_dir, tmp_path, models=(PLAN_B, PLAN_B), manifest=manifest)
    body = _readyz(serving, package, tmp_path)
    assert body["label"] == s.CACHED_LABEL


@pytest.mark.parametrize(
    ("models", "manifest", "why"),
    [
        # a CPU rehearsal with a fake vLLM server: the manifest says so
        (
            ("google/gemma-4-31B-it",),
            {**MI300X_MANIFEST, "llm_cache": {"rehearsal": True}},
            "rehearsal",
        ),
        # a package that does not name the MI300X
        (
            ("google/gemma-4-31B-it",),
            {**MI300X_MANIFEST, "llm_cache": {"rehearsal": False, "generated_on": "a CPU"}},
            "MI300X",
        ),
        # drafts of another model than the one the package names
        ((GEMMA, PLAN_B), MI300X_MANIFEST, PLAN_B),
        # a package that names no model
        (
            (GEMMA,),
            {**MI300X_MANIFEST, "llm_cache": {"rehearsal": False, "generated_on": GPU}},
            "model",
        ),
        # no package manifest next to the snapshot, or one that is not a package manifest
        (("google/gemma-4-31B-it",), None, "MANIFEST.json"),
        (("google/gemma-4-31B-it",), {"format": "other"}, "MANIFEST.json"),
    ],
)
def test_anything_else_is_labelled_a_rehearsal(
    serving: Serving,
    serving_dir: Path,
    tmp_path: Path,
    models: tuple[str, ...],
    manifest: dict[str, Any] | None,
    why: str,
) -> None:
    """A CPU package is never labelled MI300X: the label says it is a rehearsal and the check
    says why."""
    package = _package(serving_dir, tmp_path, models=models, manifest=manifest)
    body = _readyz(serving, package, tmp_path)
    assert body["label"] == s.REHEARSAL_LABEL and not _claims_mi300x(body)
    check = _cache_check(body)
    assert check["status"] == "ok" and why in check["detail"]


def test_a_broken_manifest_or_entry_is_a_rehearsal(
    serving: Serving, serving_dir: Path, tmp_path: Path
) -> None:
    package = _package(serving_dir, tmp_path, models=(), manifest=None)
    (package.parent / "MANIFEST.json").write_text("{not json", encoding="utf-8")
    (package / "llm_cache" / "x.json").write_text("{not json", encoding="utf-8")
    body = _readyz(serving, package, tmp_path)
    assert body["label"] == s.REHEARSAL_LABEL


@pytest.mark.parametrize("verified", [True, False], ids=["mi300x", "rehearsal"])
def test_the_read_only_cached_demo_shows_its_label_on_a_frozen_clock(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    verified: bool,
) -> None:
    """The cached demo as infra/compose.demo.yml starts it (PUBLIC_READ_ONLY on, after a reset):
    the label the package earns and the telemetry note that names no hardware (ADR-15 sections
    16 and 17) stand beside the demo clock frozen at the snapshot instant (sections 19 and 20),
    the demo cases (section 18) are served at that instant, and the cached readyz example shows
    the same label, clock and read-only flag."""
    manifest = (
        MI300X_MANIFEST if verified else {**MI300X_MANIFEST, "llm_cache": {"rehearsal": True}}
    )
    package = _package(serving_dir, tmp_path, models=(GEMMA,), manifest=manifest)
    database = tmp_path / "demo.sqlite"
    shutil.copyfile(seeded_db[0], database)
    settings = settings_for(package, database, demo_mode="cached", public_read_only=True)
    assert settings.frozen_clock
    with TestClient(create_app(settings, serving=serving)) as client:  # the DemoClock
        body = client.get("/readyz").json()
        validate_body("readyz_response", body)
        note = client.get("/api/telemetry").json()["note"]
        cases = client.get("/api/demo/cases")
    assert cases.status_code == 200, cases.text
    demo = s.DemoCasesResponse.model_validate(cases.json())  # served on the frozen clock
    assert demo.as_of == demo.snapshot_as_of == serving.as_of
    assert body["label"] == (s.CACHED_LABEL if verified else s.REHEARSAL_LABEL)
    assert body["public_read_only"] is True and body["mode"] == "cached"
    telemetry = next(c for c in body["checks"] if c["component"] == "telemetry")
    assert telemetry["status"] == "disabled"
    assert "MI300X" not in (telemetry["detail"] or "") and "MI300X" not in note
    clock = body["clock"]
    assert clock["kind"] == "demo" and clock["frozen"] is True
    assert clock["note"] == s.FROZEN_CLOCK_NOTE
    assert clock["now"] == clock["snapshot_as_of"]
    assert s.ClockView.model_validate(clock).now == serving.as_of
    example = json.loads((EXAMPLES / "readyz_response.cached.json").read_text(encoding="utf-8"))
    assert example["label"] == s.CACHED_LABEL and example["public_read_only"] is True
    assert {k: example["clock"][k] for k in ("kind", "frozen", "note")} == {
        k: clock[k] for k in ("kind", "frozen", "note")
    }
    assert example["clock"]["now"] == example["clock"]["snapshot_as_of"]


# ---- The MI300X batch: where the drafts come from ------------------------------------------------


def _batch_env(monkeypatch: pytest.MonkeyPatch, serving: Path, base_url: str, model: str) -> None:
    """The environment of python -m apps.api.batch_generate on a host with a vLLM server."""
    env = {
        "LLM_MODE": "vllm",
        "VLLM_BASE_URL": base_url,
        "VLLM_API_KEY": "dry-run",
        "GEMMA_MODEL": model,
        "SERVING_DIR": str(serving),
        "DEVICE": "cpu",
        "DEMO_MODE": "live",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_the_batch_refuses_a_server_that_does_not_serve_gemma_model(
    serving_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The fake vLLM answers any model name with its own drafts: the batch asks the server
    which models it serves before the first case, so they are never recorded as Gemma's."""
    snapshot = tmp_path / "serving"
    shutil.copytree(serving_dir, snapshot)
    with FakeVllm() as fake:  # serves fake/gemma-dry-run
        _batch_env(monkeypatch, snapshot, fake.base_url, GEMMA)
        assert batch_generate.main(["--limit", "2"]) == 2
        assert fake.requests == 0
    assert f"model {GEMMA} not served" in capsys.readouterr().err
    assert not list((snapshot / "llm_cache").glob("*.json"))


def test_a_fake_vllm_rehearsal_is_never_packed_or_labelled_as_the_mi300x(
    serving: Serving, serving_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rehearsal batch against the fake server records the fake's model and the CPU it ran
    on. The export refuses it as the MI300X package, with Gemma's name or with the fake's own
    as GEMMA_MODEL, and packs it only as a rehearsal, which the demo labels as one."""
    droplet = tmp_path / "droplet"
    snapshot = droplet / "serving"
    shutil.copytree(serving_dir, snapshot)
    bench = droplet / "bench" / "results"
    bench.mkdir(parents=True)
    with FakeVllm() as fake:
        _batch_env(monkeypatch, snapshot, fake.base_url, fake.model)
        assert batch_generate.main(["--limit", "2"]) == 0
    entries = [json.loads(p.read_text("utf-8")) for p in (snapshot / "llm_cache").glob("*.json")]
    assert len(entries) == 2
    assert {(e["model"], e["device"]) for e in entries} == {(fake.model, "cpu")}
    out = droplet / "demo-cache"
    for model in (GEMMA, fake.model):
        with pytest.raises(demo_export.PackageError):
            demo_export.build(snapshot, bench, out, generated_on=GPU, model=model)
    demo_export.build(snapshot, bench, out, rehearsal=True, generated_on=GPU, model=GEMMA)
    body = _readyz(serving, out / "serving", tmp_path)
    assert body["label"] == s.REHEARSAL_LABEL and not _claims_mi300x(body)


def test_the_real_batch_replaces_the_drafts_of_another_model(
    serving: Serving, tmp_path: Path
) -> None:
    """The cache is keyed by the case, not by the server: a draft of another model (a rehearsal
    against the fake server) is generated again, never kept as the MI300X batch's."""
    copy = tmp_path / "serving"
    shutil.copytree(serving.root, copy)
    snapshot = Serving.load(copy)
    assert not batch(snapshot, MockClient(), "fake/gemma-dry-run", limit=2)["failed"]
    assert batch(snapshot, MockClient(), "fake/gemma-dry-run", limit=2)["skipped_cached"] == 2
    real = batch(snapshot, MockClient(), GEMMA, limit=2, device="gpu")
    assert real["skipped_cached"] == 0 and not real["failed"]
    entries = [json.loads(p.read_text("utf-8")) for p in (copy / "llm_cache").glob("*.json")]
    assert {(e["model"], e["device"]) for e in entries} == {(GEMMA, "gpu")}
    assert batch(snapshot, MockClient(), GEMMA, limit=2, device="gpu")["skipped_cached"] == 2


# ---- Rate limits of the public demo ------------------------------------------------------------


@pytest.fixture
def public_api(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> Iterator[Api]:
    database = tmp_path / "public.sqlite"
    shutil.copyfile(seeded_db[0], database)
    settings = settings_for(serving_dir, database, public_read_only=True)
    yield from open_api(serving, settings, database)


def _ask(api: Api, client: str, who: str = "sara") -> Any:
    giulia = golden_return(api.serving, GIULIA)
    return api.client.post(
        "/api/agent/ask",
        json={"return_id": giulia, "question": "Which terms applied?", "asked_by": who},
        headers={"X-Forwarded-For": client},
    )


def _retry_after(response: Any) -> int:
    """The Retry-After header of a 429, equal to the wait the message states."""
    assert response.status_code == 429, response.text
    body = response.json()
    validate_body("error", body)
    assert body["code"] == "rate_limited"
    seconds = int(response.headers["Retry-After"])
    assert 1 <= seconds <= 60
    found = re.search(r"try again in (\d+) s", body["message"])
    assert found is not None and int(found.group(1)) == seconds
    return seconds


def test_one_client_cannot_spend_the_agent_of_another(public_api: Api) -> None:
    """On the public read-only demo the agent's limit per person is counted per client too:
    every visitor names one of three demo users, so a limit per person alone lets one
    anonymous client lock the agent for all."""
    public_api.app.state.agent_rate_limiter = RateLimiter(limit=1, window_s=60)
    assert _ask(public_api, "203.0.113.10").status_code == 200
    refused = _ask(public_api, "203.0.113.10")
    _retry_after(refused)
    assert "questions to the agent" in refused.json()["message"]
    assert _ask(public_api, "198.51.100.7").status_code == 200  # another client, same person


def test_the_public_limit_comes_before_the_agent_limit(public_api: Api) -> None:
    """A client past the public limit spends no question of the agent's budget."""
    public_api.app.state.public_rate_limiter = RateLimiter(limit=1, window_s=60)
    agent = RateLimiter(limit=5, window_s=60)
    public_api.app.state.agent_rate_limiter = agent
    assert _ask(public_api, "203.0.113.10").status_code == 200
    for _ in range(3):
        refused = _ask(public_api, "203.0.113.10")
        _retry_after(refused)
        assert "public demo" in refused.json()["message"]
    assert len(agent) == 1 and agent.retry_after("203.0.113.10 sara") is None  # 2 of 5 used


def test_a_public_429_says_when_to_retry(public_api: Api) -> None:
    public_api.app.state.public_rate_limiter = RateLimiter(limit=1, window_s=60)
    record = public_api.serving.store_return(golden_return(public_api.serving, GIULIA))
    assert record is not None
    cart = {
        "customer_id": GIULIA,
        "lines": [{"variant_id": record.variant_id, "size": "M", "quantity": 1}],
        "coupon_code": None,
    }
    first = public_api.client.post("/api/checkout/evaluate", json=cart)
    assert first.status_code == 200, first.text
    assert "Retry-After" not in first.headers
    _retry_after(public_api.client.post("/api/checkout/evaluate", json=cart))


def test_the_agent_429_of_the_laptop_says_when_to_retry_too(api: Api) -> None:
    api.app.state.agent_rate_limiter = RateLimiter(limit=1, window_s=60)
    assert _ask(api, "203.0.113.10").status_code == 200
    assert _ask(api, "198.51.100.7").status_code == 429  # per person only: not public
    _retry_after(_ask(api, "203.0.113.10"))


def test_the_limiter_forgets_idle_clients() -> None:
    """Keys whose calls are all out of the window are dropped (at most once per window), so a
    stream of distinct clients cannot grow the limiter without bound."""
    now = [0.0]
    limiter = RateLimiter(limit=2, window_s=10, clock=lambda: now[0])
    for n in range(1000):
        assert limiter.retry_after(f"client-{n}") is None
    assert len(limiter) == 1000
    now[0] = 9.0
    assert limiter.retry_after("client-0") is None  # within the window: nothing is dropped
    assert len(limiter) == 1000
    now[0] = 15.0
    assert limiter.retry_after("client-new") is None
    assert len(limiter) == 2  # client-0 (last call at 9 s) and the new one
    assert limiter.retry_after("client-0") is None and limiter.retry_after("client-0") == 4.0


def _request(forwarded: str | None, peer: str = "10.0.0.2") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "headers": headers, "client": (peer, 5000)})


@pytest.mark.parametrize(
    ("forwarded", "peer", "key"),
    [
        ("203.0.113.10", "10.0.0.2", "203.0.113.10"),
        ("203.0.113.10, 10.0.0.9", "10.0.0.2", "203.0.113.10"),  # the first hop, as before
        (None, "10.0.0.2", "10.0.0.2"),
        ("2001:db8:1:2:aaaa::1", "10.0.0.2", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:bbbb:cccc:dddd:eeee", "10.0.0.2", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "10.0.0.2", "2001:db8:1:3::/64"),
        (None, "2001:db8:9:9::5", "2001:db8:9:9::/64"),
        ("::ffff:203.0.113.10", "10.0.0.2", "203.0.113.10"),
        ("not-an-address", "10.0.0.2", "not-an-address"),
    ],
)
def test_ipv6_clients_are_counted_by_their_64_prefix(
    forwarded: str | None, peer: str, key: str
) -> None:
    """One IPv6 subscriber holds a whole /64: a key per address would give it 2^64 budgets."""
    assert client_key(_request(forwarded, peer)) == key


# ---- The access log ------------------------------------------------------------------------------


def test_an_encoded_newline_cannot_forge_an_access_log_line(
    empty_api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    path = "/healthz%0Arequest_id=req-forged method=GET path=/admin status=200%20x"
    with caplog.at_level(logging.INFO, logger="returnring.api.access"):
        empty_api.client.get(path, headers={s.REQUEST_ID_HEADER: "req-log-2"})
    lines = [r.getMessage() for r in caplog.records if r.name == "returnring.api.access"]
    line = next(m for m in lines if "request_id=req-log-2" in m)
    assert "\n" not in line and "\r" not in line
    fields = [field.split("=", 1)[0] for field in line.split(" ")]
    assert fields == ["request_id", "method", "path", "status", "duration_ms"]
    assert "path=/healthz%0Arequest_id=req-forged%20method=GET" in line
    assert " status=404 " in line


# ---- No VLLM_BASE_URL in a public body ----------------------------------------------------------


SECRET_URLS = (
    "http://admin:hunter2@vllm:8001/v1",
    "http://vllm:8001/v1?token=hunter2",
    "http://vllm:8001/v1#hunter2",
    "ftp://hunter2/v1",
    "http://hunter2.example.com/v1",
)


@pytest.mark.parametrize("url", SECRET_URLS)
def test_readyz_names_the_refused_part_of_vllm_base_url_never_the_url(
    serving: Serving, serving_dir: Path, tmp_path: Path, url: str
) -> None:
    settings = settings_for(
        serving_dir, tmp_path / "none.sqlite", llm_mode="vllm", vllm_base_url=url, vllm_api_key="k"
    )
    for api in open_api(serving, settings, tmp_path / "none.sqlite"):
        body = api.client.get("/readyz").json()
        validate_body("readyz_response", body)
        vllm = next(c for c in body["checks"] if c["component"] == "vllm")
        assert vllm["status"] == "unavailable" and "VLLM_BASE_URL" in vllm["detail"]
        assert "hunter2" not in json.dumps(body)


def test_the_agent_503_never_echoes_vllm_base_url(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    database = tmp_path / "vllm.sqlite"
    shutil.copyfile(seeded_db[0], database)
    settings = settings_for(
        serving_dir, database, llm_mode="vllm", vllm_base_url=SECRET_URLS[0], vllm_api_key="k"
    )
    for api in open_api(serving, settings, database):
        response = _ask(api, "203.0.113.10")
        assert response.status_code == 503, response.text
        assert "hunter2" not in response.text and "VLLM_BASE_URL" in response.text


# ---- The portfolio matrix off the event loop -----------------------------------------------------


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def test_the_matrix_is_built_and_encoded_off_the_event_loop(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the database reads run on the single worker's event loop. The case cards, the
    12-month counts, the 20,000 points and their encoding (json.dumps, gzip) run in a worker
    thread: they take about a second at full scale, and the loop keeps answering meanwhile."""
    from apps.api.routes import manager
    from apps.api.services.manager import matrix

    calls: dict[str, list[bool]] = {}

    def spy(module: Any, name: str) -> None:
        real = getattr(module, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calls.setdefault(name, []).append(_on_loop())
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    spy(manager, "encode_large")
    for name in ("case_values", "history_counts", "quadrant_for"):
        spy(matrix, name)
    plain = api.client.get("/api/portfolio/matrix", headers={"Accept-Encoding": "identity"})
    zipped = api.client.get("/api/portfolio/matrix", headers={"Accept-Encoding": "gzip"})
    assert plain.status_code == zipped.status_code == 200
    assert zipped.headers["Content-Encoding"] == "gzip" and "Content-Encoding" not in plain.headers
    assert zipped.json() == plain.json()  # the client decompresses
    assert calls["encode_large"] == [False, False]
    assert calls["case_values"] == calls["history_counts"] == [False, False]
    assert len(calls["quadrant_for"]) > 2 and not any(calls["quadrant_for"])


# ---- The reset waits for a database that is still starting ---------------------------------------


class _Starting:
    """An engine whose first `failures` connections are refused, as PostgreSQL's init-only
    server and its restart refuse them."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    def connect(self) -> _Starting:
        return self

    async def __aenter__(self) -> _Starting:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ConnectionRefusedError("the database system is starting up")
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: object) -> None:
        return None


def _wait(engine: _Starting, timeout_s: float = 30.0) -> list[float]:
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    asyncio.run(
        wait_for_database(
            engine,  # type: ignore[arg-type]
            timeout_s=timeout_s,
            sleep=sleep,
            clock=lambda: now[0],
        )
    )
    return slept


def test_the_reset_retries_the_first_connection() -> None:
    engine = _Starting(failures=5)
    slept = _wait(engine)
    assert engine.attempts == 6 and slept == [1.0] * 5


def test_the_reset_gives_up_after_about_30_seconds() -> None:
    engine = _Starting(failures=10_000)
    with pytest.raises(ConnectionRefusedError):
        _wait(engine)
    assert math.isclose(engine.attempts, 31, abs_tol=1)
