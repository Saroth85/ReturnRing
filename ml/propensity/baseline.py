"""The baseline of the ADR-10 go/no-go: the best single-feature leave-one-out rule (US-201).

Each candidate is one decision-time feature derived from the leave-one-out counts. A candidate
rule is fitted on the validation split, like the models' calibrators and thresholds:

1. an isotonic calibrator maps the feature to a probability (direction found from the data,
   increasing="auto"); a missing feature (no node row, or a rate with no other purchase) gets the
   validation return rate of the missing events;
2. the threshold on that probability maximises F1 on the validation split. As the calibrator is
   monotone, "p >= threshold" is a threshold on the feature itself: the rule of ADR-10.

The baseline is the candidate with the best validation F1; its cross-entropy comes from the same
calibrated probabilities. Every candidate is reported, and the go/no-go also checks the model
against every candidate on the test split (the strictest reading).
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl

from ml.propensity.calibration import IsotonicCalibrator
from ml.propensity.metrics import best_f1_threshold, precision_recall_f1

CANDIDATES: Mapping[str, str] = {
    "customer_loo_return_rate": "customer returns / customer sales, both leave-one-out",
    "variant_loo_return_rate": "variant returns / variant sales, both leave-one-out",
    "customer_returns_loo": "customer returns, leave-one-out",
    "variant_returns_loo": "variant returns, leave-one-out",
    "customer_sales_loo": "customer sales, leave-one-out",
    "variant_sales_loo": "variant sales, leave-one-out",
}


def candidate_values(frame: pl.DataFrame) -> dict[str, npt.NDArray[np.float64]]:
    """Every candidate feature of `frame` (an output of features.join_events); null is NaN."""
    exprs: list[pl.Expr] = []
    for name in CANDIDATES:
        expr: pl.Expr
        if name.endswith("_loo_return_rate"):
            node = name.removesuffix("_loo_return_rate")
            sales = pl.col(f"{node}_sales_loo")
            expr = pl.when(sales > 0).then(pl.col(f"{node}_returns_loo") / sales)
        else:
            expr = pl.col(name)
        exprs.append(expr.cast(pl.Float64).alias(name))
    table = frame.select(exprs)
    return {name: table[name].to_numpy().astype(np.float64) for name in CANDIDATES}


@dataclass(frozen=True)
class SingleFeatureRule:
    feature: str
    calibrator: IsotonicCalibrator
    threshold: float  # on the calibrated probability
    validation_f1: float
    rule: str  # the same rule written on the feature

    def predict_proba(self, values: npt.ArrayLike) -> npt.NDArray[np.float64]:
        return self.calibrator.predict(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "description": CANDIDATES[self.feature],
            "rule": self.rule,
            "threshold_on_probability": self.threshold,
            "validation_f1": self.validation_f1,
            "calibrator": self.calibrator.to_dict(),
        }


def _rule_text(
    feature: str, values: npt.NDArray[np.float64], predicted: npt.NDArray[np.bool_], missing: bool
) -> str:
    known = ~np.isnan(values)
    hit, miss = values[known & predicted], values[known & ~predicted]
    if hit.size == 0:
        text = f"kept whatever {feature}"
    elif miss.size == 0:
        text = f"returned whatever {feature}"
    elif hit.min() > miss.max():
        text = f"returned if {feature} >= {hit.min():.4g}"
    elif hit.max() < miss.min():
        text = f"returned if {feature} <= {hit.max():.4g}"
    else:  # a non-monotone mapping cannot come from isotonic regression; kept for safety
        text = f"returned if the calibrated {feature} >= the threshold"
    return f"{text}; missing -> {'returned' if missing else 'kept'}"


def fit_rule(feature: str, values: npt.ArrayLike, labels: npt.ArrayLike) -> SingleFeatureRule:
    """Fit one candidate rule on the validation split (see the module docstring)."""
    v = np.asarray(values, dtype=np.float64)
    y = np.asarray(labels).astype(np.int8)
    with warnings.catch_warnings():
        # sklearn warns when the direction of a weak feature is uncertain; such a candidate
        # simply loses on F1, so the warning adds nothing here.
        warnings.filterwarnings("ignore", message="Confidence interval of the Spearman")
        calibrator = IsotonicCalibrator.fit(v, y, increasing="auto")
    p = calibrator.predict(v)
    threshold = best_f1_threshold(y, p)
    predicted = p >= threshold
    missing_returned = calibrator.predict(np.array([np.nan]))[0] >= threshold
    return SingleFeatureRule(
        feature=feature,
        calibrator=calibrator,
        threshold=threshold,
        validation_f1=precision_recall_f1(y, p, threshold)[2],
        rule=_rule_text(feature, v, predicted, bool(missing_returned)),
    )


def fit_baseline(
    values: Mapping[str, npt.ArrayLike], labels: npt.ArrayLike
) -> tuple[SingleFeatureRule, list[SingleFeatureRule]]:
    """(the best rule by validation F1, every candidate rule in CANDIDATES order)."""
    rules = [fit_rule(name, values[name], labels) for name in CANDIDATES]
    best = max(rules, key=lambda r: r.validation_f1)  # first of equal F1 wins: CANDIDATES order
    return best, rules
