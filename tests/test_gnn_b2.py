"""Benchmark B2 through ml.gnn.b2_timing (US-202, US-902), without PyTorch.

The hook runs the US-202 job (`python -m ml.gnn asos`) once per device and data size and projects
its metrics file into bench.b2_gnn.GnnTiming: the speedup basis (median epoch) and the F1 as
US-202 states them (protocol B on the decision-time population, protocol A on the full test set
against the paper). The job itself is replaced by a fixture document here; tests/test_gnn_jobs.py
runs the real job (torch marker) and its GPU variant (gpu marker). Every number is made up
(nature synthetic).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import ml.gnn
from bench.b2_gnn import B2Config, TimingError, check_timing, find_hook, run_b2
from ml.gnn import b2
from tests.test_gnn_data import asos_doc


def test_the_projection_states_the_f1_as_us202_does() -> None:
    doc = asos_doc("gpu")
    doc["protocols"]["B"]["models"]["graphsage"]["test"]["decision_time"]["f1"] = 0.742
    doc["protocols"]["B"]["models"]["graphsage"]["test"]["all"]["f1"] = 0.70
    b_rec = b2.timing_from_metrics(doc, "B", "cuda")
    assert check_timing(b_rec, "B", "cuda") == b_rec
    assert b_rec["population"] == "decision_time" and b_rec["f1"] == 0.742  # not the full test set
    assert b_rec["f1_at_half"] == pytest.approx(0.69)
    a_rec = b2.timing_from_metrics(doc, "A", "cuda")
    assert a_rec["population"] == "all" and a_rec["f1"] == 0.79
    assert a_rec["device_effective"] == "cuda:0" and a_rec["device_requested"] == "cuda"
    assert b_rec["epoch_seconds_median"] == 0.1 and b_rec["epoch_seconds"] == [0.3, 0.1, 0.1]
    assert b_rec["epochs"] == 3 and b_rec["edges"] == 90 and b_rec["nodes"] == 100
    assert b_rec["cold_start_seconds"] == 1.5
    assert b_rec["peak_memory_gib"] == round(1.25e9 / 2**30, 3)  # torch GB, bench GiB
    assert b_rec["comparable"] == doc["comparable"] and b_rec["sample"] == 1.0
    assert "mean aggregator" in b_rec["model"] and "max-pool" not in b_rec["model"]
    assert "dgl backend" in b_rec["model"]
    assert "AMD Instinct MI300X" in b_rec["hardware"]
    assert b_rec["comparisons"]["xgboost"]["f1"] == 0.7  # the same events, the same population
    assert b_rec["measured_at"] == "2026-10-19T08:00:00Z"


def test_the_projection_refuses_a_metrics_file_it_cannot_read_as_us202() -> None:
    doc = asos_doc("cpu")
    with pytest.raises(b2.B2Error, match="protocol C"):
        b2.timing_from_metrics(doc, "C", "cpu")
    old = copy.deepcopy(doc)
    del old["protocols"]["B"]["models"]["graphsage"]["training"]["epoch_seconds"]
    with pytest.raises(b2.B2Error, match="epoch_seconds"):
        b2.timing_from_metrics(old, "B", "cpu")
    gpu_file = asos_doc("gpu")
    with pytest.raises(b2.B2Error, match="ran on cuda:0"):
        b2.timing_from_metrics(gpu_file, "B", "cpu")


def test_check_timing_holds_the_us202_populations() -> None:
    record = b2.timing_from_metrics(asos_doc("cpu"), "B", "cpu")
    wrong = {**record, "population": "all"}
    with pytest.raises(TimingError, match="decision_time"):
        check_timing(wrong, "B", "cpu")
    with pytest.raises(TimingError, match="sample"):
        check_timing({**record, "sample": 0.0}, "B", "cpu")


def test_the_hook_runs_the_job_once_per_device_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, float, bool, tuple[str, ...]]] = []

    def job(
        raw_dir: Path,
        out: Path,
        device: str,
        seed: int,
        sample: float,
        controls: bool,
        extra_args: tuple[str, ...],
    ) -> dict[str, Any]:
        calls.append((device, sample, controls, extra_args))
        doc = asos_doc(device, sample)
        doc["config"]["seed"] = seed
        return doc

    monkeypatch.setattr(b2, "_run_job", job)
    b2.clear_cache()
    raw = tmp_path / "raw"
    for protocol in ("A", "B"):
        for device in ("cpu", "cuda"):
            record = b2.b2_timing(raw, protocol=protocol, device=device, seed=42, out=tmp_path)
            assert record["protocol"] == protocol and record["device_requested"] == device
    assert calls == [("cpu", 1.0, True, ()), ("gpu", 1.0, True, ())]  # both protocols per job
    small = b2.b2_timing(
        raw, protocol="B", device="cpu", seed=42, sample=0.5, out=tmp_path, controls=False
    )
    assert small["sample"] == 0.5 and calls[-1] == ("cpu", 0.5, False, ())
    b2.clear_cache()


def test_the_hook_is_found_and_says_why_it_cannot_run_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ml.gnn, "b2_unavailable", lambda: None)  # as where torch is installed
    hook, reason = find_hook()
    assert hook is ml.gnn.b2_timing and reason == ""
    monkeypatch.setattr(ml.gnn, "b2_unavailable", lambda: "PyTorch is not installed here")
    hook, reason = find_hook()
    assert hook is None and "PyTorch is not installed here" in reason
    part = run_b2(B2Config(raw_dir=Path("data/raw")))
    assert part.status == "skipped" and "PyTorch" in part.notes[0]


def test_b2_unavailable_names_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b2, "_importable", lambda name: name != "torch")
    reason = b2.unavailable()
    assert reason is not None and "PyTorch" in reason and "trainer-graphsage" in reason
    monkeypatch.setattr(b2, "_importable", lambda name: True)
    assert b2.unavailable() is None


def test_the_metrics_file_is_where_the_us202_report_reads_it(tmp_path: Path) -> None:
    assert b2.metrics_file(tmp_path, "gpu") == tmp_path / "eval/gnn/us-202-asos-gpu.json"
    with pytest.raises(ValueError):
        b2.metrics_file(tmp_path, "cuda")


def test_a_job_that_did_not_write_its_file_is_an_error(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    stale = b2.metrics_file(tmp_path, "cpu")
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps(asos_doc("cpu")), encoding="utf-8")
    # A command that succeeds without running the job: the file is older than the call.
    with pytest.raises(b2.B2Error, match="did not write"):
        b2._run_job(
            raw,
            tmp_path,
            "cpu",
            42,
            1.0,
            True,
            (),
            command=(sys.executable, "-c", "pass"),
            now=1e12,
        )
    with pytest.raises(b2.B2Error, match="exited 3"):
        b2._run_job(
            raw,
            tmp_path,
            "cpu",
            42,
            1.0,
            True,
            (),
            command=(sys.executable, "-c", "raise SystemExit(3)"),
        )
