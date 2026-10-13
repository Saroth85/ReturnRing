"""US-203 and US-310, spec 8.3: clusters, their features, Louvain splits and disputed links.

Every number here is a made-up test fixture (nature: synthetic).
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import polars as pl
import pytest

from contracts.io import validate_table
from contracts.parquet import SCHEMAS, forbidden_features
from pipeline.graph.build import build_graph, dispute_link
from pipeline.graph.clusters import (
    CLUSTER_FEATURES,
    cluster_edges,
    cluster_id,
    clusters_table,
    communities,
)
from pipeline.graph.links import LINK_FEATURES
from pipeline.graph.snapshot import StoreSnapshot, load_snapshot
from pipeline.graph.weights import GraphParams
from tests.graph_fixtures import Line, MiniStore, Order, day

AS_OF = day(30)


def edges(pairs: list[tuple[str, str]], w: float = 1.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "customer_a": [min(a, b) for a, b in pairs],
            "customer_b": [max(a, b) for a, b in pairs],
            "w": [w] * len(pairs),
        },
        schema={"customer_a": pl.String, "customer_b": pl.String, "w": pl.Float64},
    )


def clique(prefix: str, n: int) -> list[tuple[str, str]]:
    nodes = [f"{prefix}{i:02d}" for i in range(n)]
    return [(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1 :]]


# ---- clustering ---------------------------------------------------------------------------------


def test_two_cliques_joined_by_a_bridge_are_split_by_louvain() -> None:
    pairs = clique("A", 12) + clique("B", 12) + [("A00", "B00")]
    groups = communities(edges(pairs), GraphParams())
    assert sorted(len(g) for g in groups) == [12, 12]
    assert all(len({m[0] for m in g}) == 1 for g in groups)


def test_louvain_is_deterministic_whatever_the_row_order() -> None:
    pairs = clique("A", 9) + clique("B", 8) + clique("C", 7) + [("A00", "B00"), ("B01", "C00")]
    expected = communities(edges(pairs), GraphParams())
    for seed in range(5):
        shuffled = pairs[:]
        random.Random(seed).shuffle(shuffled)
        assert communities(edges(shuffled), GraphParams()) == expected


def test_a_component_of_20_is_not_split() -> None:
    pairs = clique("A", 10) + clique("B", 10) + [("A00", "B00")]
    assert [len(g) for g in communities(edges(pairs), GraphParams())] == [20]


def test_a_clique_louvain_cannot_split_stays_whole() -> None:
    """A card shared by 25 accounts: one cluster of 25, reported, never cut at random."""
    assert [len(g) for g in communities(edges(clique("K", 25)), GraphParams())] == [25]


def test_split_parts_are_connected() -> None:
    pairs = clique("A", 12) + clique("B", 12) + [("A00", "B00"), ("A01", "B01")]
    for g in communities(edges(pairs), GraphParams(max_cluster_size=5)):
        sub = [(a, b) for a, b in pairs if a in g and b in g]
        reached = {min(g)}
        for _ in range(len(g)):
            reached |= {b for a, b in sub if a in reached} | {a for a, b in sub if b in reached}
        assert reached == set(g)


def test_cluster_ids_are_opaque_and_stable() -> None:
    cid = cluster_id(["C-ROSSI-2", "C-ROSSI-1"])
    assert re.fullmatch(r"CL-[0-9a-f]{12}", cid)
    assert cid == cluster_id(("C-ROSSI-1", "C-ROSSI-2"))
    assert cid != cluster_id(["C-ROSSI-1", "C-ROSSI-3"])
    assert "ROSSI" not in cid


# ---- features by hand ---------------------------------------------------------------------------


def ring_store() -> MiniStore:
    """R1-R2 share a card, R2-R3 a device, R1-R3 only an address (0.6, below 0.8); S is alone."""
    s = MiniStore()
    s.account("R1", day(0), card=["c1", "c2"], email="e1", address="a1")
    s.account("R2", day(2), card="c1", device="d1", email="e2")
    s.account("R3", day(5), device="d1", email="e3", address="a1")
    s.account("S", day(-400), card="c9", email="e9", address="a1")  # a1: a collective address
    s.coupons += [
        ("W1", "R1", "welcome", day(0), "O1", day(1)),
        ("W2", "R2", "welcome", day(2), "O3", day(3)),
        ("W3", "R3", "welcome", day(5), None, None),
        ("P1", "S", "promo", day(1), "O4", day(4)),
    ]
    s.orders += [
        Order(
            "O1",
            "R1",
            day(1),
            day(3),
            (Line("L1", returned="changed_mind", returned_at=day(6)), Line("L2")),
            coupon="W1",
        ),
        Order(
            "O3",
            "R2",
            day(3),
            day(5),
            (Line("L3", returned="not_as_described", returned_at=day(7)),),
            coupon="W2",
        ),
        Order("O5", "R3", day(28), None, (Line("L5"),)),  # not delivered yet
        Order("O4", "S", day(4), None, (Line("L4"),), coupon="P1"),
    ]
    return s


@pytest.fixture
def ring(tmp_path: Path) -> StoreSnapshot:
    return load_snapshot(ring_store().write(tmp_path), AS_OF)


def rows(clusters: pl.DataFrame) -> dict[str, dict[str, object]]:
    return {r["customer_id"]: r for r in clusters.iter_rows(named=True)}


def test_cluster_features_by_hand(ring: StoreSnapshot) -> None:
    result = build_graph(ring)
    got = rows(result.clusters)
    assert set(got) == {"R1", "R2", "R3", "S"}
    r1 = got["R1"]
    assert r1["cluster_id"] == cluster_id(["R1", "R2", "R3"])
    assert all(got[c]["cluster_id"] == r1["cluster_id"] for c in ("R2", "R3"))
    assert r1["cluster_size"] == 3
    assert r1["n_distinct_cards"] == 2  # c1 and c2
    assert r1["shared_strong_ids"] == 2  # c1 and d1; a1 is shared but not strong
    assert r1["strong_id_share"] == pytest.approx(2 / 3)
    assert r1["creation_spread_days"] == pytest.approx(5.0)
    assert r1["welcome_coupons"] == 2  # W3 was issued, never redeemed
    assert r1["welcome_coupons_per_account"] == pytest.approx(2 / 3)
    # L1 returned, L2 kept; L3 went back on the guarantee path; L5 is not delivered yet.
    assert r1["cluster_return_rate"] == pytest.approx(0.5)
    assert r1["as_of"] == AS_OF
    s = got["S"]
    assert s["cluster_id"] is None
    assert s["cluster_size"] == 1
    assert (s["shared_strong_ids"], s["strong_id_share"], s["welcome_coupons"]) == (0, 0.0, 0)
    assert s["n_distinct_cards"] == 1
    assert s["creation_spread_days"] == 0.0
    assert s["cluster_return_rate"] is None  # no delivered line
    assert validate_table("features/clusters", clusters_table(result.clusters)) == []


def test_weak_links_never_join_a_cluster(ring: StoreSnapshot) -> None:
    result = build_graph(ring)
    links = {(r["customer_a"], r["customer_b"]): r for r in result.links.iter_rows(named=True)}
    assert links[("R1", "S")]["weight"] == pytest.approx(0.6)  # the collective address only
    assert rows(result.clusters)["S"]["cluster_id"] is None


def test_customer_value_does_not_move_any_feature(tmp_path: Path) -> None:
    """Rule 6: prices and amounts never feed the cluster features."""
    base = ring_store()
    dear = ring_store()
    dear.orders = [
        Order(
            o.order_id,
            o.customer,
            o.placed,
            o.delivered,
            tuple(Line(x.line_id, x.price_cents * 37, x.returned, x.returned_at) for x in o.lines),
            o.coupon,
        )
        for o in base.orders
    ]
    a = build_graph(load_snapshot(base.write(tmp_path / "a"), AS_OF))
    b = build_graph(load_snapshot(dear.write(tmp_path / "b"), AS_OF))
    assert a.clusters.equals(b.clusters)
    assert a.links.equals(b.links)


def test_no_forbidden_feature_in_the_graph_outputs() -> None:
    """FAIR-01: no gender, country, age, name, hash or raw identifier among the outputs."""
    assert forbidden_features(CLUSTER_FEATURES) == []
    assert forbidden_features(LINK_FEATURES) == []
    assert forbidden_features(SCHEMAS["features/clusters"].names) == []
    assert forbidden_features(SCHEMAS["features/links"].names) == []
    assert set(CLUSTER_FEATURES) <= set(SCHEMAS["features/clusters"].names)
    assert set(LINK_FEATURES) <= set(SCHEMAS["features/links"].names)
    value_words = ("value", "spend", "price", "eur", "margin", "amount")
    assert not [f for f in (*CLUSTER_FEATURES, *LINK_FEATURES) if any(w in f for w in value_words)]


# ---- disputes (US-310) --------------------------------------------------------------------------


def test_a_disputed_link_splits_its_cluster(ring: StoreSnapshot) -> None:
    before = build_graph(ring)
    outcome = dispute_link(before, ring, "R2", "R1")
    assert outcome.affected == ("R1", "R2", "R3")
    links = outcome.result.links.filter(pl.col("disputed"))
    assert links.select("customer_a", "customer_b").rows() == [("R1", "R2")]
    after = rows(outcome.result.clusters)
    assert after["R1"]["cluster_id"] is None  # only the weak address is left
    assert after["R2"]["cluster_id"] == after["R3"]["cluster_id"] == cluster_id(["R2", "R3"])
    assert after["R2"]["cluster_size"] == 2
    assert outcome.before["cluster_size"].to_list() == [3, 3, 3]
    assert outcome.after["cluster_size"].to_list() == [1, 2, 2]
    assert rows(outcome.result.clusters)["S"] == rows(before.clusters)["S"]
    # The same as a full rebuild that knows the dispute.
    full = build_graph(ring, disputed=[("R1", "R2")])
    assert outcome.result.links.equals(full.links)
    assert outcome.result.clusters.equals(full.clusters)
    # Disputing it again changes nothing.
    again = dispute_link(outcome.result, ring, "R1", "R2")
    assert again.result.clusters.equals(outcome.result.clusters)


def test_disputing_a_weak_link_flags_it_only(ring: StoreSnapshot) -> None:
    before = build_graph(ring)
    outcome = dispute_link(before, ring, "R1", "S")
    assert outcome.result.clusters.equals(before.clusters)
    assert outcome.result.links.filter(pl.col("disputed")).height == 1


def test_disputing_a_missing_link_is_an_error(ring: StoreSnapshot) -> None:
    result = build_graph(ring)
    with pytest.raises(KeyError):
        dispute_link(result, ring, "R2", "S")
    with pytest.raises(ValueError):
        dispute_link(result, ring, "R1", "R1")


def test_cluster_edges_skip_disputed_and_weak_links(ring: StoreSnapshot) -> None:
    result = build_graph(ring, disputed=[("R2", "R3")])
    got = cluster_edges(result.links, GraphParams()).select("customer_a", "customer_b").rows()
    assert got == [("R1", "R2")]
    decayed = cluster_edges(result.links, GraphParams(weight_column="weight_decayed"))
    assert decayed.height <= len(got)
