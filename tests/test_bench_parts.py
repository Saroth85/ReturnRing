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
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import xgboost

import ml.gnn
from apps.api.schemas import BenchmarksResponse
from bench import amdsmi, record
from bench.__main__ import main as bench_main
from bench.api_view import benchmarks_response
from bench.b1_xgboost import (
    B1Config,
    compare_devices,
    compare_scoring,
    nested_subsets,
    run_b1,
)
from bench.b2_gnn import B2Config, TimingError, check_timing, find_hook, run_b2
from bench.b3_llm import B3Config, fixed_case_list, run_b3, server_info
from bench.b4_pipeline import PY, STEP_NAMES, STEPS, B4Config, Step, run_b4
from bench.batch_generation import main as batch_main
from bench.cases import NO_SNAPSHOT, build_cases
from bench.cost import decisions_per_day
from bench.fake_vllm import FakeLatency, FakeVllm
from contracts.io import read_table, write_table
from llm.case import missing_evidence_problems
from llm.client import LlmUnavailable, VllmClient, VllmConfig
from llm.validator import check_case, validate
from ml.propensity.run import training_data
from tests.bench_fixtures import (
    AS_OF,
    FIXTURE,
    MISSING_EVIDENCE,
    RETURNS,
    fake_amd_smi,
    write_store,
)
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
    # Scoring uses the fixed-rounds model: the same number of trees on every device (G9).
    for protocol in ("A", "B"):
        (scoring,) = part.details["protocols"][protocol]["scoring"]
        assert scoring["model"] == "fixed4" and scoring["trees"] == 4
    assert "trees" in metrics["score_batch_seconds_a"].label


def b1_run(device: str, variant: str, rounds: list[int], seconds: float) -> dict[str, Any]:
    return {
        "protocol": "A",
        "device": device,
        "fraction": 1.0,
        "variant": variant,
        "rows_fit": 1000,
        "rounds_each": rounds,
        "rounds_run": rounds[len(rounds) // 2],
        "train_seconds_median": seconds,
        "seconds_per_100_rounds_median": round(100 * seconds / rounds[0], 3),
    }


def test_b1_speedups_compare_identical_work_only() -> None:
    """Review G1: early stopping ends at another round on each device, so its times are shown
    side by side with their rounds; a speedup is computed for the fixed-rounds runs only, or for
    early stopping when every repeat ran the same rounds on both devices."""
    runs = [
        b1_run("cpu", "es", [120, 120], 12.0),
        b1_run("cuda", "es", [90, 90], 1.0),
        b1_run("cpu", "fixed200", [200, 200], 20.0),
        b1_run("cuda", "fixed200", [200, 200], 2.0),
    ]
    speedups, early = compare_devices(runs)
    assert speedups == [
        {
            "protocol": "A",
            "fraction": 1.0,
            "variant": "fixed200",
            "rounds": {"cpu": [200, 200], "mi300x": [200, 200]},
            "cpu_over_gpu": 10.0,
        }
    ]
    (es,) = early
    assert es["rounds"] == {"cpu": [120, 120], "mi300x": [90, 90]} and not es["same_rounds"]
    assert "cpu_over_gpu" not in es
    assert es["per_100_rounds_cpu_over_gpu"] == pytest.approx(10.0 / 1.111, 1e-3)
    same = [b1_run("cpu", "es", [90, 90], 9.0), b1_run("cuda", "es", [90, 90], 1.0)]
    _, (es_same,) = compare_devices(same)
    assert es_same["same_rounds"] and es_same["cpu_over_gpu"] == 9.0


def test_b1_scoring_ratios_need_the_same_trees() -> None:
    scoring = [
        {
            "device": "cpu",
            "trees": 200,
            "model": "fixed200",
            "batch_seconds": {"p50": 2.0},
            "single_row_ms": {"p50": 1.0},
        },
        {
            "device": "cuda",
            "trees": 200,
            "model": "fixed200",
            "batch_seconds": {"p50": 0.5},
            "single_row_ms": {"p50": 2.0},
        },
    ]
    (row,) = compare_scoring("A", scoring)
    assert row["batch_cpu_over_gpu"] == 4.0 and row["single_row_p50_cpu_over_gpu"] == 0.5
    scoring[1]["trees"] = 150
    (row,) = compare_scoring("A", scoring)
    assert "batch_cpu_over_gpu" not in row and "trees differ" in row["not_computed"]


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


COMPARABLE = {"aggregator": "mean", "backend": "dgl", "sample": 1.0, "seed": 42}


def timing(protocol: str = "A", device: str = "cpu", /, **overrides: Any) -> dict[str, Any]:
    """A record as ml.gnn.b2_timing returns it (bench.b2_gnn.GnnTiming); made-up numbers."""
    cpu = device == "cpu"
    record_: dict[str, Any] = {
        "protocol": protocol,
        "device_requested": device,
        "device_effective": "cpu" if cpu else "cuda:0",
        "model": "GraphSAGE: 1 heterogeneous SAGE layer of 16 units, mean aggregator, dropout "
        "0.2, Adam 0.01, MLP head of 64 units; dgl backend",
        "comparable": dict(COMPARABLE),
        "sample": 1.0,
        "nodes": 1200,
        "edges": 5000,
        "epochs": 5 if cpu else 7,  # early stopping fires at another epoch on each device
        "cold_start_seconds": 2.5,
        "epoch_seconds": [1.4, 1.0, 1.2, 0.9, 1.1] if cpu else [0.6] + [0.2] * 6,
        "epoch_seconds_median": 1.1 if cpu else 0.2,
        "train_seconds": 6.7 if cpu else 1.8,
        "inference_seconds": 0.4 if cpu else 0.1,
        "f1": 0.78 if protocol == "A" else 0.74,
        "f1_at_half": 0.77 if protocol == "A" else 0.72,
        "threshold": 0.45,
        "population": "all" if protocol == "A" else "decision_time",
        "population_events": 900,
        "data_origin": "test fixture",
        "versions": {"torch": "2.8.0", "dgl": "2.4.0"},
        "hardware": "test CPU (4 logical CPUs, 4 threads)" if cpu else "AMD Instinct MI300X",
        "peak_memory_gib": None if cpu else 1.5,
    }
    record_.update(overrides)
    return record_


def test_b2_without_its_function_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "ml.gnn", types.ModuleType("ml.gnn"))
    hook, reason = find_hook()
    assert hook is None and "has no b2_timing" in reason


def test_b2_turns_the_hook_record_into_measurements(tmp_path: Path) -> None:
    calls: list[tuple[str, str, float, Path, bool]] = []

    def hook(raw_dir: Path, *, protocol: str, device: str, **kw: Any) -> Mapping[str, object]:
        calls.append((protocol, device, kw["sample"], kw["out"], kw["controls"]))
        return timing(protocol, device, sample=kw["sample"])

    config = B2Config(
        raw_dir=Path("raw"),
        devices=("cpu", "cuda"),
        samples=(0.5, 1.0),
        out=tmp_path / "artifacts",
        run_dir=tmp_path / "run",
    )
    part = run_b2(config, hook=hook)
    half = tmp_path / "run" / "b2-sample-50"
    assert calls[:2] == [("A", "cpu", 0.5, half, False), ("A", "cuda", 0.5, half, False)]
    assert ("B", "cuda", 1.0, tmp_path / "artifacts", True) in calls  # US-202 files, controls
    assert part.status == "measured"
    metrics = {(m.metric, m.device): m for m in part.measurements}
    f1_a = metrics[("gnn_f1_a", "mi300x")]
    assert (
        f1_a.unit == "ratio" and "0.792" in f1_a.label and "not product performance" in (f1_a.label)
    )
    f1_b = metrics[("gnn_f1_b", "cpu")]
    assert "decision-time" in f1_b.label and "paper" not in f1_b.label.lower()  # ADR-10
    assert ("gnn_f1_at_half_a", "cpu") in metrics
    assert ("gnn_f1_a_50pct", "cpu") not in metrics  # F1 only on the full data, as US-202
    assert ("gnn_epoch_seconds_median_b_50pct", "cpu") in metrics
    cold = [m for m in part.measurements if m.phase == "cold_start"]
    assert len(cold) == 4  # one per job: device x data size, not per protocol
    assert all("import torch" in m.label for m in cold)
    speedup = next(
        s for s in part.details["speedups"] if s["protocol"] == "B" and s["sample"] == 1.0
    )
    assert speedup["epoch_cpu_over_gpu"] == pytest.approx(1.1 / 0.2, 1e-3)  # median epoch
    assert speedup["epochs"] == {"cpu": 5, "mi300x": 7}
    assert "train_cpu_over_gpu" not in speedup  # totals depend on early stopping
    assert "max-pool" not in str(part.config) and "mean aggregator" in str(part.config["model"])
    assert part.config["samples"] == "0.5,1"


def test_b2_computes_no_speedup_between_different_settings(tmp_path: Path) -> None:
    def hook(raw_dir: Path, *, protocol: str, device: str, **kw: Any) -> Mapping[str, object]:
        other = {**COMPARABLE, "seed": 7} if device == "cuda" else COMPARABLE
        return timing(protocol, device, comparable=other)

    config = B2Config(raw_dir=Path("raw"), devices=("cpu", "cuda"), samples=(1.0,), out=tmp_path)
    part = run_b2(config, hook=hook)
    assert all("epoch_cpu_over_gpu" not in s for s in part.details["speedups"])
    assert all("seed" in s["not_computed"] for s in part.details["speedups"])
    assert any("no speedup" in n for n in part.notes)


@pytest.mark.parametrize(
    ("overrides", "device", "match"),
    [
        ({"f1": 1.3}, "cpu", "f1"),
        ({"f1_at_half": -0.1}, "cpu", "f1_at_half"),
        ({"epoch_seconds": []}, "cpu", "epoch_seconds"),
        ({"nodes": 0}, "cpu", "nodes"),
        ({"protocol": "B"}, "cpu", "asked for protocol"),
        ({"device_effective": "cpu"}, "cuda", "GPU not used"),
        ({"versions": {"torch": 2}}, "cpu", "versions"),
        ({"population": "decision_time"}, "cpu", "population"),
        ({"model": " "}, "cpu", "model"),
        ({"comparable": []}, "cpu", "comparable"),
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
    assert by_key["insufficient"].case["missing_evidence"] == [MISSING_EVIDENCE]
    assert cases.as_of == AS_OF
    assert build_cases(store, limit=2).summary()["cases"] == 2
    assert NO_SNAPSHOT  # used when a guarantee request has no snapshot before it


def test_the_missing_evidence_texts_bench_uses_pass_the_case_checks() -> None:
    # llm.case refuses a missing-evidence item with a number (ADR-14 decision 11): the fixture
    # sentence and the stand-in for a customer without a snapshot must both be number-free, or
    # every insufficient-evidence case would be skipped instead of measured.
    assert missing_evidence_problems(MISSING_EVIDENCE) == []
    assert missing_evidence_problems(NO_SNAPSHOT) == []


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
        "first_b3_request_ms",
        "latency_p50_ms_batch_4",
        "batch_wall_seconds_4",
        "output_tokens_per_second_batch_1",
        "latency_p50_ms_concurrency_3",
        "request_tokens_per_second_concurrency_3",
        "cost_eur_per_1000_decisions_always_on",
    ):
        assert name in metrics, name
    # 8 decisions per load: a p95 needs at least 20 values, so none is reported (review G4).
    assert not any("p95" in name for name in metrics)
    assert metrics["latency_p50_ms_batch_4"].batch_size == 4
    assert metrics["latency_p50_ms_batch_4"].concurrency == 4
    assert "illustrative" in metrics["latency_p50_ms_batch_4"].origin.lower()
    # Without a first-request file the first decision of B3 is not a cold start (G7, G15).
    first = metrics["first_b3_request_ms"]
    assert first.phase == "generation" and first.warm is True and "not a cold start" in first.label
    d = part.details
    assert d["batches"]["4"]["decisions"] == 8 and d["batches"]["4"]["failed"] == 0
    assert d["concurrency"]["3"]["decisions"] == 8  # the same case list as every other load
    assert d["batches"]["4"]["paths"] == {"model": 8}
    assert d["batches"]["4"]["latency_ms"]["p95"] is None  # n < 20
    # Review G4: every batch size and concurrency level runs the same cases.
    loads = [*d["batches"].values(), *d["concurrency"].values()]
    assert all(load["case_mix"] == loads[0]["case_mix"] for load in loads)
    assert sum(loads[0]["case_mix"].values()) == 8
    assert d["case_list"]["decisions_per_load"] == 8
    assert d["prefix_cache_pass"]["decisions"] == len(set(d["case_list"]["return_ids"]))
    assert d["fake_server"]["max_in_flight"] >= 3
    assert "unavailable" in d["cost"]["scenarios"]["nightly-batch"]  # no live rate
    assert d["cost"]["inputs"]["decisions_per_day"] == "0.21"
    assert "vllm_start_to_healthy_seconds" not in metrics
    assert "Dry run" in part.notes[0]


def test_the_case_list_is_seeded_and_stratified_by_decision(store: Path) -> None:
    cases = build_cases(store).cases
    first = fixed_case_list(cases, 12, seed=42)
    assert [c.return_id for c in first] == [c.return_id for c in fixed_case_list(cases, 12, 42)]
    assert [c.return_id for c in first] != [c.return_id for c in fixed_case_list(cases, 12, 7)]
    mix = Counter(c.template_key for c in first)
    assert set(mix) == {c.template_key for c in cases}  # every kind of decision
    assert mix == Counter(c.template_key for c in fixed_case_list(cases, 12, 7))
    assert max(mix.values()) - min(mix.values()) <= 1  # six kinds, one case each in the store
    # A rare kind keeps one case when the list has room for every kind; the rest follows the
    # store's proportions.
    skewed = [c for c in cases if c.template_key == "Q1"] * 20 + [
        c for c in cases if c.template_key == "Q3"
    ]
    mix = Counter(c.template_key for c in fixed_case_list(skewed, 10, seed=42))
    assert mix == {"Q1": 9, "Q3": 1}


def test_b3_reports_p95_from_twenty_decisions(store: Path) -> None:
    part = run_b3(tiny_b3(store, batch_sizes=(1, 4), batch_repeats=5, concurrency=(2,)))
    metrics = {m.metric: m for m in part.measurements}
    assert part.details["case_list"]["decisions_per_load"] == 20
    assert metrics["latency_p95_ms_batch_1"].repeats == 20
    assert "latency_p95_ms_concurrency_2" in metrics


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
    (tmp_path / "conditions.txt").write_text("page cache dropped before the start\n")
    (tmp_path / "disk.txt").write_text("187\n")
    (tmp_path / "first.json").write_text(
        json.dumps({"ok": True, "latency_ms": 2345.6, "completion_tokens": 120})
    )
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
                    ],
                    "nightly": {"seconds": 25.25, "included": ["generate (command 1 of 2)"]},
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
                vllm_conditions_file=tmp_path / "conditions.txt",
                first_request_file=tmp_path / "first.json",
                disk_used_file=tmp_path / "disk.txt",
                b4_part=b4,
            )
        )
    assert part.status == "measured"
    metrics = {m.metric: m for m in part.measurements}
    start = metrics["vllm_start_to_healthy_seconds"]
    assert start.value == 450.0
    assert "page cache dropped" in start.label and "not a start from a snapshot" in start.label
    first = metrics["first_request_ms"]  # timed by make bench-vllm before B4 used vLLM
    assert first.value == 2345.6 and first.phase == "cold_start" and first.warm is False
    assert "first_b3_request_ms" in metrics and metrics["first_b3_request_ms"].warm
    nightly = part.details["cost"]["scenarios"]["nightly-batch"]
    # The nightly seconds of B4: no OOD store, no risk evaluation, no generation (G6).
    assert nightly["breakdown"]["pipeline_seconds"]["value"] == "25.3"
    vllm = nightly["breakdown"]["vllm_start_seconds"]
    assert vllm["value"] == "450.0" and "lower bound" in vllm["origin"]
    snapshot = nightly["breakdown"]["snapshot_usd_per_day"]
    assert snapshot["snapshot_gb"] == "187" and snapshot["origin"].startswith("measured")
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


def test_the_first_request_is_timed_before_anything_else_uses_vllm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review G7 and G15: `make bench-vllm` times one guided-JSON request right after vLLM is
    healthy (before B4's batch generation), the cold start B3 reports."""
    run_id = "20261014T090000Z-gpu"
    with FakeVllm(latency=FAST) as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        monkeypatch.setenv("VLLM_API_KEY", fake.api_key)
        monkeypatch.setenv("GEMMA_MODEL", fake.model)
        code = bench_main(
            ["first-request", "--run-id", run_id, "--artifacts", str(tmp_path / "artifacts")]
        )
        assert fake.requests == 1
    assert code == 0
    doc = json.loads((tmp_path / "artifacts" / run_id / "first-request.json").read_text())
    assert doc["ok"] and doc["latency_ms"] > 0 and doc["completion_tokens"] > 0
    assert doc["case"] == "contracts/examples/llm_case.json"
    monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens there
    assert bench_main(["first-request", "--run-id", run_id, "--artifacts", str(tmp_path)]) == 1
    failed = json.loads((tmp_path / run_id / "first-request.json").read_text())
    assert failed["ok"] is False and failed["error"]


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


def sleep_step(
    name: str,
    seconds: float,
    code: int = 0,
    executes_on: str = "cpu",
    nightly: tuple[bool, ...] | None = None,
    commands: int = 1,
) -> Step:
    script = f"import time, sys; time.sleep({seconds}); sys.exit({code})"
    return Step(
        name,
        f"stand-in {name}",
        tuple((PY, "-c", script) for _ in range(commands)),
        executes_on=executes_on,  # type: ignore[arg-type]
        nightly=nightly,
    )


def test_b4_times_each_step_with_the_gpu_samples_inside_it(tmp_path: Path) -> None:
    table = (sleep_step("one", 1.0), sleep_step("two", 1.0, executes_on="vllm"))
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
    # Review G3: a CPU step on the droplet is a CPU number; only the step that ran on the GPU
    # (the generation through vLLM) is an MI300X number.
    one, two = metrics["step_seconds_one"], metrics["step_seconds_two"]
    assert one.value >= 1.0 and one.device == "cpu" and "droplet CPU" in one.origin
    assert two.device == "mi300x" and "MI300X" in two.origin
    total = metrics["pipeline_seconds_total"]
    assert total.value >= 2.0 and total.device == "cpu" and "on the MI300X" in total.label
    assert metrics["pipeline_seconds_gpu_steps"].device == "mi300x"
    assert metrics["pipeline_seconds_gpu_steps"].value == two.value
    assert metrics["gpu_activity_mean_percent_one"].device == "mi300x"  # amd-smi reads the GPU
    assert [s["executes_on"] for s in part.details["steps"]] == ["cpu", "mi300x"]
    assert metrics["gpu_activity_mean_percent_one"].value == 42.0
    assert metrics["vram_used_max_gib_total"].unit == "gib"
    assert metrics["power_mean_watts_two"].value == 350.0
    assert all(m.data_nature == "synthetic" and m.phase == "pipeline" for m in part.measurements)
    assert part.details["gpu_timeline"] and part.config["llm_mode"] == "vllm"


def test_b4_generation_with_the_mock_model_is_a_cpu_step(tmp_path: Path) -> None:
    table = (sleep_step("gen", 0.0, executes_on="vllm"),)
    part = run_b4(
        B4Config(run_dir=tmp_path, device="gpu", table=table, steps=("gen",), llm_mode="mock")
    )
    (step,) = [m for m in part.measurements if m.metric == "step_seconds_gen"]
    assert step.device == "cpu" and part.details["steps"][0]["executes_on"] == "cpu"


def test_b4_sums_the_nightly_seconds_the_cost_reads(tmp_path: Path) -> None:
    """Review G6: the cost of a night counts the main store's operating steps, not the OOD
    store or the offline risk evaluation, and not the generation (the cost adds it at the B3
    rate)."""
    table = (
        sleep_step("both", 0.2, nightly=(True, False), commands=2),
        sleep_step("offline", 0.2, nightly=(False,)),
        sleep_step("gen", 0.2, executes_on="vllm"),
    )
    part = run_b4(B4Config(run_dir=tmp_path, table=table, steps=("both", "offline", "gen")))
    nightly = part.details["nightly"]
    first = part.details["steps"][0]["commands"][0]["seconds"]
    assert nightly["seconds"] == pytest.approx(first, abs=1e-3)
    assert nightly["included"] == ["both (command 1 of 2)"]
    assert set(nightly["excluded"]) == {"both (command 2 of 2)", "offline", "gen"}
    metrics = {m.metric: m for m in part.measurements}
    assert metrics["pipeline_seconds_nightly"].value == pytest.approx(first, abs=1e-3)
    assert "without" in metrics["pipeline_seconds_nightly"].label


def test_b4_default_nightly_commands_are_the_main_stores() -> None:
    nightly = {s.name: s.nightly for s in STEPS}
    for name in ("generate", "graph", "features", "anomalies"):
        assert nightly[name] == (True, False), name  # main store yes, OOD store no
    assert nightly["risk"] == (False,)  # offline training and evaluation
    assert nightly["scoring"] == (True,)
    executes = {s.name: s.executes_on for s in STEPS}
    assert executes["batch_generation"] == "vllm"
    assert {v for k, v in executes.items() if k != "batch_generation"} == {"cpu"}


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


def test_make_bench_cpu_runs_end_to_end(
    raw: Path, store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B2 trains GraphSAGE where PyTorch is installed (tests/test_gnn_jobs.py); here it says why
    # it cannot run, whatever this machine has.
    monkeypatch.setattr(ml.gnn, "b2_unavailable", lambda: "PyTorch is not installed here")
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
    # A CPU run is a harness check: it never becomes the served run by itself (review G8).
    assert not (tmp_path / "results" / "latest.json").exists()
    assert record.resolve_latest(tmp_path / "results", "latest-cpu.json").name == f"{run_id}.json"
    assert all("provenance" in m for m in doc["measurements"])
