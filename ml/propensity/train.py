"""XGBoost training with the paper's hyperparameters, on CPU or GPU (US-201, benchmark B1).

train_booster is the one training entry point: the same data, parameters and seed run with
device="cpu" here (DEVICE=cpu) and with device="cuda" on the MI300X (amd_xgboost on ROCm also
takes "cuda"; "hip" is rejected by XGBoost). Building the DMatrix and training are timed apart,
the effective device is read back from the booster (amd_xgboost falls back to CPU with only a
warning), and the booster returned holds the trees up to the best early-stopping iteration only,
so every later prediction uses the model that was selected.

Hyperparameters of the paper (arXiv:2302.14096) as given in spec 8.1: hist, max_depth 4, eta 0.1,
gamma 1, min_child_weight 1, lambda 2, early stopping after 5 rounds without a better validation
log loss. The cap on boosting rounds is ours (MAX_ROUNDS).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import xgboost as xgb

Device = Literal["cpu", "cuda"]
DEVICES: tuple[Device, ...] = ("cpu", "cuda")
PAPER_PARAMS: Mapping[str, Any] = MappingProxyType(
    {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "tree_method": "hist",
        "max_depth": 4,
        "eta": 0.1,
        "gamma": 1.0,
        "min_child_weight": 1.0,
        "lambda": 2.0,
    }
)
EARLY_STOPPING_ROUNDS = 5
MAX_ROUNDS = 2000


@dataclass(frozen=True)
class TrainedBooster:
    booster: xgb.Booster  # trees up to best_iteration only
    device_requested: Device
    device_effective: str
    threads: int
    rows_fit: int
    rows_valid: int
    dmatrix_seconds: float
    train_seconds: float
    best_iteration: int
    rounds_run: int
    # 1-minute load average of the machine just before and after training (None off Unix): other
    # jobs on the same CPUs slow a CPU timing down, so B1 reports it next to the seconds.
    load_before: float | None = None
    load_after: float | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "device_requested": self.device_requested,
            "device_effective": self.device_effective,
            "threads": self.threads,
            "rows_fit": self.rows_fit,
            "rows_valid": self.rows_valid,
            "features": self.booster.num_features(),
            "dmatrix_seconds": round(self.dmatrix_seconds, 3),
            "train_seconds": round(self.train_seconds, 3),
            "best_iteration": self.best_iteration,
            "trees_kept": self.best_iteration + 1,
            "rounds_run": self.rounds_run,
            "load_average_before": self.load_before,
            "load_average_after": self.load_after,
        }


def load_average() -> float | None:
    """The 1-minute load average, rounded to 0.01, or None where the OS does not give it."""
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):  # pragma: no cover - not on Linux
        return None


def effective_device(booster: xgb.Booster) -> str:
    config = json.loads(booster.save_config())
    return str(config["learner"]["generic_param"]["device"])


def check_device(requested: str, effective: str) -> None:
    """A GPU run that silently trained on the CPU would make B1 meaningless: refuse it."""
    if requested not in DEVICES:
        raise ValueError(f"device must be one of {DEVICES}, not {requested!r}")
    if requested == "cuda" and effective.startswith("cpu"):
        raise RuntimeError(f"GPU not used: requested cuda, effective device {effective!r}")


def train_booster(
    x_fit: npt.NDArray[np.float32],
    y_fit: npt.ArrayLike,
    x_valid: npt.NDArray[np.float32],
    y_valid: npt.ArrayLike,
    feature_names: Sequence[str],
    *,
    device: Device = "cpu",
    seed: int = 42,
    max_rounds: int = MAX_ROUNDS,
    early_stopping_rounds: int = EARLY_STOPPING_ROUNDS,
    threads: int | None = None,
) -> TrainedBooster:
    """Train on (x_fit, y_fit) with early stopping on (x_valid, y_valid); NaN is missing."""
    check_device(device, device)
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")
    nthread = threads or os.cpu_count() or 1
    names = list(feature_names)
    start = time.perf_counter()
    dfit = xgb.DMatrix(x_fit, label=np.asarray(y_fit), feature_names=names, nthread=nthread)
    dvalid = xgb.DMatrix(x_valid, label=np.asarray(y_valid), feature_names=names, nthread=nthread)
    dmatrix_seconds = time.perf_counter() - start
    params = {**PAPER_PARAMS, "device": device, "seed": seed, "nthread": nthread}
    load_before = load_average()
    start = time.perf_counter()
    booster = xgb.train(
        params,
        dfit,
        num_boost_round=max_rounds,
        evals=[(dvalid, "validation")],
        early_stopping_rounds=early_stopping_rounds,
        verbose_eval=False,
    )
    train_seconds = time.perf_counter() - start
    load_after = load_average()
    rounds_run = booster.num_boosted_rounds()
    best = int(booster.best_iteration)
    device_effective = effective_device(booster)
    check_device(device, device_effective)
    return TrainedBooster(
        booster=booster[: best + 1],
        device_requested=device,
        device_effective=device_effective,
        threads=nthread,
        rows_fit=int(dfit.num_row()),
        rows_valid=int(dvalid.num_row()),
        dmatrix_seconds=dmatrix_seconds,
        train_seconds=train_seconds,
        best_iteration=best,
        rounds_run=rounds_run,
        load_before=load_before,
        load_after=load_after,
    )


def predict(booster: xgb.Booster, x: npt.NDArray[np.float32]) -> npt.NDArray[np.float64]:
    """Raw (uncalibrated) probabilities of a booster on a feature matrix."""
    names = booster.feature_names
    matrix = xgb.DMatrix(x, feature_names=list(names) if names else None)
    return np.asarray(booster.predict(matrix), dtype=np.float64)
