"""Product anomalies per variant, return reason and sales week (US-208, spec 8.9, MOD-10).

The unit is a weekly sales cohort: the order lines of one variant sold in one Monday-to-Sunday
week (UTC). At the decision time T, for each return reason m:

- n: lines of the variant sold in the week (order lines are known when they are placed);
- O (observed): lines of the cohort with a return for m known at T (return available_at <= T);
- E (expected): what the category baseline predicts for the same lines at the same age. A line l
  sold at s_l is a_l = T - s_l old at T. Among the S lines of the same category sold in the weeks
  before the cohort's week, other variants only, h_l had a return for m known within a_l of their
  sale. Then

      p_l = (h_l + 1) / (S + 2)        E = sum of p_l over the cohort

  p_l is Laplace's rule of succession for "returned for m within a_l of the sale": a reason the
  category has not seen yet has a small rate, not a rate of zero (with h_l = 0 the plain share
  h_l / S would make z undefined however many lines come back). E is null when S = 0.
  Comparing at the same age means a cohort whose returns have not all come in is not compared with
  mature weeks. The baseline uses no week after the cohort's (no future data) and no line of the
  variant itself. Every return it counts was known before T: it belongs to a line sold before s_l
  and was known within a_l of that sale;
- z = (O - E) / sqrt(E), spec 8.9.

The flag (AnomalyThresholds, decision D-15) keeps the spec's thresholds, n >= 20 and z > 3, and
the false alarm rate that z > 3 promises (the one-sided normal tail, about 0.00135):

- exact_tail: the Poisson upper tail P(X >= O), X ~ Poisson(E), must be at most that rate. The
  normal approximation behind z needs E of about 5 or more: with E = 0.08 a single return gives
  z = 3.3, which Poisson(0.08) produces once in 13 tests;
- family_wise: the rate is shared by the len(REASONS) reasons tested on one variant-week, one
  catalogue decision (Bonferroni), so each reason is tested at the rate divided by their number.

docs/results/us-208-product-anomalies.md measures the literal rule and each step.

No customer appears in any output of this module: an anomaly is a catalogue matter, kept apart from
the customer hypotheses (spec 8.9; US-606, E2E-04).
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.stats import poisson

from pipeline.features.snapshot import StoreSnapshot, utc
from policy.types import ReturnReason

REASONS: Final = tuple(r.value for r in ReturnReason)
I64 = npt.NDArray[np.int64]


@dataclass(frozen=True)
class AnomalyThresholds:
    """Design thresholds of spec 8.9 and D-15, versioned: a change of value is a change of
    version."""

    version: str
    min_sales: int  # n >= min_sales in the week
    z: float  # z > this
    exact_tail: bool  # also P(X >= O | Poisson(E)) <= level
    family_wise: bool  # level shared by the reasons of a variant-week

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("thresholds need a version")
        if self.min_sales < 1:
            raise ValueError(f"min_sales must be at least 1, got {self.min_sales}")
        if not (math.isfinite(self.z) and self.z > 0):
            raise ValueError(f"z must be a positive number, got {self.z!r}")
        if self.family_wise and not self.exact_tail:
            raise ValueError("family_wise shares the level of the exact tail: set exact_tail")

    @property
    def level(self) -> float:
        """The tail probability a test must reach: the one-sided normal tail of z, divided by the
        number of reasons when the reasons of a variant-week share it."""
        tail = 0.5 * math.erfc(self.z / math.sqrt(2.0))
        return tail / len(REASONS) if self.family_wise else tail


ANOMALY_THRESHOLDS: Final = AnomalyThresholds(
    version="anomalies-v1-2026-10-13", min_sales=20, z=3.0, exact_tail=True, family_wise=True
)
# Measured for comparison in the results; the product uses ANOMALY_THRESHOLDS.
SPEC_LITERAL: Final = AnomalyThresholds(
    version="spec-8.9-literal", min_sales=20, z=3.0, exact_tail=False, family_wise=False
)
PER_TEST_EXACT: Final = AnomalyThresholds(
    version="exact-tail-per-test", min_sales=20, z=3.0, exact_tail=True, family_wise=False
)

CELL_COLUMNS: Final = (
    "variant_id",
    "category",
    "reason",
    "week",
    "n",
    "observed",
    "expected",
    "z",
    "tail_p",
    "prior_sales",
    "baseline_hits",
)


def week_start(instants: pd.Series) -> pd.Series:
    """Monday 00:00 UTC of the week of each instant."""
    utc_instants = instants.dt.tz_convert("UTC")
    days = utc_instants.dt.normalize()
    return days - pd.to_timedelta(utc_instants.dt.weekday, unit="D")


def _micros(values: pd.Series) -> I64:
    return np.asarray(values.dt.tz_convert("UTC").dt.as_unit("us").astype("int64"), np.int64)


def sales_frame(snap: StoreSnapshot) -> pd.DataFrame:
    """One row per order line known at snap.as_of: variant, category, sale instant and week, the
    instant the line was known, and the reason of its return when the return is known at
    snap.as_of (else null)."""
    lines = snap["store/order_lines"][["line_id", "variant_id", "event_at", "available_at"]]
    lines = lines.rename(columns={"event_at": "sold_at", "available_at": "line_known_at"})
    variants = snap["store/variants"][["variant_id", "category"]]
    out = lines.merge(variants, on="variant_id", how="left", validate="many_to_one")
    missing = int(out["category"].isna().sum())
    if missing:
        raise ValueError(f"{missing} order lines whose variant is not in the catalogue at T")
    returns = snap["store/returns"][["line_id", "reason", "available_at"]]
    returns = returns.rename(columns={"available_at": "return_known_at"})
    out = out.merge(returns, on="line_id", how="left", validate="one_to_one")
    limit = pd.Timestamp(snap.as_of)
    out = out.loc[out["line_known_at"] <= limit].copy()
    late = (out["return_known_at"] > limit).fillna(False).astype(bool)
    out.loc[late, ["reason", "return_known_at"]] = None
    out["week"] = week_start(out["sold_at"])
    return out.sort_values("line_id", kind="stable").reset_index(drop=True)


def _prior_hits(
    group_r: I64,
    week_r: I64,
    reason_r: I64,
    lag_r: I64,
    group_q: I64,
    week_q: I64,
    age_q: I64,
) -> I64:
    """hits[i, m] = number of returned lines j with group_r[j] == group_q[i], week_r[j] <
    week_q[i], reason_r[j] == m and lag_r[j] <= age_q[i]."""
    out = np.zeros((len(group_q), len(REASONS)), dtype=np.int64)
    if not len(group_q) or not len(group_r):
        return out
    order = np.lexsort((week_r, group_r))
    group_r, week_r, reason_r, lag_r = group_r[order], week_r[order], reason_r[order], lag_r[order]
    queries = pd.DataFrame({"g": group_q, "w": week_q})
    for (g, w), idx in queries.groupby(["g", "w"], sort=False).indices.items():
        lo = np.searchsorted(group_r, g, side="left")
        hi = np.searchsorted(group_r, g, side="right")
        cut = lo + np.searchsorted(week_r[lo:hi], w, side="left")  # the weeks before w
        prior_reason, prior_lag = reason_r[lo:cut], lag_r[lo:cut]
        ages = age_q[idx]
        for m in range(len(REASONS)):
            lags = np.sort(prior_lag[prior_reason == m])
            if len(lags):
                out[idx, m] = np.searchsorted(lags, ages, side="right")
    return out


def _prior_sales(group: I64, week: I64, group_q: I64, week_q: I64) -> I64:
    """Lines of the same group sold in the weeks before each query's week."""
    span = int(max(week.max(initial=0), week_q.max(initial=0))) + 1
    keys = np.sort(group * span + week)  # sorted by group, then week
    first_of_group = np.searchsorted(keys, group_q * span, side="left")
    before_week = np.searchsorted(keys, group_q * span + week_q, side="left")
    return np.asarray(before_week - first_of_group, dtype=np.int64)


def expected_returns(
    hits: pd.Series, n: pd.Series, prior_sales: pd.Series, *, succession: bool = True
) -> pd.Series:
    """E of a cohort from its summed baseline hits (sum of h_l), its n and the baseline's S:
    (hits + n) / (S + 2) with the rule of succession, hits / S without; null when S = 0."""
    s = prior_sales.to_numpy(dtype=np.float64)
    h = hits.to_numpy(dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        e = (h + n.to_numpy(dtype=np.float64)) / (s + 2.0) if succession else h / s
    return pd.Series(np.where(s > 0, e, np.nan), index=hits.index)


def cohort_cells(
    sales: pd.DataFrame,
    as_of: datetime,
    *,
    min_sales: int,
    variants: Collection[str] | None = None,
) -> pd.DataFrame:
    """O, E and z for every (variant, reason, week) whose week sold at least min_sales lines (and
    whose variant is in `variants`, when given), from the sales known at as_of (sales_frame).

    expected is null when the baseline has no earlier sale; z and tail_p (P(X >= observed) for
    X ~ Poisson(expected)) are null with it. baseline_hits is the sum of h_l over the cohort."""
    t = pd.Timestamp(utc(as_of))
    sales = sales.loc[sales["line_known_at"] <= t]
    known = (sales["return_known_at"] <= t).fillna(False).astype(bool)
    reason = sales["reason"].where(known)
    unknown = sorted(set(reason.dropna()) - set(REASONS))
    if unknown:
        raise ValueError(f"unknown return reasons {unknown}")

    cat_code, _ = pd.factorize(sales["category"], sort=True)
    var_code, variant_ids = pd.factorize(sales["variant_id"], sort=True)
    week_code, weeks = pd.factorize(sales["week"], sort=True)
    sold = _micros(sales["sold_at"])
    age = _micros(pd.Series([t]))[0] - sold
    reason_code = np.asarray(
        reason.map({r: i for i, r in enumerate(REASONS)}).fillna(-1), dtype=np.int64
    )

    cells = pd.DataFrame({"v": var_code, "w": week_code}).value_counts().rename("n").reset_index()
    cells = cells.loc[cells["n"] >= min_sales]
    if variants is not None:
        cells = cells.loc[np.isin(np.asarray(variant_ids)[cells["v"].to_numpy()], list(variants))]
    if not len(cells):
        return _empty_cells()

    in_cell = pd.MultiIndex.from_arrays([var_code, week_code]).isin(
        pd.MultiIndex.from_frame(cells[["v", "w"]])
    )
    q = np.flatnonzero(in_cell)
    r = np.flatnonzero(reason_code >= 0)
    lag = _micros(sales["return_known_at"].iloc[r]) - sold[r]
    # The category's earlier weeks minus the variant's own earlier weeks: other variants only.
    week_r, reason_r, week_q, age_q = week_code[r], reason_code[r], week_code[q], age[q]
    category_hits = _prior_hits(cat_code[r], week_r, reason_r, lag, cat_code[q], week_q, age_q)
    own_hits = _prior_hits(var_code[r], week_r, reason_r, lag, var_code[q], week_q, age_q)
    hits = category_hits - own_hits
    prior = _prior_sales(cat_code, week_code, cat_code[q], week_q) - _prior_sales(
        var_code, week_code, var_code[q], week_q
    )

    per_line = pd.DataFrame(hits, columns=[f"h_{m}" for m in REASONS])
    for i, m in enumerate(REASONS):
        per_line[f"o_{m}"] = (reason_code[q] == i).astype(np.int64)
    per_line["v"], per_line["w"], per_line["prior"] = var_code[q], week_code[q], prior
    grouped = per_line.groupby(["v", "w"], sort=True)
    sums = grouped.sum()
    n = grouped.size()
    frames = [
        pd.DataFrame(
            {
                "v": n.index.get_level_values("v"),
                "w": n.index.get_level_values("w"),
                "reason": m,
                "n": n.to_numpy(dtype=np.int64),
                "observed": sums[f"o_{m}"].to_numpy(dtype=np.int64),
                "prior_sales": grouped["prior"].first().to_numpy(dtype=np.int64),
                "baseline_hits": sums[f"h_{m}"].to_numpy(dtype=np.int64),
            }
        )
        for m in REASONS
    ]
    out = pd.concat(frames, ignore_index=True)
    out["variant_id"] = np.asarray(variant_ids)[out["v"].to_numpy()]
    out["week"] = np.asarray(weeks)[out["w"].to_numpy()]
    category_of = sales.drop_duplicates("variant_id").set_index("variant_id")["category"]
    out["category"] = out["variant_id"].map(category_of)
    out["expected"] = expected_returns(out["baseline_hits"], out["n"], out["prior_sales"])
    out["z"], out["tail_p"] = z_scores(out["observed"], out["expected"])
    out = out.sort_values(["variant_id", "week", "reason"], kind="stable").reset_index(drop=True)
    return out[list(CELL_COLUMNS)]


def _empty_cells() -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype=object) for c in CELL_COLUMNS})
    for c in ("n", "observed", "prior_sales", "baseline_hits"):
        frame[c] = frame[c].astype(np.int64)
    for c in ("expected", "z", "tail_p"):
        frame[c] = frame[c].astype(np.float64)
    frame["week"] = pd.Series(dtype="datetime64[us, UTC]")
    return frame


def z_scores(observed: pd.Series, expected: pd.Series) -> tuple[pd.Series, pd.Series]:
    """z = (O - E) / sqrt(E) of spec 8.9 and the Poisson upper tail P(X >= O), X ~ Poisson(E);
    both null when E is null or not positive."""
    o = observed.to_numpy(dtype=np.float64)
    e = expected.to_numpy(dtype=np.float64)
    valid = np.isfinite(e) & (e > 0)
    z = np.full(len(o), np.nan)
    tail = np.full(len(o), np.nan)
    z[valid] = (o[valid] - e[valid]) / np.sqrt(e[valid])
    tail[valid] = poisson.sf(o[valid] - 1, e[valid])
    return pd.Series(z, index=observed.index), pd.Series(tail, index=observed.index)


def flagged(cells: pd.DataFrame, thresholds: AnomalyThresholds = ANOMALY_THRESHOLDS) -> pd.Series:
    """True for the cells that are anomalies under `thresholds` (spec 8.9, D-15)."""
    z = cells["z"].to_numpy(dtype=np.float64)
    ok = np.isfinite(z) & (cells["n"].to_numpy() >= thresholds.min_sales) & (z > thresholds.z)
    if thresholds.exact_tail:
        tail = cells["tail_p"].to_numpy(dtype=np.float64)
        ok &= np.isfinite(tail) & (tail <= thresholds.level)
    return pd.Series(ok, index=cells.index)
