"""Domain types shared by the policy engine, the legal guard and the API.

Spec references: docs/spec.md sections 8.6 (matrix and levers) and 3 (legal framework).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from contracts.levers import LEVERS, LeverObject, format_lever_id, make_lever


class ReturnReason(StrEnum):
    SIZE = "size"
    CHANGED_MIND = "changed_mind"
    NOT_AS_EXPECTED = "not_as_expected"
    DEFECTIVE = "defective"
    DAMAGED_IN_SHIPPING = "damaged_in_shipping"
    WRONG_ITEM = "wrong_item"
    NOT_AS_DESCRIBED = "not_as_described"
    OTHER = "other"


# Non-conformity follows the legal guarantee (Directive (EU) 2019/771), never the abuse scoring.
NON_CONFORMITY_REASONS: frozenset[ReturnReason] = frozenset(
    {
        ReturnReason.DEFECTIVE,
        ReturnReason.DAMAGED_IN_SHIPPING,
        ReturnReason.WRONG_ITEM,
        ReturnReason.NOT_AS_DESCRIBED,
    }
)


class Path(StrEnum):
    WITHDRAWAL = "withdrawal"
    GUARANTEE = "guarantee"


class EvidenceQuality(StrEnum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"


class Quadrant(StrEnum):
    Q1 = "Q1"  # high value, low risk
    Q2 = "Q2"  # low value, low risk
    Q3 = "Q3"  # high value, high risk
    Q4 = "Q4"  # low value, high risk


class DecisionState(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ReturnTerms:
    """Return terms offered on an order: shown at checkout and stored as a snapshot."""

    return_window_days: int
    free_return_shipping: bool
    instant_refund: bool
    free_size_exchange: bool
    coupons_enabled: bool = True

    def is_less_generous_than(self, other: ReturnTerms) -> bool:
        """True if any dimension of these terms is worse for the customer than `other`."""
        return (
            self.return_window_days < other.return_window_days
            or (other.free_return_shipping and not self.free_return_shipping)
            or (other.instant_refund and not self.instant_refund)
            or (other.free_size_exchange and not self.free_size_exchange)
            or (other.coupons_enabled and not self.coupons_enabled)
        )

    def most_generous(self, other: ReturnTerms) -> ReturnTerms:
        """Term by term, the better of the two for the customer."""
        return ReturnTerms(
            return_window_days=max(self.return_window_days, other.return_window_days),
            free_return_shipping=self.free_return_shipping or other.free_return_shipping,
            instant_refund=self.instant_refund or other.instant_refund,
            free_size_exchange=self.free_size_exchange or other.free_size_exchange,
            coupons_enabled=self.coupons_enabled or other.coupons_enabled,
        )


@dataclass(frozen=True)
class Levers:
    """Policy levers proposed for a customer. Terms apply to future orders only."""

    future_terms: ReturnTerms
    cluster_coupons_disabled: bool
    transparency_notice: bool  # return-rate notice: Q3 and Q4 only, a restriction
    size_advice: bool
    priority_size_exchange: bool = False  # Q1 only: free size exchange "with priority"

    @classmethod
    def standard(cls, base: ReturnTerms) -> Levers:
        """The merchant base terms with the standard services: the reference for differences."""
        return cls(
            future_terms=base,
            cluster_coupons_disabled=False,
            transparency_notice=False,
            size_advice=True,
        )

    def values(self) -> dict[str, int | bool]:
        """Value of every lever, keyed by its id in the lever catalogue (contracts/levers.py)."""
        t = self.future_terms
        return {
            "return_window_days": t.return_window_days,
            "free_return_shipping": t.free_return_shipping,
            "instant_refund": t.instant_refund,
            "free_size_exchange": t.free_size_exchange,
            "coupons_enabled": t.coupons_enabled,
            "cluster_coupons_disabled": self.cluster_coupons_disabled,
            "transparency_notice": self.transparency_notice,
            "size_advice": self.size_advice,
            "priority_size_exchange": self.priority_size_exchange,
        }

    def to_contract(self, base: ReturnTerms) -> list[LeverObject]:
        """The levers that differ from the standard levers on `base`, as {id, value, scope}
        objects in catalogue order (ADR-13): the `levers` of llm_case and decision_request.
        Raises LeverError for a value the catalogue refuses, such as an all_orders lever set to
        false (the engine never restricts the size exchange)."""
        mine, standard = self.values(), Levers.standard(base).values()
        return [
            make_lever(lever.id, mine[lever.id])
            for lever in LEVERS
            if mine[lever.id] != standard[lever.id]
        ]

    def lever_ids(self, base: ReturnTerms) -> list[str]:
        """Stable "id:value" identifiers of the levers that differ from the merchant base policy,
        for example "instant_refund:false"; the decision echo of the LLM must match them."""
        return [format_lever_id(x["id"], x["value"]) for x in self.to_contract(base)]


@dataclass(frozen=True)
class ReturnRequest:
    return_id: str
    order_id: str
    customer_id: str
    reason: ReturnReason
    delivered_on: date
    notice_on: date  # day the customer communicated the return
    order_terms: ReturnTerms  # snapshot stored at purchase (ADR-05)


@dataclass(frozen=True)
class CaseScores:
    risk: float  # calibrated probability in [0, 1]
    value_eur: Decimal  # net margin over 12 months; never feeds the risk score
    evidence_quality: EvidenceQuality
    cluster_id: str | None = None


@dataclass(frozen=True)
class DiminishedValueClaim:
    amount_eur: Decimal
    photo_evidence_ids: tuple[str, ...]
    confirmed_by: str | None  # warehouse operator who confirmed the observation
    rule_id: str | None  # approved rule used to compute the amount


@dataclass(frozen=True)
class CouponRuleApplication:
    rule_version: str | None  # general rule approved and versioned by the manager
    confirmed_by: str | None  # operator who confirmed this specific application


@dataclass(frozen=True)
class Decision:
    return_id: str
    decided_on: date
    path: Path
    evidence_quality: EvidenceQuality
    quadrant: Quadrant | None
    levers: Levers
    # The order snapshot, never restricted retroactively. On the guarantee path no restrictive
    # lever applies (the better of snapshot and base) and return shipping is always free.
    current_return_terms: ReturnTerms
    refund_due_by: date
    requires_approval: bool
    state: DecisionState = DecisionState.PROPOSED
    approver: str | None = None
    review_on: date | None = None
    diminished_value_claim: DiminishedValueClaim | None = None
    coupon_rule: CouponRuleApplication | None = None
    rules_version: str = "policy-2026-10-12"
