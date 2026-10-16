"""Risk models: XGBoost with isotonic calibration, and the A0 rules calibrated the same way
(spec 8.4, 8.6; MOD-05, MOD-06).

- XGBoost on CPU (xgboost-cpu, hist) with the spec 8.1 starting point: depth 4, learning rate
  0.1, gamma 1, lambda 2, early stopping after 5 rounds on the validation log loss, fixed seed;
  one thread, so a run is deterministic and does not stall on a shared CPU. The same parameters
  run on the MI300X in phase 6 with device="cuda" (spec 8.4 names amd_xgboost).
- Isotonic calibration on the validation split (ml.propensity.calibration, JSON-serialisable).
- The risk threshold of spec 8.6: the lowest calibrated score whose rule "risk >= t" has precision
  >= 0.8 on the validation split; None when no score reaches it (nothing is flagged).
- Contributions: native TreeSHAP of the booster (pred_contribs=True), log-odds scale.

Artifacts: <dir>/<name>.json (XGBoost JSON) or <name>.rules.json (A0), <name>.calibrator.json
and <name>.meta.json (features, threshold, version, sizes). No ground-truth row is stored.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pandas as pd
import xgboost as xgb

from contracts.files import atomic_write
from ml.propensity.calibration import IsotonicCalibrator
from ml.propensity.train import Device, check_device, effective_device
from ml.risk.features import check_feature_names, matrix
from ml.risk.metrics import PRECISION_TARGET, threshold_for_precision
from ml.risk.rules import RuleBaseline

F64 = npt.NDArray[np.float64]
MODEL_VERSION: Final = "v1"
XGB_PARAMS: Final[dict[str, Any]] = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "max_depth": 4,
    "eta": 0.1,
    "gamma": 1.0,
    "lambda": 2.0,
    "tree_method": "hist",
}


@dataclass(frozen=True)
class TrainConfig:
    seed: int = 42
    max_rounds: int = 500
    early_stopping_rounds: int = 5
    nthread: int = 1
    precision_target: float = PRECISION_TARGET
    device: Device = "cpu"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write(path, lambda out: out.write(text.encode("utf-8")))


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


@dataclass
class RiskModel:
    """A calibrated XGBoost risk model on a fixed feature list."""

    name: str
    features: tuple[str, ...]
    booster: xgb.Booster
    best_iteration: int
    calibrator: IsotonicCalibrator
    threshold: float | None
    trained_at: datetime
    sizes: dict[str, int] = field(default_factory=dict)

    @property
    def learner(self) -> str:
        return "xgboost"

    @property
    def version(self) -> str:
        return f"risk-{self.name}-xgboost-{MODEL_VERSION}@{self.trained_at:%Y-%m-%dT%H:%MZ}"

    def _dmatrix(self, frame: pd.DataFrame) -> xgb.DMatrix:
        x = matrix(frame, self.features)
        return xgb.DMatrix(x, feature_names=list(self.features), missing=np.nan, nthread=1)

    def raw(self, frame: pd.DataFrame) -> F64:
        """Probabilities before calibration."""
        if len(frame) == 0:
            return np.zeros(0, dtype=np.float64)
        out = self.booster.predict(
            self._dmatrix(frame), iteration_range=(0, self.best_iteration + 1)
        )
        return np.asarray(out, dtype=np.float64)

    def predict(self, frame: pd.DataFrame) -> F64:
        """Calibrated risk in [0, 1]."""
        return self.calibrator.predict(self.raw(frame))

    def contributions(self, frame: pd.DataFrame) -> F64:
        """TreeSHAP contributions, one column per feature plus the bias (last), log-odds."""
        n = len(self.features) + 1
        if len(frame) == 0:
            return np.zeros((0, n), dtype=np.float64)
        out = self.booster.predict(
            self._dmatrix(frame),
            pred_contribs=True,
            iteration_range=(0, self.best_iteration + 1),
        )
        return np.asarray(out, dtype=np.float64).reshape(len(frame), n)

    def meta(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "learner": self.learner,
            "version": self.version,
            "features": list(self.features),
            "best_iteration": self.best_iteration,
            "threshold": self.threshold,
            "trained_at": self.trained_at.isoformat(),
            "params": XGB_PARAMS,
            "sizes": self.sizes,
        }

    def save(self, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        booster_path = directory / f"{self.name}.json"
        raw = self.booster.save_raw(raw_format="json")
        atomic_write(booster_path, lambda out: out.write(bytes(raw)))
        _write_json(directory / f"{self.name}.calibrator.json", self.calibrator.to_dict())
        _write_json(directory / f"{self.name}.meta.json", self.meta())
        return [
            booster_path,
            directory / f"{self.name}.calibrator.json",
            directory / f"{self.name}.meta.json",
        ]

    @classmethod
    def load(cls, directory: Path, name: str) -> RiskModel:
        meta = _read_json(directory / f"{name}.meta.json")
        if meta.get("learner") != "xgboost":
            raise ValueError(f"{name}: not an XGBoost risk model")
        features = tuple(str(f) for f in meta["features"])
        check_feature_names(features)
        booster = xgb.Booster()
        booster.load_model(str(directory / f"{name}.json"))
        booster.set_param({"nthread": 1})
        threshold = meta["threshold"]
        return cls(
            name=name,
            features=features,
            booster=booster,
            best_iteration=int(meta["best_iteration"]),
            calibrator=IsotonicCalibrator.from_dict(
                _read_json(directory / f"{name}.calibrator.json")
            ),
            threshold=None if threshold is None else float(threshold),
            trained_at=datetime.fromisoformat(str(meta["trained_at"])),
            sizes={str(k): int(v) for k, v in dict(meta.get("sizes", {})).items()},
        )


@dataclass
class RuleModel:
    """A0: the rule score, calibrated on validation like every model."""

    name: str
    baseline: RuleBaseline
    calibrator: IsotonicCalibrator
    threshold: float | None
    trained_at: datetime
    sizes: dict[str, int] = field(default_factory=dict)

    @property
    def learner(self) -> str:
        return "rules"

    @property
    def features(self) -> tuple[str, ...]:
        return self.baseline.features

    @property
    def version(self) -> str:
        return f"risk-{self.name}-rules-{MODEL_VERSION}@{self.trained_at:%Y-%m-%dT%H:%MZ}"

    def raw(self, frame: pd.DataFrame) -> F64:
        return self.baseline.score(frame)

    def predict(self, frame: pd.DataFrame) -> F64:
        return self.calibrator.predict(self.raw(frame))

    def save(self, directory: Path) -> list[Path]:
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / f"{self.name}.rules.json", self.baseline.to_dict())
        _write_json(directory / f"{self.name}.calibrator.json", self.calibrator.to_dict())
        meta = {
            "name": self.name,
            "learner": self.learner,
            "version": self.version,
            "features": list(self.features),
            "rules": [r.describe() for r in self.baseline.rules],
            "threshold": self.threshold,
            "trained_at": self.trained_at.isoformat(),
            "sizes": self.sizes,
        }
        _write_json(directory / f"{self.name}.meta.json", meta)
        return [directory / f"{self.name}.{s}.json" for s in ("rules", "calibrator", "meta")]


def _labels(y: npt.ArrayLike) -> npt.NDArray[np.int8]:
    labels = np.asarray(y).astype(np.int8)
    if labels.ndim != 1 or not np.isin(labels, (0, 1)).all():
        raise ValueError("labels must be 0 and 1")
    return labels


def _sizes(y_train: npt.NDArray[np.int8], y_val: npt.NDArray[np.int8]) -> dict[str, int]:
    return {
        "train_rows": int(y_train.size),
        "train_positives": int(y_train.sum()),
        "validation_rows": int(y_val.size),
        "validation_positives": int(y_val.sum()),
    }


def fit_xgboost(
    name: str,
    features: Sequence[str],
    train: pd.DataFrame,
    y_train: npt.ArrayLike,
    validation: pd.DataFrame,
    y_val: npt.ArrayLike,
    trained_at: datetime,
    config: TrainConfig | None = None,
) -> RiskModel:
    """Train on `train`, stop early, calibrate and choose the threshold on `validation`."""
    cfg = config or TrainConfig()
    feats = tuple(features)
    check_feature_names(feats)
    yt, yv = _labels(y_train), _labels(y_val)
    if yt.min(initial=1) == yt.max(initial=0) or yv.sum() == 0:
        raise ValueError(f"{name}: training needs both classes and validation positives")
    check_device(cfg.device, cfg.device)
    params = {**XGB_PARAMS, "seed": cfg.seed, "nthread": cfg.nthread, "device": cfg.device}
    dtrain = xgb.DMatrix(matrix(train, feats), label=yt, feature_names=list(feats), nthread=1)
    dval = xgb.DMatrix(matrix(validation, feats), label=yv, feature_names=list(feats), nthread=1)
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=cfg.max_rounds,
        evals=[(dval, "validation")],
        early_stopping_rounds=cfg.early_stopping_rounds,
        verbose_eval=False,
    )
    check_device(cfg.device, effective_device(booster))
    model = RiskModel(
        name=name,
        features=feats,
        booster=booster,
        best_iteration=int(booster.best_iteration),
        calibrator=IsotonicCalibrator.fit(np.zeros(1), np.zeros(1)),  # replaced below
        threshold=None,
        trained_at=trained_at,
        sizes=_sizes(yt, yv),
    )
    model.calibrator = IsotonicCalibrator.fit(model.raw(validation), yv)
    model.threshold = threshold_for_precision(yv, model.predict(validation), cfg.precision_target)
    return model


def fit_rules(
    name: str,
    train: pd.DataFrame,
    y_train: npt.ArrayLike,
    validation: pd.DataFrame,
    y_val: npt.ArrayLike,
    trained_at: datetime,
    config: TrainConfig | None = None,
) -> RuleModel:
    """A0: thresholds fitted on `train`, calibration and risk threshold on `validation`."""
    cfg = config or TrainConfig()
    yt, yv = _labels(y_train), _labels(y_val)
    baseline = RuleBaseline.fit(train, yt)
    calibrator = IsotonicCalibrator.fit(baseline.score(validation), yv)
    model = RuleModel(name, baseline, calibrator, None, trained_at, _sizes(yt, yv))
    model.threshold = threshold_for_precision(yv, model.predict(validation), cfg.precision_target)
    return model
