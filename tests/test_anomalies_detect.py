"""US-208, spec 8.9: observed and expected returns per variant, reason and sales week, z, and the
flag rule (MOD-10 formulas; rule 7 and MOD-08 on the frames). Values are made up (nature:
synthetic)."""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from pipeline.anomalies.detect import (
    ANOMALY_THRESHOLDS,
    PER_TEST_EXACT,
    REASONS,
    SPEC_LITERAL,
    AnomalyThresholds,
    cohort_cells,
    expected_returns,
    flagged,
    week_start,
    z_scores,
)
from tests.anomaly_store import Sale, at, batch, frame

T = at(14, 12)  # Monday 2026-01-19 12:00Z: the cohort of week 1 is 7 days old


def coats() -> list[Sale]:
    """Week 0 (from Monday 2026-01-05): B and C sell 10 lines each, A sells 5. Week 1: A sells 20
    lines at 2026-01-12 12:00Z, 6 of them returned for size and known 4 days later. Another
    category (shoes) returns a lot in week 0 and must not move the coats baseline."""
    return [
        *batch("B0", "B", "coats", at(1, 10), 10, [("size", 3), ("size", 10), ("defective", 2)]),
        *batch("C0", "C", "coats", at(2, 10), 10, [("size", 5)]),
        *batch("A0", "A", "coats", at(3, 10), 5, [("size", 1)]),  # A's own past: left out
        *batch("D0", "D", "shoes", at(1, 10), 10, [("size", 1)] * 5),
        *batch("A1", "A", "coats", at(7, 12), 20, [("size", 4)] * 6),
    ]


def cell(cells: pd.DataFrame, variant: str, reason: str) -> pd.Series:
    rows = cells.loc[(cells["variant_id"] == variant) & (cells["reason"] == reason)]
    assert len(rows) == 1, rows
    return rows.iloc[0]


# ---- Formulas --------------------------------------------------------------------------------


def test_expected_observed_and_z_match_the_hand_calculation() -> None:
    cells = cohort_cells(frame(coats()), T, min_sales=20)
    assert set(cells["variant_id"]) == {"A"}  # only A's week 1 sells at least 20
    assert list(cells["reason"]) == sorted(REASONS)
    size = cell(cells, "A", "size")
    # Baseline: the 20 lines of B and C sold in week 0; A's own week 0 and the shoes left out.
    # Within 7 days of the sale: B's size return at 3 days and C's at 5 days, not B's at 10 days.
    assert size["prior_sales"] == 20
    assert size["baseline_hits"] == 20 * 2
    assert size["n"] == 20
    assert size["observed"] == 6
    assert size["expected"] == pytest.approx(20 * (2 + 1) / (20 + 2))
    e = 60 / 22
    assert size["z"] == pytest.approx((6 - e) / math.sqrt(e))
    defective = cell(cells, "A", "defective")
    assert defective["observed"] == 0
    assert defective["expected"] == pytest.approx(20 * (1 + 1) / 22)
    other = cell(cells, "A", "wrong_item")
    assert other["baseline_hits"] == 0
    assert other["expected"] == pytest.approx(20 / 22)  # an unseen reason is rare, not impossible
    assert pd.Timestamp(size["week"]) == pd.Timestamp(at(7))
    assert size["category"] == "coats"


def test_the_baseline_is_read_at_the_cohorts_age() -> None:
    """A younger cohort is compared with the earlier weeks at the same age: at 4 days and one
    hour, C's size return (5 days) has not come in yet."""
    young = at(11, 13)
    cells = cohort_cells(frame(coats()), young, min_sales=20)
    size = cell(cells, "A", "size")
    assert size["baseline_hits"] == 20 * 1
    assert size["expected"] == pytest.approx(20 * 2 / 22)
    assert size["observed"] == 6  # known at 4 days, before `young`
    older = cell(cohort_cells(frame(coats()), T, min_sales=20), "A", "size")
    assert size["expected"] < older["expected"]


def test_returns_known_after_the_decision_time_are_not_observed() -> None:
    cells = cohort_cells(frame(coats()), at(11, 11), min_sales=20)  # an hour before they are known
    assert cell(cells, "A", "size")["observed"] == 0


def test_rows_unknown_at_the_decision_time_change_nothing() -> None:
    """Rule 7 and MOD-08: lines sold after T, a line of the cohort recorded after T, and lines of
    the cohort's own week (not an earlier week) leave the cells at T as they are."""
    base = cohort_cells(frame(coats()), T, min_sales=20)
    extra = [
        *batch("B2", "B", "coats", at(16), 30, [("size", 1)] * 30),  # sold after T
        Sale("A1-late", "A", "coats", at(7, 13), recorded_after=timedelta(days=8)),
        *batch("B1", "B", "coats", at(8), 10, [("size", 1)] * 10),  # week 1: not a prior week
    ]
    pd.testing.assert_frame_equal(base, cohort_cells(frame([*coats(), *extra]), T, min_sales=20))


def test_a_return_known_after_the_decision_time_is_no_return_at_that_time() -> None:
    late = batch("B3", "B", "coats", at(2), 10, [("size", 30)] * 10)  # known 30 days later
    kept = batch("B3", "B", "coats", at(2), 10)
    with_late = cohort_cells(frame([*coats(), *late]), T, min_sales=20)
    pd.testing.assert_frame_equal(
        with_late, cohort_cells(frame([*coats(), *kept]), T, min_sales=20)
    )
    a = cell(with_late, "A", "size")
    assert (a["prior_sales"], a["baseline_hits"]) == (30, 20 * 2)  # 10 more lines, no more hits
    b = cell(with_late, "B", "size")  # B's week 0 now sells 20 lines
    assert b["observed"] == 2  # B's returns at 3 and 10 days; not the 10 known after T


def test_a_category_without_earlier_sales_has_no_baseline() -> None:
    sales = frame(batch("A", "A", "coats", at(1), 25, [("size", 1)] * 5))
    cells = cohort_cells(sales, T, min_sales=20)
    assert cells["expected"].isna().all()
    assert cells["z"].isna().all()
    assert not flagged(cells).any()
    assert not flagged(cells, SPEC_LITERAL).any()


def test_min_sales_and_variant_filter() -> None:
    sales = frame(coats())
    assert set(cohort_cells(sales, T, min_sales=10)["variant_id"]) == {"A", "B", "C", "D"}
    assert set(cohort_cells(sales, T, min_sales=21)["variant_id"]) == set()
    only = cohort_cells(sales, T, min_sales=1, variants=["C"])
    assert set(only["variant_id"]) == {"C"}
    assert only["expected"].isna().all()  # C's week 0 is the first week of the category


def test_the_vectorised_counts_equal_a_brute_force_count() -> None:
    """baseline_hits and prior_sales against a plain loop over a random store."""
    rng = np.random.default_rng(7)
    sales: list[Sale] = []
    for i in range(1500):
        sold = at(float(rng.uniform(0, 60)))
        variant = f"V{int(rng.integers(0, 12))}"
        category = "coats" if variant < "V6" else "shoes"
        returned = rng.random() < 0.4
        reason = str(rng.choice(REASONS)) if returned else None
        after = timedelta(days=float(rng.uniform(0.5, 30))) if returned else None
        late = timedelta(hours=float(rng.uniform(0, 3)))
        sales.append(Sale(f"L{i:05d}", variant, category, sold, reason, after, late))
    t = at(50, 7)
    data = frame(sales)
    cells = cohort_cells(data, t, min_sales=1)
    known = data.loc[data["line_known_at"] <= pd.Timestamp(t)]
    for row in cells.sample(120, random_state=1).itertuples(index=False):
        mine = known.loc[(known["variant_id"] == row.variant_id) & (known["week"] == row.week)]
        prior = known.loc[
            (known["category"] == row.category)
            & (known["variant_id"] != row.variant_id)
            & (known["week"] < row.week)
        ]
        assert row.n == len(mine)
        assert row.prior_sales == len(prior)
        expected_hits = 0
        for sold in mine["sold_at"]:
            age = pd.Timestamp(t) - sold
            lag = prior["return_known_at"] - prior["sold_at"]
            expected_hits += int(((prior["reason"] == row.reason) & (lag <= age)).sum())
        assert row.baseline_hits == expected_hits
        observed = (mine["reason"] == row.reason) & (mine["return_known_at"] <= pd.Timestamp(t))
        assert row.observed == int(observed.sum())


# ---- Expected returns, z and the flag rule ---------------------------------------------------


def test_expected_returns_with_and_without_the_rule_of_succession() -> None:
    hits, n, prior = pd.Series([0, 6, 3]), pd.Series([20, 20, 5]), pd.Series([100, 100, 0])
    with_rule = expected_returns(hits, n, prior)
    assert with_rule[0] == pytest.approx(20 / 102)
    assert with_rule[1] == pytest.approx(26 / 102)
    assert math.isnan(with_rule[2])
    plain = expected_returns(hits, n, prior, succession=False)
    assert plain[0] == 0.0
    assert plain[1] == pytest.approx(0.06)
    assert math.isnan(plain[2])


def poisson_upper_tail(observed: int, mean: float) -> float:
    return 1.0 - sum(math.exp(-mean) * mean**k / math.factorial(k) for k in range(observed))


def test_z_and_the_exact_poisson_tail() -> None:
    z, tail = z_scores(pd.Series([1, 8, 0, 3]), pd.Series([0.08, 2.0, 0.0, np.nan]))
    assert z[0] == pytest.approx((1 - 0.08) / math.sqrt(0.08))
    assert tail[0] == pytest.approx(poisson_upper_tail(1, 0.08))
    assert tail[1] == pytest.approx(poisson_upper_tail(8, 2.0))
    assert math.isnan(z[2]) and math.isnan(tail[2])  # E = 0: no z
    assert math.isnan(z[3]) and math.isnan(tail[3])


def cells_for_rules() -> pd.DataFrame:
    rows = [
        # n, O, E
        (20, 1, 0.08),  # z = 3.25 but one return; Poisson(0.08) gives it once in 13 tests
        (20, 8, 2.0),  # z = 4.24; tail 0.0011: passes one test at z > 3, not 8 reasons at once
        (20, 14, 2.0),  # tail about 1e-8: an anomaly under every rule
        (19, 30, 1.0),  # under the minimum of 20 sales
        (20, 5, 2.0),  # z = 2.1
    ]
    frame_ = pd.DataFrame(rows, columns=["n", "observed", "expected"])
    frame_["z"], frame_["tail_p"] = z_scores(frame_["observed"], frame_["expected"])
    return frame_


def test_the_literal_rule_the_exact_tail_and_the_family_wise_level() -> None:
    cells = cells_for_rules()
    assert flagged(cells, SPEC_LITERAL).tolist() == [True, True, True, False, False]
    assert flagged(cells, PER_TEST_EXACT).tolist() == [False, True, True, False, False]
    assert flagged(cells, ANOMALY_THRESHOLDS).tolist() == [False, False, True, False, False]
    assert flagged(cells).tolist() == flagged(cells, ANOMALY_THRESHOLDS).tolist()


def test_the_levels_derive_from_z() -> None:
    normal_tail = 0.5 * math.erfc(3 / math.sqrt(2))
    assert normal_tail == pytest.approx(0.0013499, rel=1e-4)
    assert PER_TEST_EXACT.level == pytest.approx(normal_tail)
    assert ANOMALY_THRESHOLDS.level == pytest.approx(normal_tail / len(REASONS))
    assert len(REASONS) == 8
    assert (ANOMALY_THRESHOLDS.min_sales, ANOMALY_THRESHOLDS.z) == (20, 3.0)  # spec 8.9, D-15
    assert ANOMALY_THRESHOLDS.version.startswith("anomalies-v1")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": " "},
        {"min_sales": 0},
        {"z": 0.0},
        {"z": float("nan")},
        {"exact_tail": False, "family_wise": True},
    ],
)
def test_thresholds_refuse_invalid_values(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "version": "v",
        "min_sales": 20,
        "z": 3.0,
        "exact_tail": True,
        "family_wise": True,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        AnomalyThresholds(**values)  # type: ignore[arg-type]


def test_weeks_start_on_monday_utc() -> None:
    instants = pd.Series(
        pd.to_datetime(
            [
                "2026-01-11T23:59:59Z",  # Sunday
                "2026-01-12T00:00:00Z",  # Monday
                "2026-01-12T01:00:00+02:00",  # Sunday 23:00 UTC
                "2026-01-14T15:30:00Z",
            ],
            utc=True,
        )
    )
    weeks = week_start(instants)
    assert [w.isoformat() for w in weeks] == [
        "2026-01-05T00:00:00+00:00",
        "2026-01-12T00:00:00+00:00",
        "2026-01-05T00:00:00+00:00",
        "2026-01-12T00:00:00+00:00",
    ]


def test_unknown_reasons_are_refused() -> None:
    sales = frame(batch("A", "A", "coats", at(1), 20, [("broken_heart", 1)]))
    with pytest.raises(ValueError, match="unknown return reasons"):
        cohort_cells(sales, T, min_sales=20)
