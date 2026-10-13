"""Decision-time line frame and history counts (rule 7, rule 4, rule 8; MOD-08; spec 8.2).

Every value here is a made-up fixture (origin: hand-built test store; nature: synthetic).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from contracts.parquet import is_forbidden_feature
from pipeline.features.lines import as_of_counts, line_frame, line_history, observations
from pipeline.features.snapshot import COLUMNS, load_snapshot, month_ends, utc
from tests.feature_store import Line, MiniStore, at


def store() -> MiniStore:
    s = MiniStore()
    for c in ("C-1", "C-2", "C-3"):
        s.customer(c, at(-300))
    s.variant("V-1", "P-1").variant("V-2", "P-1").variant("V-3", "P-2", "jeans", "80", "26-36")
    # O-1: two colours of P-1, delivered day 2 -> settled day 33; L-1 returned for size.
    s.order("O-1", "C-1", at(0), [Line("L-1", "V-1", "40"), Line("L-2", "V-2", "60")], at(2))
    s.ret("R-1", "L-1", "size", at(5), 3)
    # O-2: returned as defective before settlement (day 34): guarantee path, excluded.
    s.order("O-2", "C-2", at(1), [Line("L-3", "V-1", "40")], at(3))
    s.ret("R-2", "L-3", "defective", at(6), 3)
    # O-3: settled day 43 as kept; a not_as_described return known on day 47 does not change it.
    s.order("O-3", "C-2", at(10), [Line("L-4", "V-1", "40")], at(12))
    s.ret("R-3", "L-4", "not_as_described", at(47), 35)
    # O-4: delivered day 21 but known only on day 60: settled when known.
    s.order("O-4", "C-3", at(20), [Line("L-5", "V-3", "80", "30")], at(21), at(60))
    # O-5: never delivered.
    s.order("O-5", "C-3", at(30), [Line("L-6", "V-1", "40")])
    # O-6: a 60-day window, delivered day 42 -> settled day 103.
    s.order("O-6", "C-1", at(40), [Line("L-7", "V-1", "40")], at(42), window_days=60)
    return s


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return store().write(tmp_path / "store")


def frame_at(root: Path, days: float) -> pd.DataFrame:
    return line_frame(load_snapshot(root, at(days))).set_index("line_id")


def test_snapshot_never_loads_forbidden_columns(root: Path) -> None:
    snap = load_snapshot(root, at(200))
    for name, frame in snap.frames.items():
        assert not [c for c in frame.columns if is_forbidden_feature(c)], name
    assert "display_name" not in snap["store/customers"].columns
    assert "value_hash" not in snap["store/identifiers"].columns
    assert not [c for cols in COLUMNS.values() for c in cols if is_forbidden_feature(c)]
    assert snap["store/order_lines"]["price_cents"].tolist()[:2] == [4000, 6000]


def test_settlement_closes_the_return_window_plus_one_day(root: Path) -> None:
    before = frame_at(root, 32.99)
    assert not before["observed"].any()
    after = frame_at(root, 33)
    assert after.loc["L-1", "settled_at"] == pd.Timestamp(at(33))
    assert after.loc["L-1", "observed"] and after.loc["L-1", "returned"]
    assert after.loc["L-2", "observed"] and not after.loc["L-2", "returned"]


def test_non_conformity_returns_are_not_observations_of_behaviour(root: Path) -> None:
    """Rule 4: a defect return follows the guarantee path, so it is excluded, not a return."""
    lines = frame_at(root, 50)
    assert lines.loc["L-3", "excluded"] and not lines.loc["L-3", "observed"]
    assert lines.loc["L-3", "nonconformity_return"] and not lines.loc["L-3", "withdrawal_return"]
    assert not lines.loc["L-3", "returned"]


def test_outcome_is_read_at_settlement(root: Path) -> None:
    """L-4 settled on day 43 as kept; its later guarantee claim does not rewrite it."""
    lines = frame_at(root, 50)
    assert lines.loc["L-4", "observed"] and not lines.loc["L-4", "returned"]
    assert lines.loc["L-4", "nonconformity_return"]
    assert not frame_at(root, 46).loc["L-4", "nonconformity_return"]


def test_late_delivery_record_delays_settlement(root: Path) -> None:
    assert not frame_at(root, 59.9).loc["L-5", "observed"]
    assert not frame_at(root, 59.9).loc["L-5", "delivered"]
    lines = frame_at(root, 60)
    assert lines.loc["L-5", "settled_at"] == pd.Timestamp(at(60))
    assert lines.loc["L-5", "observed"]


def test_undelivered_lines_never_settle_and_windows_come_from_the_snapshot(root: Path) -> None:
    lines = frame_at(root, 200)
    assert not lines.loc["L-6", "delivered"] and not lines.loc["L-6", "observed"]
    assert pd.isna(lines.loc["L-6", "settled_at"])
    assert lines.loc["L-7", "settled_at"] == pd.Timestamp(at(42 + 61))


def test_order_context(root: Path) -> None:
    lines = frame_at(root, 200)
    assert lines.loc["L-1", "order_lines"] == 2 and lines.loc["L-1", "order_same_product"] == 1
    assert lines.loc["L-4", "order_lines"] == 1 and lines.loc["L-4", "order_same_product"] == 0


def test_mod08_line_frame_ignores_rows_available_after_the_decision(
    root: Path, tmp_path: Path
) -> None:
    """MOD-08: the frame at T from the full store equals the frame from the rows known at T,
    and equals the restricted later snapshot."""
    for days in (4, 33, 46, 59, 103):
        full = line_frame(load_snapshot(root, at(days)))
        late = line_frame(load_snapshot(root, at(400)).restrict(at(days)))
        pd.testing.assert_frame_equal(full, late)
    truncated = MiniStore()
    for name, rows in store().rows.items():
        truncated.rows[name] = [r for r in rows if r["available_at"] <= at(46)]
    small = truncated.write(tmp_path / "truncated")
    pd.testing.assert_frame_equal(
        line_frame(load_snapshot(root, at(46))), line_frame(load_snapshot(small, at(46)))
    )


def test_inconsistent_snapshots_are_refused(tmp_path: Path) -> None:
    s = MiniStore().customer("C-1", at(-1)).variant("V-1", "P-1")
    s.order("O-1", "C-1", at(0), [Line("L-1", "V-1", "40")])
    s.rows["store/order_lines"][0]["variant_id"] = "V-UNKNOWN"
    root = s.write(tmp_path / "bad")
    with pytest.raises(ValueError, match="catalogue variant"):
        line_frame(load_snapshot(root, at(1)))


def test_as_of_counts_include_ties_and_respect_keys() -> None:
    t = pd.Timestamp(at(10))
    events = pd.DataFrame(
        {
            "settled_at": [t, t, t + timedelta(days=1), t - timedelta(days=1)],
            "k": ["a", "a", "a", "b"],
            "returned": [True, False, True, True],
        }
    )
    queries = pd.DataFrame(
        {"t": [t, t + timedelta(days=1), t - timedelta(days=2), t], "k": list("aaab")}
    )
    n, r = as_of_counts(events, queries, ["k"])
    assert n.tolist() == [2, 3, 0, 1] and r.tolist() == [1, 2, 0, 1]
    n, r = as_of_counts(events, queries, [])
    assert n.tolist() == [3, 4, 0, 3] and r.tolist() == [2, 3, 0, 2]


def test_history_leaves_the_customer_out_and_the_line_itself_out(root: Path) -> None:
    lines = line_frame(load_snapshot(root, at(200)))
    events = observations(lines)
    q = lines.set_index("line_id").loc[["L-7"]].reset_index()
    for days, expected in (
        (103, dict(variant_n=1, variant_r=0, product_n=1, product_r=0, category_n=1,
                   all_n=2, all_r=0, customer_n=2, customer_r=1)),
        (50, dict(variant_n=1, variant_r=0, product_n=1, product_r=0, category_n=1,
                  all_n=1, all_r=0, customer_n=2, customer_r=1)),
    ):  # fmt: skip
        q["t"] = pd.Timestamp(at(days))
        got = line_history(events, q).iloc[0]
        for column, value in expected.items():
            assert got[column] == value, (days, column)


def test_history_of_an_empty_store_is_zero() -> None:
    events = pd.DataFrame(
        {
            "settled_at": pd.Series([], dtype="datetime64[us, UTC]"),
            "line_id": pd.Series([], dtype=str),
            "customer_id": pd.Series([], dtype=str),
            "variant_id": pd.Series([], dtype=str),
            "product_id": pd.Series([], dtype=str),
            "category": pd.Series([], dtype=str),
            "returned": pd.Series([], dtype=bool),
        }
    )
    q = pd.DataFrame(
        {
            "t": [pd.Timestamp(at(0))],
            "line_id": ["L"],
            "customer_id": ["C"],
            "variant_id": ["V"],
            "product_id": ["P"],
            "category": ["x"],
        }
    )
    got = line_history(events, q).iloc[0]
    assert (got == 0).all()


def test_decision_times_are_utc_and_snapshots_only_narrow(root: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        utc(datetime(2026, 1, 1))
    snap = load_snapshot(root, at(10))
    with pytest.raises(ValueError, match="later instant"):
        snap.restrict(at(11))
    assert snap.restrict(at(5)).as_of == at(5)


def test_month_ends() -> None:
    ends = month_ends(datetime(2025, 10, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC))
    assert ends[0] == datetime(2025, 11, 1, tzinfo=UTC)
    assert ends[-1] == datetime(2026, 10, 1, tzinfo=UTC)
    assert len(ends) == 12
    assert month_ends(datetime(2026, 12, 15, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)) == [
        datetime(2027, 1, 1, tzinfo=UTC)
    ]
