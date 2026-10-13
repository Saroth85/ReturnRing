"""UNIT-04 and policy engine behaviour (docs/spec.md section 8.6)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from policy.engine import (
    REVIEW_DAYS,
    PolicyError,
    Thresholds,
    approve,
    decide,
    levers_for,
    quadrant_for,
)
from policy.legal_guard import is_restrictive
from policy.types import (
    CaseScores,
    DecisionState,
    EvidenceQuality,
    Levers,
    Path,
    Quadrant,
    ReturnReason,
    ReturnTerms,
)
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


# UNIT-04: the lever table of docs/spec.md section 8.6 for the Navigli base, cell by cell.
# Columns of ReturnTerms: window days, free return shipping, instant refund, free size exchange,
# individual coupons. Q4 blocks the coupons of the whole cluster only when there is a cluster.
SPEC_8_6 = {
    Quadrant.Q1: Levers(
        ReturnTerms(60, True, True, True, True),
        cluster_coupons_disabled=False,
        transparency_notice=False,
        size_advice=True,
        priority_size_exchange=True,
    ),
    Quadrant.Q2: Levers(
        ReturnTerms(30, True, True, True, True),
        cluster_coupons_disabled=False,
        transparency_notice=False,
        size_advice=True,
    ),
    Quadrant.Q3: Levers(
        ReturnTerms(30, True, False, True, True),
        cluster_coupons_disabled=False,
        transparency_notice=True,
        size_advice=True,
    ),
    Quadrant.Q4: Levers(
        ReturnTerms(14, False, False, True, False),
        cluster_coupons_disabled=True,
        transparency_notice=True,
        size_advice=True,
    ),
}


@pytest.mark.parametrize("quadrant", list(Quadrant))
def test_unit04_lever_table_matches_spec_cell_by_cell(quadrant: Quadrant) -> None:
    assert levers_for(quadrant, BASE, has_cluster=True) == SPEC_8_6[quadrant]


def test_review_days_is_90() -> None:
    assert REVIEW_DAYS == 90  # US-607 design hypothesis, D-15


@pytest.mark.parametrize(
    ("risk", "value", "expected"),
    [
        (0.5, "150", Quadrant.Q3),  # both thresholds are inclusive
        (0.5, "149.99", Quadrant.Q4),
        (0.4999, "150", Quadrant.Q1),
        (0.4999, "149.99", Quadrant.Q2),
    ],
)
def test_quadrant_threshold_boundaries(risk: float, value: str, expected: Quadrant) -> None:
    assert quadrant_for(scores(risk=risk, value=value), THRESHOLDS) is expected


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
    q1 = levers_for(Quadrant.Q1, BASE, has_cluster=False)
    assert q1.lever_ids(BASE) == ["return_window_days:60", "priority_size_exchange:true"]
    from dataclasses import replace

    no_exchange = replace(q4, future_terms=replace(q4.future_terms, free_size_exchange=False))
    assert "free_size_exchange:false" in no_exchange.lever_ids(BASE)


@pytest.mark.parametrize("quadrant_scores", [(0.9, "500"), (0.9, "20")])
def test_q3_and_q4_need_approval_and_review_with_any_base(
    quadrant_scores: tuple[float, str],
) -> None:
    # A base already at the legal minimum: Q4 changes no term, yet it still needs approval.
    minimum = ReturnTerms(14, False, False, True, False)
    risk, value = quadrant_scores
    d = decide(request(terms=minimum), scores(risk, value), minimum, THRESHOLDS, TODAY)
    assert d.quadrant in (Quadrant.Q3, Quadrant.Q4)
    assert d.requires_approval and d.review_on == TODAY + timedelta(days=REVIEW_DAYS)


@pytest.mark.parametrize(
    "bad",
    [
        CaseScores(float("nan"), Decimal("800"), EvidenceQuality.SUFFICIENT),
        CaseScores(float("inf"), Decimal("800"), EvidenceQuality.SUFFICIENT),
        CaseScores(-0.1, Decimal("800"), EvidenceQuality.SUFFICIENT),
        CaseScores(1.1, Decimal("800"), EvidenceQuality.SUFFICIENT),
        CaseScores(0.1, Decimal("NaN"), EvidenceQuality.SUFFICIENT),
        CaseScores(0.1, Decimal("Infinity"), EvidenceQuality.SUFFICIENT),
    ],
)
def test_invalid_scores_stop_the_engine(bad: CaseScores) -> None:
    with pytest.raises(PolicyError):
        decide(request(), bad, BASE, THRESHOLDS, TODAY)


def test_invalid_scores_never_block_the_guarantee_or_insufficient_paths() -> None:
    nan = CaseScores(float("nan"), Decimal("NaN"), EvidenceQuality.SUFFICIENT)
    d = decide(request(ReturnReason.DEFECTIVE), nan, BASE, THRESHOLDS, TODAY)
    assert d.path is Path.GUARANTEE
    low = replace(nan, evidence_quality=EvidenceQuality.INSUFFICIENT)
    assert decide(request(), low, BASE, THRESHOLDS, TODAY).quadrant is None


@pytest.mark.parametrize("approver", ["", "   ", "\t"])
def test_approve_rejects_a_blank_approver(approver: str) -> None:
    req = request()
    d = decide(req, scores(0.95, "15"), BASE, THRESHOLDS, TODAY)
    with pytest.raises(PolicyError):
        approve(d, req, BASE, approver=approver)


def test_approve_strips_the_approver_name() -> None:
    req = request()
    d = decide(req, scores(0.95, "15"), BASE, THRESHOLDS, TODAY)
    assert approve(d, req, BASE, approver="  sara ").approver == "sara"


def test_approve_only_a_proposed_decision() -> None:
    req = request()
    d = decide(req, scores(0.95, "15"), BASE, THRESHOLDS, TODAY)
    for state in (DecisionState.REJECTED, DecisionState.APPROVED):
        with pytest.raises(PolicyError):
            approve(replace(d, state=state), req, BASE, approver="sara")


def test_approve_without_a_coupon_rule() -> None:
    req = request()
    d = decide(req, scores(0.9, "500"), BASE, THRESHOLDS, TODAY)  # Q3, no cluster
    assert d.coupon_rule is None
    assert approve(d, req, BASE, approver="sara").coupon_rule is None


def test_quadrant_for_refuses_invalid_scores() -> None:
    with pytest.raises(PolicyError):
        quadrant_for(
            CaseScores(float("nan"), Decimal("500"), EvidenceQuality.SUFFICIENT), THRESHOLDS
        )


@pytest.mark.parametrize(
    ("risk", "value"),
    [
        (float("nan"), "150"),
        (0.0, "150"),
        (1.5, "150"),
        (0.5, "NaN"),
        (0.5, "Infinity"),
    ],
)
def test_thresholds_must_be_valid(risk: float, value: str) -> None:
    with pytest.raises(PolicyError):
        Thresholds(risk=risk, value_eur=Decimal(value))


def test_approve_refuses_a_missing_approver() -> None:
    req = request()
    d = decide(req, scores(0.95, "15"), BASE, THRESHOLDS, TODAY)
    with pytest.raises(PolicyError):
        approve(d, req, BASE, approver=None)  # type: ignore[arg-type]


def test_most_generous_takes_the_better_term_each() -> None:
    q4 = ReturnTerms(14, False, False, True, False)
    upgrade = ReturnTerms(60, True, True, False, True)
    assert q4.most_generous(upgrade) == ReturnTerms(60, True, True, True, True)
    assert q4.most_generous(q4) == q4
