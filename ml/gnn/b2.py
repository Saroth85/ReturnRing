"""Benchmark B2 (US-902) on the US-202 job: ml.gnn.b2_timing, the hook bench.b2_gnn calls.

    b2_timing(raw_dir, *, protocol, device, seed, sample=1.0, out=Path("artifacts"),
              controls=True, extra_args=()) -> bench.b2_gnn.GnnTiming

One US-202 run per device and data size, in a fresh process (`python -m ml.gnn asos`, the command
of `make gnn-asos-gpu` and `make gnn-asos-cpu-container`), so B2 and the US-202 report time the
same thing once and the cold start (torch and DGL import, device context, first kernels) is a real
cold start. The run writes <out>/eval/gnn/us-202-asos-<cpu|gpu>.json; the record of a protocol is
projected from it:

- the speedup basis is the median epoch time (every epoch, the first included), as the US-202
  report computes it; the whole training loop is reported too, but its length depends on when
  early stopping fires on each device;
- the F1 is the one US-202 states: protocol B on the decision-time population (ADR-10 amendment
  1), protocol A on the full test set (the paper's protocol, compared with the paper's 0.792), at
  the validation threshold, with F1 at 0.5 next to it;
- `comparable` is the run's configuration without device and threads: bench computes a speedup
  only between two records whose `comparable` is identical (spec 12, 'Qualità comparabile').

Both protocols come from one run, so the second protocol of a device reuses it (a per-process
cache). Standard library only at import time: bench finds this hook where torch is absent and
`unavailable()` says why it cannot run there.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import statistics
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEVICE_NAMES = {"cpu": "cpu", "cuda": "gpu"}  # bench's device names -> the job's --device
HEADLINE = {"B": "decision_time", "A": "all"}  # the population US-202 states per protocol
STATED_AS = {
    "decision_time": "decision-time test population (ADR-10 amendment 1)",
    "all": "full test set",
}


class B2Error(RuntimeError):
    """The US-202 job failed, or its metrics file is not the run that was asked for."""


def metrics_file(out: Path, device: str) -> Path:
    """Where `python -m ml.gnn asos --out <out> --device <cpu|gpu>` writes its metrics."""
    if device not in ("cpu", "gpu"):
        raise ValueError(f"device must be cpu or gpu, not {device!r}")
    return Path(out) / "eval" / "gnn" / f"us-202-asos-{device}.json"


def _importable(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def unavailable() -> str | None:
    """Why the hook cannot run in this environment (None: it can)."""
    missing = [name for name in ("torch", "polars", "sklearn") if not _importable(name)]
    if not missing:
        return None
    what = ", ".join("PyTorch" if m == "torch" else m for m in missing)
    return (
        f"{what} not installed here: B2 trains GraphSAGE where ml.gnn runs (the "
        "trainer-graphsage image on the droplet, `make bench-b2 DEVICE=gpu`, or a laptop with "
        "infra/requirements-gnn-cpu.txt)"
    )


def _need(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise B2Error(f"metrics file: no {where}.{key} (rerun `python -m ml.gnn asos`)")
    return mapping[key]


def _hardware(env: Mapping[str, Any]) -> str:
    if str(env.get("device", "")).startswith("cuda"):
        return (
            f"{env.get('gpu_name')} ({env.get('gpu_arch')}, {env.get('gpu_total_gb')} GB); "
            f"host CPU {env.get('cpu')}"
        )
    return (
        f"{env.get('cpu')} ({env.get('cpu_count')} logical CPUs, "
        f"{env.get('torch_threads')} torch threads)"
    )


def _model(doc: Mapping[str, Any], entry: Mapping[str, Any]) -> str:
    config = doc.get("config", {})
    encoder = entry.get("encoder") or {}
    layers = encoder.get("layers", config.get("layers"))
    hidden = encoder.get("hidden", config.get("hidden"))
    aggregator = encoder.get("aggregator", config.get("aggregator"))
    lr = (config.get("train") or {}).get("lr")
    return (
        f"GraphSAGE: {layers} heterogeneous SAGE layer(s) of {hidden} units, {aggregator} "
        f"aggregator, dropout {config.get('dropout')}, Adam {lr}, MLP head of "
        f"{entry.get('head_hidden', config.get('head_hidden'))} units; "
        f"{doc.get('environment', {}).get('backend')} backend"
    )


def timing_from_metrics(
    doc: Mapping[str, Any], protocol: str, device: str, path: Path | None = None
) -> dict[str, Any]:
    """The GnnTiming record of `protocol` from a US-202 metrics document of `device`."""
    if protocol not in HEADLINE:
        raise B2Error(f"protocol {protocol} is not one of ADR-10's (A, B)")
    env = doc.get("environment") or {}
    effective = str(env.get("device", ""))
    if (device == "cuda") != effective.startswith("cuda"):
        raise B2Error(f"asked for {device}, but the metrics file ran on {effective or 'unknown'}")
    entry = (doc.get("protocols") or {}).get(protocol)
    if entry is None:
        raise B2Error(f"metrics file: protocol {protocol} was not run")
    models = entry.get("models") or {}
    model = models.get("graphsage")
    if model is None:
        raise B2Error(f"metrics file: no GraphSAGE model for protocol {protocol}")
    training = model["training"]
    epochs = [float(v) for v in _need(training, "epoch_seconds", "training")]
    if not epochs:
        raise B2Error("metrics file: training.epoch_seconds is empty")
    population = HEADLINE[protocol]
    test = _need(model["test"], population, "test")
    train_graph = (entry.get("data") or {}).get("train") or {}
    timings = doc.get("timings") or {}
    provenance = doc.get("provenance") or {}
    versions = {
        key: str(env[key])
        for key in ("torch", "torch_hip", "dgl", "python")
        if env.get(key) is not None
    }
    versions["backend"] = str(env.get("backend"))
    record: dict[str, Any] = {
        "protocol": protocol,
        "device_requested": device,
        "device_effective": effective,
        "model": _model(doc, model),
        "comparable": dict(doc.get("comparable") or {}),
        "sample": float((doc.get("config") or {}).get("sample", 1.0)),
        "nodes": int(train_graph.get("customer_nodes", 0) + train_graph.get("variant_nodes", 0)),
        "edges": int(_need(training, "rows_fit", "training")),
        "epochs": int(_need(training, "epochs_run", "training")),
        "cold_start_seconds": float(_need(timings, "cold_start_seconds", "timings")),
        "epoch_seconds": epochs,
        "epoch_seconds_median": float(
            training.get("epoch_seconds_median") or statistics.median(epochs)
        ),
        "train_seconds": float(_need(training, "train_seconds", "training")),
        "inference_seconds": float(model["inference"]["seconds"]),
        "f1": float(test["f1"]),
        "f1_at_half": float(test["f1_at_half"]),
        "threshold": float(test.get("threshold", model.get("threshold"))),
        "population": population,
        "population_events": int(test["n"]),
        "data_origin": str(provenance.get("origin", "unknown")),
        "versions": versions,
        "hardware": _hardware(env),
        "peak_memory_gib": _gib(training.get("peak_allocated_gb")),
        "graph_build_seconds": entry.get("data_loading_seconds"),
        "precision": test.get("precision"),
        "recall": test.get("recall"),
        "best_epoch": training.get("best_epoch"),
        "load_average_before_after": [
            training.get("load_average_before"),
            training.get("load_average_after"),
        ],
        "measured_at": timings.get("measured_at"),
        "comparisons": {
            kind: {"f1": float(m["test"][population]["f1"])}
            for kind, m in models.items()
            if kind != "graphsage" and (m.get("test") or {}).get(population)
        },
    }
    if path is not None:
        record["metrics_file"] = str(path)
    return record


def _gib(gigabytes: Any) -> float | None:
    """torch reports bytes / 1e9 (GB); the benchmark unit is GiB."""
    return None if gigabytes is None else round(float(gigabytes) * 1e9 / 2**30, 3)


def _run_job(
    raw_dir: Path,
    out: Path,
    device: str,
    seed: int,
    sample: float,
    controls: bool,
    extra_args: Sequence[str],
    *,
    command: Sequence[str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Run `python -m ml.gnn asos` in a fresh process and read the metrics file it wrote."""
    argv = [
        *(command or (sys.executable, "-m", "ml.gnn", "asos")),
        "--raw",
        str(Path(raw_dir).resolve()),
        "--out",
        str(Path(out).resolve()),
        "--device",
        device,
        "--seed",
        str(seed),
        "--sample",
        f"{sample:g}",
        *([] if controls else ["--no-mlp", "--no-xgboost"]),
        *extra_args,
    ]
    started = time.time() if now is None else now
    code = subprocess.run(argv, cwd=ROOT, env=dict(os.environ), check=False).returncode
    if code != 0:
        hint = " (2: GPU not visible, or a usage error)" if code == 2 else ""
        raise B2Error(f"`{shlex.join(argv)}` exited {code}{hint}")
    path = metrics_file(out, device)
    if not path.exists() or path.stat().st_mtime < started - 1.0:
        raise B2Error(f"`{shlex.join(argv)}` did not write {path}")
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    config = doc.get("config") or {}
    if config.get("seed") != seed or float(config.get("sample", -1)) != float(sample):
        raise B2Error(
            f"{path} holds another run (seed {config.get('seed')}, sample "
            f"{config.get('sample')}), not seed {seed}, sample {sample}"
        )
    doc["_metrics_file"] = str(path)
    return doc


_RUNS: dict[tuple[Any, ...], dict[str, Any]] = {}


def clear_cache() -> None:
    _RUNS.clear()


def b2_timing(
    raw_dir: Path,
    *,
    protocol: str,
    device: str,
    seed: int,
    sample: float = 1.0,
    out: Path = Path("artifacts"),
    controls: bool = True,
    extra_args: Sequence[str] = (),
) -> dict[str, Any]:
    """The GnnTiming of `protocol` on `device` ("cpu" or "cuda", bench's names). The first call
    for a device and data size runs the US-202 job (both protocols; with `controls`, the MLP
    and XGBoost rows of the US-202 report as well), later calls reuse it."""
    if device not in DEVICE_NAMES:
        raise ValueError(f"device must be cpu or cuda, not {device!r}")
    name = DEVICE_NAMES[device]
    where = (str(Path(raw_dir).resolve()), str(Path(out).resolve()))
    key: tuple[Any, ...] = (*where, name, seed, sample, controls, tuple(extra_args))
    doc = _RUNS.get(key)
    if doc is None:
        doc = _run_job(Path(raw_dir), Path(out), name, seed, sample, controls, tuple(extra_args))
        _RUNS[key] = doc
    path = doc.get("_metrics_file")
    return timing_from_metrics(doc, protocol, device, Path(path) if path else None)
