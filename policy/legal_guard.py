"""Legal guard: rejects any decision outside the limits of docs/spec.md section 8.6.

Pure functions only. This module must keep 100% test coverage (CLAUDE.md rule 3).
It encodes explicit design constraints; it does not certify legal compliance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from policy.types import (
    NON_CONFORMITY_REASONS,
    Decision,
    DecisionState,
    EvidenceQuality,
    Levers,
    Path,
    ReturnRequest,
    ReturnTerms,
)

LEGAL_WITHDRAWAL_DAYS = 14  # Directive 2011/83/EU, art. 9
LEGAL_REFUND_DAYS = 14  # Directive 2011/83/EU, art. 13
MAX_REVIEW_DAYS = 180  # restrictions are never permanent (US-607)


@dataclass(frozen=True)
class Violation:
    code: str
    message: str


def is_restrictive(levers: Levers, base: ReturnTerms) -> bool:
    """A decision is restrictive if it is less generous than the merchant base policy."""
    return levers.future_terms.is_less_generous_than(base) or levers.cluster_coupons_disabled


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
    if decision.path is Path.GUARANTEE and (restrictive or decision.quadrant is not None):
        v.append(Violation("GUARANTEE_RESTRICTED", "Guarantee path is never scored or restricted"))

    if decision.evidence_quality is EvidenceQuality.INSUFFICIENT and restrictive:
        v.append(Violation("INSUFFICIENT_EVIDENCE_RESTRICTED", "No restriction without evidence"))

    if restrictive and not decision.requires_approval:
        v.append(Violation("APPROVAL_MISSING", "Restrictive decision not flagged for approval"))
    if restrictive and decision.state is DecisionState.APPROVED and not decision.approver:
        v.append(Violation("APPROVAL_MISSING", "Restrictive decision approved without approver"))

    if restrictive:
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
        if rule is None or not rule.rule_version:
            v.append(Violation("COUPON_RULE_MISSING", "Cluster coupon block without a rule"))
        elif decision.state is DecisionState.APPROVED and not rule.confirmed_by:
            v.append(Violation("COUPON_CONFIRMATION_MISSING", "Rule applied without confirmation"))

    claim = decision.diminished_value_claim
    if claim is not None:
        if decision.path is Path.GUARANTEE:
            v.append(Violation("CLAIM_ON_GUARANTEE", "No diminished-value claim on a defect"))
        if not claim.photo_evidence_ids or not claim.confirmed_by or not claim.rule_id:
            v.append(Violation("CLAIM_WITHOUT_EVIDENCE", "Claim needs photo, confirmation, rule"))

    return v
