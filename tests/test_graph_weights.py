"""US-203, spec 8.3: identifier weights and the link formulas, checked against hand calculations.

Every number here is a made-up test value (nature: synthetic).
"""

from __future__ import annotations

import math

import pytest

from contracts.parquet import IDENTIFIER_KINDS
from pipeline.graph.weights import (
    CLUSTER_THRESHOLD,
    KIND_WEIGHTS,
    MAX_CLUSTER_SIZE,
    STRONG_KINDS,
    TAU_DAYS,
    GraphParams,
    decayed_kind_weight,
    link_weight,
    prunable_kinds,
)


def test_kind_weights_are_the_spec_table() -> None:
    assert dict(KIND_WEIGHTS) == {
        "card": 1.0,
        "email": 0.9,
        "device": 0.8,
        "address": 0.6,
        "phone": 0.6,
        "ip_prefix": 0.2,
    }
    assert set(KIND_WEIGHTS) == set(IDENTIFIER_KINDS)
    assert TAU_DAYS == 180.0
    assert CLUSTER_THRESHOLD == 0.8
    assert MAX_CLUSTER_SIZE == 20


def test_strong_kinds_reach_the_cluster_threshold_alone() -> None:
    assert STRONG_KINDS == ("card", "email", "device")
    assert all(KIND_WEIGHTS[k] >= CLUSTER_THRESHOLD for k in STRONG_KINDS)
    assert prunable_kinds(0.8) == ("address", "phone", "ip_prefix")
    assert prunable_kinds(0.6) == ("ip_prefix",)  # address and phone link alone at 0.6
    assert prunable_kinds(0.1) == ()


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        ((), 0.0),
        (("card",), 1.0),
        (("device",), 0.8),
        (("ip_prefix",), 0.2),
        (("device", "address"), 1 - 0.2 * 0.4),  # 0.92
        (("address", "ip_prefix"), 1 - 0.4 * 0.8),  # 0.68: a pickup point and a carrier prefix
        (("address", "phone"), 1 - 0.4 * 0.4),  # 0.84
        (("device", "address", "ip_prefix"), 1 - 0.2 * 0.4 * 0.8),  # 0.936: the Rossi flat
        (("card", "address"), 1.0),
    ],
)
def test_link_weight_by_hand(kinds: tuple[str, ...], expected: float) -> None:
    assert link_weight(KIND_WEIGHTS[k] for k in kinds) == pytest.approx(expected, abs=1e-12)


def test_link_weight_rejects_values_outside_0_1() -> None:
    with pytest.raises(ValueError):
        link_weight([1.2])
    with pytest.raises(ValueError):
        link_weight([-0.1])


def test_two_accounts_keep_the_full_weight() -> None:
    """d_k = 2: 1/log2(2) = 1, so a fresh link between two accounts keeps w_k (spec 8.3)."""
    for kind, w in KIND_WEIGHTS.items():
        assert decayed_kind_weight(kind, 0.0, 2) == pytest.approx(w, abs=1e-15)


def test_a_pickup_point_falls_to_about_an_eighth() -> None:
    """Spec 8.3: an address shared by 200 customers keeps 1/log2(200) = 0.131 of its weight."""
    got = decayed_kind_weight("address", 0.0, 200)
    assert got == pytest.approx(0.6 / math.log2(200), rel=1e-12)
    assert 0.12 < got / 0.6 < 0.14


def test_time_decay_by_hand() -> None:
    assert decayed_kind_weight("card", 180.0, 2) == pytest.approx(math.exp(-1), rel=1e-12)
    assert decayed_kind_weight("device", 90.0, 4) == pytest.approx(
        0.8 * math.exp(-0.5) / 2.0, rel=1e-12
    )
    assert decayed_kind_weight("email", 30.0, 3, tau_days=60.0) == pytest.approx(
        0.9 * math.exp(-0.5) / math.log2(3), rel=1e-12
    )


def test_decay_never_exceeds_the_kind_weight() -> None:
    for kind, w in KIND_WEIGHTS.items():
        for age in (0.0, 1.0, 365.0):
            for d in (2, 3, 50, 10_000):
                assert 0.0 < decayed_kind_weight(kind, age, d) <= w


@pytest.mark.parametrize(("age", "sharers"), [(-1.0, 2), (0.0, 1), (0.0, 0), (math.nan, 2)])
def test_decay_rejects_impossible_inputs(age: float, sharers: int) -> None:
    """A negative age is a use after the decision time (rule 7); one account shares nothing."""
    with pytest.raises(ValueError):
        decayed_kind_weight("card", age, sharers)


def test_decay_rejects_an_unknown_kind() -> None:
    with pytest.raises(KeyError):
        decayed_kind_weight("iban", 0.0, 2)


def test_params_defaults_and_validation() -> None:
    p = GraphParams()
    assert (p.threshold, p.max_cluster_size, p.hub_degree, p.tau_days) == (0.8, 20, 20, 180.0)
    assert p.weight_column == "weight"
    for bad in (
        {"threshold": 0.0},
        {"threshold": 1.5},
        {"max_cluster_size": 1},
        {"hub_degree": 1},
        {"tau_days": 0.0},
        {"weight_column": "kinds"},
    ):
        with pytest.raises(ValueError):
            GraphParams(**bad)
    assert GraphParams(hub_degree=None).hub_degree is None
