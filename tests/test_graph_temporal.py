"""MOD-08 and rule 7 for the account graph: no feature uses an event available after the decision.

The strongest check: the graph built at T from the whole store equals the graph built at T from a
store extracted at T (every row available after T removed). Every value is a made-up test fixture
(nature: synthetic).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from contracts.io import ContractError, validate_table
from pipeline.graph.build import build_graph
from pipeline.graph.clusters import clusters_table
from pipeline.graph.links import links_table
from pipeline.graph.snapshot import load_snapshot
from tests.graph_fixtures import Line, MiniStore, Order, day

T = day(30)
LATER = day(50)


def timeline() -> MiniStore:
    s = MiniStore()
    s.account("A", day(0), device="d", email="ea")
    s.account("B", day(0), device="d", email="eb")
    s.account("C", day(10), email="ec")
    s.use("C", "card", "k", day(40))  # C starts paying with A's card after T
    s.use("A", "card", "k", day(12))
    s.account("D", day(35), card="k", email="ed")  # opened after T
    s.coupons += [
        ("W-A", "A", "welcome", day(0), "O-A2", day(32)),  # redeemed after T
        ("W-B", "B", "welcome", day(0), "O-B1", day(20)),
    ]
    s.orders += [
        # B: delivered before T, returned after T.
        Order(
            "O-B1",
            "B",
            day(20),
            day(25),
            (Line("L-B1", returned="changed_mind", returned_at=day(33)),),
            "W-B",
        ),
        # A: placed before T, delivered after T.
        Order("O-A2", "A", day(28), day(31), (Line("L-A2"),), "W-A"),
    ]
    return s


def rows(frame: object) -> dict[str, dict[str, object]]:
    return {r["customer_id"]: r for r in frame.iter_rows(named=True)}  # type: ignore[attr-defined]


def test_the_whole_store_and_the_store_extracted_at_t_give_the_same_graph(tmp_path: Path) -> None:
    store = timeline()
    whole = build_graph(load_snapshot(store.write(tmp_path / "whole"), T))
    cut = build_graph(load_snapshot(store.write(tmp_path / "cut", before=T), T))
    assert whole.links.equals(cut.links)
    assert whole.clusters.equals(cut.clusters)


def test_facts_after_t_are_not_known_at_t(tmp_path: Path) -> None:
    root = timeline().write(tmp_path)
    at_t = build_graph(load_snapshot(root, T))
    pairs = set(at_t.links.select("customer_a", "customer_b").rows())
    assert pairs == {("A", "B")}  # C's card and D come later
    got = rows(at_t.clusters)
    assert set(got) == {"A", "B", "C"}  # D does not exist yet
    ab = got["A"]
    assert ab["cluster_size"] == 2
    assert ab["welcome_coupons"] == 1  # B's; A redeems after T
    assert ab["cluster_return_rate"] == 0.0  # B's line is delivered, its return comes later
    assert ab["n_distinct_cards"] == 1  # A's card k, first used on day 12
    assert all(t <= T for t in at_t.links["last_shared_at"].to_list())
    assert validate_table("features/links", links_table(at_t.links)) == []
    assert validate_table("features/clusters", clusters_table(at_t.clusters)) == []

    later = build_graph(load_snapshot(root, LATER))
    pairs = set(later.links.select("customer_a", "customer_b").rows())
    assert {("A", "C"), ("A", "D"), ("C", "D")} <= pairs
    got = rows(later.clusters)
    assert got["A"]["cluster_size"] == 4
    assert got["A"]["welcome_coupons"] == 2
    # B's line returned; A's line delivered and kept.
    assert got["A"]["cluster_return_rate"] == pytest.approx(0.5)


def test_links_decay_from_the_sharing_instant_to_the_decision_time(tmp_path: Path) -> None:
    root = timeline().write(tmp_path)
    early = build_graph(load_snapshot(root, T)).links.row(0, named=True)
    late = build_graph(load_snapshot(root, LATER)).links
    ab = late.filter((late["customer_a"] == "A") & (late["customer_b"] == "B")).row(0, named=True)
    assert early["weight"] == ab["weight"]
    assert ab["weight_decayed"] < early["weight_decayed"] < early["weight"] + 1e-12


def test_a_naive_decision_time_is_refused(tmp_path: Path) -> None:
    root = timeline().write(tmp_path)
    with pytest.raises((ValueError, ContractError)):
        load_snapshot(root, datetime(2026, 1, 31))
