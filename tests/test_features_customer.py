"""Customer behaviour features at a decision time (US-204 inputs; rules 4, 6, 7; MOD-08).

Every value here is a made-up fixture (origin: hand-built test store; nature: synthetic).
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from contracts.parquet import forbidden_features
from pipeline.features.customer import CUSTOMER_FEATURE_COLUMNS, behaviour_features
from pipeline.features.lines import line_frame
from pipeline.features.snapshot import load_snapshot
from pipeline.features.value import VALUE_COLUMNS
from tests.feature_store import Line, MiniStore, at


def store() -> MiniStore:
    s = MiniStore()
    s.customer("C-1", at(-100)).customer("C-2", at(50)).customer("C-3", at(150))
    s.variant("V-1", "P-1").variant("V-2", "P-2").variant("V-3", "P-3")
    # Monday 5 Jan 2026: two lines, welcome coupon; L-1 returned for size after 3 days.
    s.order(
        "O-1",
        "C-1",
        at(0),
        [Line("L-1", "V-1", "40"), Line("L-2", "V-2", "60")],
        at(2),
        coupon=("WLC-1", "welcome", "15"),
    )
    s.ret("R-1", "L-1", "size", at(5), 3)
    # Saturday: L-3 returned, changed mind, after 5 days.
    s.order("O-2", "C-1", at(5), [Line("L-3", "V-3", "20")], at(7))
    s.ret("R-3", "L-3", "changed_mind", at(12), 5)
    # Sunday with a promo coupon: L-4 returned as defective (guarantee path, rule 4).
    s.order(
        "O-3",
        "C-1",
        at(6),
        [Line("L-4", "V-1", "40")],
        at(8),
        coupon=("PRM-1", "promo", "10"),
    )
    s.ret("R-4", "L-4", "defective", at(9), 1)
    # Friday 6 Mar 2026: not delivered yet.
    s.order("O-4", "C-1", at(60), [Line("L-5", "V-2", "60")])
    return s


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return store().write(tmp_path / "store")


def features(root: Path, days: float) -> pd.DataFrame:
    snap = load_snapshot(root, at(days))
    return behaviour_features(snap, line_frame(snap)).set_index("customer_id")


def test_features_of_a_customer_by_hand(root: Path) -> None:
    c1 = features(root, 100).loc["C-1"]
    assert (c1["n_orders"], c1["n_lines"], c1["n_delivered_lines"]) == (4, 5, 4)
    # Observations: L-1 (returned), L-2 (kept), L-3 (returned); L-4 is a defect return.
    assert c1["n_observations"] == 3
    assert c1["return_rate"] == pytest.approx(2 / 3)
    assert c1["n_returns"] == 2
    assert c1["return_rate_to_date"] == pytest.approx(2 / 3)  # 2 of 4 delivered minus 1 defect
    assert c1["size_share"] == pytest.approx(0.5)
    assert c1["changed_mind_share"] == pytest.approx(0.5)
    assert c1["not_as_expected_share"] == 0 and c1["other_reason_share"] == 0
    assert c1["median_days_to_return"] == pytest.approx(4.0)
    assert c1["tenure_days"] == pytest.approx(200.0)
    assert c1["n_welcome_coupons_used"] == 1 and c1["n_promo_coupons_used"] == 1
    assert c1["coupon_order_share"] == pytest.approx(0.5)
    assert c1["weekend_order_share"] == pytest.approx(0.5)
    assert c1["mean_line_price_eur"] == pytest.approx(44.0)


def test_a_new_account_without_orders_has_missing_rates_not_zeros(root: Path) -> None:
    out = features(root, 100)
    c2 = out.loc["C-2"]
    assert c2["n_orders"] == 0 and c2["n_observations"] == 0
    assert c2["tenure_days"] == pytest.approx(50.0)
    for column in (
        "return_rate",
        "return_rate_to_date",
        "size_share",
        "median_days_to_return",
        "coupon_order_share",
        "weekend_order_share",
        "mean_line_price_eur",
    ):
        assert math.isnan(c2[column]), column
    assert "C-3" not in out.index  # opened after the decision


def test_features_use_only_what_is_known_at_the_decision(root: Path) -> None:
    early = features(root, 34).loc["C-1"]
    assert early["n_observations"] == 2  # L-3 settles on day 38
    assert early["return_rate"] == pytest.approx(0.5)
    assert early["n_returns"] == 2
    before_return = features(root, 4).loc["C-1"]
    assert before_return["n_returns"] == 0 and math.isnan(before_return["size_share"])
    assert before_return["n_orders"] == 1  # O-2 is placed on day 5


def test_defect_returns_never_enter_the_behaviour_features(root: Path) -> None:
    """Rule 4: R-4 (defective) changes no share, rate or count of returns."""
    with_defect = features(root, 100).loc["C-1"]
    s = store()
    s.rows["store/returns"] = [r for r in s.rows["store/returns"] if r["return_id"] != "R-4"]
    other = s.write(root.parent / "no-defect")
    snap = load_snapshot(other, at(100))
    without = behaviour_features(snap, line_frame(snap)).set_index("customer_id").loc["C-1"]
    for column in ("n_returns", "size_share", "changed_mind_share", "median_days_to_return"):
        assert with_defect[column] == without[column], column
    # The defect line is not an observation; kept instead, it is one more kept observation.
    assert with_defect["n_observations"] == without["n_observations"] - 1


def test_no_forbidden_and_no_value_column_feeds_risk() -> None:
    """FAIR-01 and rule 6: the model-facing customer features hold no forbidden name and no
    customer value."""
    assert forbidden_features(CUSTOMER_FEATURE_COLUMNS) == []
    assert not set(CUSTOMER_FEATURE_COLUMNS) & set(VALUE_COLUMNS)
    assert not any("value" in column for column in CUSTOMER_FEATURE_COLUMNS)
