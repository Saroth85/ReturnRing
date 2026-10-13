"""US-203, spec 8.3: features/links, checked against hand calculations and a brute-force reference.

Every number here is a made-up test fixture (nature: synthetic).
"""

from __future__ import annotations

import itertools
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from contracts.io import validate_table
from contracts.parquet import IDENTIFIER_KINDS
from pipeline.graph.links import build_links, hub_values, links_table
from pipeline.graph.weights import (
    KIND_WEIGHTS,
    GraphParams,
    decayed_kind_weight,
    link_weight,
    prunable_kinds,
)

AS_OF = datetime(2026, 10, 1, tzinfo=UTC)
Row = tuple[str, str, str, datetime]  # customer, kind, value, first use


def ago(days: float) -> datetime:
    return AS_OF - timedelta(days=days)


def frame(rows: Iterable[Row]) -> pl.DataFrame:
    return pl.DataFrame(
        list(rows),
        schema={
            "customer_id": pl.String,
            "kind": pl.String,
            "value_hash": pl.String,
            "used_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )


@dataclass(frozen=True)
class Ref:
    kinds: list[str]
    weight: float
    weight_decayed: float
    first_shared_at: datetime
    last_shared_at: datetime
    n_shared_events: int
    max_sharers: int
    values: tuple[tuple[str, str, int], ...]  # (kind, value, d)


def reference(
    rows: Iterable[Row], as_of: datetime = AS_OF, tau: float = 180.0
) -> dict[tuple[str, str], Ref]:
    """Brute force: every pair that shares any value, straight from the formulas of 8.3."""
    users: dict[tuple[str, str], dict[str, datetime]] = defaultdict(dict)
    for c, k, v, t in rows:
        assert t <= as_of
        users[(k, v)][c] = min(t, users[(k, v)].get(c, t))
    shared: dict[tuple[str, str], list[tuple[str, str, datetime, int]]] = defaultdict(list)
    for (k, v), who in users.items():
        for a, b in itertools.combinations(sorted(who), 2):
            shared[(a, b)].append((k, v, max(who[a], who[b]), len(who)))
    out: dict[tuple[str, str], Ref] = {}
    for pair, values in shared.items():
        kinds = sorted({k for k, _, _, _ in values}, key=IDENTIFIER_KINDS.index)
        best: dict[str, float] = {}
        for k, _, s, d in values:
            age = (as_of - s) / timedelta(days=1)
            best[k] = max(best.get(k, 0.0), decayed_kind_weight(k, age, d, tau))
        weight = link_weight(KIND_WEIGHTS[k] for k in kinds)
        out[pair] = Ref(
            kinds=kinds,
            weight=weight,
            weight_decayed=min(weight, link_weight(best.values())),
            first_shared_at=min(s for _, _, s, _ in values),
            last_shared_at=max(s for _, _, s, _ in values),
            n_shared_events=len(values),
            max_sharers=max(d for _, _, _, d in values),
            values=tuple(sorted((k, v, d) for k, v, _, d in values)),
        )
    return out


def as_dict(links: pl.DataFrame) -> dict[tuple[str, str], dict[str, object]]:
    return {(r["customer_a"], r["customer_b"]): r for r in links.iter_rows(named=True)}


def assert_matches(row: dict[str, object], ref: Ref) -> None:
    assert row["kinds"] == ref.kinds
    assert row["weight"] == pytest.approx(ref.weight, abs=1e-12)
    assert row["weight_decayed"] == pytest.approx(ref.weight_decayed, abs=1e-12)
    assert row["first_shared_at"] == ref.first_shared_at
    assert row["last_shared_at"] == ref.last_shared_at
    assert row["n_shared_events"] == ref.n_shared_events
    assert row["max_sharers"] == ref.max_sharers


# ---- formulas by hand ---------------------------------------------------------------------------


def test_one_pair_by_hand() -> None:
    """A and B share a card (2 sharers, 10 days ago) and an address (3 sharers, 100 days ago)."""
    rows = [
        ("A", "card", "c1", ago(30)),
        ("B", "card", "c1", ago(10)),  # B came to share the card 10 days ago
        ("A", "address", "a1", ago(200)),
        ("B", "address", "a1", ago(100)),
        ("C", "address", "a1", ago(150)),
        ("C", "email", "e9", ago(5)),
    ]
    links = as_dict(build_links(frame(rows), AS_OF))
    assert set(links) == {("A", "B"), ("A", "C"), ("B", "C")}
    ab = links[("A", "B")]
    assert ab["kinds"] == ["card", "address"]  # strongest first, each kind once
    assert ab["weight"] == 1.0
    card = 1.0 * math.exp(-10 / 180)
    address = 0.6 * math.exp(-100 / 180) / math.log2(3)
    assert ab["weight_decayed"] == pytest.approx(1 - (1 - card) * (1 - address), abs=1e-12)
    assert ab["first_shared_at"] == ago(100)
    assert ab["last_shared_at"] == ago(10)
    assert ab["n_shared_events"] == 2
    assert ab["max_sharers"] == 3
    assert ab["disputed"] is False
    assert ab["as_of"] == AS_OF
    ac = links[("A", "C")]
    assert ac["kinds"] == ["address"]
    assert ac["weight"] == pytest.approx(0.6)
    assert ac["weight_decayed"] == pytest.approx(0.6 * math.exp(-150 / 180) / math.log2(3))
    assert ac["first_shared_at"] == ac["last_shared_at"] == ago(150)


def test_two_values_of_one_kind_count_once_in_the_weight() -> None:
    """Kinds enter w_ij once; the strongest decayed value of a kind is its w_k*."""
    rows = [
        ("A", "ip_prefix", "p1", ago(400)),
        ("B", "ip_prefix", "p1", ago(400)),
        ("A", "ip_prefix", "p2", ago(1)),
        ("B", "ip_prefix", "p2", ago(1)),
        ("C", "ip_prefix", "p2", ago(1)),
    ]
    ab = as_dict(build_links(frame(rows), AS_OF))[("A", "B")]
    assert ab["kinds"] == ["ip_prefix"]
    assert ab["weight"] == pytest.approx(0.2)
    newer = 0.2 * math.exp(-1 / 180) / math.log2(3)
    older = 0.2 * math.exp(-400 / 180)
    assert ab["weight_decayed"] == pytest.approx(max(newer, older), abs=1e-12)
    assert ab["n_shared_events"] == 2
    assert ab["max_sharers"] == 3


def test_matches_the_brute_force_reference_without_pruning() -> None:
    rows = random_rows(np.random.default_rng(1), customers=30, values=40)
    links = build_links(frame(rows), AS_OF, GraphParams(hub_degree=None))
    ref = reference(rows)
    got = as_dict(links)
    assert set(got) == set(ref)
    for pair, row in got.items():
        assert_matches(row, ref[pair])


def test_rows_are_sorted_and_keyed_a_before_b() -> None:
    rows = random_rows(np.random.default_rng(2), customers=25, values=30)
    links = build_links(frame(rows), AS_OF)
    keys = list(zip(links["customer_a"], links["customer_b"], strict=True))
    assert keys == sorted(keys)
    assert all(a < b for a, b in keys)


def test_the_table_passes_its_contract() -> None:
    rows = random_rows(np.random.default_rng(3), customers=25, values=30)
    table = links_table(build_links(frame(rows), AS_OF, disputed=[("C-002", "C-001")]))
    assert validate_table("features/links", table) == []
    assert table.num_rows > 0


def test_an_empty_store_gives_an_empty_valid_table() -> None:
    table = links_table(build_links(frame([]), AS_OF))
    assert table.num_rows == 0
    assert validate_table("features/links", table) == []


def test_disputed_pairs_are_flagged_in_either_order() -> None:
    rows = [("A", "card", "c", ago(1)), ("B", "card", "c", ago(1)), ("C", "card", "c", ago(1))]
    links = as_dict(build_links(frame(rows), AS_OF, disputed=[("B", "A")]))
    assert links[("A", "B")]["disputed"] is True
    assert links[("A", "C")]["disputed"] is False


# ---- rule 7 -----------------------------------------------------------------------------------


def test_a_use_after_the_decision_time_is_refused() -> None:
    rows = [("A", "card", "c", ago(1)), ("B", "card", "c", AS_OF + timedelta(seconds=1))]
    with pytest.raises(ValueError, match="rule 7"):
        build_links(frame(rows), AS_OF)


def test_a_naive_decision_time_is_refused() -> None:
    with pytest.raises(ValueError):
        build_links(frame([]), datetime(2026, 10, 1))


def test_a_use_exactly_at_the_decision_time_counts_with_no_decay() -> None:
    rows = [("A", "device", "d", AS_OF), ("B", "device", "d", AS_OF)]
    ab = as_dict(build_links(frame(rows), AS_OF))[("A", "B")]
    assert ab["weight_decayed"] == pytest.approx(0.8)


# ---- hub pruning --------------------------------------------------------------------------------


def pickup_rows(sharers: int) -> list[Row]:
    """A pickup point of `sharers` accounts; A and B also share a device; C and D also share a
    carrier prefix that is itself a hub."""
    rows: list[Row] = [(f"P{i:03d}", "address", "pickup", ago(i + 1)) for i in range(sharers)]
    rows += [("A", "address", "pickup", ago(3)), ("B", "address", "pickup", ago(2))]
    rows += [("A", "device", "dev", ago(50)), ("B", "device", "dev", ago(40))]
    rows += [("C", "address", "pickup", ago(5)), ("D", "address", "pickup", ago(4))]
    rows += [(f"M{i:03d}", "ip_prefix", "mobile", ago(9)) for i in range(sharers)]
    rows += [("C", "ip_prefix", "mobile", ago(8)), ("D", "ip_prefix", "mobile", ago(7))]
    return rows


def test_a_hub_creates_no_pair_alone_but_counts_in_every_link() -> None:
    rows = pickup_rows(30)
    links = as_dict(build_links(frame(rows), AS_OF, GraphParams(hub_degree=20)))
    ref = reference(rows)
    # Only A-B (a device besides the hub) and C-D (two hubs) are candidate pairs.
    assert set(links) == {("A", "B"), ("C", "D")}
    ab = links[("A", "B")]
    assert ab["kinds"] == ["device", "address"]
    assert ab["max_sharers"] == 34  # the hub's real degree, counted before pruning
    pickup = 0.6 * math.exp(-2 / 180) / math.log2(34)
    device = 0.8 * math.exp(-40 / 180)
    assert ab["weight_decayed"] == pytest.approx(1 - (1 - pickup) * (1 - device), abs=1e-12)
    assert_matches(ab, ref[("A", "B")])
    assert_matches(links[("C", "D")], ref[("C", "D")])
    # Every pair left out shares one hub value and nothing else: its weight is below 0.8.
    for pair in set(ref) - set(links):
        assert len(ref[pair].values) == 1
        assert ref[pair].weight < 0.8


def test_hub_values_lists_the_pruned_values() -> None:
    hubs = hub_values(frame(pickup_rows(30)), GraphParams(hub_degree=20))
    assert sorted(zip(hubs["kind"], hubs["sharers"], strict=True)) == [
        ("address", 34),
        ("ip_prefix", 32),
    ]
    assert sorted(hubs["pairs"].to_list()) == [32 * 31 // 2, 34 * 33 // 2]
    assert hub_values(frame(pickup_rows(30)), GraphParams(hub_degree=None)).height == 0


def test_a_strong_identifier_is_never_pruned() -> None:
    """A card shared by 25 accounts links them all: one card alone makes a cluster link."""
    rows = [(f"K{i:02d}", "card", "farm", ago(3)) for i in range(25)]
    links = build_links(frame(rows), AS_OF, GraphParams(hub_degree=20))
    assert links.height == 25 * 24 // 2
    assert (links["weight"] == 1.0).all()


def test_pruning_follows_the_threshold() -> None:
    """At threshold 0.6 an address alone links, so address hubs are kept."""
    rows = pickup_rows(30)
    low = build_links(frame(rows), AS_OF, GraphParams(threshold=0.6, hub_degree=20))
    pickup = 34 * 33 // 2
    assert low.filter(pl.col("kinds").list.contains("address")).height == pickup
    assert "ip_prefix" in prunable_kinds(0.6)


@pytest.mark.parametrize("seed", range(6))
def test_pruning_is_exact_on_random_stores(seed: int) -> None:
    """Pruned links equal the brute-force links on every pair kept; a pair is left out only if
    its one shared value is a hub, so its weight stays below the threshold."""
    rows = random_rows(np.random.default_rng(seed), customers=60, values=70)
    params = GraphParams(hub_degree=4)
    links = as_dict(build_links(frame(rows), AS_OF, params))
    ref = reference(rows)
    assert set(links) <= set(ref)
    for pair, row in links.items():
        assert_matches(row, ref[pair])
    degree = Counter((k, v) for _, k, v, _ in rows)
    prunable = prunable_kinds(params.threshold)
    hubs = {kv for kv, d in degree.items() if d > 4 and kv[0] in prunable}
    dropped = set(ref) - set(links)
    for pair in dropped:
        ((kind, value, _),) = ref[pair].values
        assert (kind, value) in hubs
        assert ref[pair].weight < params.threshold
    for pair in links:  # a pair kept shares a value that is not a hub, or two values
        values = ref[pair].values
        assert len(values) > 1 or values[0][:2] not in hubs
    assert dropped, "the random store should exercise pruning"


def random_rows(rng: np.random.Generator, customers: int, values: int) -> list[Row]:
    """Values of every kind with 1 to 12 sharers, first used at random instants before AS_OF."""
    ids = [f"C-{i:03d}" for i in range(customers)]
    rows: dict[tuple[str, str, str], datetime] = {}
    for v in range(values):
        kind = IDENTIFIER_KINDS[int(rng.integers(0, len(IDENTIFIER_KINDS)))]
        sharers = int(rng.choice([1, 2, 2, 3, 4, 6, 9, 12]))
        for c in rng.choice(customers, size=min(sharers, customers), replace=False):
            at = AS_OF - timedelta(minutes=int(rng.integers(0, 600 * 24 * 60)))
            rows[(ids[int(c)], kind, f"{kind}-{v}")] = at
    return [(c, k, v, at) for (c, k, v), at in sorted(rows.items())]
