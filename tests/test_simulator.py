"""ECO-01: the economic simulator reproduces the illustrative example of docs/spec.md 8.10."""

from __future__ import annotations

from decimal import Decimal

from policy.simulator import PROPOSAL_EXAMPLE, net_benefit, sensitivity


def test_eco01_base_example_is_368() -> None:
    result = net_benefit(PROPOSAL_EXAMPLE)
    assert result.total == Decimal("368.00")
    assert result.components["time_saved_value"] == Decimal("88.00")
    assert result.nature == "simulated"


def test_eco01_sensitivity_can_change_sign() -> None:
    s = sensitivity(PROPOSAL_EXAMPLE)
    assert s["logistics_halved"].total == Decimal("48.00")
    assert s["abandonment_doubled"].total == Decimal("128.00")
    assert s["both"].total == Decimal("-192.00")


def test_refund_is_never_a_component() -> None:
    names = net_benefit(PROPOSAL_EXAMPLE).components
    assert not any("refund" in name for name in names)


def test_components_sum_to_total() -> None:
    r = net_benefit(PROPOSAL_EXAMPLE)
    assert sum(r.components.values(), Decimal("0")) == r.total
