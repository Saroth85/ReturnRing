"""UNIT-03 and US-205: customer value of spec 8.5 reconciles to the cent with the orders, and
never feeds the risk features (rule 6).

Every amount here is a made-up fixture (origin: hand-built test store; nature: synthetic).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from contracts.io import validate_table
from pipeline.features.snapshot import load_snapshot
from pipeline.features.value import (
    VALUE_ASSUMPTIONS,
    VALUE_COLUMNS,
    ValueAssumptions,
    customer_value,
    reconcile,
    value_table,
)
from tests.feature_store import Line, MiniStore, at


def store() -> MiniStore:
    s = MiniStore()
    for c in ("C-1", "C-2", "C-3"):
        s.customer(c, at(-500))
    for v in ("V-1", "V-2", "V-3", "V-4", "V-5", "V-6", "V-7", "V-0"):
        s.variant(v, "P-" + v)
    # Outside the 12-month window at T = day 200 (window starts on day -165).
    s.order("O-0", "C-1", at(-200), [Line("L-0", "V-0", "50.00")], at(-198))
    # C-1: welcome coupon 15.00 on O-1, promo 10.00 on O-2; L-1 and L-4 returned.
    s.order(
        "O-1",
        "C-1",
        at(0),
        [Line("L-1", "V-1", "40.00"), Line("L-2", "V-2", "60.00")],
        at(2),
        coupon=("WLC-1", "welcome", "15.00"),
    )
    s.ret("R-1", "L-1", "size", at(5), 3)
    s.order(
        "O-2",
        "C-1",
        at(100),
        [Line("L-3", "V-3", "19.99"), Line("L-4", "V-4", "30.00")],
        at(102),
        coupon=("PRM-1", "promo", "10.00"),
    )
    s.ret("R-4", "L-4", "changed_mind", at(104), 2)
    # C-2: a defect return still costs logistics (spec 8.5 counts every return); L-7 not yet
    # delivered at T is kept; L-5 is returned on day 201, after T.
    s.order(
        "O-3",
        "C-2",
        at(150),
        [Line("L-5", "V-5", "100.00"), Line("L-6", "V-6", "20.00")],
        at(152),
    )
    s.ret("R-6", "L-6", "defective", at(155), 3)
    s.order("O-4", "C-2", at(199), [Line("L-7", "V-7", "9.10")])
    s.ret("R-5", "L-5", "size", at(201), 49)
    return s


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return store().write(tmp_path / "store")


def by_customer(root: Path, days: float) -> dict[str, dict[str, object]]:
    values = customer_value(load_snapshot(root, at(days)))
    return {row["customer_id"]: row for row in values.to_dict("records")}


def test_unit03_value_matches_the_hand_computation(root: Path) -> None:
    v = by_customer(root, 200)
    # C-1: kept 60.00 + 19.99 = 79.99; 0.55 * 79.99 = 43.9945 -> 43.99; 2 returns: -16.00;
    # discounts 15.00 + 10.00: -25.00. V = 2.99. O-0 is outside the 12 months.
    c1 = v["C-1"]
    assert c1["kept_eur"] == Decimal("79.99")
    assert c1["returned_eur"] == Decimal("70.00")
    assert c1["discount_eur"] == Decimal("25.00")
    assert c1["orders_total_eur"] == Decimal("124.99")
    assert (c1["n_orders"], c1["n_lines"], c1["n_returns"]) == (2, 4, 2)
    assert c1["value_eur"] == Decimal("2.99")
    # C-2: kept 100.00 + 9.10 = 109.10; 0.55 * 109.10 = 60.005 -> 60.01 (half up); 1 return.
    c2 = v["C-2"]
    assert c2["kept_eur"] == Decimal("109.10")
    assert c2["value_eur"] == Decimal("52.01")
    assert (c2["n_orders"], c2["n_lines"], c2["n_returns"]) == (2, 3, 1)
    # C-3 has no order: value 0.
    assert v["C-3"]["value_eur"] == Decimal("0.00")
    assert v["C-3"]["n_orders"] == 0


def test_value_uses_only_returns_known_at_the_decision(root: Path) -> None:
    """R-5 is requested on day 201: at day 200 L-5 is kept, at day 202 it is returned."""
    c2 = by_customer(root, 202)["C-2"]
    # kept 9.10: 0.55 * 9.10 = 5.005 -> 5.01; 2 returns -16.00. V = -10.99.
    assert c2["kept_eur"] == Decimal("9.10")
    assert c2["value_eur"] == Decimal("-10.99")


def test_unit03_reconciles_to_the_cent_with_the_orders(root: Path) -> None:
    for days in (0, 3, 101, 200, 202, 600):
        values = customer_value(load_snapshot(root, at(days)))
        assert reconcile(values).empty, days
        for row in values.to_dict("records"):
            lines = row["kept_eur"] + row["returned_eur"] - row["discount_eur"]
            assert lines == row["orders_total_eur"]


def test_reconcile_reports_a_mismatch(root: Path) -> None:
    values = customer_value(load_snapshot(root, at(200)))
    values.loc[values["customer_id"] == "C-1", "discount_eur"] = Decimal("24.99")
    bad = reconcile(values)
    assert bad["customer_id"].tolist() == ["C-1"]
    assert bad["difference_eur"].tolist() == [Decimal("0.01")]


def test_window_is_twelve_months_up_to_the_decision(root: Path) -> None:
    assert by_customer(root, 160)["C-1"]["n_orders"] == 3  # O-0 at day -200 is within 365 days
    assert by_customer(root, 166)["C-1"]["n_orders"] == 2


def test_assumptions_are_the_spec_hypotheses_and_versioned() -> None:
    assert VALUE_ASSUMPTIONS.gross_margin == Decimal("0.55")
    assert VALUE_ASSUMPTIONS.logistics_cost_per_return_eur == Decimal("8.00")
    assert VALUE_ASSUMPTIONS.window_days == 365
    assert VALUE_ASSUMPTIONS.version
    with pytest.raises(ValueError, match="margin"):
        replace(VALUE_ASSUMPTIONS, gross_margin=Decimal("1.5"))
    with pytest.raises(ValueError, match="logistics"):
        replace(VALUE_ASSUMPTIONS, logistics_cost_per_return_eur=Decimal("-1"))
    with pytest.raises(ValueError, match="window"):
        replace(VALUE_ASSUMPTIONS, window_days=0)


def test_other_assumptions_change_the_value_only(root: Path) -> None:
    snap = load_snapshot(root, at(200))
    lean = ValueAssumptions("test-lean", Decimal("0.40"), Decimal("5.00"), 365, "test")
    c1 = customer_value(snap, lean).set_index("customer_id").loc["C-1"]
    # 0.40 * 79.99 = 31.996 -> 32.00; 2 * 5.00; 25.00 of discounts. V = -3.00.
    assert c1["value_eur"] == Decimal("-3.00")
    assert c1["assumptions_version"] == "test-lean"


def test_value_table_is_a_valid_contract_table(root: Path) -> None:
    snap = load_snapshot(root, at(200))
    table = value_table(customer_value(snap), snap.as_of)
    assert validate_table("features/customer_value", table) == []
    assert set(VALUE_COLUMNS) <= set(table.column_names)
