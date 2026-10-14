"""B2: GraphSAGE on ASOS GraphReturns, CPU against the MI300X, and F1 against the paper (US-902).

ml/gnn is built in parallel (US-202). B2 calls one function of it and nothing else:

    ml.gnn.b2_timing(raw_dir: Path, *, protocol: "A" | "B", device: "cpu" | "cuda",
                     seed: int) -> Mapping[str, object]

which trains the model of spec 8.1 (SAGEConv 16, max-pool, dropout 0.2, Adam 0.01, heterogeneous
graph with brand, product type and reason nodes) on the raw ASOS tables of `raw_dir` with the
same data, parameters and seed on both devices, and returns the record GnnTiming below (one
process per call is fine; the first epoch includes the device initialisation). B2 checks the
record and turns it into measurements; a requested GPU run whose device_effective is the CPU is
refused, never reported as a GPU number. When ml.gnn or b2_timing is absent, B2 is "skipped" with
the reason, and the run goes on.

Standard library only: B2 runs in the trainer-gnn image (rocm/dgl), next to torch and DGL.
"""

from __future__ import annotations

import importlib
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from bench import env
from bench.record import Measurement, Part, short, utc_now
from bench.stats import median, summary

GNN_MODULE = "ml.gnn"
GNN_FUNCTION = "b2_timing"
PAPER_GNN_F1 = 0.792  # arXiv:2302.14096, GNN (spec 8.1)
API_PROTOCOL = {"A": "paper", "B": "decision_time"}
API_DEVICE = {"cpu": "cpu", "cuda": "mi300x"}

Log = Callable[[str], None]
Hook = Callable[..., Mapping[str, object]]


class GnnTiming(TypedDict):
    """What ml.gnn.b2_timing returns (times in seconds, wall clock)."""

    protocol: Literal["A", "B"]
    device_requested: Literal["cpu", "cuda"]
    device_effective: str  # torch device actually used, e.g. "cuda:0" or "cpu"
    nodes: int
    edges: int  # labelled customer-variant edges trained on
    epochs: int
    cold_start_seconds: float  # first epoch, device initialisation and kernel loading included
    epoch_seconds: list[float]  # every epoch after the first
    train_seconds: float  # the whole training loop, graph building excluded
    inference_seconds: float  # scoring the test edges
    f1: float  # on the test split, at `threshold`
    threshold: float
    population: str  # which test events the F1 is on, e.g. "full test set"
    data_origin: str  # provenance origin of the raw tables read (rule 11)
    versions: dict[str, str]  # torch, dgl, hip
    peak_memory_gib: NotRequired[float | None]
    graph_build_seconds: NotRequired[float | None]
    precision: NotRequired[float | None]
    recall: NotRequired[float | None]


# Written out: with postponed annotations TypedDict cannot see NotRequired.
REQUIRED_KEYS = (
    "protocol",
    "device_requested",
    "device_effective",
    "nodes",
    "edges",
    "epochs",
    "cold_start_seconds",
    "epoch_seconds",
    "train_seconds",
    "inference_seconds",
    "f1",
    "threshold",
    "population",
    "data_origin",
    "versions",
)
_NUMBERS = ("cold_start_seconds", "train_seconds", "inference_seconds", "f1", "threshold")
_INTEGERS = ("nodes", "edges", "epochs")
_TEXTS = ("device_effective", "population", "data_origin")


class TimingError(ValueError):
    """A record of ml.gnn.b2_timing that does not follow GnnTiming."""


@dataclass(frozen=True)
class B2Config:
    raw_dir: Path
    devices: tuple[str, ...] = ("cpu",)
    protocols: tuple[str, ...] = ("A", "B")
    seed: int = 42


def find_hook() -> tuple[Hook | None, str]:
    """ml.gnn.b2_timing, or None and the reason B2 is skipped."""
    try:
        module = importlib.import_module(GNN_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name in (GNN_MODULE, "ml"):
            return None, (
                f"{GNN_MODULE} is not in this checkout (US-202 is built in parallel): B2 skipped"
            )
        raise
    hook = getattr(module, GNN_FUNCTION, None)
    if not callable(hook):
        return None, f"{GNN_MODULE} has no {GNN_FUNCTION}(): B2 skipped"
    return hook, ""


def _finite(value: object, name: str, low: float = 0.0, high: float = math.inf) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TimingError(f"{name} must be a number, got {value!r}")
    if not (math.isfinite(value) and low <= value <= high):
        raise TimingError(f"{name} must be in [{low}, {high}], got {value!r}")
    return float(value)


def check_timing(record: Mapping[str, object], protocol: str, device: str) -> GnnTiming:
    """The record as GnnTiming, or TimingError."""
    missing = [k for k in REQUIRED_KEYS if k not in record]
    if missing:
        raise TimingError(f"missing keys {sorted(missing)}")
    if record["protocol"] != protocol or record["device_requested"] != device:
        raise TimingError(
            f"asked for protocol {protocol} on {device}, got {record['protocol']} on "
            f"{record['device_requested']}"
        )
    for name in _NUMBERS:
        _finite(record[name], name, high=1.0 if name in ("f1", "threshold") else math.inf)
    for name in _INTEGERS:
        value = record[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise TimingError(f"{name} must be a positive integer, got {value!r}")
    for name in _TEXTS:
        if not isinstance(record[name], str) or not str(record[name]).strip():
            raise TimingError(f"{name} must be a non-empty string")
    epochs = record["epoch_seconds"]
    if not isinstance(epochs, list) or not epochs:
        raise TimingError("epoch_seconds must be a non-empty list")
    for value in epochs:
        _finite(value, "epoch_seconds")
    versions = record["versions"]
    if not isinstance(versions, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in versions.items()
    ):
        raise TimingError("versions must map names to version strings")
    if device == "cuda" and str(record["device_effective"]).startswith("cpu"):
        raise TimingError("GPU not used: requested cuda, effective device cpu")
    for name in ("peak_memory_gib", "graph_build_seconds", "precision", "recall"):
        if record.get(name) is not None:
            _finite(record[name], name, high=1.0 if name in ("precision", "recall") else math.inf)
    timing: GnnTiming = GnnTiming(**record)  # type: ignore[typeddict-item]
    return timing


def _measurements(
    timing: GnnTiming, started_on: Any, hardware: Mapping[str, Any]
) -> list[Measurement]:
    device = timing["device_requested"]
    protocol = timing["protocol"]
    where = "MI300X" if device == "cuda" else f"{hardware.get('cpu')}"
    versions = ", ".join(f"{k} {v}" for k, v in sorted(timing["versions"].items()))
    origin = short(
        f"B2, measured by bench through ml.gnn.b2_timing on {where} ({versions}); "
        f"{timing['data_origin']}; protocol {protocol} (ADR-10)"
    )
    graph = f"{timing['nodes']:,} nodes, {timing['edges']:,} labelled edges"
    common: dict[str, Any] = {
        "benchmark": "B2",
        "device": API_DEVICE[device],
        "origin": origin,
        "period_start": started_on,
        "period_end": utc_now().date(),
        "nature": "real",
        "data_nature": "real",
        "protocol": API_PROTOCOL[protocol],
        "rows": timing["edges"],
    }
    p = protocol.lower()
    epoch_median = round(median(timing["epoch_seconds"]), 4)
    out = [
        Measurement(
            metric=f"gnn_cold_start_seconds_{p}",
            label=short(
                f"GraphSAGE first epoch, protocol {protocol} ({graph}), device "
                "initialisation included"
            ),
            value=round(timing["cold_start_seconds"], 3),
            unit="seconds",
            phase="cold_start",
            warm=False,
            repeats=1,
            **common,
        ),
        Measurement(
            metric=f"gnn_train_seconds_{p}",
            label=short(
                f"GraphSAGE training, protocol {protocol} ({graph}), {timing['epochs']} epochs"
            ),
            value=round(timing["train_seconds"], 3),
            unit="seconds",
            phase="training",
            warm=False,
            repeats=1,
            **common,
        ),
        Measurement(
            metric=f"gnn_epoch_seconds_median_{p}",
            label=short(f"GraphSAGE epoch time, median after the first, protocol {protocol}"),
            value=epoch_median,
            unit="seconds",
            phase="training",
            warm=True,
            repeats=len(timing["epoch_seconds"]),
            detail={"epoch_seconds": summary(timing["epoch_seconds"], 4)},
            **common,
        ),
        Measurement(
            metric=f"gnn_inference_seconds_{p}",
            label=short(f"GraphSAGE scoring of the test edges, protocol {protocol}"),
            value=round(timing["inference_seconds"], 3),
            unit="seconds",
            phase="scoring",
            warm=True,
            repeats=1,
            **common,
        ),
        Measurement(
            metric=f"gnn_f1_{p}",
            label=short(
                f"GraphSAGE F1 on the ASOS test split ({timing['population']}), protocol "
                f"{protocol}, threshold {timing['threshold']:.3f}; paper GNN F1 {PAPER_GNN_F1}"
                + (" (paper protocol: not product performance, NFR-16)" if protocol == "A" else "")
            ),
            value=round(timing["f1"], 4),
            unit="ratio",
            phase="scoring",
            repeats=1,
            **common,
        ),
    ]
    peak = timing.get("peak_memory_gib")
    if peak is not None:
        out.append(
            Measurement(
                metric=f"gnn_peak_memory_gib_{p}",
                label=short(f"GraphSAGE peak device memory, protocol {protocol}"),
                value=round(float(peak), 3),
                unit="gib",
                phase="training",
                repeats=1,
                **common,
            )
        )
    return out


def run_b2(config: B2Config, log: Log = lambda _m: None, hook: Hook | None = None) -> Part:
    started = utc_now()
    gpu = "cuda" in config.devices
    hardware = env.hardware(gpu=gpu)
    found, reason = (hook, "") if hook is not None else find_hook()
    if found is None:
        log(reason)
        return Part(
            benchmark="B2",
            status="skipped",
            title="GraphSAGE on ASOS GraphReturns: CPU against MI300X, F1",
            dataset="ASOS GraphReturns, paper and decision-time protocols (ADR-10)",
            started_at=started,
            finished_at=utc_now(),
            versions=env.software(),
            notes=[reason],
            details={
                "interface": f"{GNN_MODULE}.{GNN_FUNCTION}(raw_dir, *, protocol, device, "
                "seed) -> GnnTiming (bench/b2_gnn.py)"
            },
            hardware=hardware,
        )
    measurements: list[Measurement] = []
    records: list[dict[str, Any]] = []
    versions = env.software(("torch", "dgl"))
    for protocol in config.protocols:
        for device in config.devices:
            t0 = time.perf_counter()
            raw = found(config.raw_dir, protocol=protocol, device=device, seed=config.seed)
            timing = check_timing(raw, protocol, device)
            log(f"B2 {protocol} {device}: {timing['train_seconds']:.1f} s, F1 {timing['f1']:.4f}")
            versions.update(timing["versions"])
            records.append({**timing, "call_seconds": round(time.perf_counter() - t0, 3)})
            measurements += _measurements(timing, started.date(), hardware)
    speedups = []
    for protocol in config.protocols:
        by_device = {r["device_requested"]: r for r in records if r["protocol"] == protocol}
        if "cpu" in by_device and "cuda" in by_device:
            cpu, gpu_r = by_device["cpu"], by_device["cuda"]
            speedups.append(
                {
                    "protocol": protocol,
                    "train_cpu_over_gpu": round(cpu["train_seconds"] / gpu_r["train_seconds"], 3)
                    if gpu_r["train_seconds"] > 0
                    else None,
                }
            )
    return Part(
        benchmark="B2",
        status="measured",
        title="GraphSAGE on ASOS GraphReturns: CPU against MI300X, F1 against the paper",
        dataset="ASOS GraphReturns (OSF c793h, real), protocols A (paper) and B (decision time) "
        "of ADR-10",
        started_at=started,
        finished_at=utc_now(),
        versions=versions,
        config={
            "seed": config.seed,
            "devices": ",".join(API_DEVICE[d] for d in config.devices),
            "model": "SAGEConv 16, max-pool, dropout 0.2, Adam 0.01 (spec 8.1)",
        },
        measurements=measurements,
        notes=[
            f"Paper GNN F1 {PAPER_GNN_F1} (arXiv:2302.14096); protocol A numbers measure the "
            "dataset, not the product (ADR-10)."
        ],
        details={"records": records, "speedups": speedups},
        hardware=hardware,
    )
