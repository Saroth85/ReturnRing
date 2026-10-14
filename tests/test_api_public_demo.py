"""The public demo after the review of 14 Oct 2026 (docs/review-backlog.md: "A judge following
the README" and "Security of the public deployment"; spec 11; rules 11 and 13).

- /readyz shows "Precomputed on AMD MI300X" only when the demo package's MANIFEST.json says the
  drafts were made on the MI300X and every cache entry is GEMMA_MODEL's; a rehearsal label
  otherwise;
- the public read-only agent counts its questions per client before per person, so one anonymous
  client cannot lock the agent for every visitor, and a 429 says when to retry (Retry-After);
- the rate limiters forget idle clients and count an IPv6 client by its /64;
- the access log cannot be forged by an encoded newline in the path;
- no VLLM_BASE_URL reaches a public body;
- the portfolio matrix is encoded off the event loop;
- the reset waits for a database that is still starting.
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
from starlette.requests import Request

from apps.api import schemas as s
from apps.api.ratelimit import RateLimiter
from apps.api.reset import wait_for_database
from apps.api.roles import client_key
from apps.api.serving import Serving
from pipeline.generator.golden import GIULIA
from tests.api_harness import Api, golden_return, open_api, settings_for, validate_body

GPU = "AMD Instinct MI300X (AMD Developer Cloud)"
MI300X_MANIFEST: dict[str, Any] = {
    "format": "returnring.demo_export.v1",
    "llm_cache": {"rehearsal": False, "generated_on": GPU, "gemma_model": "google/gemma-4-31B-it"},
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
    for api in open_api(serving, settings, tmp_path / "none.sqlite"):
        body: dict[str, Any] = api.client.get("/readyz").json()
        validate_body("readyz_response", body)
        assert str(tmp_path) not in json.dumps(body)  # no server path in a public body (H26)
        return body
    raise AssertionError("no app")


def _cache_check(body: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in body["checks"] if c["component"] == "demo_cache")


def test_the_mi300x_label_needs_the_package_to_say_so(
    serving: Serving, serving_dir: Path, tmp_path: Path
) -> None:
    gemma = settings_for(serving_dir, tmp_path / "x").gemma_model
    package = _package(serving_dir, tmp_path, models=(gemma, gemma), manifest=MI300X_MANIFEST)
    body = _readyz(serving, package, tmp_path)
    assert body["label"] == s.CACHED_LABEL == "Precomputed on AMD MI300X"
    assert _cache_check(body)["status"] == "ok"


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
        # drafts of another model than GEMMA_MODEL
        (("google/gemma-4-31B-it", "fake-vllm"), MI300X_MANIFEST, "fake-vllm"),
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
    assert body["label"] == s.REHEARSAL_LABEL and "MI300X" not in body["label"].split("not")[0]
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


def test_the_matrix_is_encoded_off_the_event_loop(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """json.dumps and gzip of about 20,000 points run in a worker thread, so the single worker
    keeps answering other requests meanwhile."""
    from apps.api.routes import manager

    loops: list[bool] = []
    encode = manager.encode_large

    def spy(model: Any, gzipped: bool) -> tuple[bytes, bool]:
        try:
            asyncio.get_running_loop()
            loops.append(True)
        except RuntimeError:
            loops.append(False)
        return encode(model, gzipped)

    monkeypatch.setattr(manager, "encode_large", spy)
    plain = api.client.get("/api/portfolio/matrix", headers={"Accept-Encoding": "identity"})
    zipped = api.client.get("/api/portfolio/matrix", headers={"Accept-Encoding": "gzip"})
    assert plain.status_code == zipped.status_code == 200
    assert zipped.headers["Content-Encoding"] == "gzip" and "Content-Encoding" not in plain.headers
    assert zipped.json() == plain.json()  # the client decompresses
    assert loops == [False, False]


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
