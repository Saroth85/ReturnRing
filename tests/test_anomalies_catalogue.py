"""US-606: the catalogue task of a flagged anomaly (spec 8.9: description, size guide, lot check;
no customer, E2E-04) and the follow-up after a change, where a decrease alone does not prove the
effect. Values are made up (nature: synthetic)."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from contracts.parquet import is_forbidden_feature
from pipeline.anomalies.catalogue import (
    CHECK_ORDER,
    CHECKS,
    FOLLOW_UP_NOTE,
    PRODUCT_NOTE,
    before_after,
    catalogue_task,
    weekly_series,
)
from policy.types import ReturnReason
from tests.anomaly_store import FIXTURE, Sale, at, batch, frame

AS_OF = datetime(2026, 3, 2, tzinfo=UTC)


def anomaly_rows(variant: str = "V-JKT", reason: str = "not_as_described") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "variant_id": [variant, variant],
            "reason": [reason, reason],
            "week": [date(2026, 2, 9), date(2026, 2, 2)],
            "observed": [16, 12],
            "expected": [0.1234, 0.0951],
            "z": [45.21, 38.6],
            "n": [24, 31],
            "status": ["open", "open"],
            "as_of": [pd.Timestamp(AS_OF)] * 2,
        }
    )


def keys(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return [k for key, value in payload.items() for k in (key, *keys(value))]
    if isinstance(payload, list):
        return [k for item in payload for k in keys(item)]
    return []


def test_the_task_carries_the_flagged_weeks_and_the_three_checks() -> None:
    task = catalogue_task(anomaly_rows(), product_id="P-JKT", category="coats", provenance=FIXTURE)
    assert task["task_id"] == "CAT-V-JKT-not_as_described-20260202"
    assert [w["week"] for w in task["weeks"]] == ["2026-02-02", "2026-02-09"]
    assert task["weeks"][0] == {
        "week": "2026-02-02",
        "n": 31,
        "observed": 12,
        "expected": 0.095,
        "z": 38.6,
    }
    assert task["totals"] == {"n": 55, "observed": 28, "expected": pytest.approx(0.2185, abs=1e-3)}
    assert [c["id"] for c in task["checks"]] == ["description", "size_guide", "lot_check"]
    assert task["as_of"] == "2026-03-02T00:00:00+00:00"
    assert task["provenance"] == {
        "origin": FIXTURE.origin,
        "period_start": "2025-01-01",
        "period_end": "2026-12-31",
        "nature": "synthetic",
    }
    assert (task["note"], task["follow_up"]) == (PRODUCT_NOTE, FOLLOW_UP_NOTE)
    json.dumps(task)  # ready for the API


def test_the_task_names_no_customer() -> None:
    """E2E-04, data side: the task is about the product; no key names a customer or a forbidden
    feature (rule 6)."""
    task = catalogue_task(anomaly_rows(), product_id="P-JKT", category="coats", provenance=FIXTURE)
    names = keys(task)
    assert not [k for k in names if "customer" in k.lower()]
    assert not [k for k in names if is_forbidden_feature(k)]
    assert "customer" not in json.dumps(task["weeks"])


def test_the_first_check_follows_the_reason() -> None:
    assert set(CHECK_ORDER) == {r.value for r in ReturnReason}
    for order in CHECK_ORDER.values():
        assert sorted(order) == sorted(CHECKS)
    rows = anomaly_rows(reason="defective")
    task = catalogue_task(rows, product_id="P", category="coats", provenance=FIXTURE)
    assert task["checks"][0]["id"] == "lot_check"
    task = catalogue_task(
        anomaly_rows(reason="size"), product_id="P", category="c", provenance=FIXTURE
    )
    assert task["checks"][0]["id"] == "size_guide"


def test_a_task_covers_one_variant_reason_and_decision_time() -> None:
    rows = anomaly_rows()
    with pytest.raises(ValueError, match="at least one"):
        catalogue_task(rows.iloc[0:0], product_id="P", category="c", provenance=FIXTURE)
    mixed = rows.assign(variant_id=["V-1", "V-2"])
    with pytest.raises(ValueError, match="variant_id"):
        catalogue_task(mixed, product_id="P", category="c", provenance=FIXTURE)
    with pytest.raises(ValueError, match="unknown return reason"):
        catalogue_task(
            rows.assign(reason="moody"), product_id="P", category="c", provenance=FIXTURE
        )


# ---- Follow-up after a change ------------------------------------------------------------------

CHANGE = at(7 * 8)  # Monday of week 8


def weekly(variant: str, weeks: range, n: int, returned: int, reason: str = "size") -> list[Sale]:
    out: list[Sale] = []
    for w in weeks:
        out += batch(
            f"{variant}{w:02d}", variant, "coats", at(7 * w + 1), n, [(reason, 2)] * returned
        )
    return out


def store(v_before: int, v_after: int, others_before: int, others_after: int) -> list[Sale]:
    """V sells 10 lines a week, X and Y 20 each; weeks 0-7 before the change, 8-15 after."""
    rows = weekly("V", range(8), 10, v_before) + weekly("V", range(8, 16), 10, v_after)
    for o in ("X", "Y"):
        rows += weekly(o, range(8), 20, others_before) + weekly(o, range(8, 16), 20, others_after)
    return rows


LATER = at(7 * 16 + 1)  # Tuesday of week 16: every return of weeks 0-15 is known


def test_a_fall_against_the_rest_of_the_category() -> None:
    result = before_after(frame(store(5, 1, 2, 2)), "V", "size", CHANGE, LATER)
    assert result.assessable
    assert (result.before.n, result.before.observed) == (80, 40)
    assert (result.after.n, result.after.observed) == (80, 8)
    assert (result.before.comparison_n, result.before.comparison_observed) == (320, 32)
    assert result.difference == pytest.approx((0.1 - 0.5) - (0.1 - 0.1))
    spread = 1.959963984540054 * math.sqrt(0.25 / 80 + 0.09 / 80 + 0.09 / 320 + 0.09 / 320)
    assert result.interval == pytest.approx((-0.4 - spread, -0.4 + spread))
    assert result.conclusion.startswith("The size return rate fell by 40.0 points more than")
    assert result.caveat == FOLLOW_UP_NOTE
    assert result.series["phase"].value_counts().to_dict() == {"before": 8, "after": 8}


def test_a_decrease_shared_by_the_category_is_no_effect() -> None:
    """Spec 8.9: a decrease alone does not prove the effect of the change."""
    result = before_after(frame(store(5, 1, 10, 2)), "V", "size", CHANGE, LATER)
    assert result.after.observed < result.before.observed
    assert result.difference == pytest.approx(0.0)
    assert result.conclusion.startswith("No difference from the rest of the category")


def test_variants_with_their_own_anomaly_leave_the_comparison() -> None:
    """A defective variant fixed at the same date would make a steady variant look worse."""
    rows = store(1, 1, 2, 2) + weekly("Z", range(8), 20, 18) + weekly("Z", range(8, 16), 20, 2)
    biased = before_after(frame(rows), "V", "size", CHANGE, LATER)
    clean = before_after(frame(rows), "V", "size", CHANGE, LATER, exclude=["Z"])
    assert biased.difference is not None and biased.difference > 0.1
    assert clean.difference == pytest.approx(0.0)


def test_too_few_sales_after_the_change_is_not_assessable() -> None:
    result = before_after(frame(store(5, 1, 2, 2)), "V", "size", CHANGE, CHANGE + timedelta(days=3))
    assert not result.assessable
    assert result.difference is None and result.interval is None
    assert result.after.n == 10
    assert result.conclusion.startswith("Not assessable yet: 80 sales before and 10 after")


def test_the_week_of_a_mid_week_change_belongs_to_neither_period() -> None:
    change = CHANGE + timedelta(days=2, hours=9)  # Wednesday of week 8
    result = before_after(frame(store(5, 1, 2, 2)), "V", "size", change, LATER)
    phases = result.series.set_index("week")["phase"]
    assert phases[pd.Timestamp(CHANGE)] == "change"
    # Eight calendar weeks on each side: 0-7, and 9-16 (week 16 has no sale of V yet).
    assert (result.before.weeks, result.after.weeks) == (8, 8)
    assert (result.before.n, result.after.n) == (80, 70)


def test_the_weekly_series_has_every_calendar_week() -> None:
    rows = weekly("V", [0, 2], 10, 3) + weekly("X", range(3), 20, 2)
    series = weekly_series(frame(rows), "V", "size", at(21, 1))
    assert [pd.Timestamp(w).isoformat()[:10] for w in series["week"]] == [
        "2026-01-05",
        "2026-01-12",
        "2026-01-19",
        "2026-01-26",
    ]
    assert series["n"].tolist() == [10, 0, 10, 0]
    assert series["observed"].tolist() == [3, 0, 3, 0]
    assert series["comparison_n"].tolist() == [20, 20, 20, 0]
    assert math.isnan(series["expected"][1])
    assert series["expected"][2] > 0  # week 2 has a baseline: X in weeks 0 and 1


def test_follow_up_refuses_inconsistent_requests() -> None:
    sales = frame(store(5, 1, 2, 2))
    with pytest.raises(ValueError, match="before the decision time"):
        before_after(sales, "V", "size", LATER + timedelta(days=1), LATER)
    with pytest.raises(ValueError, match="unknown return reason"):
        weekly_series(sales, "V", "moody", LATER)
    with pytest.raises(ValueError, match="no sale of W"):
        weekly_series(sales, "W", "size", LATER)
