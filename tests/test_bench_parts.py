"""US-902, benchmarks B1 to B4 on fakes and tiny fixtures (no GPU, no model, no network).

B1 runs on the seeded ASOS-shaped replica of tests/propensity_fixtures.py, B3 on the hand-built
scored store of tests/bench_fixtures.py through the local fake OpenAI-compatible server, B4 on
stand-in steps with the fake amd-smi. Every number is a test value; real measurements come from
`make bench` (docs/results/benchmarks-*.md)."""

from __future__ import annotations

import json
import sys
import time
import types
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import xgboost

from apps.api.schemas import BenchmarksResponse
from bench import amdsmi, record
from bench.__main__ import main as bench_main
from bench.api_view import benchmarks_response
from bench.b1_xgboost import B1Config, nested_subsets, run_b1
from bench.b2_gnn import B2Config, TimingError, check_timing, find_hook, run_b2
from bench.b3_llm import B3Config, run_b3, server_info
from bench.b4_pipeline import PY, STEP_NAMES, STEPS, B4Config, Step, run_b4
from bench.batch_generation import main as batch_main
from bench.cases import NO_SNAPSHOT, build_cases
from bench.cost import decisions_per_day
from bench.fake_vllm import FakeLatency, FakeVllm
from contracts.io import read_table, write_table
from llm.client import LlmUnavailable, VllmClient, VllmConfig
from llm.validator import check_case, validate
from ml.propensity.run import training_data
from tests.bench_fixtures import AS_OF, FIXTURE, RETURNS, fake_amd_smi, write_store
from tests.propensity_fixtures import random_raw, write_raw

ROOT = Path(__file__).resolve().parents[1]
FAST = FakeLatency(ttft_s=0.001, tokens_per_s=1e6)


@pytest.fixture(scope="module")
def raw(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bench-asos")
    return write_raw(root / "data", *random_raw(seed=5, events_per_split=1500))


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_store(tmp_path_factory.mktemp("bench-store"))


def tiny_b1(raw: Path, **overrides: Any) -> B1Config:
    fields: dict[str, Any] = {
        "raw_dir": raw,
        "fractions": (0.5, 1.0),
        "repeats": 2,
        "max_rounds": 30,
        "fixed_rounds": 4,
        "single_row_calls": 5,
        "threads": 1,
        "warmup_rows": 300,
        "warmup_rounds": 2,
    }
    fields.update(overrides)
    return B1Config(**fields)


# ---- B1 -----------------------------------------------------------------------------------------


def test_b1_times_both_protocols_on_growing_sizes_with_their_quality(raw: Path) -> None:
    part = run_b1(tiny_b1(raw))
    assert part.status == "measured"
    metrics = {m.metric: m for m in part.measurements}
    assert "cold_start_seconds" in metrics and metrics["cold_start_seconds"].warm is False
    for p in ("a", "b"):
        for variant in ("es", "fixed4"):
            small = metrics[f"train_seconds_{p}_{variant}_50pct"]
            full = metrics[f"train_seconds_{p}_{variant}_100pct"]
            assert small.rows is not None and full.rows is not None and small.rows < full.rows
            assert small.repeats == 2 and full.phase == "training" and full.warm
        assert metrics[f"score_batch_seconds_{p}"].phase == "scoring"
        assert metrics[f"score_single_row_p95_ms_{p}"].unit == "milliseconds"
    assert {m.protocol for m in part.measurements if m.protocol} == {"paper", "decision_time"}
    assert all(m.nature == "real" and m.data_nature == "real" for m in part.measurements)
    runs = part.details["protocols"]["A"]["runs"]
    fixed = [r for r in runs if r["variant"] == "fixed4"]
    assert fixed and all(r["rounds_each"] == [4, 4] for r in fixed)  # identical work
    assert all(r["validation_logloss_median"] > 0 and r["device_effective"] == "cpu" for r in runs)
    # The full size is exactly the matrices US-201 trains on.
    data = training_data(raw, "A")
    assert metrics["train_seconds_a_es_100pct"].rows == data.y_fit.size
    assert part.config["max_depth"] == 4 and part.config["threads"] == 1
    assert part.versions["xgboost"] == xgboost.__version__
    assert "CPU only" in " ".join(part.notes)


def test_b1_refuses_a_gpu_run_that_did_not_use_the_gpu(raw: Path) -> None:
    """xgboost-cpu has no GPU: the cuda run either errors or falls back to the CPU, and a CPU
    fallback is never reported as an MI300X number (train.check_device)."""
    with pytest.raises((RuntimeError, xgboost.core.XGBoostError)):
        run_b1(tiny_b1(raw, devices=("cuda",), fractions=(1.0,), repeats=1))


def test_nested_subsets_grow_and_are_seeded() -> None:
    subsets = nested_subsets(100, (0.1, 0.5, 1.0), seed=4)
    assert [len(s) for s in subsets.values()] == [10, 50, 100]
    assert set(subsets[0.1]) <= set(subsets[0.5]) <= set(subsets[1.0])
    assert list(subsets[0.5]) == sorted(subsets[0.5])
    assert (nested_subsets(100, (0.5,), seed=4)[0.5] == subsets[0.5]).all()
    with pytest.raises(ValueError):
        B1Config(raw_dir=Path("x"), fractions=(0.0,))
    with pytest.raises(ValueError):
        B1Config(raw_dir=Path("x"), devices=("tpu",))  # type: ignore[arg-type]


# ---- B2 -----------------------------------------------------------------------------------------


def timing(protocol: str = "A", device: str = "cpu", /, **overrides: Any) -> dict[str, Any]:
    record_: dict[str, Any] = {
        "protocol": protocol,
        "device_requested": device,
        "device_effective": "cuda:0" if device == "cuda" else "cpu",
        "nodes": 1200,
        "edges": 5000,
        "epochs": 5,
        "cold_start_seconds": 2.5,
        "epoch_seconds": [1.0, 1.2, 0.9, 1.1],
        "train_seconds": 6.7 if device == "cpu" else 1.2,
        "inference_seconds": 0.4,
        "f1": 0.78,
        "threshold": 0.45,
        "population": "full test set",
        "data_origin": "test fixture",
        "versions": {"torch": "2.8.0", "dgl": "2.4.0"},
        "peak_memory_gib": 1.5,
    }
    record_.update(overrides)
    return record_


def test_b2_is_skipped_while_ml_gnn_is_absent() -> None:
    hook, reason = find_hook()
    if hook is not None:  # pragma: no cover - once US-202 lands
        pytest.skip("ml.gnn is present")
    assert "B2 skipped" in reason
    part = run_b2(B2Config(raw_dir=Path("data/raw")))
    assert part.status == "skipped" and part.measurements == [] and "b2_timing" in str(part.details)


def test_b2_without_its_function_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ml.gnn", types.ModuleType("ml.gnn"))
    hook, reason = find_hook()
    assert hook is None and "has no b2_timing" in reason


def test_b2_turns_the_hook_record_into_measurements() -> None:
    calls: list[tuple[str, str]] = []

    def hook(raw_dir: Path, *, protocol: str, device: str, seed: int) -> Mapping[str, object]:
        calls.append((protocol, device))
        return timing(protocol, device)

    part = run_b2(B2Config(raw_dir=Path("raw"), devices=("cpu", "cuda")), hook=hook)
    assert calls == [("A", "cpu"), ("A", "cuda"), ("B", "cpu"), ("B", "cuda")]
    assert part.status == "measured"
    names = {(m.metric, m.device) for m in part.measurements}
    assert ("gnn_f1_a", "mi300x") in names and ("gnn_epoch_seconds_median_b", "cpu") in names
    f1 = next(m for m in part.measurements if m.metric == "gnn_f1_a")
    assert f1.unit == "ratio" and "not product performance" in f1.label
    assert part.details["speedups"][0]["train_cpu_over_gpu"] == pytest.approx(6.7 / 1.2, 1e-3)


@pytest.mark.parametrize(
    ("overrides", "device", "match"),
    [
        ({"f1": 1.3}, "cpu", "f1"),
        ({"epoch_seconds": []}, "cpu", "epoch_seconds"),
        ({"nodes": 0}, "cpu", "nodes"),
        ({"protocol": "B"}, "cpu", "asked for protocol"),
        ({"device_effective": "cpu"}, "cuda", "GPU not used"),
        ({"versions": {"torch": 2}}, "cpu", "versions"),
        ({"population": " "}, "cpu", "population"),
        ({"train_seconds": float("nan")}, "cpu", "train_seconds"),
        ({"recall": 2.0}, "cpu", "recall"),
    ],
)
def test_b2_refuses_records_off_the_interface(
    overrides: dict[str, Any], device: str, match: str
) -> None:
    with pytest.raises(TimingError, match=match):
        check_timing(timing("A", device, **overrides), "A", device)
    broken = timing()
    del broken["f1"]
    with pytest.raises(TimingError, match="missing"):
        check_timing(broken, "A", "cpu")


# ---- Cases from the store -----------------------------------------------------------------------


def test_cases_are_the_engine_decisions_of_the_open_returns_golden_first(store: Path) -> None:
    cases = build_cases(store)
    assert cases.skipped == {}
    assert [c.customer_id for c in cases.cases][-1] == "C-NEW-1"  # not golden: last
    assert all(c.golden for c in cases.cases[:-1])
    by_key = {c.template_key: c for c in cases.cases}
    assert set(by_key) == {"Q1", "Q2", "Q3", "Q4", "insufficient", "guarantee"}
    for c in cases.cases:
        assert check_case(c.case, c.expected) == []
        assert all(f["nature"] == "synthetic" for f in c.case["facts"])
    marco = by_key["Q4"].case
    assert marco["cluster"] == {"id": "CL-TEST", "size": 2, "shared": ["card", "device"]}
    assert "risk" in marco and len(marco["risk"]["top_factors"]) == 3
    guarantee = by_key["guarantee"].case
    assert "risk" not in guarantee and "cluster" not in guarantee  # rule 4
    assert by_key["insufficient"].case["missing_evidence"] == ["fewer than 3 orders observed"]
    assert cases.as_of == AS_OF
    assert build_cases(store, limit=2).summary()["cases"] == 2
    assert NO_SNAPSHOT  # used when a guarantee request has no snapshot before it


def test_a_row_that_cannot_form_a_case_is_skipped_with_its_reason(
    store: Path, tmp_path: Path
) -> None:
    copy = write_store(tmp_path)
    table, _ = read_table("store/returns", copy)
    write_table("store/returns", table.filter(pc_not_ret2(table)), copy, FIXTURE)
    cases = build_cases(copy)
    assert list(cases.skipped) == ["RET-2"] and "KeyError" in cases.skipped["RET-2"]
    assert len(cases.cases) == len(RETURNS) - 1


def pc_not_ret2(table: Any) -> Any:
    import pyarrow.compute as pc

    return pc.not_equal(table.column("return_id"), "RET-2")


def test_cases_and_cost_never_read_the_ground_truth() -> None:
    """Rule 12: the B3 inputs are what a running service sees; no truth/ table is read."""
    for name in ("cases.py", "b3_llm.py", "cost.py", "batch_generation.py"):
        text = (ROOT / "bench" / name).read_text(encoding="utf-8")
        assert '"truth/' not in text and "read_labels" not in text, name


def test_decisions_per_day_counts_the_requests_before_the_scoring(store: Path) -> None:
    assert decisions_per_day(store, AS_OF) == Decimal("0.21")  # 6 requests in 28 days
    assert decisions_per_day(store, datetime(2026, 9, 1, tzinfo=UTC)) == Decimal("0.00")


# ---- The fake server and B3 ---------------------------------------------------------------------


def test_the_fake_server_speaks_to_the_real_client(store: Path) -> None:
    case = build_cases(store).cases[0]
    with FakeVllm(latency=FAST) as fake:
        client = VllmClient(VllmConfig(fake.base_url, fake.api_key, fake.model))
        completion = client.complete(case.case)
        assert validate(completion.content, case.expected, case.case).output is not None
        assert completion.completion_tokens and completion.completion_tokens > 10
        info = server_info(fake.base_url, fake.api_key)
        assert info == {
            "vllm_version": "fake-openai-0",
            "served_model": fake.model,
            "max_model_len": 16384,
        }
        with pytest.raises(LlmUnavailable, match="401"):
            VllmClient(VllmConfig(fake.base_url, "wrong-key", fake.model)).complete(case.case)
    with pytest.raises(ValueError, match="Rule 2"):
        server_info("http://example.com/v1", "key")


def tiny_b3(store: Path, **overrides: Any) -> B3Config:
    fields: dict[str, Any] = {
        "mode": "dry_run",
        "data_root": store,
        "batch_sizes": (1, 4),
        "batch_repeats": 2,
        "concurrency": (1, 3),
        "requests_per_level": 4,
        "warmup": 1,
        "fake_latency": FAST,
    }
    fields.update(overrides)
    return B3Config(**fields)


def test_b3_dry_run_measures_the_harness_and_says_so(store: Path) -> None:
    # Slow enough that the requests of a batch overlap on the fake server.
    part = run_b3(tiny_b3(store, fake_latency=FakeLatency(ttft_s=0.1, tokens_per_s=1e6)))
    assert part.status == "dry_run"
    assert {m.nature for m in part.measurements} == {"simulated"}
    metrics = {m.metric: m for m in part.measurements}
    for name in (
        "first_request_ms",
        "latency_p50_ms_batch_4",
        "latency_p95_ms_batch_4",
        "batch_wall_seconds_4",
        "output_tokens_per_second_batch_1",
        "latency_p95_ms_concurrency_3",
        "request_tokens_per_second_concurrency_3",
        "cost_eur_per_1000_decisions_always_on",
    ):
        assert name in metrics, name
    assert metrics["latency_p50_ms_batch_4"].batch_size == 4
    assert metrics["latency_p50_ms_batch_4"].concurrency == 4
    assert "illustrative" in metrics["latency_p50_ms_batch_4"].origin.lower()
    d = part.details
    assert d["batches"]["4"]["decisions"] == 8 and d["batches"]["4"]["failed"] == 0
    assert d["concurrency"]["3"]["decisions"] == 6  # max(4, 2 x 3)
    assert d["batches"]["4"]["paths"] == {"model": 8}
    assert d["fake_server"]["max_in_flight"] >= 3
    assert "unavailable" in d["cost"]["scenarios"]["nightly-batch"]  # no live rate
    assert d["cost"]["inputs"]["decisions_per_day"] == "0.21"
    assert "vllm_start_to_healthy_seconds" not in metrics
    assert "Dry run" in part.notes[0]


def test_b3_live_counts_failures_and_reports_no_latency_from_them(store: Path) -> None:
    with FakeVllm(latency=FAST, fail_status=500) as fake:
        part = run_b3(
            tiny_b3(
                store, mode="live", base_url=fake.base_url, api_key=fake.api_key, model=fake.model
            )
        )
    assert part.status == "failed"
    assert not any(m.phase == "generation" for m in part.measurements)
    assert part.details["batches"]["4"]["failed"] == 8
    assert part.details["batches"]["4"]["fallbacks"] == {"unavailable": 8}
    assert any("failed" in n for n in part.notes)


def test_b3_live_reads_the_cold_start_and_the_b4_pipeline(store: Path, tmp_path: Path) -> None:
    (tmp_path / "start.txt").write_text("2026-10-14T09:00:00Z\n")
    (tmp_path / "healthy.txt").write_text("2026-10-14T09:07:30Z\n")
    b4 = tmp_path / "B4.json"
    b4.write_text(
        json.dumps(
            {
                "status": "measured",
                "details": {
                    "steps": [
                        {"name": "generate", "seconds": 40.0},
                        {"name": "risk", "seconds": 140.5},
                        {"name": "batch_generation", "seconds": 300.0},
                    ]
                },
            }
        )
    )
    with FakeVllm(latency=FAST) as fake:
        part = run_b3(
            tiny_b3(
                store,
                mode="live",
                base_url=fake.base_url,
                api_key=fake.api_key,
                model=fake.model,
                batch_sizes=(1, 32),
                batch_repeats=1,
                concurrency=(2,),
                vllm_start_file=tmp_path / "start.txt",
                vllm_healthy_file=tmp_path / "healthy.txt",
                b4_part=b4,
            )
        )
    assert part.status == "measured"
    metrics = {m.metric: m for m in part.measurements}
    assert metrics["vllm_start_to_healthy_seconds"].value == 450.0
    nightly = part.details["cost"]["scenarios"]["nightly-batch"]
    assert nightly["breakdown"]["pipeline_seconds"]["value"] == "180.5"  # generation step out
    assert nightly["breakdown"]["vllm_start_seconds"] == {"value": "450.0", "origin": "measured"}
    assert metrics["cost_eur_per_1000_decisions_nightly_batch"].nature == "simulated"
    assert metrics["latency_p50_ms_batch_32"].nature == "real"
    projection = benchmarks_response(
        {"run_id": "20261014T090000Z-gpu", "benchmarks": {"B3": part.to_dict()}}
    )
    b3 = BenchmarksResponse.model_validate(projection).items[2]
    assert b3.status == "measured" and b3.results
    assert all(r.value.provenance.nature == "real" for r in b3.results)
    assert any("nightly-batch" in n and "simulated" in n for n in b3.notes)
    with pytest.raises(ValueError):
        B3Config(mode="live", data_root=store)  # no URL, key or model


def test_batch_generation_step_runs_every_open_return(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_MODE", "mock")
    out = tmp_path / "gen.jsonl"
    assert batch_main(["--data", str(store), "--out", str(out), "--concurrency", "3"]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == len(RETURNS) and {r["path"] for r in rows} == {"model"}
    summary = json.loads(capsys.readouterr().out)
    assert summary["llm_mode"] == "mock" and "no generation" in summary["note"]
    monkeypatch.setenv("LLM_MODE", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens there
    monkeypatch.setenv("VLLM_API_KEY", "k")
    assert batch_main(["--data", str(store), "--out", str(out), "--limit", "2"]) == 1
    with pytest.raises(SystemExit):
        batch_main(["--data", str(store), "--out", str(out), "--concurrency", "0"])


# ---- B4 -----------------------------------------------------------------------------------------


def sleep_step(name: str, seconds: float, code: int = 0) -> Step:
    script = f"import time, sys; time.sleep({seconds}); sys.exit({code})"
    return Step(name, f"stand-in {name}", ((PY, "-c", script),))


def test_b4_times_each_step_with_the_gpu_samples_inside_it(tmp_path: Path) -> None:
    table = (sleep_step("one", 1.0), sleep_step("two", 1.0))
    part = run_b4(
        B4Config(
            run_dir=tmp_path,
            device="gpu",
            table=table,
            steps=("one", "two"),
            sampler="inprocess",
            amd_smi=tuple(fake_amd_smi(tmp_path)),
            interval_s=0.05,
        )
    )
    assert part.status == "measured"
    metrics = {m.metric: m for m in part.measurements}
    assert metrics["step_seconds_one"].value >= 1.0 and metrics["step_seconds_one"].device == (
        "mi300x"
    )
    assert metrics["pipeline_seconds_total"].value >= 2.0
    assert metrics["gpu_activity_mean_percent_one"].value == 42.0
    assert metrics["vram_used_max_gib_total"].unit == "gib"
    assert metrics["power_mean_watts_two"].value == 350.0
    assert all(m.data_nature == "synthetic" and m.phase == "pipeline" for m in part.measurements)
    assert part.details["gpu_timeline"] and part.config["llm_mode"] == "vllm"


def test_b4_stops_at_the_first_failing_step_and_keeps_what_it_measured(tmp_path: Path) -> None:
    table = (sleep_step("one", 0.0), sleep_step("two", 0.0, code=4), sleep_step("three", 0.0))
    part = run_b4(B4Config(run_dir=tmp_path, table=table, steps=("one", "two", "three")))
    assert part.status == "failed"
    assert [s["name"] for s in part.details["steps"]] == ["one", "two"]
    assert [m.metric for m in part.measurements] == ["step_seconds_one"]
    assert any("Step two failed (exit 4)" in n for n in part.notes)
    assert any("mock model" in n for n in part.notes)
    log = Path(part.details["steps"][1]["commands"][0]["log"])
    assert log.exists()


def test_b4_reads_an_external_samples_file(tmp_path: Path) -> None:
    samples = tmp_path / "host.jsonl"
    reading = amdsmi.GpuReading(0, 80.0, 120.0, 192.0, 600.0)
    now = time.time()
    lines = [amdsmi.Sample(now + i * 0.1, (reading,)).to_dict() for i in range(-50, 50)]
    samples.write_text("".join(json.dumps(s) + "\n" for s in lines))
    part = run_b4(
        B4Config(
            run_dir=tmp_path,
            device="gpu",
            table=(sleep_step("one", 0.3),),
            steps=("one",),
            sampler="external",
            samples_file=samples,
        )
    )
    metrics = {m.metric: m for m in part.measurements}
    assert metrics["gpu_activity_max_percent_one"].value == 80.0
    with pytest.raises(ValueError):
        B4Config(run_dir=tmp_path, steps=("nightly",))


def test_b4_default_steps_are_the_pipeline_targets() -> None:
    """The steps time what `make pipeline`, `make score` and the batch generation run, on the
    bench's own copy: no command writes to data/ or docs/ of the checkout."""
    assert STEP_NAMES == (
        "generate",
        "graph",
        "features",
        "anomalies",
        "risk",
        "scoring",
        "batch_generation",
    )
    commands = {s.name: [" ".join(c) for c in s.commands] for s in STEPS}
    assert [c.split("--scenario ")[1] for c in commands["generate"]] == ["main", "ood"]
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for module in (
        "pipeline.graph",
        "pipeline.features",
        "pipeline.anomalies",
        "ml.risk",
        "ml.scoring",
        "pipeline.generator",
    ):
        assert f"python -m {module}" in makefile
        assert any(f"-m {module}" in c for cs in commands.values() for c in cs), module
    for command in (c for cs in commands.values() for c in cs):
        for token in command.split():
            assert not token.startswith(("data", "docs")), command
    assert "--source {data}" in commands["features"][1]
    assert "{artifacts}/risk/models" in commands["scoring"][0]


# ---- The CLI on CPU ------------------------------------------------------------------------------


def test_make_bench_cpu_runs_end_to_end(raw: Path, store: Path, tmp_path: Path) -> None:
    run_id = "20261013T120000Z-cpu"
    args = [
        "run",
        "--device",
        "cpu",
        "--run-id",
        run_id,
        "--parts",
        "B1,B2,B3",
        "--raw",
        str(raw),
        "--data",
        str(store),
        "--artifacts",
        str(tmp_path / "artifacts"),
        "--results",
        str(tmp_path / "results"),
        "--b1-protocols",
        "B",
        "--b1-fractions",
        "1",
        "--b1-repeats",
        "1",
        "--b1-fixed-rounds",
        "0",
        "--b1-single-row-calls",
        "3",
        "--threads",
        "1",
        "--b3-batches",
        "1,2",
        "--b3-repeats",
        "1",
        "--b3-concurrency",
        "2",
        "--b3-requests",
        "2",
        "--b3-warmup",
        "1",
    ]
    assert bench_main(args) == 0
    doc = record.read_json(tmp_path / "results" / f"{run_id}.json")
    assert {b: p["status"] for b, p in doc["benchmarks"].items()} == {
        "B1": "measured",
        "B2": "skipped",
        "B3": "dry_run",
    }
    api = BenchmarksResponse.model_validate(doc["api"])
    assert [i.status for i in api.items] == ["measured", "pending", "pending", "pending"]
    assert record.resolve_latest(tmp_path / "results").name == f"{run_id}.json"
    assert all("provenance" in m for m in doc["measurements"])
