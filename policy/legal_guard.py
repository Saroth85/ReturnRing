"""Legal guard: rejects any decision outside the limits of docs/spec.md section 8.6.

Pure functions only. This module must keep 100% test coverage (CLAUDE.md rule 3).
It encodes explicit design constraints; it does not certify legal compliance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from policy.types import (
    NON_CONFORMITY_REASONS,
    Decision,
    DecisionState,
    EvidenceQuality,
    Levers,
    Path,
    Quadrant,
    ReturnRequest,
    ReturnTerms,
)

LEGAL_WITHDRAWAL_DAYS = 14  # Directive 2011/83/EU, art. 9
LEGAL_REFUND_DAYS = 14  # Directive 2011/83/EU, art. 13
MAX_REVIEW_DAYS = 180  # restrictions are never permanent (US-607)
APPROVAL_QUADRANTS = frozenset({Quadrant.Q3, Quadrant.Q4})  # spec 8.6: always a human approval


@dataclass(frozen=True)
class Violation:
    code: str
    message: str


def is_restrictive(levers: Levers, base: ReturnTerms) -> bool:
    """Restrictive: less generous than the merchant base, a cluster coupon block, or the
    return-rate notice, which tells a customer they are treated differently."""
    return (
        levers.future_terms.is_less_generous_than(base)
        or levers.cluster_coupons_disabled
        or levers.transparency_notice
    )


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def check(decision: Decision, request: ReturnRequest, base: ReturnTerms) -> list[Violation]:
    """Return every violation found; an empty list means the decision is admissible."""
    v: list[Violation] = []
    restrictive = is_restrictive(decision.levers, base)

    if decision.levers.future_terms.return_window_days < LEGAL_WITHDRAWAL_DAYS:
        v.append(Violation("WINDOW_BELOW_LEGAL", "Future return window below 14 days"))
    if decision.current_return_terms.return_window_days < LEGAL_WITHDRAWAL_DAYS:
        v.append(Violation("WINDOW_BELOW_LEGAL", "Current return window below 14 days"))

    if decision.refund_due_by > request.notice_on + timedelta(days=LEGAL_REFUND_DAYS):
        v.append(Violation("REFUND_LATE", "Refund due more than 14 days after the notice"))

    if decision.current_return_terms.is_less_generous_than(request.order_terms):
        v.append(
            Violation(
                "RETROACTIVE_RESTRICTION",
                "Terms of the current order are worse than the snapshot shown at purchase",
            )
        )

    if request.reason in NON_CONFORMITY_REASONS and decision.path is not Path.GUARANTEE:
        v.append(Violation("NON_CONFORMITY_ON_WITHDRAWAL_PATH", "Defects must follow guarantee"))
    if decision.path is Path.GUARANTEE and (
        restrictive
        or decision.quadrant is not None
        or decision.current_return_terms.is_less_generous_than(base)
    ):
        v.append(Violation("GUARANTEE_RESTRICTED", "Guarantee path is never scored or restricted"))
    if decision.path is Path.GUARANTEE and not decision.current_return_terms.free_return_shipping:
        v.append(
            Violation(
                "GUARANTEE_SHIPPING_CHARGED",
                "Return shipping of a defect is at the seller's cost (Directive 2019/771, art. 14)",
            )
        )

    # US-307: insufficient evidence means standard terms, never a quadrant or an upgrade.
    if decision.evidence_quality is EvidenceQuality.INSUFFICIENT and (
        restrictive or decision.quadrant is not None or decision.levers.future_terms != base
    ):
        v.append(
            Violation("INSUFFICIENT_EVIDENCE_RESTRICTED", "Insufficient evidence: standard terms")
        )

    needs_approval = restrictive or decision.quadrant in APPROVAL_QUADRANTS
    if needs_approval and not decision.requires_approval:
        v.append(Violation("APPROVAL_MISSING", "Q3, Q4 or restriction not flagged for approval"))
    if needs_approval and decision.state is DecisionState.APPROVED and _blank(decision.approver):
        v.append(Violation("APPROVAL_MISSING", "Decision approved without an approver"))

    if needs_approval:
        if decision.review_on is None:
            v.append(Violation("REVIEW_MISSING", "Restriction without review date"))
        elif not (
            decision.decided_on
            < decision.review_on
            <= decision.decided_on + timedelta(days=MAX_REVIEW_DAYS)
        ):
            v.append(Violation("REVIEW_OUT_OF_RANGE", "Review date outside the allowed range"))

    if decision.levers.cluster_coupons_disabled:
        rule = decision.coupon_rule
        if rule is None or _blank(rule.rule_version):
            v.append(Violation("COUPON_RULE_MISSING", "Cluster coupon block without a rule"))
        else:
            if _blank(rule.cluster_id):
                v.append(
                    Violation("COUPON_CLUSTER_MISSING", "Cluster coupon block without a cluster")
                )
            if decision.state is DecisionState.APPROVED and _blank(rule.confirmed_by):
                v.append(
                    Violation("COUPON_CONFIRMATION_MISSING", "Rule applied without confirmation")
                )

    claim = decision.diminished_value_claim
    if claim is not None:
        if decision.path is Path.GUARANTEE:
            v.append(Violation("CLAIM_ON_GUARANTEE", "No diminished-value claim on a defect"))
        missing = (
            not claim.photo_evidence_ids
            or any(_blank(photo) for photo in claim.photo_evidence_ids)
            or _blank(claim.confirmed_by)
            or _blank(claim.rule_id)
        )
        if missing:
            v.append(Violation("CLAIM_WITHOUT_EVIDENCE", "Claim needs photo, confirmation, rule"))
        if not claim.amount_eur.is_finite() or claim.amount_eur <= Decimal(0):
            v.append(Violation("CLAIM_AMOUNT_INVALID", "Claim amount must be a positive number"))

    return v
