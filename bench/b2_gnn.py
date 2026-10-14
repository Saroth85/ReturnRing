"""B2: GraphSAGE on ASOS GraphReturns, CPU against the MI300X, and F1 as US-202 states it.

B2 calls one function of ml/gnn (US-202) and nothing else:

    ml.gnn.b2_timing(raw_dir: Path, *, protocol: "A" | "B", device: "cpu" | "cuda", seed: int,
                     sample: float, out: Path, controls: bool, extra_args: tuple[str, ...])
        -> Mapping[str, object]  # GnnTiming below

which runs the US-202 job (`python -m ml.gnn asos`, a fresh process per device and data size) and
projects its metrics file (ml/gnn/b2.py). The full-data runs write the US-202 files
(<out>/eval/gnn/us-202-asos-<cpu|gpu>.json, read by `make gnn-results`), so the droplet trains
each device once for B2 and US-202. B2 checks each record and turns it into measurements:

- speedup: CPU median epoch over GPU median epoch (the US-202 definition; every epoch is the same
  work on both devices), and CPU over GPU inference, only between two records whose `comparable`
  settings are identical (spec 12, 'Qualità comparabile'). The whole training loop is reported
  but never divided: early stopping ends at a different epoch on each device;
- F1 as US-202 states it: protocol B on the decision-time population (no paper reference, ADR-10),
  protocol A on the full test set next to the paper's GNN F1 0.792 (paper protocol, NFR-16);
  measured on the full data only;
- cold start: the job's own (torch and DGL import, device context, first kernels, in a fresh
  process), once per device and data size;
- growing loads (spec 12): seeded customer samples (default 25%, 50% and 100%), the smaller ones
  timing GraphSAGE alone (no MLP or XGBoost control), written under the run folder so they never
  replace the US-202 files.

A requested GPU run whose effective device is the CPU is refused, never reported as a GPU number.
When ml.gnn is absent, has no b2_timing, or cannot train here (b2_unavailable: no PyTorch), B2 is
"skipped" with the reason, and the run goes on.

Standard library only: B2 runs in the trainer-graphsage image (rocm/dgl plus the US-202 stack).
"""

from __future__ import annotations

import importlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from bench import env
from bench.record import Measurement, Part, short, utc_now
from bench.stats import summary

GNN_MODULE = "ml.gnn"
GNN_FUNCTION = "b2_timing"
GNN_UNAVAILABLE = "b2_unavailable"
PAPER_GNN_F1 = 0.792  # arXiv:2302.14096, GNN, paper protocol (A) only
API_PROTOCOL = {"A": "paper", "B": "decision_time"}
API_DEVICE = {"cpu": "cpu", "cuda": "mi300x"}
POPULATION = {"A": "all", "B": "decision_time"}  # the population US-202 states per protocol
POPULATION_TEXT = {
    "all": "full test set",
    "decision_time": "decision-time test population",
}

Log = Callable[[str], None]
Hook = Callable[..., Mapping[str, object]]


class GnnTiming(TypedDict):
    """What ml.gnn.b2_timing returns (times in seconds, wall clock)."""

    protocol: Literal["A", "B"]
    device_requested: Literal["cpu", "cuda"]
    device_effective: str  # torch device actually used, e.g. "cuda:0" or "cpu"
    model: str  # what was trained, from the run's own configuration
    comparable: dict[str, object]  # settings two devices must share for a speedup
    sample: float  # share of customers kept (1.0: every event)
    nodes: int  # customer and variant nodes of the train graph
    edges: int  # labelled events trained on
    epochs: int  # epochs run (early stopping)
    cold_start_seconds: float  # the job's: torch and DGL import, device context, first kernels
    epoch_seconds: list[float]  # every epoch, the first included
    epoch_seconds_median: float  # median of epoch_seconds: the speedup basis (US-202)
    train_seconds: float  # the whole training loop
    inference_seconds: float  # scoring the test events
    f1: float  # at the validation threshold, on `population`
    f1_at_half: float
    threshold: float
    population: str  # "decision_time" (protocol B) or "all" (protocol A), as US-202 states it
    population_events: int
    data_origin: str  # provenance origin of the raw tables read (rule 11)
    versions: dict[str, str]  # torch, HIP, DGL, backend
    hardware: str  # CPU model and threads, or GPU name and architecture
    peak_memory_gib: NotRequired[float | None]
    graph_build_seconds: NotRequired[float | None]
    precision: NotRequired[float | None]
    recall: NotRequired[float | None]
    best_epoch: NotRequired[int | None]
    load_average_before_after: NotRequired[list[float | None]]
    measured_at: NotRequired[str | None]
    comparisons: NotRequired[dict[str, dict[str, float]]]  # other models, same events
    metrics_file: NotRequired[str]


# Written out: with postponed annotations TypedDict cannot see NotRequired.
REQUIRED_KEYS = (
    "protocol",
    "device_requested",
    "device_effective",
    "model",
    "comparable",
    "sample",
    "nodes",
    "edges",
    "epochs",
    "cold_start_seconds",
    "epoch_seconds",
    "epoch_seconds_median",
    "train_seconds",
    "inference_seconds",
    "f1",
    "f1_at_half",
    "threshold",
    "population",
    "population_events",
    "data_origin",
    "versions",
    "hardware",
)
_SECONDS = ("cold_start_seconds", "epoch_seconds_median", "train_seconds", "inference_seconds")
_RATIOS = ("f1", "f1_at_half", "threshold")
_INTEGERS = ("nodes", "edges", "epochs", "population_events")
_TEXTS = ("device_effective", "model", "population", "data_origin", "hardware")


class TimingError(ValueError):
    """A record of ml.gnn.b2_timing that does not follow GnnTiming."""


@dataclass(frozen=True)
class B2Config:
    raw_dir: Path
    devices: tuple[str, ...] = ("cpu",)
    protocols: tuple[str, ...] = ("A", "B")
    samples: tuple[float, ...] = (0.25, 0.5, 1.0)
    seed: int = 42
    out: Path = Path("artifacts")  # the US-202 artifacts folder (full-data runs)
    run_dir: Path | None = None  # the bench run folder (sampled runs go below it)
    job_args: tuple[str, ...] = ()  # extra arguments of the job (tests: fewer epochs)

    def __post_init__(self) -> None:
        if not self.samples or any(not 0 < s <= 1 for s in self.samples):
            raise ValueError("samples must be in (0, 1]")
        if not self.devices or any(d not in API_DEVICE for d in self.devices):
            raise ValueError(f"devices must be among {tuple(API_DEVICE)}")

    def out_for(self, sample: float) -> Path:
        """Full data: the US-202 folder; a sample: its own folder under the run."""
        if sample >= 1.0:
            return self.out
        base = self.run_dir if self.run_dir is not None else self.out / "bench-b2"
        return base / f"b2-sample-{_pct(sample)}"


def _pct(sample: float) -> str:
    return f"{sample * 100:g}".replace(".", "_")


def find_hook() -> tuple[Hook | None, str]:
    """ml.gnn.b2_timing, or None and the reason B2 is skipped."""
    try:
        module = importlib.import_module(GNN_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name in (GNN_MODULE, "ml"):
            return None, f"{GNN_MODULE} is not in this checkout: B2 skipped"
        raise
    hook = getattr(module, GNN_FUNCTION, None)
    if not callable(hook):
        return None, f"{GNN_MODULE} has no {GNN_FUNCTION}(): B2 skipped"
    check = getattr(module, GNN_UNAVAILABLE, None)
    reason = check() if callable(check) else None
    if reason:
        return None, f"{reason}: B2 skipped"
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
    for name in _SECONDS:
        _finite(record[name], name)
    for name in _RATIOS:
        _finite(record[name], name, high=1.0)
    sample = record["sample"]
    if isinstance(sample, bool) or not isinstance(sample, int | float) or not 0 < sample <= 1:
        raise TimingError(f"sample must be in (0, 1], got {sample!r}")
    for name in _INTEGERS:
        value = record[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise TimingError(f"{name} must be a positive integer, got {value!r}")
    for name in _TEXTS:
        if not isinstance(record[name], str) or not str(record[name]).strip():
            raise TimingError(f"{name} must be a non-empty string")
    if record["population"] != POPULATION[protocol]:
        raise TimingError(
            f"US-202 states protocol {protocol} on population {POPULATION[protocol]}, got "
            f"{record['population']!r}"
        )
    epochs = record["epoch_seconds"]
    if not isinstance(epochs, list) or not epochs:
        raise TimingError("epoch_seconds must be a non-empty list")
    for value in epochs:
        _finite(value, "epoch_seconds")
    if not isinstance(record["comparable"], Mapping):
        raise TimingError("comparable must map setting names to values")
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


def _measurements(timing: GnnTiming, started_on: Any, first_of_job: bool) -> list[Measurement]:
    device = timing["device_requested"]
    protocol = timing["protocol"]
    where = f"MI300X: {timing['hardware']}" if device == "cuda" else timing["hardware"]
    versions = ", ".join(f"{k} {v}" for k, v in sorted(timing["versions"].items()))
    full = timing["sample"] >= 1.0
    data = "every event" if full else f"seeded {timing['sample']:.0%} customer sample"
    origin = short(
        f"B2, measured by bench through ml.gnn.b2_timing (US-202 job) on {where} ({versions}); "
        f"{timing['data_origin']}; protocol {protocol} (ADR-10), {data}"
    )
    graph = f"{timing['nodes']:,} nodes, {timing['edges']:,} training events, {data}"
    common: dict[str, Any] = {
        "benchmark": "B2",
        "device": API_DEVICE[device],
        "origin": origin,
        "period_start": started_on,
        "period_end": utc_now().date(),
        "nature": "real",
        "data_nature": "real",
        "rows": timing["edges"],
    }
    p = protocol.lower()
    size = f"{_pct(timing['sample'])}pct"
    out: list[Measurement] = []
    if first_of_job:
        out.append(
            Measurement(
                metric=f"gnn_cold_start_seconds_{size}",
                label=short(
                    "GraphSAGE job cold start: import torch and DGL, device context and first "
                    f"kernels, in a fresh process ({data})"
                ),
                value=round(timing["cold_start_seconds"], 3),
                unit="seconds",
                phase="cold_start",
                warm=False,
                repeats=1,
                **{**common, "rows": None},
            )
        )
    out += [
        Measurement(
            metric=f"gnn_epoch_seconds_median_{p}_{size}",
            label=short(
                f"GraphSAGE median epoch, protocol {protocol} ({graph}): the speedup basis, the "
                "same work on every device"
            ),
            value=round(timing["epoch_seconds_median"], 4),
            unit="seconds",
            phase="training",
            warm=True,
            repeats=len(timing["epoch_seconds"]),
            detail={"epoch_seconds": summary(timing["epoch_seconds"], 4)},
            protocol=API_PROTOCOL[protocol],
            **common,
        ),
        Measurement(
            metric=f"gnn_train_seconds_{p}_{size}",
            label=short(
                f"GraphSAGE training loop, protocol {protocol} ({graph}), {timing['epochs']} "
                "epochs until early stopping: not a speedup basis"
            ),
            value=round(timing["train_seconds"], 3),
            unit="seconds",
            phase="training",
            warm=False,
            repeats=1,
            protocol=API_PROTOCOL[protocol],
            **common,
        ),
        Measurement(
            metric=f"gnn_inference_seconds_{p}_{size}",
            label=short(f"GraphSAGE scoring of the test events, protocol {protocol} ({data})"),
            value=round(timing["inference_seconds"], 3),
            unit="seconds",
            phase="scoring",
            warm=True,
            repeats=1,
            protocol=API_PROTOCOL[protocol],
            **common,
        ),
    ]
    if full:
        population = POPULATION_TEXT[timing["population"]]
        events = f"{timing['population_events']:,} events"
        if protocol == "A":
            reference = (
                f"; paper GNN F1 {PAPER_GNN_F1} (paper protocol: not product performance, NFR-16)"
            )
        else:
            reference = "; decision-time protocol (ADR-10): no published figure applies"
        for metric, value, at in (
            (f"gnn_f1_{p}", timing["f1"], f"validation threshold {timing['threshold']:.3f}"),
            (f"gnn_f1_at_half_{p}", timing["f1_at_half"], "threshold 0.5"),
        ):
            out.append(
                Measurement(
                    metric=metric,
                    label=short(
                        f"GraphSAGE F1, protocol {protocol}, ASOS {population} ({events}), {at}"
                        + reference
                    ),
                    value=round(value, 4),
                    unit="ratio",
                    phase="scoring",
                    repeats=1,
                    protocol=API_PROTOCOL[protocol],
                    **common,
                )
            )
    peak = timing.get("peak_memory_gib")
    if peak is not None:
        out.append(
            Measurement(
                metric=f"gnn_peak_memory_gib_{p}_{size}",
                label=short(f"GraphSAGE peak device memory, protocol {protocol} ({data})"),
                value=round(float(peak), 3),
                unit="gib",
                phase="training",
                repeats=1,
                protocol=API_PROTOCOL[protocol],
                **common,
            )
        )
    return out


def _speedup(cpu: Mapping[str, Any], gpu: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "protocol": cpu["protocol"],
        "sample": cpu["sample"],
        "epochs": {"cpu": cpu["epochs"], "mi300x": gpu["epochs"]},
        "epoch_seconds_median": {
            "cpu": cpu["epoch_seconds_median"],
            "mi300x": gpu["epoch_seconds_median"],
        },
    }
    if cpu["comparable"] != gpu["comparable"]:
        differ = sorted(
            k
            for k in set(cpu["comparable"]) | set(gpu["comparable"])
            if cpu["comparable"].get(k) != gpu["comparable"].get(k)
        )
        out["not_computed"] = f"different settings on the two devices: {', '.join(differ)}"
        return out
    if gpu["epoch_seconds_median"] > 0:
        ratio = cpu["epoch_seconds_median"] / gpu["epoch_seconds_median"]
        out["epoch_cpu_over_gpu"] = round(ratio, 3)
    if gpu["inference_seconds"] > 0:
        out["inference_cpu_over_gpu"] = round(
            cpu["inference_seconds"] / gpu["inference_seconds"], 3
        )
    if cpu["sample"] >= 1.0:
        out["f1"] = {"cpu": cpu["f1"], "mi300x": gpu["f1"]}
    return out


def _skipped(started: Any, reason: str, hardware: dict[str, Any]) -> Part:
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
            "interface": f"{GNN_MODULE}.{GNN_FUNCTION}(raw_dir, *, protocol, device, seed, "
            "sample, out, controls, extra_args) -> GnnTiming (bench/b2_gnn.py)"
        },
        hardware=hardware,
    )


def run_b2(config: B2Config, log: Log = lambda _m: None, hook: Hook | None = None) -> Part:
    started = utc_now()
    gpu = "cuda" in config.devices
    hardware = env.hardware(gpu=gpu)
    found, reason = (hook, "") if hook is not None else find_hook()
    if found is None:
        log(reason)
        return _skipped(started, reason, hardware)
    measurements: list[Measurement] = []
    records: list[dict[str, Any]] = []
    versions = env.software(("torch", "dgl"))
    jobs_seen: set[tuple[str, float]] = set()
    for sample in config.samples:
        out = config.out_for(sample)
        for protocol in config.protocols:
            for device in config.devices:
                raw = found(
                    config.raw_dir,
                    protocol=protocol,
                    device=device,
                    seed=config.seed,
                    sample=sample,
                    out=out,
                    controls=sample >= 1.0,
                    extra_args=config.job_args,
                )
                timing = check_timing(raw, protocol, device)
                if timing["sample"] != sample:
                    raise TimingError(f"asked for a {sample:g} sample, got {timing['sample']:g}")
                log(
                    f"B2 {protocol} {device} {sample:.0%}: median epoch "
                    f"{timing['epoch_seconds_median']:.3f} s, F1 {timing['f1']:.4f}"
                )
                versions.update(timing["versions"])
                records.append(dict(timing))
                first = (device, sample) not in jobs_seen
                jobs_seen.add((device, sample))
                measurements += _measurements(timing, started.date(), first)
    speedups = []
    for sample in config.samples:
        for protocol in config.protocols:
            by_device = {
                r["device_requested"]: r
                for r in records
                if r["protocol"] == protocol and r["sample"] == sample
            }
            if "cpu" in by_device and "cuda" in by_device:
                speedups.append(_speedup(by_device["cpu"], by_device["cuda"]))
    notes = [
        f"Protocol A F1: the paper's protocol, next to the paper's GNN F1 {PAPER_GNN_F1} "
        "(arXiv:2302.14096); it measures the dataset, not the product (ADR-10, NFR-16). "
        "Protocol B F1: the decision-time population, with no paper reference.",
        "Speedup: CPU median epoch over MI300X median epoch (the same work per epoch), computed "
        "only between runs with identical settings; the training loops are reported but not "
        "divided, because early stopping ends at a different epoch on each device.",
        "The full-data runs are the US-202 runs: they also wrote "
        f"{config.out}/eval/gnn/us-202-asos-<cpu|gpu>.json for `make gnn-results`.",
    ]
    if any("not_computed" in s for s in speedups):
        notes.append(
            "For some sizes the two devices ran with different settings: no speedup is "
            "computed for them (details)."
        )
    model = records[0]["model"] if records else "not run"
    return Part(
        benchmark="B2",
        status="measured",
        title="GraphSAGE on ASOS GraphReturns: CPU against MI300X, F1 as in US-202"
        if gpu
        else "GraphSAGE on ASOS GraphReturns: CPU (MI300X pending), F1 as in US-202",
        dataset="ASOS GraphReturns (OSF c793h, real), protocols A (paper) and B (decision time) "
        "of ADR-10; seeded customer samples for the smaller sizes",
        started_at=started,
        finished_at=utc_now(),
        versions=versions,
        config={
            "seed": config.seed,
            "devices": ",".join(API_DEVICE[d] for d in config.devices),
            "samples": ",".join(f"{s:g}" for s in config.samples),
            "model": short(model, 200),
            "speedup_basis": "median epoch (US-202)",
        },
        measurements=measurements,
        notes=notes,
        details={"records": records, "speedups": speedups},
        hardware=hardware,
    )
