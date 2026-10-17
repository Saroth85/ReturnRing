"""B1: XGBoost training and scoring time on ASOS GraphReturns, CPU against the MI300X (US-902).

The data are the exact matrices US-201 trains on (ml.propensity.run.training_data): protocol A
(paper protocol, every train event, 59 features: the heavier, published configuration that B1
compares with the paper) and protocol B (decision-time protocol, ADR-10). The training call is
ml.propensity.train.train_booster with the paper's parameters, the same seed and thread count;
only `device` changes ("cpu", or "cuda", which is the MI300X under amd_xgboost on ROCm). On the
droplet both devices run in the same container with the same amd_xgboost build, so data, version
and parameters are identical (spec 12, "Qualità comparabile").

For each protocol, data size (nested seeded subsets of the fit and validation rows, "Carichi
crescenti") and device:

- cold start: the first training call on the device in the process (device initialisation
  included), on a small slice, timed apart;
- training with early stopping (the product's configuration), `repeats` times after the cold
  start: median wall time, rounds run, validation log loss and AUC of the kept trees, so a time
  never stands without the quality it bought;
- training with a fixed number of rounds and no early stopping: identical work on both devices,
  the only training runs a CPU-over-GPU speedup is computed from (spec 12, 'Qualità
  comparabile'); early stopping ends at a different round on each device (other quantile cuts,
  other floating point sums), so its runs are shown side by side with their rounds and a ratio
  of seconds per 100 rounds, and a speedup only when every repeat ran the same rounds;
- scoring with the fixed-rounds model of the largest size (all its trees: the same model size on
  both devices; the early-stopping model when no fixed-rounds run is asked): one batch over every
  fit row, and single-row calls (the latency an API call would see, DMatrix build included), p50
  and p95; a CPU-over-GPU scoring ratio only between models with the same number of trees.

A requested GPU run that trained on the CPU stops the part (train.check_device): it is never
reported as a GPU number. The CPU baseline must run alone on the machine; the 1-minute load
average before and after each training is kept next to its time.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow.parquet as pq
from sklearn.metrics import roc_auc_score

from bench import env
from bench.record import Measurement, Part, short, utc_now
from bench.stats import median, summary
from contracts.io import META_PROVENANCE, Provenance, path_for
from ml.propensity.features import PROTOCOL_LABELS, Protocol
from ml.propensity.run import RunConfig, TrainingData, training_data
from ml.propensity.train import (
    EARLY_STOPPING_ROUNDS,
    MAX_ROUNDS,
    PAPER_PARAMS,
    Device,
    TrainedBooster,
    predict,
    train_booster,
)

Log = Callable[[str], None]
API_PROTOCOL = {"A": "paper", "B": "decision_time"}
API_DEVICE = {"cpu": "cpu", "cuda": "mi300x"}
EPS = 1e-7


@dataclass(frozen=True)
class B1Config:
    raw_dir: Path
    devices: tuple[Device, ...] = ("cpu",)
    protocols: tuple[Protocol, ...] = ("A", "B")
    fractions: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0)
    repeats: int = 3
    seed: int = 42
    threads: int | None = None
    max_rounds: int = MAX_ROUNDS
    fixed_rounds: int | None = 200
    single_row_calls: int = 200
    warmup_rows: int = 20_000
    warmup_rounds: int = 10

    def __post_init__(self) -> None:
        if not self.devices or any(d not in API_DEVICE for d in self.devices):
            raise ValueError(f"devices must be among {tuple(API_DEVICE)}")
        if not self.fractions or any(not 0 < f <= 1 for f in self.fractions):
            raise ValueError("fractions must be in (0, 1]")
        if self.repeats < 1 or self.single_row_calls < 1:
            raise ValueError("repeats and single_row_calls must be at least 1")


@dataclass
class _Timing:
    """The repeats of one (protocol, fraction, variant, device)."""

    trained: list[TrainedBooster] = field(default_factory=list)
    logloss: list[float] = field(default_factory=list)
    auc: list[float] = field(default_factory=list)


def raw_provenance(raw_dir: Path) -> Provenance:
    """The provenance of the raw ASOS events, read from the file metadata alone."""
    meta = pq.read_schema(path_for("raw/asos_events", raw_dir.parent)).metadata or {}
    return Provenance.from_json(meta[META_PROVENANCE])


def nested_subsets(n: int, fractions: Sequence[float], seed: int) -> dict[float, npt.NDArray[Any]]:
    """Sorted row indices of each fraction; smaller fractions are subsets of larger ones."""
    order = np.random.default_rng(seed).permutation(n)
    return {f: np.sort(order[: max(1, round(n * f))]) for f in fractions}


def validation_quality(
    booster_out: TrainedBooster, x: npt.NDArray[np.float32], y: npt.NDArray[np.int8]
) -> tuple[float, float | None]:
    """Log loss (nats) and AUC of the kept trees on the validation rows (AUC None when the
    rows hold one class only)."""
    p = np.clip(predict(booster_out.booster, x), EPS, 1 - EPS)
    logloss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    auc = float(roc_auc_score(y, p)) if 0 < y.sum() < y.size else None
    return logloss, auc


def _train(
    data: TrainingData,
    fit: npt.NDArray[Any],
    valid: npt.NDArray[Any],
    device: Device,
    config: B1Config,
    rounds: int | None,
) -> TrainedBooster:
    """One training call: early stopping (rounds None) or exactly `rounds` rounds."""
    return train_booster(
        data.x_fit[fit],
        data.y_fit[fit],
        data.x_valid[valid],
        data.y_valid[valid],
        data.feature_names,
        device=device,
        seed=config.seed,
        max_rounds=rounds or config.max_rounds,
        # Patience longer than the rounds: early stopping never triggers (identical work), and
        # the model keeps all its trees (the same model size to score on every device).
        early_stopping_rounds=rounds + 1 if rounds else EARLY_STOPPING_ROUNDS,
        threads=config.threads,
        keep_all_rounds=bool(rounds),
    )


def compare_devices(
    runs: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """CPU against GPU for each (protocol, size, variant) timed on both devices: speedups of
    identical work (fixed rounds), and the early-stopping runs side by side with their rounds
    and the ratio of seconds per 100 rounds (labelled as such, never as a speedup); an
    early-stopping speedup only when every repeat ran the same rounds on both devices."""
    by_key: dict[tuple[str, float, str], dict[str, Mapping[str, Any]]] = {}
    for run in runs:
        key = (run["protocol"], run["fraction"], run["variant"])
        by_key.setdefault(key, {})[run["device"]] = run
    speedups: list[dict[str, Any]] = []
    early: list[dict[str, Any]] = []
    for (protocol, fraction, variant), pair in sorted(by_key.items()):
        if "cpu" not in pair or "cuda" not in pair:
            continue
        cpu, gpu = pair["cpu"], pair["cuda"]
        rounds = {"cpu": list(cpu["rounds_each"]), "mi300x": list(gpu["rounds_each"])}
        same = len(set(rounds["cpu"]) | set(rounds["mi300x"])) == 1
        ratio = (
            round(cpu["train_seconds_median"] / gpu["train_seconds_median"], 3)
            if gpu["train_seconds_median"] > 0
            else None
        )
        row: dict[str, Any] = {
            "protocol": protocol,
            "fraction": fraction,
            "variant": variant,
            "rounds": rounds,
        }
        if variant != "es":
            if same and ratio is not None:
                speedups.append({**row, "cpu_over_gpu": ratio})
            continue
        row["same_rounds"] = same
        row["train_seconds_median"] = {
            "cpu": cpu["train_seconds_median"],
            "mi300x": gpu["train_seconds_median"],
        }
        cpu_100 = cpu["seconds_per_100_rounds_median"]
        gpu_100 = gpu["seconds_per_100_rounds_median"]
        if gpu_100 > 0:
            row["per_100_rounds_cpu_over_gpu"] = round(cpu_100 / gpu_100, 3)
        if same and ratio is not None:
            row["cpu_over_gpu"] = ratio
        early.append(row)
    return speedups, early


def compare_scoring(protocol: str, scoring: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """CPU over GPU scoring times, only between models with the same number of trees."""
    by_device = {s["device"]: s for s in scoring}
    if "cpu" not in by_device or "cuda" not in by_device:
        return []
    cpu, gpu = by_device["cpu"], by_device["cuda"]
    row: dict[str, Any] = {
        "protocol": protocol,
        "trees": {"cpu": cpu["trees"], "mi300x": gpu["trees"]},
        "model": {"cpu": cpu.get("model"), "mi300x": gpu.get("model")},
    }
    if cpu["trees"] != gpu["trees"]:
        row["not_computed"] = f"trees differ (cpu {cpu['trees']}, mi300x {gpu['trees']})"
        return [row]
    for name, field_name in (("batch", "batch_seconds"), ("single_row_p50", "single_row_ms")):
        g = gpu[field_name]["p50"]
        if g:
            row[f"{name}_cpu_over_gpu"] = round(cpu[field_name]["p50"] / g, 3)
    return [row]


def _pct(fraction: float) -> str:
    return f"{fraction * 100:g}".replace(".", "_")


@dataclass
class _Context:
    config: B1Config
    origin_of: Callable[[Device, str], str]
    started_on: date
    measurements: list[Measurement] = field(default_factory=list)

    def add(self, device: Device, protocol: str | None, **fields: Any) -> None:
        end = utc_now().date()
        self.measurements.append(
            Measurement(
                benchmark="B1",
                device=API_DEVICE[device],
                origin=self.origin_of(device, protocol or "A and B"),
                period_start=self.started_on,
                period_end=end,
                nature="real",
                data_nature="real",
                protocol=API_PROTOCOL[protocol] if protocol else None,
                **fields,
            )
        )


def run_b1(config: B1Config, log: Log = lambda _m: None) -> Part:
    started = utc_now()
    gpu = "cuda" in config.devices
    hardware = env.hardware(gpu=gpu)
    versions = env.software(("numpy", "polars", "sklearn"))
    build = env.xgboost_build()
    versions.update({k: str(v) for k, v in build.items() if v is not None})
    provenance = raw_provenance(config.raw_dir)
    threads = config.threads or hardware["logical_cpus"] or 1
    gpu_name = _gpu_name(hardware)

    def origin_of(device: Device, protocol: str) -> str:
        where = (
            f"MI300X ({gpu_name})" if device == "cuda" else f"{hardware['cpu']}, {threads} threads"
        )
        return short(
            f"B1, measured by bench on {where}; xgboost {build.get('xgboost')}"
            f"{' HIP build' if build.get('xgboost_use_hip') else ''}; {provenance.origin}; "
            f"protocol {protocol} (ADR-10)"
        )

    ctx = _Context(config, origin_of, started.date())
    details: dict[str, Any] = {
        "protocols": {},
        "speedups": [],
        "early_stopping": [],
        "scoring_speedups": [],
    }
    cold_done: set[str] = set()
    for protocol in config.protocols:
        t0 = time.perf_counter()
        data = training_data(config.raw_dir, protocol, RunConfig(seed=config.seed))
        prepare_s = time.perf_counter() - t0
        log(
            f"B1 protocol {protocol}: {data.y_fit.size} fit rows, {len(data.feature_names)} "
            f"features, prepared in {prepare_s:.1f} s"
        )
        fits = nested_subsets(data.y_fit.size, config.fractions, config.seed)
        valids = nested_subsets(data.y_valid.size, config.fractions, config.seed + 1)
        proto_detail: dict[str, Any] = {
            "label": PROTOCOL_LABELS[protocol],
            "features": len(data.feature_names),
            "fit_rows": int(data.y_fit.size),
            "validation_rows": int(data.y_valid.size),
            "prepare_seconds": round(prepare_s, 3),
            "runs": [],
        }
        for device in config.devices:
            if device not in cold_done:
                warm = np.arange(min(config.warmup_rows, data.y_fit.size))
                warm_valid = np.arange(min(config.warmup_rows // 10 + 1, data.y_valid.size))
                t0 = time.perf_counter()
                first = _train(data, warm, warm_valid, device, config, config.warmup_rounds)
                cold_s = time.perf_counter() - t0
                cold_done.add(device)
                details.setdefault("cold_start", []).append(
                    {
                        "device": device,
                        "seconds": round(cold_s, 3),
                        "device_effective": first.device_effective,
                        "load_average_before_after": (first.load_before, first.load_after),
                    }
                )
                ctx.add(
                    device,
                    None,
                    metric="cold_start_seconds",
                    label=short(
                        f"Cold start: first training call on the {API_DEVICE[device]} in the "
                        f"process ({warm.size:,} rows, {config.warmup_rounds} rounds), device "
                        "initialisation included"
                    ),
                    value=round(cold_s, 3),
                    unit="seconds",
                    phase="cold_start",
                    rows=int(warm.size),
                    warm=False,
                    repeats=1,
                )
                log(f"B1 {device}: cold start {cold_s:.2f} s")
            variants = [("es", None)] + (
                [(f"fixed{config.fixed_rounds}", config.fixed_rounds)]
                if config.fixed_rounds
                else []
            )
            largest: tuple[str, TrainedBooster] | None = None
            scoring_variant = variants[-1][0]  # fixed rounds when asked: the same trees
            for fraction in config.fractions:
                fit, valid = fits[fraction], valids[fraction]
                for variant, rounds in variants:
                    timing = _Timing()
                    for _ in range(config.repeats):
                        trained = _train(data, fit, valid, device, config, rounds)
                        loss, auc = validation_quality(
                            trained, data.x_valid[valid], data.y_valid[valid]
                        )
                        timing.trained.append(trained)
                        timing.logloss.append(loss)
                        if auc is not None:
                            timing.auc.append(auc)
                    if variant == scoring_variant and fraction == max(config.fractions):
                        largest = (variant, timing.trained[-1])
                    run = _record_training(ctx, protocol, device, fraction, variant, timing)
                    proto_detail["runs"].append(run)
                    log(
                        f"B1 {protocol} {device} {_pct(fraction)}% {variant}: "
                        f"{run['train_seconds_median']:.2f} s, {run['rounds_run']} rounds"
                    )
            if largest is not None:
                proto_detail.setdefault("scoring", []).append(
                    _record_scoring(ctx, protocol, device, data, largest, config)
                )
        speedups, early = compare_devices(proto_detail["runs"])
        details["speedups"] += speedups
        details["early_stopping"] += early
        details["scoring_speedups"] += compare_scoring(protocol, proto_detail.get("scoring", []))
        details["protocols"][protocol] = proto_detail
        del data
    finished = utc_now()
    config_out: dict[str, str | int | float | bool] = {
        **{k: v for k, v in PAPER_PARAMS.items() if isinstance(v, str | int | float)},
        "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
        "max_rounds": config.max_rounds,
        "fixed_rounds": config.fixed_rounds or 0,
        "seed": config.seed,
        "repeats": config.repeats,
        "fractions": ",".join(f"{f:g}" for f in config.fractions),
        "threads": threads,
        "cpu": short(str(hardware["cpu"]), 64),
        "devices": ",".join(API_DEVICE[d] for d in config.devices),
    }
    if gpu:
        config_out["gpu"] = short(gpu_name, 64)
    notes = [
        "Protocol A numbers measure the dataset, not the product (ADR-10, NFR-16); B1 compares "
        "times, not accuracy.",
        "Times are wall clock of one process; the CPU baseline ran with the load average shown "
        "next to each run (details).",
    ]
    if not gpu:
        notes.append("CPU only: no MI300X run (retired, ADR-18).")
    return Part(
        benchmark="B1",
        status="measured",
        title="XGBoost training and scoring on ASOS GraphReturns: CPU against MI300X"
        if gpu
        else "XGBoost training and scoring on ASOS GraphReturns: CPU (MI300X pending)",
        dataset=short(
            f"ASOS GraphReturns train split ({provenance.origin}; nature {provenance.nature}), "
            "protocols A (paper) and B (decision time) of ADR-10"
        ),
        started_at=started,
        finished_at=finished,
        versions=versions,
        config=config_out,
        measurements=ctx.measurements,
        notes=notes,
        details=details,
        hardware=hardware,
    )


def _gpu_name(hardware: dict[str, Any]) -> str:
    gpus = (hardware.get("gpu") or {}).get("gpus") or []
    names = sorted({str(g.get("market_name") or g.get("target_graphics_version")) for g in gpus})
    return ", ".join(names) if names else "GPU name unknown"


def _record_training(
    ctx: _Context,
    protocol: Protocol,
    device: Device,
    fraction: float,
    variant: str,
    timing: _Timing,
) -> dict[str, Any]:
    first = timing.trained[0]
    seconds = [t.train_seconds for t in timing.trained]
    rounds = [t.rounds_run for t in timing.trained]
    per_100 = [100 * t.train_seconds / t.rounds_run for t in timing.trained]
    run = {
        "protocol": protocol,
        "device": device,
        "device_effective": first.device_effective,
        "fraction": fraction,
        "variant": variant,
        "rows_fit": first.rows_fit,
        "rows_valid": first.rows_valid,
        "threads": first.threads,
        "train_seconds": summary(seconds),
        "train_seconds_median": round(median(seconds), 3),
        "dmatrix_seconds_median": round(median([t.dmatrix_seconds for t in timing.trained]), 3),
        "rounds_run": int(median(rounds)),
        "rounds_each": rounds,
        "best_iteration_each": [t.best_iteration for t in timing.trained],
        "seconds_per_100_rounds_median": round(median(per_100), 3),
        "validation_logloss_median": round(median(timing.logloss), 6),
        "validation_auc_median": round(median(timing.auc), 6) if timing.auc else None,
        "load_average_before_after": [(t.load_before, t.load_after) for t in timing.trained],
    }
    what = "early stopping" if variant == "es" else f"{variant[5:]} rounds, no early stopping"
    ctx.add(
        device,
        protocol,
        metric=f"train_seconds_{protocol.lower()}_{variant}_{_pct(fraction)}pct",
        label=short(
            f"Training time, protocol {protocol}, {_pct(fraction).replace('_', '.')}% of the "
            f"train split ({first.rows_fit:,} rows), {what}; median of {len(seconds)}, "
            f"{run['rounds_run']} rounds, validation log loss "
            f"{run['validation_logloss_median']:.4f}"
        ),
        value=run["train_seconds_median"],
        unit="seconds",
        phase="training",
        rows=first.rows_fit,
        warm=True,
        repeats=len(seconds),
        detail={
            k: run[k]
            for k in (
                "rounds_run",
                "seconds_per_100_rounds_median",
                "validation_logloss_median",
                "validation_auc_median",
                "dmatrix_seconds_median",
                "device_effective",
            )
        },
    )
    return run


def _record_scoring(
    ctx: _Context,
    protocol: Protocol,
    device: Device,
    data: TrainingData,
    scored: tuple[str, TrainedBooster],
    config: B1Config,
) -> dict[str, Any]:
    """Batch scoring of every fit row, and single-row calls, with the model of the largest size
    (fixed rounds when the run has them: the same trees on every device)."""
    variant, model = scored
    trees = model.booster.num_boosted_rounds()
    what = (
        f"{trees} trees, {variant[5:]} fixed rounds"
        if variant != "es"
        else f"{trees} trees, early stopping"
    )
    x = data.x_fit
    batch = []
    for _ in range(config.repeats):
        t0 = time.perf_counter()
        predict(model.booster, x)
        batch.append(time.perf_counter() - t0)
    rows = x[: min(config.single_row_calls, x.shape[0])]
    predict(model.booster, rows[:1])  # first call excluded from the latency
    single = []
    for i in range(config.single_row_calls):
        row = rows[i % rows.shape[0] : i % rows.shape[0] + 1]
        t0 = time.perf_counter()
        predict(model.booster, row)
        single.append((time.perf_counter() - t0) * 1000)
    batch_s = round(median(batch), 3)
    ms = summary(single)
    ctx.add(
        device,
        protocol,
        metric=f"score_batch_seconds_{protocol.lower()}",
        label=short(
            f"Batch scoring, protocol {protocol}: {x.shape[0]:,} rows with the model of the "
            f"largest size ({model.rows_fit:,} fit rows, {what}), DMatrix build included; "
            f"median of {len(batch)}"
        ),
        value=batch_s,
        unit="seconds",
        phase="scoring",
        rows=int(x.shape[0]),
        warm=True,
        repeats=len(batch),
        detail={
            "rows_per_second": round(x.shape[0] / batch_s, 1) if batch_s else None,
            "trees": trees,
        },
    )
    for q in ("p50", "p95"):
        ctx.add(
            device,
            protocol,
            metric=f"score_single_row_{q}_ms_{protocol.lower()}",
            label=short(
                f"Single-row scoring latency {q}, protocol {protocol}: {len(single)} calls of one "
                f"row each ({what}), DMatrix build included"
            ),
            value=round(float(ms[q]), 3),
            unit="milliseconds",
            phase="scoring",
            rows=1,
            warm=True,
            repeats=len(single),
            detail={"summary_ms": ms},
        )
    return {
        "device": device,
        "rows": int(x.shape[0]),
        "batch_seconds": summary(batch),
        "single_row_ms": ms,
        "trees": trees,
        "model": variant,
    }
