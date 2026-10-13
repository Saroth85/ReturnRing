"""A0: the returnguard-like rule baseline (spec 8.4, MOD-04), implemented here, not imported.

Three rules on a customer's behaviour, the way rule-based return tools flag customers:

- return rate: return_rate_to_date >= r (withdrawal returns over delivered lines);
- return window: median_days_to_return <= d (items sent back soon after delivery);
- value: mean_line_price_eur >= v (dear items). This is the price of what the customer buys,
  never the customer value V_c, which does not feed risk (rule 6).

A rule whose feature is missing does not fire. The score is the share of rules that fire (0, 1/3,
2/3 or 1); the probability is that score calibrated on the validation split (isotonic), like every
other model. Each threshold is fitted on the training rows, not chosen by hand: the quantile of the
feature (5% to 95% in steps of 5%) whose single rule has the best F1 on the training rows, the
lowest such quantile on a tie.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from ml.risk.features import A0_FEATURES

F64 = npt.NDArray[np.float64]
Direction = Literal[">=", "<="]
DIRECTIONS: Final[dict[str, Direction]] = {
    "return_rate_to_date": ">=",
    "median_days_to_return": "<=",
    "mean_line_price_eur": ">=",
}
QUANTILES: Final = tuple(round(q, 2) for q in np.arange(0.05, 0.951, 0.05))
FORMAT: Final = "returnring.risk.rules.v1"
assert tuple(DIRECTIONS) == A0_FEATURES


@dataclass(frozen=True)
class Rule:
    feature: str
    direction: Direction
    threshold: float

    def fires(self, values: npt.ArrayLike) -> npt.NDArray[np.bool_]:
        x = np.asarray(values, dtype=np.float64)
        known = ~np.isnan(x)
        hit = x >= self.threshold if self.direction == ">=" else x <= self.threshold
        return np.asarray(known & hit, dtype=np.bool_)

    def describe(self) -> str:
        return f"{self.feature} {self.direction} {self.threshold:.4g}"


def _f1(fired: npt.NDArray[np.bool_], y: npt.NDArray[np.int8]) -> float:
    tp = int((fired & (y == 1)).sum())
    fp = int((fired & (y == 0)).sum())
    fn = int((~fired & (y == 1)).sum())
    return 2 * tp / (2 * tp + fp + fn) if tp else 0.0


def fit_rule(feature: str, values: npt.ArrayLike, y: npt.ArrayLike) -> Rule:
    """The threshold of one rule: the training quantile with the best single-rule F1."""
    x = np.asarray(values, dtype=np.float64)
    labels = np.asarray(y).astype(np.int8)
    known = x[~np.isnan(x)]
    direction = DIRECTIONS[feature]
    if known.size == 0:
        raise ValueError(f"{feature}: no known value to fit a rule on")
    best: Rule | None = None
    best_f1 = -1.0
    for q in QUANTILES:
        rule = Rule(feature, direction, float(np.quantile(known, q)))
        f1 = _f1(rule.fires(x), labels)
        if f1 > best_f1:
            best, best_f1 = rule, f1
    assert best is not None
    return best


@dataclass(frozen=True)
class RuleBaseline:
    rules: tuple[Rule, ...]

    @classmethod
    def fit(cls, frame: pd.DataFrame, y: npt.ArrayLike) -> RuleBaseline:
        return cls(tuple(fit_rule(f, frame[f].to_numpy(dtype=np.float64), y) for f in A0_FEATURES))

    @property
    def features(self) -> tuple[str, ...]:
        return tuple(r.feature for r in self.rules)

    def fired(self, frame: pd.DataFrame) -> npt.NDArray[np.int64]:
        """How many rules fire for each row."""
        counts = np.zeros(len(frame), dtype=np.int64)
        for rule in self.rules:
            counts += rule.fires(frame[rule.feature].to_numpy(dtype=np.float64))
        return counts

    def score(self, frame: pd.DataFrame) -> F64:
        """The share of rules that fire: the uncalibrated rule score."""
        return np.asarray(self.fired(frame) / len(self.rules), dtype=np.float64)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "rules": [
                {"feature": r.feature, "direction": r.direction, "threshold": r.threshold}
                for r in self.rules
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleBaseline:
        if data.get("format") != FORMAT:
            raise ValueError(f"not a {FORMAT} document: {data.get('format')!r}")
        rules: Sequence[dict[str, Any]] = data["rules"]
        return cls(
            tuple(
                Rule(str(r["feature"]), DIRECTIONS[str(r["feature"])], float(r["threshold"]))
                for r in rules
            )
        )
