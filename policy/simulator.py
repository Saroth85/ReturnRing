"""Economic simulator: net benefit of a policy under explicit assumptions (docs/spec.md 8.10).

The refund owed to the consumer is never a saving, so it has no field here (CLAUDE.md rule 9).
All components are disjoint: no double counting between margin, discounts and refunds.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

CENT = Decimal("0.01")
Nature = Literal["simulated", "realized"]


@dataclass(frozen=True)
class Assumptions:
    period: str
    logistics_returns_avoided: int
    logistics_cost_per_return_eur: Decimal
    undue_discounts_avoided: int
    avg_undue_discount_eur: Decimal
    minutes_saved: Decimal
    hourly_cost_eur: Decimal
    incentives_eur: Decimal
    system_cost_eur: Decimal
    error_cost_eur: Decimal
    abandonment_margin_lost_eur: Decimal
    incremental_margin_eur: Decimal = Decimal("0")
    nature: Nature = "simulated"
    version: str = "assumptions-v1"


@dataclass(frozen=True)
class NetBenefit:
    components: dict[str, Decimal]
    total: Decimal
    period: str
    nature: Nature
    version: str


def _eur(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def net_benefit(a: Assumptions) -> NetBenefit:
    components = {
        "logistics_costs_avoided": _eur(
            a.logistics_returns_avoided * a.logistics_cost_per_return_eur
        ),
        "undue_discounts_avoided": _eur(a.undue_discounts_avoided * a.avg_undue_discount_eur),
        "time_saved_value": _eur(a.minutes_saved / Decimal(60) * a.hourly_cost_eur),
        "incremental_margin": _eur(a.incremental_margin_eur),
        "incentives": -_eur(a.incentives_eur),
        "system_cost": -_eur(a.system_cost_eur),
        "error_cost": -_eur(a.error_cost_eur),
        "abandonment_margin_lost": -_eur(a.abandonment_margin_lost_eur),
    }
    return NetBenefit(
        components=components,
        total=_eur(sum(components.values(), Decimal("0"))),
        period=a.period,
        nature=a.nature,
        version=a.version,
    )


def sensitivity(a: Assumptions) -> dict[str, NetBenefit]:
    """Scenarios, not confidence intervals: the result can change sign and the UI must show it."""
    halved = replace(a, logistics_cost_per_return_eur=a.logistics_cost_per_return_eur / 2)
    return {
        "base": net_benefit(a),
        "logistics_halved": net_benefit(halved),
        "abandonment_doubled": net_benefit(
            replace(a, abandonment_margin_lost_eur=a.abandonment_margin_lost_eur * 2)
        ),
        "both": net_benefit(
            replace(halved, abandonment_margin_lost_eur=a.abandonment_margin_lost_eur * 2)
        ),
    }


# Illustrative example from the proposals document of 12 October 2026. Not a ReturnRing result.
PROPOSAL_EXAMPLE = Assumptions(
    period="month, 1,000 return requests (illustrative)",
    logistics_returns_avoided=80,
    logistics_cost_per_return_eur=Decimal("8"),
    undue_discounts_avoided=25,
    avg_undue_discount_eur=Decimal("12"),
    minutes_saved=Decimal("240"),
    hourly_cost_eur=Decimal("22"),
    incentives_eur=Decimal("200"),
    system_cost_eur=Decimal("120"),
    error_cost_eur=Decimal("100"),
    abandonment_margin_lost_eur=Decimal("240"),
)
