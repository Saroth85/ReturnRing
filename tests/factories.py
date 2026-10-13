"""Small builders shared by the tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from policy.engine import NAVIGLI_BASE_TERMS, Thresholds
from policy.types import CaseScores, EvidenceQuality, ReturnReason, ReturnRequest, ReturnTerms

BASE = NAVIGLI_BASE_TERMS
THRESHOLDS = Thresholds(risk=0.5, value_eur=Decimal("150"))
TODAY = date(2026, 10, 14)


def request(
    reason: ReturnReason = ReturnReason.CHANGED_MIND,
    terms: ReturnTerms = BASE,
    notice_on: date = TODAY,
) -> ReturnRequest:
    return ReturnRequest(
        return_id="RET-1",
        order_id="ORD-1",
        customer_id="C-1",
        reason=reason,
        delivered_on=date(2026, 10, 8),
        notice_on=notice_on,
        order_terms=terms,
    )


def scores(
    risk: float = 0.1,
    value: str = "50",
    evidence: EvidenceQuality = EvidenceQuality.SUFFICIENT,
    cluster: str | None = None,
) -> CaseScores:
    return CaseScores(
        risk=risk, value_eur=Decimal(value), evidence_quality=evidence, cluster_id=cluster
    )
