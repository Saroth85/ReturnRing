"""US-902, benchmark harness: records, statistics, the amd-smi sampler, cost, the API projection,
the report and the CLI. Every number here is a test value (nature synthetic or simulated); real
measurements come from `make bench` (docs/results/benchmarks-*.md)."""

from __future__ import annotations

import ast
import json
import shlex
import sys
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from apps.api.schemas import BenchmarksResponse
from bench import amdsmi, api_view, cost, env, record, stats
from bench.__main__ import main as bench_main
from bench.record import BenchError, Measurement, Part
from bench.report import render
from tests.bench_fixtures import fake_amd_smi

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 10, 13)
T0 = datetime(2026, 10, 13, 9, tzinfo=UTC)


def measurement(**overrides: Any) -> Measurement:
    fields: dict[str, Any] = {
        "benchmark": "B1",
        "metric": "train_seconds_a_es_100pct",
        "label": "Training time",
        "value": 12.5,
        "unit": "seconds",
        "device": "cpu",
        "phase": "training",
        "origin": "test measurement",
        "period_start": DAY,
        "period_end": DAY,
        "nature": "real",
        "data_nature": "real",
        "protocol": "paper",
    }
    fields.update(overrides)
    return Measurement(**fields)


def part(benchmark: str = "B1", status: str = "measured", **overrides: Any) -> Part:
    fields: dict[str, Any] = {
        "benchmark": benchmark,
        "status": status,
        "title": f"{benchmark} test part",
        "dataset": "test data",
        "started_at": T0,
        "finished_at": T0,
        "measurements": [measurement(benchmark=benchmark)] if status == "measured" else [],
        "notes": [f"{benchmark} note"],
    }
    fields.update(overrides)
    return Part(**fields)


# ---- Measurements, parts, run documents ----------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"benchmark": "B9"},
        {"metric": "Train Seconds"},
        {"metric": "x" * 65},
        {"label": ""},
        {"origin": "o" * 301},
        {"value": float("nan")},
        {"value": True},
        {"unit": "count", "value": 1.5},
        {"unit": "percent", "value": 101.0},
        {"unit": "ratio", "value": 1.2},
        {"unit": "eur", "value": 0.0034},
        {"unit": "seconds", "value": -1.0},
        {"unit": "furlongs"},
        {"device": "gpu"},
        {"phase": "warmup"},
        {"nature": "measured"},
        {"data_nature": "fake"},
        {"protocol": "A"},
        {"period_start": date(2026, 10, 14)},
        {"concurrency": 0},
        {"rows": True},
    ],
)
def test_a_measurement_without_its_rules_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(BenchError):
        measurement(**overrides)


def test_a_measurement_round_trips_with_its_provenance() -> None:
    m = measurement(rows=1000, warm=True, repeats=3, detail={"rounds": 12})
    data = m.to_dict()
    assert data["provenance"] == {
        "origin": "test measurement",
        "period_start": "2026-10-13",
        "period_end": "2026-10-13",
        "nature": "real",
    }
    assert Measurement.from_dict(json.loads(json.dumps(data))) == m
    with pytest.raises(BenchError):
        Measurement.from_dict({"metric": "x"})


def test_part_statuses_say_what_the_numbers_are() -> None:
    simulated = measurement(nature="simulated")
    with pytest.raises(BenchError, match="real measurements"):
        part(measurements=[simulated])  # measured needs a real number
    with pytest.raises(BenchError, match="dry run"):
        part(status="dry_run", measurements=[measurement()])
    with pytest.raises(BenchError, match="skipped"):
        part(status="skipped", measurements=[simulated])
    with pytest.raises(BenchError, match="belongs to"):
        part("B2", measurements=[measurement()])
    with pytest.raises(BenchError):
        part(status="done")
    assert part(status="failed", measurements=[measurement()]).status == "failed"
    assert part(status="dry_run", measurements=[simulated]).status == "dry_run"


def test_run_ids_are_sortable_file_names() -> None:
    assert record.new_run_id("cpu", T0) == "20261013T090000Z-cpu"
    assert record.check_run_id("20261013T090000Z-gpu-session-2") == "20261013T090000Z-gpu-session-2"
    assert record.run_device("20261013T090000Z-gpu") == "gpu"
    for bad in ("latest", "20261013-cpu", "20261013T090000Z-tpu", "../20261013T090000Z-cpu"):
        with pytest.raises(BenchError):
            record.check_run_id(bad)


def _run(tmp_path: Path, run_id: str, parts: list[Part]) -> Path:
    run_dir = tmp_path / "artifacts" / run_id
    for p in parts:
        record.write_part(run_dir, p)
    return run_dir


def test_merge_assembles_parts_and_a_gpu_run_stays_the_latest(tmp_path: Path) -> None:
    results = tmp_path / "results"
    cpu_id, gpu_id, later_cpu = (
        "20261013T090000Z-cpu",
        "20261014T090000Z-gpu",
        "20261015T090000Z-cpu",
    )
    for run_id in (cpu_id, gpu_id, later_cpu):
        run_dir = _run(tmp_path, run_id, [part("B1"), part("B2", "skipped")])
        doc = record.merge(run_id, run_dir, git={"commit": "abc", "dirty": False}, created_at=T0)
        assert [m["metric"] for m in doc["measurements"]] == ["train_seconds_a_es_100pct"]
        assert set(doc["benchmarks"]) == {"B1", "B2"}
        assert doc["rules"].keys() == {
            "comparable_quality",
            "separate_times",
            "full_cost",
            "growing_loads",
        }
        target, latest = record.write_results(results, doc)
        assert target == results / f"{run_id}.json"
        assert record.read_json(results / f"latest-{record.run_device(run_id)}.json")["run_id"] == (
            run_id
        )
        if run_id == later_cpu:
            assert latest is None  # a laptop run never replaces the MI300X run the API serves
        else:
            assert latest == results / "latest.json"
    assert record.resolve_latest(results) == results / f"{gpu_id}.json"
    record.write_results(
        results, record.read_json(results / f"{later_cpu}.json"), force_latest=True
    )
    assert record.resolve_latest(results) == results / f"{later_cpu}.json"
    BenchmarksResponse.model_validate(record.read_json(results / f"{gpu_id}.json")["api"])


def test_merge_refuses_an_empty_run_and_a_tampered_part(tmp_path: Path) -> None:
    with pytest.raises(BenchError, match="no part"):
        record.merge("20261013T090000Z-cpu", tmp_path)
    run_dir = _run(tmp_path, "20261013T090000Z-cpu", [part("B1")])
    doc = record.read_json(run_dir / "B1.json")
    doc["measurements"][0]["provenance"]["nature"] = "invented"
    record.write_json(run_dir / "B1.json", doc)
    with pytest.raises(BenchError):
        record.merge("20261013T090000Z-cpu", run_dir)


def test_resolve_latest_refuses_a_pointer_to_a_missing_file(tmp_path: Path) -> None:
    record.write_json(
        tmp_path / "latest.json", {"run_id": "20261013T090000Z-cpu", "file": "other.json"}
    )
    with pytest.raises(BenchError):
        record.resolve_latest(tmp_path)


# ---- Statistics ---------------------------------------------------------------------------------


def test_percentiles_match_numpy_linear_interpolation() -> None:
    rng = np.random.default_rng(3)
    for n in (1, 2, 5, 16, 33):
        values = rng.exponential(2.0, n).tolist()
        for q in (0, 50, 95, 100):
            assert stats.percentile(values, q) == pytest.approx(np.percentile(values, q))
    assert stats.median([4.0, 1.0, 3.0, 2.0]) == 2.5
    with pytest.raises(ValueError):
        stats.percentile([], 50)
    with pytest.raises(ValueError):
        stats.percentile([1.0], 101)
    with pytest.raises(ValueError):
        stats.percentile([float("inf")], 50)
    assert stats.summary([]) == {"n": 0}
    assert stats.summary([1.0, 3.0]) == {
        "n": 2,
        "min": 1.0,
        "p50": 2.0,
        "p95": 2.9,
        "max": 3.0,
        "mean": 2.0,
    }


# ---- amd-smi ------------------------------------------------------------------------------------


def test_amd_smi_layouts_are_all_read() -> None:
    old = json.dumps(
        [
            {
                "gpu": 0,
                "usage": {"gfx_activity": 17, "umc_activity": 3},
                "power": {"socket_power": "141 W"},
                "mem_usage": {"total_vram": "196592 MB", "used_vram": "283 MB"},
            }
        ]
    )
    (reading,) = amdsmi.parse_metric_json(old)
    assert reading.gfx_activity_percent == 17.0
    assert reading.power_w == 141.0
    assert reading.vram_total_gib == pytest.approx(191.984, abs=1e-3)  # MB read as MiB
    assert reading.vram_used_gib == pytest.approx(0.276, abs=1e-3)
    new = "WARNING: something\n" + json.dumps(
        {
            "gpu_data": [
                {
                    "gpu": 1,
                    "usage": {"gfx_activity": {"value": "N/A", "unit": "%"}},
                    "power": {"average_socket_power": {"value": 90, "unit": "W"}},
                    "mem_usage": {"total_vram": {"value": 192, "unit": "GB"}},
                }
            ]
        }
    )
    (reading,) = amdsmi.parse_metric_json(new)
    assert (reading.gpu, reading.gfx_activity_percent, reading.power_w) == (1, None, 90.0)
    assert reading.vram_total_gib == 192.0
    assert reading.vram_used_gib is None
    single = json.dumps({"gpu": 0, "usage": {"gfx_activity": 250}})  # out of range: dropped
    assert amdsmi.parse_metric_json(single)[0].gfx_activity_percent is None
    for bad in ("no json at all", '{"devices": []}', '"text"'):
        with pytest.raises(ValueError):
            amdsmi.parse_metric_json(bad)


def test_a_sample_keeps_the_error_of_a_failed_call(tmp_path: Path) -> None:
    good = amdsmi.sample_once(fake_amd_smi(tmp_path))
    assert good.error is None and good.gpus[0].gfx_activity_percent == 42.0
    failed = amdsmi.sample_once(fake_amd_smi(tmp_path, fail=True))
    assert failed.gpus == () and failed.error is not None and "exit 3" in failed.error
    missing = amdsmi.sample_once(["/nonexistent/amd-smi", "metric", "--json"])
    assert missing.error is not None and missing.error.startswith("FileNotFoundError")
    garbage = amdsmi.sample_once([sys.executable, "-c", "print('[not json')"])
    assert garbage.error is not None and "unparsable" in garbage.error
    assert amdsmi.Sample.from_dict(good.to_dict()).gpus == good.gpus


def test_the_sampler_writes_samples_and_a_window_summarises_them(tmp_path: Path) -> None:
    out = tmp_path / "samples.jsonl"
    start = time.time()
    with amdsmi.Sampler(out, fake_amd_smi(tmp_path), interval_s=0.05):
        time.sleep(1.5)  # each sample runs a subprocess: slow on a busy machine
    end = time.time()
    with out.open("a") as handle:
        handle.write('{"t": 1, "gpus": [')  # a line cut by a kill
    samples = amdsmi.read_samples(out)
    assert len(samples) >= 2
    summary = amdsmi.window_summary(samples, start, end)
    assert summary["n"] == len(samples) and summary["errors"] == 0
    assert summary["gfx_activity_mean_percent"] == 42.0
    assert summary["vram_used_max_gib"] == pytest.approx(105.469, abs=1e-3)
    assert summary["power_mean_w"] == 350.0
    assert amdsmi.window_summary(samples, end + 10, end + 20)["n"] == 0
    assert amdsmi.read_samples(tmp_path / "absent.jsonl") == []
    with pytest.raises(ValueError):
        amdsmi.Sampler(out, interval_s=0)


def test_the_sampler_cli_runs_for_a_duration(tmp_path: Path) -> None:
    out = tmp_path / "cli.jsonl"
    command = fake_amd_smi(tmp_path, fail=True)
    code = amdsmi.main(
        [
            "--out",
            str(out),
            "--interval",
            "0.05",
            "--duration",
            "0.2",
            f"--command={shlex.join(command)}",
        ]
    )
    assert code == 0
    samples = amdsmi.read_samples(out)
    assert samples and all(s.error for s in samples)  # failures are written, never skipped


def test_the_host_sampler_runs_on_python_3_10_with_the_standard_library_only() -> None:
    """The droplet host may have Python 3.10 and no packages: bench/amdsmi.py and the package
    __init__ import the standard library only and avoid datetime.UTC (3.11+)."""
    for path in (ROOT / "bench" / "amdsmi.py", ROOT / "bench" / "__init__.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = (
                    [node.module]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                )
                for name in names:
                    top = (name or "").split(".")[0]
                    assert top == "__future__" or top in sys.stdlib_module_names, (path, name)
                if isinstance(node, ast.ImportFrom) and node.module == "datetime":
                    assert "UTC" not in {a.name for a in node.names}, path


# ---- Environment --------------------------------------------------------------------------------


def test_environment_probes_never_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_IMAGE", "vllm/vllm-openai-rocm:v0.31.0@sha256:" + "a" * 64)
    monkeypatch.setenv("GEMMA_REVISION", "842da37")
    hw = env.hardware(gpu=False)
    assert hw["cpu"] and hw["logical_cpus"] and "gpu" not in hw
    sw = env.software(("numpy", "not_a_module_at_all"))
    assert sw["python"] and sw["numpy"] == np.__version__ and "not_a_module_at_all" not in sw
    assert sw["vllm_image"].endswith("a" * 64) and sw["gemma_revision"] == "842da37"
    assert env.xgboost_build()["xgboost"]
    static = tmp_path / "static.py"
    static.write_text(
        "import json; print(json.dumps({'gpu_data': [{'gpu': 0, 'asic': {'market_name': "
        "'AMD Instinct MI300X', 'target_graphics_version': 'gfx942'}, 'vram': {'size': "
        "{'value': 196592, 'unit': 'MB'}}}]}))"
    )
    gpu = env.gpu_static([sys.executable, str(static)])
    assert gpu == {
        "gpus": [
            {
                "gpu": 0,
                "market_name": "AMD Instinct MI300X",
                "target_graphics_version": "gfx942",
                "vram_size": 196592,
                "vram_unit": "MB",
            }
        ]
    }
    assert env.gpu_static(["/nonexistent/amd-smi"]) is None
    assert env.gpu_static([sys.executable, "-c", "print('nothing')"]) is None
    monkeypatch.setenv("BENCH_GIT_COMMIT", "deadbeef")
    monkeypatch.setenv("BENCH_GIT_DIRTY", "1")
    assert env.git_info() == {"commit": "deadbeef", "dirty": True}
    monkeypatch.delenv("BENCH_GIT_COMMIT")
    assert env.git_info(tmp_path)["commit"] is None or env.git_info(tmp_path)["commit"]


# ---- Cost per decision (NFR-13) ------------------------------------------------------------------


def test_the_nightly_cost_counts_every_second_the_droplet_exists() -> None:
    inputs = cost.Inputs(
        decisions_per_day=Decimal(150),
        seconds_per_decision=Decimal("0.5"),
        pipeline_seconds=Decimal(900),
        vllm_start_seconds=Decimal(300),
    )
    result = cost.cost("nightly-batch", inputs)
    # boot 600 + vLLM 300 + pipeline 900 + generation 150 x 0.5 + idle 600 = 2,475 s
    assert result.droplet_seconds_per_day == Decimal("2475.0")
    eur_day = Decimal(2475) / 3600 * Decimal("1.99") * Decimal("0.86")
    assert result.eur_per_day == eur_day.quantize(Decimal("0.01"))
    assert result.eur_per_decision == (eur_day / 150).quantize(Decimal("0.000001"))
    assert result.meets_target
    assert result.breakdown["vllm_start_seconds"]["origin"] == "measured"
    assert result.breakdown["boot_seconds"]["origin"] == "assumed"
    # The break-even volume is the smallest that meets the target.
    n = result.break_even_decisions_per_day
    for volume, meets in ((n, True), (n - 1, False)):
        check = cost.cost(
            "nightly-batch",
            cost.Inputs(Decimal(volume), Decimal("0.5"), Decimal(900), Decimal(300)),
        )
        assert check.meets_target is meets


def test_always_on_and_unavailable_scenarios() -> None:
    inputs = cost.Inputs(decisions_per_day=Decimal(150))
    day = cost.cost("always-on", inputs)
    assert day.usd_per_day == Decimal("47.76") and not day.meets_target
    assert day.break_even_decisions_per_day == 4108  # 41.07 EUR a day / 0.01 EUR
    assert cost.cost("business-hours", inputs).usd_per_day == Decimal("23.88")
    with pytest.raises(cost.CostUnavailable):
        cost.cost("nightly-batch", inputs)
    every = cost.all_costs(inputs)
    assert "unavailable" in every["nightly-batch"] and "eur_per_decision" in every["always-on"]
    with pytest.raises(ValueError):
        cost.cost("weekly", inputs)
    with pytest.raises(ValueError):
        cost.cost("always-on", cost.Inputs(decisions_per_day=Decimal(0)))
    slow = cost.Inputs(Decimal(10), Decimal(90), Decimal(900), None)
    never = cost.cost("nightly-batch", slow)
    assert never.break_even_decisions_per_day == -1  # generation alone costs above the target
    assert never.breakdown["vllm_start_seconds"]["origin"] == "assumed"
    capacity = cost.droplet_seconds("business-hours", cost.Inputs(Decimal(10_000), Decimal(5)))
    assert capacity["parts"]["capacity_ok"] is False
    assert cost.ASSUMPTIONS.as_dict()["version"] == "cost-v1"


# ---- API projection and report ------------------------------------------------------------------


def _doc(tmp_path: Path) -> dict[str, Any]:
    cost_m = measurement(
        benchmark="B3",
        metric="cost_eur_per_1000_decisions_always_on",
        label="Cost per 1,000 decisions, scenario always-on",
        value=273.82,
        unit="eur",
        device="mi300x",
        phase="cost",
        nature="simulated",
        data_nature="synthetic",
        protocol=None,
    )
    latency = measurement(
        benchmark="B3",
        metric="latency_p50_ms_batch_32",
        label="Latency p50",
        value=5100.0,
        unit="milliseconds",
        device="mi300x",
        phase="generation",
        data_nature="synthetic",
        protocol=None,
        concurrency=32,
        batch_size=32,
    )
    parts = [
        part(
            "B1",
            versions={"vllm_image": "vllm/vllm-openai-rocm:v0.31.0@sha256:" + "b" * 64},
            config={"max_depth": 4, "devices": "cpu,mi300x"},
        ),
        part("B2", "skipped"),
        part("B3", measurements=[latency, cost_m]),
        part(
            "B4",
            "failed",
            measurements=[
                measurement(benchmark="B4", phase="pipeline", metric="step_seconds_generate")
            ],
        ),
    ]
    run_dir = _run(tmp_path, "20261014T090000Z-gpu", parts)
    return record.merge("20261014T090000Z-gpu", run_dir, created_at=T0)


def test_the_api_projection_is_a_valid_benchmarks_response(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    api = BenchmarksResponse.model_validate(doc["api"])
    b1, b2, b3, b4 = api.items
    assert (b1.status, b2.status, b3.status, b4.status) == (
        "measured",
        "pending",
        "measured",
        "pending",
    )
    assert b1.versions["vllm_image"] == "vllm/vllm-openai-rocm:v0.31.0@sha256:bbbbbbbbbbbb"
    assert [r.metric for r in b3.results] == ["latency_p50_ms_batch_32"]  # the cost is simulated
    assert any("273.82 EUR per 1,000 decisions" in n and "simulated" in n for n in b3.notes)
    assert b2.notes[0].startswith("Skipped") and b4.notes[0].startswith("Run 20261014T090000Z")
    assert all(r.value.provenance.nature == "real" for item in api.items for r in item.results)
    absent = api_view.benchmarks_response({"run_id": "x", "benchmarks": {}})
    assert [i["status"] for i in absent["items"]] == ["pending"] * 4


def test_short_versions_fit_the_api() -> None:
    assert api_view.short_version("3.2.0") == "3.2.0"
    long_name = "registry.example/" + "n" * 60 + ":tag@sha256:" + "c" * 64
    assert api_view.short_version(long_name) == "sha256:cccccccccccc"
    assert len(api_view.short_version("x" * 80)) == 64


def test_the_report_labels_every_section(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    text = render(doc, "python -m bench report --run X --out Y")
    assert text.startswith("# Benchmarks B1-B4: run 20261014T090000Z-gpu (MI300X droplet)")
    for heading in ("## B1:", "## B2:", "## B3:", "## B4:", "## Hardware and software"):
        assert heading in text
    assert "Nature: real" in text and "Nature: simulated" in text
    assert "Do not edit by hand" in text and "This run had no GPU" not in text
    assert "Configuration: " in text


# ---- CLI ----------------------------------------------------------------------------------------


def test_a_part_that_raises_is_written_as_failed_and_the_run_goes_on(tmp_path: Path) -> None:
    args = [
        "run",
        "--device",
        "cpu",
        "--run-id",
        "20261013T090000Z-cpu",
        "--parts",
        "B1,B2",
        "--raw",
        str(tmp_path / "missing" / "raw"),
        "--artifacts",
        str(tmp_path / "artifacts"),
        "--results",
        str(tmp_path / "results"),
    ]
    assert bench_main(args) == 1
    run_dir = tmp_path / "artifacts" / "20261013T090000Z-cpu"
    b1 = record.read_json(run_dir / "B1.json")
    assert b1["status"] == "failed" and b1["notes"][0]
    assert record.read_json(run_dir / "B2.json")["status"] == "skipped"
    doc = record.read_json(tmp_path / "results" / "20261013T090000Z-cpu.json")
    BenchmarksResponse.model_validate(doc["api"])
    out = tmp_path / "report.md"
    assert (
        bench_main(
            [
                "report",
                "--run",
                str(tmp_path / "results" / "20261013T090000Z-cpu.json"),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    report = out.read_text()
    assert "B1 failed" in report and "This run had no GPU" in report
    assert (
        bench_main(
            [
                "merge",
                "--run-id",
                "20261013T090000Z-cpu",
                "--artifacts",
                str(tmp_path / "artifacts"),
                "--results",
                str(tmp_path / "r2"),
            ]
        )
        == 0
    )
    with pytest.raises(SystemExit):
        bench_main(["run", "--device", "gpu", "--run-id", "20261013T090000Z-cpu"])
    with pytest.raises(SystemExit):
        bench_main(["run", "--device", "cpu", "--parts", "B7"])
