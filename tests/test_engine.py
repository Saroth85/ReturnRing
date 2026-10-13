"""UNIT-04 and policy engine behaviour (docs/spec.md section 8.6)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from policy.engine import (
    REVIEW_DAYS,
    PolicyError,
    approve,
    decide,
    levers_for,
    quadrant_for,
)
from policy.legal_guard import is_restrictive
from policy.types import DecisionState, EvidenceQuality, Path, Quadrant, ReturnReason
from tests.factories import BASE, THRESHOLDS, TODAY, request, scores


@pytest.mark.parametrize(
    ("risk", "value", "expected"),
    [
        (0.1, "500", Quadrant.Q1),
        (0.1, "20", Quadrant.Q2),
        (0.9, "500", Quadrant.Q3),
        (0.9, "20", Quadrant.Q4),
    ],
)
def test_quadrant_for(risk: float, value: str, expected: Quadrant) -> None:
    assert quadrant_for(scores(risk=risk, value=value), THRESHOLDS) is expected


def test_lever_table_matches_spec() -> None:
    q1 = levers_for(Quadrant.Q1, BASE, has_cluster=False)
    assert q1.future_terms.return_window_days == 60 and q1.future_terms.instant_refund
    assert not q1.transparency_notice and not is_restrictive(q1, BASE)

    q2 = levers_for(Quadrant.Q2, BASE, has_cluster=False)
    assert q2.future_terms == BASE and not is_restrictive(q2, BASE)

    q3 = levers_for(Quadrant.Q3, BASE, has_cluster=True)
    assert not q3.future_terms.instant_refund and q3.future_terms.free_return_shipping
    assert q3.transparency_notice and not q3.cluster_coupons_disabled

    q4 = levers_for(Quadrant.Q4, BASE, has_cluster=True)
    assert q4.future_terms.return_window_days == 14
    assert not q4.future_terms.free_return_shipping and not q4.future_terms.instant_refund
    assert q4.cluster_coupons_disabled and q4.transparency_notice

    for lev in (q1, q2, q3, q4):
        assert lev.future_terms.free_size_exchange


def test_q4_without_cluster_blocks_only_individual_coupons() -> None:
    q4 = levers_for(Quadrant.Q4, BASE, has_cluster=False)
    assert not q4.cluster_coupons_disabled and not q4.future_terms.coupons_enabled


def test_giulia_gets_upgrade_without_approval() -> None:
    d = decide(request(ReturnReason.SIZE), scores(0.1, "800"), BASE, THRESHOLDS, TODAY)
    assert d.quadrant is Quadrant.Q1
    assert not d.requires_approval and d.review_on is None


def test_marco_restriction_needs_approval_and_review() -> None:
    d = decide(request(), scores(0.95, "15", cluster="R-MARCO"), BASE, THRESHOLDS, TODAY)
    assert d.quadrant is Quadrant.Q4 and d.requires_approval
    assert d.review_on == TODAY + timedelta(days=REVIEW_DAYS)
    assert d.coupon_rule is not None and d.coupon_rule.confirmed_by is None
    assert d.current_return_terms == BASE  # never restricted retroactively


@pytest.mark.parametrize(
    "reason",
    [
        ReturnReason.DEFECTIVE,
        ReturnReason.DAMAGED_IN_SHIPPING,
        ReturnReason.WRONG_ITEM,
        ReturnReason.NOT_AS_DESCRIBED,
    ],
)
def test_defects_follow_guarantee_path(reason: ReturnReason) -> None:
    d = decide(request(reason), scores(0.99, "5", cluster="R-X"), BASE, THRESHOLDS, TODAY)
    assert d.path is Path.GUARANTEE and d.quadrant is None and not d.requires_approval
    assert not is_restrictive(d.levers, BASE)


def test_insufficient_evidence_keeps_standard_terms() -> None:
    s = scores(0.99, "5", evidence=EvidenceQuality.INSUFFICIENT)
    d = decide(request(), s, BASE, THRESHOLDS, TODAY)
    assert d.quadrant is None and d.levers.future_terms == BASE and not d.requires_approval


def test_refund_due_within_14_days_of_notice() -> None:
    d = decide(request(), scores(), BASE, THRESHOLDS, TODAY)
    assert d.refund_due_by == TODAY + timedelta(days=14)


def test_approve_confirms_coupon_rule() -> None:
    req = request()
    d = decide(req, scores(0.95, "15", cluster="R-MARCO"), BASE, THRESHOLDS, TODAY)
    a = approve(d, req, BASE, approver="sara")
    assert a.state is DecisionState.APPROVED and a.approver == "sara"
    assert a.coupon_rule is not None and a.coupon_rule.confirmed_by == "sara"


def test_approve_requires_a_name() -> None:
    req = request()
    d = decide(req, scores(0.95, "15"), BASE, THRESHOLDS, TODAY)
    with pytest.raises(PolicyError):
        approve(d, req, BASE, approver="")


def test_approve_rejects_an_inadmissible_decision() -> None:
    from dataclasses import replace

    req = request()
    d = decide(req, scores(0.95, "15", cluster="R-MARCO"), BASE, THRESHOLDS, TODAY)
    broken = replace(d, review_on=None)
    with pytest.raises(PolicyError):
        approve(broken, req, BASE, approver="sara")


def test_decide_rejects_missing_coupon_rule() -> None:
    with pytest.raises(PolicyError):
        decide(
            request(),
            scores(0.95, "15", cluster="R-MARCO"),
            BASE,
            THRESHOLDS,
            TODAY,
            coupon_rule_version=None,
        )


def test_lever_ids_list_only_the_differences_from_base() -> None:
    q4 = levers_for(Quadrant.Q4, BASE, has_cluster=True)
    assert q4.lever_ids(BASE) == [
        "return_window_days:14",
        "free_return_shipping:false",
        "instant_refund:false",
        "coupons_enabled:false",
        "cluster_coupons_disabled:true",
        "transparency_notice:true",
    ]
    assert levers_for(Quadrant.Q2, BASE, has_cluster=False).lever_ids(BASE) == []
    from dataclasses import replace

    no_exchange = replace(q4, future_terms=replace(q4.future_terms, free_size_exchange=False))
    assert "free_size_exchange:false" in no_exchange.lever_ids(BASE)
