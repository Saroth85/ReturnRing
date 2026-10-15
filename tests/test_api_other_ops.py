"""GET /api/benchmarks (US-902) and GET /api/telemetry (US-905), and the routes of the other
group (ADR-15 sections 3 and 4; rule 11, NFR-16).

Benchmarks come from the versioned run file the benchmark job writes (bench/results), measured
or pending, never illustrative; telemetry from the table the amd-smi sampler fills, empty and
labelled on a CPU, with no GPU or LLM numbers in cached mode.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api.routes import other
from apps.api.routes.stubs import endpoints_of, implemented
from apps.api.services.other import telemetry as tm
from apps.api.services.other.benchmarks import BENCHMARK_IDS, load
from contracts.sql import llm_generations
from tests.api_harness import Api, open_api, settings_for, sqlite_url, validate_body

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples" / "api"


def test_every_endpoint_of_the_group_has_its_route() -> None:
    assert {(e.method, e.path) for e in implemented("other", other.router)} == {
        (e.method, e.path) for e in endpoints_of("other")
    }


# ---- Benchmarks ---------------------------------------------------------------------------------


def _measured_b1() -> dict[str, Any]:
    prov = {"origin": "B1, measured by bench on the droplet CPU; ASOS GraphReturns, CC BY 4.0",
            "period_start": "2026-10-14", "period_end": "2026-10-14", "nature": "real"}  # fmt: skip
    return {
        "id": "B1",
        "title": "XGBoost training on ASOS GraphReturns: CPU against MI300X",
        "status": "measured",
        "measured_at": "2026-10-14T00:39:31Z",
        "dataset": "ASOS GraphReturns train split",
        "versions": {"xgboost": "3.2.0"},
        "config": {"max_depth": 4, "tree_method": "hist"},
        "results": [
            {
                "metric": "train_seconds",
                "label": "Training time",
                "device": "cpu",
                "phase": "training",
                "value": {"value": 46.27, "unit": "seconds", "provenance": prov},
                "concurrency": None,
                "batch_size": None,
                "protocol": "paper",
            }
        ],  # fmt: skip
        "notes": ["CPU only"],
    }


def _write_run(
    folder: Path, items: list[dict[str, Any]], run_id: str = "20261014T0000Z-cpu"
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    run = {"schema": "returnring.bench/1", "run_id": run_id, "api": {"items": items}}
    (folder / f"{run_id}.json").write_text(json.dumps(run), encoding="utf-8")
    pointer = {"schema": "returnring.bench.latest/1", "run_id": run_id, "file": f"{run_id}.json"}
    (folder / "latest.json").write_text(json.dumps(pointer), encoding="utf-8")


def test_without_a_run_every_benchmark_is_pending(tmp_path: Path) -> None:
    body = load(tmp_path).model_dump(mode="json")
    validate_body("benchmarks", body)
    assert [b["id"] for b in body["items"]] == list(BENCHMARK_IDS)
    assert {b["status"] for b in body["items"]} == {"pending"}
    assert all("No benchmark run" in b["notes"][0] for b in body["items"])


def test_a_measured_run_is_served_with_its_provenance(tmp_path: Path) -> None:
    illustrative = json.loads((EXAMPLES / "benchmarks.json").read_text(encoding="utf-8"))
    b3 = next(b for b in illustrative["items"] if b["id"] == "B3")
    _write_run(tmp_path, [_measured_b1(), b3, {"id": "B2", "status": "nonsense"}])
    body = load(tmp_path).model_dump(mode="json")
    validate_body("benchmarks", body)
    items = {b["id"]: b for b in body["items"]}
    assert items["B1"]["status"] == "measured"
    assert items["B1"]["results"][0]["value"]["provenance"]["nature"] == "real"
    assert any("20261014T0000Z-cpu" in n for n in items["B1"]["notes"])
    assert items["B3"]["status"] == "pending" and "illustrative" in items["B3"]["notes"][0]
    assert items["B2"]["status"] == "pending" and items["B4"]["status"] == "pending"


def test_a_broken_pointer_or_run_is_pending(tmp_path: Path) -> None:
    (tmp_path / "latest.json").write_text('{"schema": "x", "file": "../../etc/passwd"}', "utf-8")
    assert {b.status for b in load(tmp_path).items} == {"pending"}
    _write_run(tmp_path, [_measured_b1()])
    (tmp_path / "20261014T0000Z-cpu.json").write_text("{", "utf-8")
    assert {b.status for b in load(tmp_path).items} == {"pending"}


def test_get_benchmarks_reads_the_directory_of_the_env(
    api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_run(tmp_path / "results", [_measured_b1()])
    monkeypatch.setenv("BENCHMARKS_DIR", str(tmp_path / "results"))
    response = api.client.get("/api/benchmarks")
    assert response.status_code == 200
    body = response.json()
    validate_body("benchmarks", body)
    assert body["items"][0]["status"] == "measured"


def test_the_committed_run_if_any_is_served_as_the_contract_says() -> None:
    folder = ROOT / "bench" / "results"
    if not (folder / "latest.json").is_file():
        pytest.skip("no benchmark run committed in this branch")
    validate_body("benchmarks", load(folder).model_dump(mode="json"))


# ---- Telemetry ----------------------------------------------------------------------------------

AMD_SMI = json.dumps([{
    "gpu": 0,
    "usage": {"gfx_activity": {"value": 64, "unit": "%"}},
    "power": {"socket_power": {"value": 412, "unit": "W"}},
    "mem_usage": {"used_vram": {"value": 145612, "unit": "MB"},
                  "total_vram": {"value": 196592, "unit": "MB"}},
}])  # fmt: skip


def test_amd_smi_layouts_are_read() -> None:
    (gpu,) = tm.parse_amd_smi("WARNING: something\n" + AMD_SMI)
    assert gpu.utilization_percent == 64 and gpu.power_watts == 412
    assert gpu.memory_total_gib == pytest.approx(191.984, abs=1e-3)
    wrapped = tm.parse_amd_smi(json.dumps({"gpu_data": json.loads(AMD_SMI)}))
    assert wrapped == [gpu]
    na = tm.parse_amd_smi('[{"gpu": 0, "usage": {"gfx_activity": "N/A"}}]')
    assert na[0].utilization_percent is None and na[0].memory_used_gib is None
    with pytest.raises(ValueError):
        tm.parse_amd_smi("amd-smi: command not found")


def test_on_a_cpu_telemetry_is_empty_and_labelled(api: Api) -> None:
    response = api.client.get("/api/telemetry")
    assert response.status_code == 200
    body = response.json()
    validate_body("telemetry", body)
    assert body["mode"] == "live" and body["gpu"] is None and body["gpu_history"] == []
    assert body["llm"] is None and body["api_latency_p95"] is None
    assert "sampler is not running" in body["note"] and body["interval_s"] == 5


def test_cached_telemetry_has_no_gpu_or_llm_numbers(api: Api) -> None:
    settings = settings_for(api.settings.serving_dir, api.database, demo_mode="cached")
    for cached in open_api(api.serving, settings, api.database, clock=api.clock):
        body = cached.client.get("/api/telemetry").json()
        validate_body("telemetry", body)
        assert body["mode"] == "cached" and body["gpu"] is None and body["llm"] is None
        assert body["note"] == tm.CACHED_NOTE


def test_sampled_gpu_metrics_are_served(api: Api, tmp_path: Path) -> None:
    source = tmp_path / "telemetry.json"
    source.write_text(AMD_SMI, encoding="utf-8")
    url = sqlite_url(api.database)
    assert asyncio.run(tm.sample_loop(url, source_file=source, interval_s=0, count=2)) == 2

    async def recent(conn: AsyncConnection) -> None:  # the sampler writes the wall clock time
        await conn.execute(
            tm.telemetry_samples.update().values(sampled_at=api.clock.now() - timedelta(seconds=5))
        )

    api.run(recent)
    body = api.client.get("/api/telemetry").json()
    validate_body("telemetry", body)
    assert body["device"] == tm.DEFAULT_DEVICE and body["note"] is None
    assert body["gpu"]["utilization"]["value"] == 64 and len(body["gpu_history"]) == 2
    assert body["gpu"]["memory_used"]["provenance"]["nature"] == "real"
    api.clock.advance(timedelta(minutes=5))
    stale = api.client.get("/api/telemetry").json()
    validate_body("telemetry", stale)
    assert stale["gpu"] is None and "No valid GPU sample" in stale["note"]


def test_a_failed_sample_is_kept_with_its_error(api: Api, tmp_path: Path) -> None:
    url = sqlite_url(api.database)
    missing = tmp_path / "nothing.json"
    assert asyncio.run(tm.sample_loop(url, source_file=missing, interval_s=0, count=1)) == 1
    body = api.client.get("/api/telemetry").json()
    validate_body("telemetry", body)
    assert body["gpu"] is None and body["gpu_history"] == []


def test_an_amd_smi_file_the_writer_stopped_updating_is_not_a_sample(
    api: Api, tmp_path: Path
) -> None:
    """The telemetry container rewrites the file every 5 s into a volume that survives a
    reboot. A file older than SOURCE_MAX_AGE_S is a reading of the past: the sampler stores an
    error, never a sample stamped now (rule 11), and the GPU check stays unavailable."""
    source = tmp_path / "amd-smi.json"
    source.write_text(AMD_SMI, encoding="utf-8")
    readings, error = tm.read_source(tm.AMD_SMI, source)
    assert len(readings) == 1 and error is None
    old = time.time() - tm.SOURCE_MAX_AGE_S - 1
    os.utime(source, (old, old))
    readings, error = tm.read_source(tm.AMD_SMI, source)
    assert readings == [] and error is not None and "not updated" in error
    assert asyncio.run(tm.sample_loop(sqlite_url(api.database), source_file=source, count=1)) == 1
    gpu = settings_for(api.settings.serving_dir, api.database, device="gpu")
    check = api.run(lambda conn: tm.component_check(conn, gpu, datetime.now(UTC)))  # wall time
    assert check.status == "unavailable"


def test_vllm_generations_give_the_llm_stats(api: Api) -> None:
    at = api.clock.now()

    async def generations(conn: AsyncConnection) -> None:
        for i, (latency, tokens) in enumerate([(2000, 200), (4000, 300), (3000, None)]):
            await conn.execute(llm_generations.insert().values(
                generation_id=f"GEN-T-{i}", kind="case_draft", return_id=None, decision_id=None,
                model="google/gemma-4-31B-it", llm_mode="vllm", served_from="live",
                generation_path="model", fallback_reason=None, model_calls=1,
                prompt_version="p", input_digest="0" * 64, llm_case={}, output={},
                validator_codes=[], latency_ms=latency, prompt_tokens=None,
                completion_tokens=tokens, tokens_per_second=None,
                created_at=at - timedelta(seconds=30 + i),
            ))  # fmt: skip

    api.run(generations)
    stats = api.run(lambda conn: tm.llm_stats(conn, at))
    assert stats is not None and stats.requests.value == 3
    assert stats.latency_p50.value == 3000 and stats.tokens_per_second.value == 83.33
    settings = settings_for(api.settings.serving_dir, api.database, llm_mode="vllm")
    for live in open_api(api.serving, settings, api.database, clock=api.clock):
        body = live.client.get("/api/telemetry").json()
        validate_body("telemetry", body)
        assert body["llm"]["requests"]["value"] == 3 and body["gpu"] is None


def test_the_sampler_command(api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """python -m apps.api.services.other.telemetry: DATABASE_URL, a source file, a count."""
    source = tmp_path / "telemetry.json"
    source.write_text(AMD_SMI, encoding="utf-8")
    argv = ["--source-file", str(source), "--count", "1", "--interval", "0"]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert tm.main(argv) == 2
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{api.database}")
    assert tm.main(argv) == 0
    readings, error = tm.read_source(["false"], None)
    assert readings == [] and error is not None and error.startswith("amd-smi exit")


def test_the_readiness_check_of_telemetry(api: Api, tmp_path: Path) -> None:
    at = api.clock.now()
    cpu = api.run(lambda conn: tm.component_check(conn, api.settings, at))
    assert (cpu.component, cpu.status) == ("telemetry", "disabled")
    gpu_settings = settings_for(api.settings.serving_dir, api.database, device="gpu")
    silent = api.run(lambda conn: tm.component_check(conn, gpu_settings, at))
    assert silent.status == "unavailable"
    cached = settings_for(api.settings.serving_dir, api.database, demo_mode="cached")
    assert api.run(lambda conn: tm.component_check(conn, cached, at)).status == "disabled"
    source = tmp_path / "telemetry.json"
    source.write_text(AMD_SMI, encoding="utf-8")
    asyncio.run(tm.sample_loop(sqlite_url(api.database), source_file=source, count=1))

    async def recent(conn: AsyncConnection) -> None:
        await conn.execute(tm.telemetry_samples.update().values(sampled_at=at))

    api.run(recent)
    assert api.run(lambda conn: tm.component_check(conn, gpu_settings, at)).status == "ok"


def test_readyz_reports_the_telemetry_sampler(api: Api, tmp_path: Path) -> None:
    """/readyz carries the telemetry check: disabled on a CPU, unavailable on a GPU whose
    sampler is silent (degraded), ok with a fresh amd-smi sample."""
    checks = {c["component"]: c for c in api.client.get("/readyz").json()["checks"]}
    assert checks["telemetry"]["status"] == "disabled"
    gpu = settings_for(api.settings.serving_dir, api.database, device="gpu")
    for app in open_api(api.serving, gpu, api.database, clock=api.clock):
        body = app.client.get("/readyz").json()
        validate_body("readyz_response", body)
        checks = {c["component"]: c for c in body["checks"]}
        assert checks["telemetry"]["status"] == "unavailable" and body["status"] == "degraded"
        source = tmp_path / "telemetry.json"
        source.write_text(AMD_SMI, encoding="utf-8")
        asyncio.run(tm.sample_loop(sqlite_url(api.database), source_file=source, count=1))

        async def recent(conn: AsyncConnection) -> None:
            await conn.execute(tm.telemetry_samples.update().values(sampled_at=api.clock.now()))

        api.run(recent)
        body = app.client.get("/readyz").json()
        checks = {c["component"]: c for c in body["checks"]}
        assert checks["telemetry"]["status"] == "ok" and body["status"] == "ready"


def test_the_sampler_survives_a_sample_it_cannot_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A sample written while the reset recreates the tables is skipped, not fatal."""
    source = tmp_path / "telemetry.json"
    source.write_text(AMD_SMI, encoding="utf-8")
    calls = 0
    original = tm.store_sample

    async def flaky(*args: Any, **kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise tm.SQLAlchemyError("the reset is recreating the tables")
        return await original(*args, **kwargs)

    monkeypatch.setattr(tm, "store_sample", flaky)
    url = sqlite_url(tmp_path / "telemetry.sqlite")
    written = asyncio.run(tm.sample_loop(url, source_file=source, count=2, interval_s=0))
    assert calls == 2 and written >= 1
    assert "sample not stored" in capsys.readouterr().err
