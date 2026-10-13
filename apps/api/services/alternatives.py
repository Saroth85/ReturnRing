"""The admissible actions of a decision version, priced by policy.simulator (US-308, US-602;
rule 9; spec 8.6 "Alternative" and 8.10).

Up to three alternatives: the engine's proposal (recommended, with exactly its levers), then the
ones that fit the case: a size exchange for a size return, a check at the warehouse for a
withdrawal, a report to the catalogue for a guarantee return or a variant with an anomaly, and
the current terms when the proposal changes them. Each one is a net benefit of
policy.simulator.net_benefit on its own quantities, labelled "<decision_id>/<kind>", with the
unit costs of the stored assumption version (Assumptions.derive). The refund owed to the
customer is never a saving: no quantity counts it (rule 9).

The quantities of an alternative over the 12 months after the decision are hypotheses, explicit
and versioned in ALTERNATIVE_ASSUMPTIONS (a change of value is a change of version), computed from
the customer snapshot the engine read: its 12-month returns and kept margin, and the linked
accounts of its cluster. They are simulated figures (nature simulated), never measured.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

from apps.api import schemas as s
from apps.api.services.provenance import provenance
from policy.types import Decision, EvidenceQuality, Path, Quadrant, ReturnReason

CENT: Final = Decimal("0.01")


@dataclass(frozen=True)
class AlternativeAssumptions:
    """Hypotheses of the quantities of each alternative (design values, to calibrate)."""

    version: str
    gross_margin: Decimal  # spec 4: average gross margin of the merchant
    q1_margin_uplift: Decimal  # Q1: extra margin, share of the 12-month kept margin
    q1_incentive_share: Decimal  # Q1: cost of the upgrade, share of that extra margin
    returns_avoided: dict[Quadrant, Decimal]  # Q3, Q4: share of the 12-month returns avoided
    abandonment: dict[Quadrant, Decimal]  # Q3, Q4: share of the kept margin lost to abandonment
    minutes_per_return: Decimal  # handling minutes saved per return avoided
    restriction_error_eur: Decimal  # expected repair cost of a wrong restriction
    system_eur: Decimal  # infrastructure and handling per case
    size_exchange_accept: Decimal  # share of size returns turned into an exchange
    size_exchange_shipping_eur: Decimal  # the outbound parcel of the other size
    verification_minutes: Decimal  # an operator's check at the warehouse
    product_report_minutes: Decimal  # a catalogue report with the evidence of the return


ALTERNATIVE_ASSUMPTIONS: Final = AlternativeAssumptions(
    version="alternatives-v1-2026-10-13",
    gross_margin=Decimal("0.55"),
    q1_margin_uplift=Decimal("0.10"),
    q1_incentive_share=Decimal("0.25"),
    returns_avoided={Quadrant.Q3: Decimal("0.10"), Quadrant.Q4: Decimal("0.25")},
    abandonment={Quadrant.Q3: Decimal("0.10"), Quadrant.Q4: Decimal("0.30")},
    minutes_per_return=Decimal("6"),
    restriction_error_eur=Decimal("6.00"),
    system_eur=Decimal("0.50"),
    size_exchange_accept=Decimal("0.5"),
    size_exchange_shipping_eur=Decimal("4.00"),
    verification_minutes=Decimal("12"),
    product_report_minutes=Decimal("20"),
)

PROPOSAL_SUMMARIES: Final[dict[str, str]] = {
    "Q1": "Q1 upgrade for future orders: 60-day window and priority size exchange",
    "Q2": "Standard terms: no change for future orders",
    "Q3": "Q3 terms for future orders: no instant refund and a return-rate notice, until the "
    "review",
    "Q4": "Q4 terms for future orders: 14-day window, paid return shipping, no instant refund "
    "and coupons paused, until the review",
    "Q4_cluster": "Q4 terms for future orders and coupons paused for the cluster, until the review",
    "insufficient": "Standard terms: the evidence is insufficient (US-307)",
    "guarantee": "Legal guarantee: free return, then repair, replacement or refund (rule 4)",
}


@dataclass(frozen=True)
class CaseFacts:
    """What the quantities read: the customer snapshot the engine was given (12 months)."""

    customer_id: str
    returns_12m: int
    kept_eur_12m: Decimal
    cluster_id: str | None
    cluster_size: int  # 1 without a cluster
    item_price_eur: Decimal
    reason: ReturnReason
    variant_anomaly: bool


def _eur(x: Decimal) -> float:
    return float(x.quantize(CENT, rounding=ROUND_HALF_UP))


def _minutes_cost(minutes: Decimal, stored: s.Assumptions) -> float:
    return _eur(minutes / Decimal(60) * Decimal(str(stored.hourly_cost_eur)))


def proposal_quantities(
    decision: Decision, facts: CaseFacts, a: AlternativeAssumptions = ALTERNATIVE_ASSUMPTIONS
) -> dict[str, Any]:
    q = decision.quadrant
    system = {"system_cost_eur": _eur(a.system_eur)}
    kept_margin = a.gross_margin * facts.kept_eur_12m
    if q is Quadrant.Q1:
        uplift = a.q1_margin_uplift * kept_margin
        return {
            "incremental_margin_eur": _eur(max(uplift, Decimal(0))),
            "incentives_eur": _eur(max(uplift * a.q1_incentive_share, Decimal(0))),
            **system,
        }
    if q in (Quadrant.Q3, Quadrant.Q4):
        avoided = int(
            (a.returns_avoided[q] * facts.returns_12m).to_integral_value(rounding=ROUND_HALF_UP)
        )
        clustered = q is Quadrant.Q4 and decision.levers.cluster_coupons_disabled
        return {
            "logistics_returns_avoided": avoided,
            "undue_discounts_avoided": facts.cluster_size - 1 if clustered else 0,
            "minutes_saved": float(a.minutes_per_return * avoided),
            "error_cost_eur": _eur(a.restriction_error_eur),
            "abandonment_margin_lost_eur": _eur(max(a.abandonment[q] * kept_margin, Decimal(0))),
            **system,
        }
    return system


def _proposal_summary(decision: Decision) -> str:
    if decision.path is Path.GUARANTEE:
        return PROPOSAL_SUMMARIES["guarantee"]
    if decision.evidence_quality is EvidenceQuality.INSUFFICIENT or decision.quadrant is None:
        return PROPOSAL_SUMMARIES["insufficient"]
    if decision.quadrant is Quadrant.Q4 and decision.levers.cluster_coupons_disabled:
        return PROPOSAL_SUMMARIES["Q4_cluster"]
    return PROPOSAL_SUMMARIES[decision.quadrant.value]


def alternatives(
    decision_id: str,
    decision: Decision,
    proposed_levers: list[s.Lever],
    facts: CaseFacts,
    stored: s.Assumptions,
    a: AlternativeAssumptions = ALTERNATIVE_ASSUMPTIONS,
) -> list[s.Alternative]:
    """The alternatives of a version of `decision` (the engine's proposal), at most three."""
    whom = (
        f"cluster {facts.cluster_id} ({facts.cluster_size} linked accounts)"
        if facts.cluster_id is not None and facts.cluster_size > 1
        else f"customer {facts.customer_id}"
    )
    period = f"12 months after the decision, {whom}"
    start = decision.decided_on
    prov = provenance(
        f"economic simulator (policy.simulator), unit costs of {stored.version}; quantities of "
        f"{a.version} (hypotheses) on the customer snapshot of the decision",
        start,
        start + timedelta(days=364),
        "simulated",
    )

    def make(
        kind: s.AlternativeKind,
        summary: str,
        quantities: dict[str, Any],
        levers: list[s.Lever] | None = None,
        recommended: bool = False,
    ) -> s.Alternative:
        label = s.alternative_label(decision_id, kind)
        view = s.NetBenefitView.build(stored.derive(label, period, **quantities), prov)
        return s.Alternative(
            kind=kind,
            summary=summary,
            recommended=recommended,
            levers=levers or [],
            net_benefit=view,
        )

    out = [
        make(
            "proposal",
            _proposal_summary(decision),
            proposal_quantities(decision, facts, a),
            proposed_levers,
            recommended=True,
        )
    ]
    others: list[s.Alternative] = []
    guarantee = decision.path is Path.GUARANTEE
    if not guarantee and facts.reason is ReturnReason.SIZE:
        margin = a.size_exchange_accept * a.gross_margin * facts.item_price_eur
        others.append(
            make(
                "size_exchange",
                "Offer another size of the same item instead of a refund",
                {
                    "incremental_margin_eur": _eur(margin),
                    "incentives_eur": _eur(a.size_exchange_shipping_eur),
                    "system_cost_eur": _eur(a.system_eur),
                },
            )
        )
    if guarantee or facts.variant_anomaly:
        others.append(
            make(
                "product_report",
                "Report the item to the catalogue team: a product check, not a customer matter",
                {"system_cost_eur": _minutes_cost(a.product_report_minutes, stored)},
            )
        )
    if not guarantee and not others:
        others.append(
            make(
                "verification",
                "Check the item at the warehouse before closing the return",
                {"system_cost_eur": _minutes_cost(a.verification_minutes, stored)},
            )
        )
    if proposed_levers:
        others = others[:1]
        others.append(make("current_terms", "Keep the current terms", {}))
    return [*out, *others][:3]
