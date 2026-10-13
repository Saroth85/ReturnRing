"""LEG-01 .. LEG-09: the legal guard (docs/spec.md sections 8.6 and 12)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from policy.engine import decide
from policy.legal_guard import check
from policy.types import (
    CouponRuleApplication,
    Decision,
    DecisionState,
    DiminishedValueClaim,
    EvidenceQuality,
    Path,
    Quadrant,
    ReturnReason,
    ReturnTerms,
)
from tests.factories import BASE, THRESHOLDS, TODAY, request, scores

terms_st = st.builds(
    ReturnTerms,
    return_window_days=st.integers(min_value=14, max_value=90),
    free_return_shipping=st.booleans(),
    instant_refund=st.booleans(),
    free_size_exchange=st.booleans(),
    coupons_enabled=st.booleans(),
)


@settings(max_examples=10_000, deadline=None)
@given(
    reason=st.sampled_from(list(ReturnReason)),
    risk=st.floats(min_value=0.0, max_value=1.0),
    value=st.decimals(min_value=-500, max_value=5000, places=2),
    evidence=st.sampled_from(list(EvidenceQuality)),
    cluster=st.one_of(st.none(), st.just("R-1")),
    snapshot=terms_st,
    notice_offset=st.integers(min_value=0, max_value=30),
)
def test_leg01_engine_never_produces_violations(
    reason: ReturnReason,
    risk: float,
    value: Decimal,
    evidence: EvidenceQuality,
    cluster: str | None,
    snapshot: ReturnTerms,
    notice_offset: int,
) -> None:
    req = request(reason, terms=snapshot, notice_on=TODAY - timedelta(days=notice_offset))
    s = replace(scores(risk, "0", evidence, cluster), value_eur=value)
    d = decide(req, s, BASE, THRESHOLDS, TODAY)
    assert check(d, req, BASE) == []
    assert d.levers.future_terms.return_window_days >= 14
    assert d.refund_due_by <= req.notice_on + timedelta(days=14)
    assert d.current_return_terms == snapshot


def _q4() -> tuple[Decision, object]:
    req = request()
    return decide(req, scores(0.95, "15", cluster="R-MARCO"), BASE, THRESHOLDS, TODAY), req


def codes(decision: Decision, req: object) -> set[str]:
    return {v.code for v in check(decision, req, BASE)}  # type: ignore[arg-type]


def test_window_below_legal() -> None:
    d, req = _q4()
    bad = replace(
        d,
        levers=replace(d.levers, future_terms=replace(d.levers.future_terms, return_window_days=7)),
    )
    assert "WINDOW_BELOW_LEGAL" in codes(bad, req)
    bad2 = replace(d, current_return_terms=replace(BASE, return_window_days=10))
    assert "WINDOW_BELOW_LEGAL" in codes(bad2, req)


def test_leg02_refund_late() -> None:
    d, req = _q4()
    assert "REFUND_LATE" in codes(replace(d, refund_due_by=TODAY + timedelta(days=15)), req)


def test_leg04_retroactive_restriction() -> None:
    d, req = _q4()
    bad = replace(d, current_return_terms=replace(BASE, instant_refund=False))
    assert "RETROACTIVE_RESTRICTION" in codes(bad, req)


def test_leg07_non_conformity_must_use_guarantee() -> None:
    req = request(ReturnReason.DEFECTIVE)
    d = decide(req, scores(), BASE, THRESHOLDS, TODAY)
    assert "NON_CONFORMITY_ON_WITHDRAWAL_PATH" in codes(replace(d, path=Path.WITHDRAWAL), req)
    d4, _ = _q4()
    restricted = replace(d4, path=Path.GUARANTEE)
    assert "GUARANTEE_RESTRICTED" in codes(restricted, req)


def test_insufficient_evidence_cannot_restrict() -> None:
    d, req = _q4()
    bad = replace(d, evidence_quality=EvidenceQuality.INSUFFICIENT)
    assert "INSUFFICIENT_EVIDENCE_RESTRICTED" in codes(bad, req)


def test_leg03_approval_required() -> None:
    d, req = _q4()
    assert "APPROVAL_MISSING" in codes(replace(d, requires_approval=False), req)
    approved_anon = replace(d, state=DecisionState.APPROVED, approver=None)
    assert "APPROVAL_MISSING" in codes(approved_anon, req)


def test_leg08_review_date() -> None:
    d, req = _q4()
    assert "REVIEW_MISSING" in codes(replace(d, review_on=None), req)
    assert "REVIEW_OUT_OF_RANGE" in codes(replace(d, review_on=TODAY), req)
    assert "REVIEW_OUT_OF_RANGE" in codes(replace(d, review_on=TODAY + timedelta(days=400)), req)


def test_leg09_coupon_rule_and_confirmation() -> None:
    d, req = _q4()
    assert "COUPON_RULE_MISSING" in codes(replace(d, coupon_rule=None), req)
    no_version = replace(d, coupon_rule=CouponRuleApplication(rule_version=None, confirmed_by=None))
    assert "COUPON_RULE_MISSING" in codes(no_version, req)
    approved = replace(d, state=DecisionState.APPROVED, approver="sara")
    assert "COUPON_CONFIRMATION_MISSING" in codes(approved, req)


def test_leg05_diminished_value_claim_needs_evidence() -> None:
    d, req = _q4()
    weak = DiminishedValueClaim(Decimal("20"), (), confirmed_by=None, rule_id=None)
    assert "CLAIM_WITHOUT_EVIDENCE" in codes(replace(d, diminished_value_claim=weak), req)
    good = DiminishedValueClaim(Decimal("20"), ("photo-1",), confirmed_by="ahmed", rule_id="dv-1")
    assert "CLAIM_WITHOUT_EVIDENCE" not in codes(replace(d, diminished_value_claim=good), req)


def test_no_claim_on_guarantee_path() -> None:
    req = request(ReturnReason.DEFECTIVE)
    d = decide(req, scores(), BASE, THRESHOLDS, TODAY)
    claim = DiminishedValueClaim(Decimal("20"), ("photo-1",), confirmed_by="ahmed", rule_id="dv-1")
    assert "CLAIM_ON_GUARANTEE" in codes(replace(d, diminished_value_claim=claim), req)


def test_admissible_decisions_pass() -> None:
    d, req = _q4()
    assert codes(d, req) == set()
    assert d.quadrant is Quadrant.Q4
    _ = date(2026, 1, 1)
