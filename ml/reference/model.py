"""Training and prediction of the line predictors (spec 8.1, 8.2, 8.4; rule 8; MOD-06, MOD-09).

Point in time. Every line is priced with the features known when it was ordered: history counts
of the lines settled before its order instant, leaving its customer out. Its own outcome settles a
month later, so it can never be inside its own features. (A leave-one-out encoding computed at the
training instant instead leaks the label: within a variant, a returned line gets a lower rate
than a kept one, and the trees learn to decode it. Measured on the generated store on 13 Oct 2026:
in-sample AUC 0.92, out-of-time AUC 0.44.) The checkout and the request features use the same
semantics: what was known at that instant.

fit trains one predictor on the observed lines at an instant:

- XGBoost on CPU (xgboost-cpu, histogram method) with the published starting point of spec 8.1:
  depth 4, learning rate 0.1, gamma 1, lambda 2, early stopping after 5 rounds on a validation
  split of 10% of the customers (grouped, so one customer is never on both sides);
- isotonic calibration on the same validation split (spec 8.1);
- probabilities kept in [P_FLOOR, 1 - P_FLOOR], so the variance p(1 - p) of a line is never 0;
- with too few lines or a single class, the smoothed fallback: the variant rate shrunk to the
  category rate, p = (returns + k * category rate) / (lines + k).

crossfit predicts each customer's lines with a model trained on the other customers only (folds
by customer). The residual uses it at its own instant: the model that prices a customer's lines
never saw that customer's outcomes, not even through its training (rule 8).

observed_line_probabilities is the entry point of the residual: p_l for every observed line of a
store at an instant, from a model trained at that instant on the same store (cross-fitted) or on
another store (for example main for the out-of-distribution store, which is never trained on).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

from ml.reference.features import (
    PERSONALIZED_FEATURES,
    REFERENCE_FEATURES,
    Encoding,
    check_feature_names,
    design,
    history_frame,
    line_queries,
)
from pipeline.features.lines import observations

F64 = npt.NDArray[np.float64]
Role = Literal["reference", "personalized"]
Kind = Literal["xgboost", "smoothed"]

MODEL_VERSION: Final = "v1"
P_FLOOR: Final = 0.01  # no line is priced as certain to be kept or returned (hypothesis)
DEFAULT_K: Final = 10.0  # pseudo-count when no line can fit k
XGB_PARAMS: Final[dict[str, Any]] = {
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "max_depth": 4,
    "eta": 0.1,
    "gamma": 1.0,
    "lambda": 2.0,
    "tree_method": "hist",
}
MAX_ROUNDS: Final = 400
EARLY_STOPPING_ROUNDS: Final = 5


@dataclass(frozen=True)
class TrainConfig:
    validation_share: float = 0.1
    folds: int = 5
    seed: int = 42
    min_train_lines: int = 1000
    min_validation_lines: int = 200
    k_grid: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0)
    # One thread: the hist method on these sizes takes seconds, and OpenMP threads on a shared CPU
    # stall at every barrier (measured 13 Oct 2026: 50 rounds on 7,046 lines took 0.04 s with 1
    # thread and 25 s with 4, while other jobs held the cores).
    nthread: int = 1


def role_features(role: Role) -> tuple[str, ...]:
    names = REFERENCE_FEATURES if role == "reference" else PERSONALIZED_FEATURES
    check_feature_names(names)  # FAIR-01, rule 6, review F6
    return names


def log_loss(y: npt.ArrayLike, p: npt.ArrayLike) -> float:
    yy = np.asarray(y, dtype=np.float64)
    pp = np.clip(np.asarray(p, dtype=np.float64), 1e-12, 1 - 1e-12)
    return float(-np.mean(yy * np.log(pp) + (1 - yy) * np.log(1 - pp)))


def choose_k(frame: pd.DataFrame, y: npt.ArrayLike, grid: Sequence[float]) -> float:
    """The pseudo-count of the grid whose shrunk variant rate has the lowest log loss."""
    yy = np.asarray(y, dtype=np.float64)
    if len(yy) == 0 or yy.min() == yy.max():
        return DEFAULT_K
    losses = []
    for k in grid:
        rate = design(frame, Encoding(float(k), (), ()), ["variant_rate"])["variant_rate"]
        losses.append(log_loss(yy, rate.to_numpy()))
    return float(grid[int(np.argmin(losses))])


def _shuffled_groups(
    groups: npt.ArrayLike, seed: int
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """Per row, the index of its group among the sorted distinct groups; and a permutation of
    those indices drawn with `seed`. Depends on the set of groups, not on the row order."""
    _, codes = np.unique(np.asarray(groups), return_inverse=True)
    n = int(codes.max()) + 1 if len(codes) else 0
    perm = np.random.default_rng(seed).permutation(n)
    return codes.astype(np.int64), perm.astype(np.int64)


def validation_mask(groups: npt.ArrayLike, share: float, seed: int) -> npt.NDArray[np.bool_]:
    """Rows of a random `share` of the groups (customers), drawn with `seed`."""
    codes, perm = _shuffled_groups(groups, seed)
    n_val = int(np.ceil(share * len(perm))) if len(perm) > 1 else 0
    chosen = np.zeros(len(perm), dtype=np.bool_)
    chosen[perm[:n_val]] = True
    return chosen[codes]


def fold_ids(groups: npt.ArrayLike, folds: int, seed: int) -> npt.NDArray[np.int64]:
    """The fold of each row: groups (customers) dealt round-robin in a random order."""
    codes, perm = _shuffled_groups(groups, seed)
    fold_of = np.empty(len(perm), dtype=np.int64)
    fold_of[perm] = np.arange(len(perm), dtype=np.int64) % folds
    return fold_of[codes]


@dataclass
class LineModel:
    """A fitted predictor: role, kind, encoding, and the booster and isotonic map if any."""

    role: Role
    kind: Kind
    encoding: Encoding
    trained_at: datetime
    n_train: int
    booster: xgb.Booster | None = None
    best_iteration: int | None = None
    isotonic: IsotonicRegression | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def features(self) -> tuple[str, ...]:
        return role_features(self.role)

    @property
    def version(self) -> str:
        return f"{self.role}-{self.kind}-{MODEL_VERSION}@{self.trained_at:%Y-%m-%dT%H:%MZ}"

    def raw(self, frame: pd.DataFrame) -> F64:
        """Probabilities before isotonic calibration and the floor."""
        if self.booster is None:
            rate = design(frame, self.encoding, ["variant_rate"])["variant_rate"]
            return np.asarray(rate.to_numpy(), dtype=np.float64)
        x = design(frame, self.encoding, self.features)
        matrix = xgb.DMatrix(
            x.to_numpy(), feature_names=list(self.features), missing=np.nan, nthread=1
        )
        assert self.best_iteration is not None
        out = self.booster.predict(matrix, iteration_range=(0, self.best_iteration + 1))
        return np.asarray(out, dtype=np.float64)

    def predict(self, frame: pd.DataFrame) -> F64:
        p = self.raw(frame)
        if self.isotonic is not None:
            p = np.asarray(self.isotonic.predict(p), dtype=np.float64)
        return np.clip(p, P_FLOOR, 1 - P_FLOOR)


def _smoothed(
    frame: pd.DataFrame,
    y: npt.NDArray[np.float64],
    role: Role,
    at: datetime,
    note: str,
    cfg: TrainConfig,
) -> LineModel:
    k = choose_k(frame, y, cfg.k_grid)
    return LineModel(role, "smoothed", Encoding.from_lines(frame, k), at, len(y), notes=[note])


def fit(
    frame: pd.DataFrame,
    y: npt.ArrayLike,
    groups: npt.ArrayLike,
    role: Role,
    trained_at: datetime,
    config: TrainConfig | None = None,
) -> LineModel:
    """Train a predictor on history_frame rows `frame` with outcomes `y` (1: returned on the
    withdrawal path) grouped by customer in `groups`."""
    cfg = config or TrainConfig()
    yy = np.asarray(y, dtype=np.float64)
    gg = np.asarray(groups)
    if len(yy) < cfg.min_train_lines:
        return _smoothed(frame, yy, role, trained_at, f"{len(yy)} lines: smoothed fallback", cfg)
    if yy.min() == yy.max():
        return _smoothed(frame, yy, role, trained_at, "a single outcome: smoothed fallback", cfg)
    val = validation_mask(gg, cfg.validation_share, cfg.seed)
    train = ~val
    if not val.any() or yy[train].min() == yy[train].max():
        note = "no validation customer or a single outcome in training: smoothed fallback"
        return _smoothed(frame, yy, role, trained_at, note, cfg)
    k = choose_k(frame.loc[train], yy[train], cfg.k_grid)
    enc = Encoding.from_lines(frame.loc[train], k)
    features = role_features(role)
    x = design(frame, enc, features).to_numpy()
    names = list(features)
    dtrain = xgb.DMatrix(
        x[train], label=yy[train], feature_names=names, missing=np.nan, nthread=cfg.nthread
    )
    dval = xgb.DMatrix(
        x[val], label=yy[val], feature_names=names, missing=np.nan, nthread=cfg.nthread
    )
    params = {**XGB_PARAMS, "seed": cfg.seed, "nthread": cfg.nthread}
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=MAX_ROUNDS,
        evals=[(dval, "validation")],
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
        verbose_eval=False,
    )
    best = int(booster.best_iteration)
    model = LineModel(role, "xgboost", enc, trained_at, int(train.sum()), booster, best)
    y_val = yy[val]
    if len(y_val) >= cfg.min_validation_lines and y_val.min() != y_val.max():
        raw_val = booster.predict(dval, iteration_range=(0, best + 1))
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(raw_val, y_val)
        model.isotonic = iso
    else:
        model.notes.append(f"{len(y_val)} validation lines: no isotonic calibration")
    return model


def crossfit(
    frame: pd.DataFrame,
    y: npt.ArrayLike,
    groups: npt.ArrayLike,
    role: Role,
    trained_at: datetime,
    config: TrainConfig | None = None,
) -> tuple[F64, list[LineModel]]:
    """Out-of-fold probabilities: each customer's rows priced by a model trained on the other
    folds of customers only."""
    cfg = config or TrainConfig()
    yy = np.asarray(y, dtype=np.float64)
    gg = np.asarray(groups)
    p = np.zeros(len(yy), dtype=np.float64)
    models: list[LineModel] = []
    if len(yy) == 0:
        return p, models
    folds = fold_ids(gg, cfg.folds, cfg.seed)
    for f in range(cfg.folds):
        hold = folds == f
        if not hold.any():
            continue
        rest = frame.loc[~hold].reset_index(drop=True)
        model = fit(rest, yy[~hold], gg[~hold], role, trained_at, cfg)
        p[hold] = model.predict(frame.loc[hold])
        models.append(model)
    return p, models


@dataclass(frozen=True)
class Priced:
    """p_l of the observed lines of a store at an instant, and how they were obtained."""

    lines: pd.DataFrame  # the observed lines, with columns p and the history_frame columns
    version: str
    kinds: tuple[str, ...]


def observed_frame(lines: pd.DataFrame) -> pd.DataFrame:
    """history_frame rows of the observed lines of `lines` (a line_frame), each at its own order
    instant (point in time), with its settled outcome in column `returned`."""
    events = observations(lines)
    frame = history_frame(events, line_queries(events, events["ordered_at"]))
    frame["returned"] = events["returned"].to_numpy()
    frame["ordered_at"] = events["ordered_at"].array  # keeps the UTC datetime dtype
    return frame


def observed_line_probabilities(
    lines: pd.DataFrame,
    at: datetime,
    role: Role = "reference",
    config: TrainConfig | None = None,
    source: pd.DataFrame | None = None,
) -> Priced:
    """p_l for every observed line of `lines` (a line_frame at `at`), each priced with the
    features of its order instant. The model is trained at `at`: cross-fitted on the same store,
    or, with `source` (the line_frame of another store at `at`), trained on that store only and
    applied here."""
    cfg = config or TrainConfig()
    frame = observed_frame(lines)
    y = frame["returned"].to_numpy(dtype=np.float64)
    if source is None:
        p, models = crossfit(frame, y, frame["customer_id"].to_numpy(), role, at, cfg)
        kinds = tuple(sorted({m.kind for m in models}))
        what = "+".join(kinds) or "no-settled-line"  # nothing to price yet (first month)
        version = f"{role}-{what}-{MODEL_VERSION}-crossfit{cfg.folds}@{at:%Y-%m-%dT%H:%MZ}"
    else:
        train = observed_frame(source)
        ty = train["returned"].to_numpy(dtype=np.float64)
        model = fit(train, ty, train["customer_id"].to_numpy(), role, at, cfg)
        p = model.predict(frame) if len(frame) else np.zeros(0)
        kinds = (model.kind,)
        version = f"{model.version} (trained on another store)"
    frame["p"] = p
    return Priced(frame, version, kinds)
