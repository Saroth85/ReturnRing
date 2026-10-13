"""Policy engine: path, quadrant and levers for a return request (docs/spec.md section 8.6).

The engine decides; models only estimate and the LLM only explains (CLAUDE.md rule 1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from policy.legal_guard import (
    APPROVAL_QUADRANTS,
    LEGAL_REFUND_DAYS,
    LEGAL_WITHDRAWAL_DAYS,
    check,
    is_restrictive,
)
from policy.types import (
    NON_CONFORMITY_REASONS,
    CaseScores,
    CouponRuleApplication,
    Decision,
    DecisionState,
    EvidenceQuality,
    Levers,
    Path,
    Quadrant,
    ReturnReason,
    ReturnRequest,
    ReturnTerms,
)

REVIEW_DAYS = 90  # design hypothesis, see open question D-15
Q1_WINDOW_DAYS = 60

# Base policy of the demo merchant "Navigli Fashion" (docs/spec.md section 4).
NAVIGLI_BASE_TERMS = ReturnTerms(
    return_window_days=30,
    free_return_shipping=True,
    instant_refund=True,
    free_size_exchange=True,
    coupons_enabled=True,
)


@dataclass(frozen=True)
class Thresholds:
    risk: float  # chosen on validation so that precision >= 0.8 in the high-risk band
    value_eur: Decimal  # 70th percentile of customer value


class PolicyError(ValueError):
    """Raised when an operation would produce an inadmissible decision."""


def choose_path(reason: ReturnReason) -> Path:
    return Path.GUARANTEE if reason in NON_CONFORMITY_REASONS else Path.WITHDRAWAL


def check_scores(scores: CaseScores) -> None:
    """Scores come from ml/: a NaN or out-of-range value is a bug upstream, never a decision."""
    if not (math.isfinite(scores.risk) and 0.0 <= scores.risk <= 1.0):
        raise PolicyError(f"risk must be a probability in [0, 1], got {scores.risk!r}")
    if not scores.value_eur.is_finite():
        raise PolicyError(f"value_eur must be finite, got {scores.value_eur!r}")


def quadrant_for(scores: CaseScores, thresholds: Thresholds) -> Quadrant:
    high_risk = scores.risk >= thresholds.risk
    high_value = scores.value_eur >= thresholds.value_eur
    if high_value:
        return Quadrant.Q3 if high_risk else Quadrant.Q1
    return Quadrant.Q4 if high_risk else Quadrant.Q2


def guarantee_terms(order_terms: ReturnTerms) -> ReturnTerms:
    """Terms of a defect return: the order snapshot, with return shipping always at the seller's
    cost (Directive (EU) 2019/771, art. 14), whatever the withdrawal terms said."""
    return replace(order_terms, free_return_shipping=True)


def standard_levers(base: ReturnTerms) -> Levers:
    return Levers(
        future_terms=base,
        cluster_coupons_disabled=False,
        transparency_notice=False,
        size_advice=True,
    )


def levers_for(quadrant: Quadrant, base: ReturnTerms, has_cluster: bool) -> Levers:
    """Lever table of docs/spec.md section 8.6. Size exchange stays free in every quadrant."""
    if quadrant is Quadrant.Q1:
        terms = replace(
            base,
            return_window_days=max(base.return_window_days, Q1_WINDOW_DAYS),
            free_return_shipping=True,
            instant_refund=True,
            free_size_exchange=True,
        )
        return Levers(
            terms,
            cluster_coupons_disabled=False,
            transparency_notice=False,
            size_advice=True,
            priority_size_exchange=True,
        )
    if quadrant is Quadrant.Q2:
        return standard_levers(base)
    if quadrant is Quadrant.Q3:
        terms = replace(base, instant_refund=False, free_size_exchange=True)
        return Levers(
            terms, cluster_coupons_disabled=False, transparency_notice=True, size_advice=True
        )
    terms = replace(
        base,
        return_window_days=LEGAL_WITHDRAWAL_DAYS,
        free_return_shipping=False,
        instant_refund=False,
        free_size_exchange=True,
        coupons_enabled=False,
    )
    return Levers(
        terms, cluster_coupons_disabled=has_cluster, transparency_notice=True, size_advice=True
    )


def decide(
    request: ReturnRequest,
    scores: CaseScores,
    base: ReturnTerms,
    thresholds: Thresholds,
    decided_on: date,
    coupon_rule_version: str | None = "coupon-rule-v1",
) -> Decision:
    """Propose a decision. Q3, Q4 and any restrictive proposal require human approval."""
    path = choose_path(request.reason)
    refund_due_by = request.notice_on + timedelta(days=LEGAL_REFUND_DAYS)

    if path is Path.GUARANTEE or scores.evidence_quality is EvidenceQuality.INSUFFICIENT:
        decision = Decision(
            return_id=request.return_id,
            decided_on=decided_on,
            path=path,
            evidence_quality=scores.evidence_quality,
            quadrant=None,
            levers=standard_levers(base),
            current_return_terms=(
                guarantee_terms(request.order_terms)
                if path is Path.GUARANTEE
                else request.order_terms
            ),
            refund_due_by=refund_due_by,
            requires_approval=False,
        )
    else:
        check_scores(scores)
        quadrant = quadrant_for(scores, thresholds)
        levers = levers_for(quadrant, base, has_cluster=scores.cluster_id is not None)
        needs_approval = quadrant in APPROVAL_QUADRANTS or is_restrictive(levers, base)
        decision = Decision(
            return_id=request.return_id,
            decided_on=decided_on,
            path=path,
            evidence_quality=scores.evidence_quality,
            quadrant=quadrant,
            levers=levers,
            current_return_terms=request.order_terms,
            refund_due_by=refund_due_by,
            requires_approval=needs_approval,
            review_on=decided_on + timedelta(days=REVIEW_DAYS) if needs_approval else None,
            coupon_rule=(
                CouponRuleApplication(rule_version=coupon_rule_version, confirmed_by=None)
                if levers.cluster_coupons_disabled
                else None
            ),
        )

    violations = check(decision, request, base)
    if violations:
        raise PolicyError("; ".join(f"{x.code}: {x.message}" for x in violations))
    return decision


def approve(
    decision: Decision, request: ReturnRequest, base: ReturnTerms, approver: str
) -> Decision:
    """Record a human approval. The approver also confirms any cluster coupon application."""
    approver = approver.strip()
    if not approver:
        raise PolicyError("An approver is required")
    if decision.state is not DecisionState.PROPOSED:
        raise PolicyError(f"Only a proposed decision can be approved, not a {decision.state} one")
    coupon_rule = decision.coupon_rule
    if coupon_rule is not None:
        coupon_rule = replace(coupon_rule, confirmed_by=approver)
    approved = replace(
        decision, state=DecisionState.APPROVED, approver=approver, coupon_rule=coupon_rule
    )
    violations = check(approved, request, base)
    if violations:
        raise PolicyError("; ".join(f"{x.code}: {x.message}" for x in violations))
    return approved
