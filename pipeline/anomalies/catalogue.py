"""The catalogue task of a product anomaly and the follow-up of its variant (US-606, spec 8.9).

catalogue_task(rows, ...) turns the flagged weeks of one variant and reason at one decision time
(rows of features/product_anomalies) into the payload of a catalogue task: the weeks with n,
observed, expected and z, and the three checks of spec 8.9 (description, size guide, lot check),
ordered by what the reason points to. The payload names no customer: the anomaly is a product
matter and changes no customer's risk, terms or messages (spec 8.9; E2E-04).

before_after(sales, ...) follows the variant after a change to the listing or the stock. A decrease
of returns alone does not prove the effect of the change (spec 8.9): sales move, and recent weeks
have had less time to be returned. So the variant's return rate for the reason, per sales week, is
compared with the rest of its category over the same weeks, read at the same decision time (so
equally mature): a difference in differences with an approximate 95% interval. The week of the
change is left out of both periods. The variants with their own anomaly for the reason (`exclude`,
from features/product_anomalies) are left out of the comparison: a defective variant fixed in the
same weeks would otherwise make every other variant of its category look worse.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from contracts.io import Provenance
from pipeline.anomalies.detect import ANOMALY_THRESHOLDS, REASONS, cohort_cells, week_start
from pipeline.features.snapshot import utc
from policy.types import ReturnReason

CHECKS: Final[Mapping[str, tuple[str, str]]] = {
    "description": (
        "Description and photos",
        "Compare the product page (text, photos, material, colour) with items returned for this "
        "reason, and correct the page where it differs from the goods.",
    ),
    "size_guide": (
        "Size guide",
        "Measure returned items against the size chart of the product page, size by size, and "
        "correct the chart or add a fit note where they differ.",
    ),
    "lot_check": (
        "Lot check",
        "Inspect the stock and the supplier lot shipped in the flagged weeks (labels, finishing, "
        "packaging) and hold the lot if the fault is confirmed.",
    ),
}
# Which check comes first for each reason: what the reason points to.
CHECK_ORDER: Final[Mapping[str, tuple[str, str, str]]] = {
    ReturnReason.SIZE.value: ("size_guide", "description", "lot_check"),
    ReturnReason.CHANGED_MIND.value: ("description", "size_guide", "lot_check"),
    ReturnReason.NOT_AS_EXPECTED.value: ("description", "size_guide", "lot_check"),
    ReturnReason.DEFECTIVE.value: ("lot_check", "description", "size_guide"),
    ReturnReason.DAMAGED_IN_SHIPPING.value: ("lot_check", "description", "size_guide"),
    ReturnReason.WRONG_ITEM.value: ("lot_check", "description", "size_guide"),
    ReturnReason.NOT_AS_DESCRIBED.value: ("description", "size_guide", "lot_check"),
    ReturnReason.OTHER.value: ("description", "size_guide", "lot_check"),
}
PRODUCT_NOTE: Final = (
    "Product check: this task concerns the variant, not the customers who returned it. It names "
    "no customer and changes no customer's risk, terms or messages."
)
FOLLOW_UP_NOTE: Final = (
    "After a change, follow the weekly returns of the variant against the rest of its category "
    "over the same weeks: a decrease alone does not prove the effect of the change."
)
FOLLOW_WEEKS: Final = 8  # weeks before and after a change in the follow-up (design default)
Z_95: Final = 1.959963984540054


def task_id(variant_id: str, reason: str, first_week: date) -> str:
    return f"CAT-{variant_id}-{reason}-{first_week:%Y%m%d}"


def catalogue_task(
    rows: pd.DataFrame,
    *,
    product_id: str,
    category: str,
    provenance: Provenance,
    thresholds_version: str = ANOMALY_THRESHOLDS.version,
) -> dict[str, Any]:
    """The payload of the catalogue task for the flagged weeks in `rows`: rows of
    features/product_anomalies of one variant, one reason and one as_of (JSON-ready)."""
    if rows.empty:
        raise ValueError("a catalogue task needs at least one flagged week")
    for column in ("variant_id", "reason", "as_of"):
        if rows[column].nunique() != 1:
            raise ValueError(f"the rows of one task share one {column}")
    reason = str(rows["reason"].iloc[0])
    if reason not in CHECK_ORDER:
        raise ValueError(f"unknown return reason {reason!r}")
    variant_id = str(rows["variant_id"].iloc[0])
    as_of = utc(pd.Timestamp(rows["as_of"].iloc[0]).to_pydatetime())
    rows = rows.sort_values("week")
    weeks = [
        {
            "week": pd.Timestamp(r.week).date().isoformat(),
            "n": int(r.n),
            "observed": int(r.observed),
            "expected": round(float(r.expected), 3),
            "z": round(float(r.z), 2),
        }
        for r in rows.itertuples(index=False)
    ]
    first_week = pd.Timestamp(rows["week"].iloc[0]).date()
    return {
        "task_id": task_id(variant_id, reason, first_week),
        "kind": "catalogue_check",
        "variant_id": variant_id,
        "product_id": product_id,
        "category": category,
        "reason": reason,
        "as_of": as_of.isoformat(),
        "weeks": weeks,
        "totals": {
            "n": int(rows["n"].sum()),
            "observed": int(rows["observed"].sum()),
            "expected": round(float(rows["expected"].sum()), 3),
        },
        "checks": [
            {"id": check, "title": CHECKS[check][0], "instruction": CHECKS[check][1]}
            for check in CHECK_ORDER[reason]
        ],
        "note": PRODUCT_NOTE,
        "follow_up": FOLLOW_UP_NOTE,
        "thresholds_version": thresholds_version,
        "provenance": {
            "origin": provenance.origin,
            "period_start": provenance.period_start.isoformat(),
            "period_end": provenance.period_end.isoformat(),
            "nature": provenance.nature,
        },
    }


# ---- Follow-up after a change (US-606) ---------------------------------------------------------


def weekly_series(
    sales: pd.DataFrame,
    variant_id: str,
    reason: str,
    as_of: datetime,
    *,
    first_week: datetime | None = None,
    last_week: datetime | None = None,
    exclude: Collection[str] = (),
) -> pd.DataFrame:
    """One row per calendar week (Monday 00:00 UTC) from first_week to last_week (default: the
    variant's first sales week to the week of as_of), from the sales known at as_of
    (detect.sales_frame): n, observed, expected and z of the variant for the reason (spec 8.9,
    whatever n; expected and z are null in a week without sales) and the rest of its category in
    the same week, without the variants in `exclude`: comparison_n lines sold,
    comparison_observed returned for the reason."""
    if reason not in REASONS:
        raise ValueError(f"unknown return reason {reason!r}")
    t = pd.Timestamp(utc(as_of))
    known = sales.loc[sales["line_known_at"] <= t]
    mine = known.loc[known["variant_id"] == variant_id]
    if mine.empty:
        raise ValueError(f"no sale of {variant_id} known at {t.isoformat()}")
    start = pd.Timestamp(utc(first_week)) if first_week is not None else mine["week"].min()
    end = pd.Timestamp(utc(last_week)) if last_week is not None else week_start(pd.Series([t]))[0]
    calendar = pd.DataFrame({"week": pd.date_range(start, end, freq="7D")})
    if calendar.empty or (calendar["week"].dt.weekday != 0).any():
        raise ValueError("the series runs over Mondays 00:00 UTC, first_week <= last_week")
    # The baseline of spec 8.9 reads the variant's own category only.
    known = known.loc[known["category"] == mine["category"].iloc[0]]
    cells = cohort_cells(known, t.to_pydatetime(), min_sales=1, variants=[variant_id])
    cells = cells.loc[cells["reason"] == reason, ["week", "n", "observed", "expected", "z"]]
    others = known.loc[~known["variant_id"].isin([variant_id, *exclude])]
    returned = (others["reason"] == reason) & (others["return_known_at"] <= t)
    comparison = (
        pd.DataFrame({"week": others["week"], "returned": returned.fillna(False).astype("int64")})
        .groupby("week")
        .agg(comparison_n=("returned", "size"), comparison_observed=("returned", "sum"))
        .reset_index()
    )
    out = calendar.merge(cells, on="week", how="left").merge(comparison, on="week", how="left")
    counts = ["n", "observed", "comparison_n", "comparison_observed"]
    out[counts] = out[counts].fillna(0).astype("int64")
    return out


def _ns(column: pd.Series) -> npt.NDArray[np.int64]:
    """The nanoseconds since the epoch of a timezone-aware datetime column (NaT: NAT_NS)."""
    values: npt.NDArray[np.int64] = column.array.as_unit("ns").asi8
    return values


NAT_NS: Final = np.iinfo(np.int64).min  # pandas' integer of NaT
WEEK_NS: Final = 7 * 24 * 3600 * 10**9


def weekly_counts(lines: pd.DataFrame, reason: str, as_of: datetime) -> pd.DataFrame:
    """The week, n and observed columns of weekly_series for one variant, from the rows of the
    sales frame (detect.sales_frame) of that variant only: one row per calendar week from its
    first sales week known at as_of to the week of as_of, n lines sold, observed returned for the
    reason by as_of. Equal to weekly_series(sales, variant, reason, as_of)[["week", "n",
    "observed"]] (tested) without the category baseline, and counted with numpy, so a service
    can show the trend of many anomalies at once (the API's anomaly list and case card)."""
    if reason not in REASONS:
        raise ValueError(f"unknown return reason {reason!r}")
    t = pd.Timestamp(utc(as_of))
    limit = int(t.value)
    mine = _ns(lines["line_known_at"]) <= limit
    if not mine.any():
        raise ValueError(f"no sale known at {t.isoformat()}")
    if len(set(lines["variant_id"].to_numpy()[mine])) != 1:
        raise ValueError("weekly_counts reads the lines of one variant")
    weeks = _ns(lines["week"])[mine]
    returned_at = _ns(lines["return_known_at"])[mine]
    reasons = lines["reason"].to_numpy(dtype=object, na_value=None)[mine]
    returned = (reasons == reason) & (returned_at != NAT_NS) & (returned_at <= limit)
    end = week_start(pd.Series([t]))[0]
    calendar = pd.date_range(lines.loc[mine, "week"].min(), end, freq="7D")
    slot = (weeks - int(weeks.min())) // WEEK_NS
    size = len(calendar)
    return pd.DataFrame(
        {
            "week": calendar,
            "n": np.bincount(slot, minlength=size).astype("int64"),
            "observed": np.bincount(slot, weights=returned, minlength=size).astype("int64"),
        }
    )


@dataclass(frozen=True)
class Period:
    """Sales and returns for the reason of the variant and of the rest of its category."""

    weeks: int
    n: int
    observed: int
    comparison_n: int
    comparison_observed: int

    @property
    def rate(self) -> float | None:
        return self.observed / self.n if self.n else None

    @property
    def comparison_rate(self) -> float | None:
        return self.comparison_observed / self.comparison_n if self.comparison_n else None


def _period(series: pd.DataFrame) -> Period:
    return Period(
        weeks=len(series),
        n=int(series["n"].sum()),
        observed=int(series["observed"].sum()),
        comparison_n=int(series["comparison_n"].sum()),
        comparison_observed=int(series["comparison_observed"].sum()),
    )


def _variance(rate: float, n: int) -> float:
    return rate * (1.0 - rate) / n


@dataclass(frozen=True)
class BeforeAfter:
    """The follow-up of a variant after a change (US-606). difference is (after - before) of the
    variant's rate minus that of the rest of its category, in rate units (0.10 = 10 points)."""

    variant_id: str
    reason: str
    change_at: datetime
    as_of: datetime
    series: pd.DataFrame  # weekly_series with a phase column: before, change, after
    before: Period
    after: Period
    difference: float | None
    interval: tuple[float, float] | None  # approximate 95% interval of the difference
    assessable: bool
    conclusion: str
    caveat: str = FOLLOW_UP_NOTE


def before_after(
    sales: pd.DataFrame,
    variant_id: str,
    reason: str,
    change_at: datetime,
    as_of: datetime,
    *,
    weeks: int = FOLLOW_WEEKS,
    min_sales: int = ANOMALY_THRESHOLDS.min_sales,
    exclude: Collection[str] = (),
) -> BeforeAfter:
    """The weekly returns of the variant for the reason in the `weeks` calendar weeks before and
    after a change at change_at, against the rest of its category over the same weeks without the
    variants in `exclude` (those with their own anomaly for the reason), from the sales known at
    as_of. A week that contains the change belongs to neither period; the after period stops at
    the week of as_of."""
    change, t = pd.Timestamp(utc(change_at)), pd.Timestamp(utc(as_of))
    if change > t:
        raise ValueError("the change must come before the decision time")
    if weeks < 1:
        raise ValueError("at least one week on each side")
    change_week = week_start(pd.Series([change]))[0]
    first_after = change_week if change == change_week else change_week + pd.Timedelta(days=7)
    first = change_week - pd.Timedelta(weeks=weeks)
    last = min(first_after + pd.Timedelta(weeks=weeks - 1), week_start(pd.Series([t]))[0])
    series = weekly_series(
        sales,
        variant_id,
        reason,
        t.to_pydatetime(),
        first_week=first.to_pydatetime(),
        last_week=max(last, first).to_pydatetime(),
        exclude=exclude,
    )
    phase = pd.Series("change", index=series.index)
    phase[series["week"] < change_week] = "before"
    phase[series["week"] >= first_after] = "after"
    series = series.assign(phase=phase)
    before = _period(series.loc[series["phase"] == "before"])
    after = _period(series.loc[series["phase"] == "after"])
    common = (variant_id, reason, change.to_pydatetime(), t.to_pydatetime(), series, before, after)
    if min(before.n, after.n) < min_sales:
        return BeforeAfter(
            *common, None, None, False,
            f"Not assessable yet: {before.n} sales before and {after.n} after the change; at "
            f"least {min_sales} are needed on each side.",
        )  # fmt: skip
    rb, ra = before.rate, after.rate
    cb, ca = before.comparison_rate, after.comparison_rate
    if rb is None or ra is None or cb is None or ca is None:
        return BeforeAfter(
            *common, None, None, False,
            "Not assessable: the rest of the category sold nothing in one of the periods.",
        )  # fmt: skip
    difference = (ra - rb) - (ca - cb)
    spread = Z_95 * math.sqrt(
        _variance(rb, before.n)
        + _variance(ra, after.n)
        + _variance(cb, before.comparison_n)
        + _variance(ca, after.comparison_n)
    )
    low, high = difference - spread, difference + spread
    label = reason.replace("_", " ")
    points = f"95% interval {100 * low:+.1f} to {100 * high:+.1f} points"
    if high < 0:
        conclusion = (
            f"The {label} return rate fell by {100 * -difference:.1f} points more than in the "
            f"rest of the category over the same weeks ({points})."
        )
    elif low > 0:
        conclusion = (
            f"The {label} return rate rose by {100 * difference:.1f} points more than in the "
            f"rest of the category over the same weeks ({points})."
        )
    else:
        conclusion = (
            "No difference from the rest of the category is measurable over the same weeks "
            f"({points})."
        )
    return BeforeAfter(*common, difference, (low, high), True, conclusion)
