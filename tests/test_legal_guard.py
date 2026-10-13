"""LEG-01 .. LEG-09: the legal guard (docs/spec.md sections 8.6 and 12)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from policy.engine import REVIEW_DAYS, decide, standard_levers
from policy.legal_guard import MAX_REVIEW_DAYS, check, is_restrictive
from policy.types import (
    CouponRuleApplication,
    Decision,
    DecisionState,
    DiminishedValueClaim,
    EvidenceQuality,
    Levers,
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
    base=terms_st,
    notice_offset=st.integers(min_value=0, max_value=30),
)
def test_leg01_engine_never_produces_violations(
    reason: ReturnReason,
    risk: float,
    value: Decimal,
    evidence: EvidenceQuality,
    cluster: str | None,
    snapshot: ReturnTerms,
    base: ReturnTerms,
    notice_offset: int,
) -> None:
    # Any lawful merchant base, not only Navigli: rules must not depend on the base (POL-1).
    req = request(reason, terms=snapshot, notice_on=TODAY - timedelta(days=notice_offset))
    s = replace(scores(risk, "0", evidence, cluster), value_eur=value)
    d = decide(req, s, base, THRESHOLDS, TODAY)
    assert check(d, req, base) == []
    assert d.levers.future_terms.return_window_days >= 14
    assert d.refund_due_by <= req.notice_on + timedelta(days=14)
    if d.quadrant in (Quadrant.Q3, Quadrant.Q4):  # LEG-03 and LEG-08, spec 8.6
        assert d.requires_approval and d.review_on == TODAY + timedelta(days=REVIEW_DAYS)
    else:
        assert not d.levers.transparency_notice


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


@pytest.mark.parametrize(
    "worse",
    [
        {"return_window_days": 20},
        {"free_return_shipping": False},
        {"instant_refund": False},
        {"free_size_exchange": False},
        {"coupons_enabled": False},
    ],
)
def test_leg04_retroactive_restriction_on_every_term(worse: dict[str, object]) -> None:
    d, req = _q4()
    bad = replace(d, current_return_terms=replace(BASE, **worse))  # type: ignore[arg-type]
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
    approved_blank = replace(d, state=DecisionState.APPROVED, approver="   ")
    assert "APPROVAL_MISSING" in codes(approved_blank, req)


@pytest.mark.parametrize("quadrant", [Quadrant.Q3, Quadrant.Q4])
def test_leg03_q3_q4_need_approval_even_with_standard_levers(quadrant: Quadrant) -> None:
    d, req = _q4()
    plain = replace(
        d,
        quadrant=quadrant,
        levers=standard_levers(BASE),
        requires_approval=False,
        review_on=None,
        coupon_rule=None,
    )
    assert {"APPROVAL_MISSING", "REVIEW_MISSING"} <= codes(plain, req)


def test_transparency_notice_is_a_restriction() -> None:
    req = request(ReturnReason.DEFECTIVE)
    g = decide(req, scores(), BASE, THRESHOLDS, TODAY)
    notice = replace(g.levers, transparency_notice=True)
    assert "GUARANTEE_RESTRICTED" in codes(replace(g, levers=notice), req)
    req2 = request()
    i = decide(req2, scores(evidence=EvidenceQuality.INSUFFICIENT), BASE, THRESHOLDS, TODAY)
    found = codes(replace(i, levers=replace(i.levers, transparency_notice=True)), req2)
    assert {"INSUFFICIENT_EVIDENCE_RESTRICTED", "APPROVAL_MISSING"} <= found


def test_leg08_review_date() -> None:
    d, req = _q4()
    assert "REVIEW_MISSING" in codes(replace(d, review_on=None), req)
    assert "REVIEW_OUT_OF_RANGE" in codes(replace(d, review_on=TODAY), req)
    assert "REVIEW_OUT_OF_RANGE" in codes(replace(d, review_on=TODAY + timedelta(days=400)), req)
    assert codes(replace(d, review_on=TODAY + timedelta(days=MAX_REVIEW_DAYS)), req) == set()
    late = replace(d, review_on=TODAY + timedelta(days=MAX_REVIEW_DAYS + 1))
    assert "REVIEW_OUT_OF_RANGE" in codes(late, req)


def test_max_review_days_is_180() -> None:
    assert MAX_REVIEW_DAYS == 180


@pytest.mark.parametrize(
    ("levers", "restrictive"),
    [
        (standard_levers(BASE), False),
        (replace(standard_levers(BASE), priority_size_exchange=True), False),
        (replace(standard_levers(BASE), cluster_coupons_disabled=True), True),
        (replace(standard_levers(BASE), transparency_notice=True), True),
        (replace(standard_levers(BASE), future_terms=replace(BASE, return_window_days=60)), False),
        (replace(standard_levers(BASE), future_terms=replace(BASE, return_window_days=14)), True),
        (replace(standard_levers(BASE), future_terms=replace(BASE, instant_refund=False)), True),
    ],
)
def test_is_restrictive_lever_by_lever(levers: Levers, restrictive: bool) -> None:
    assert is_restrictive(levers, BASE) is restrictive


def test_leg09_coupon_rule_and_confirmation() -> None:
    d, req = _q4()
    assert "COUPON_RULE_MISSING" in codes(replace(d, coupon_rule=None), req)
    no_version = replace(d, coupon_rule=CouponRuleApplication(rule_version=None, confirmed_by=None))
    assert "COUPON_RULE_MISSING" in codes(no_version, req)
    blank = replace(d, coupon_rule=CouponRuleApplication(rule_version=" ", confirmed_by=None))
    assert "COUPON_RULE_MISSING" in codes(blank, req)
    approved = replace(d, state=DecisionState.APPROVED, approver="sara")
    assert "COUPON_CONFIRMATION_MISSING" in codes(approved, req)
    blank_confirm = replace(
        approved, coupon_rule=CouponRuleApplication(rule_version="coupon-rule-v1", confirmed_by=" ")
    )
    assert "COUPON_CONFIRMATION_MISSING" in codes(blank_confirm, req)


GOOD_CLAIM = DiminishedValueClaim(Decimal("20"), ("photo-1",), confirmed_by="ahmed", rule_id="dv-1")


@pytest.mark.parametrize(
    "missing",
    [
        {"photo_evidence_ids": ()},
        {"photo_evidence_ids": (" ",)},
        {"confirmed_by": None},
        {"confirmed_by": "  "},
        {"rule_id": None},
        {"rule_id": ""},
    ],
)
def test_leg05_claim_needs_photo_confirmation_and_rule(missing: dict[str, object]) -> None:
    d, req = _q4()
    weak = replace(GOOD_CLAIM, **missing)  # type: ignore[arg-type]
    assert "CLAIM_WITHOUT_EVIDENCE" in codes(replace(d, diminished_value_claim=weak), req)


@pytest.mark.parametrize("amount", ["0", "-5", "NaN", "Infinity"])
def test_leg05_claim_amount_must_be_positive(amount: str) -> None:
    d, req = _q4()
    bad = replace(GOOD_CLAIM, amount_eur=Decimal(amount))
    assert "CLAIM_AMOUNT_INVALID" in codes(replace(d, diminished_value_claim=bad), req)


def test_leg05_complete_claim_passes() -> None:
    d, req = _q4()
    assert codes(replace(d, diminished_value_claim=GOOD_CLAIM), req) == set()


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
