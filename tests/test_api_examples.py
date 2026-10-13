"""The golden API examples agree with policy/ and llm/ (ADR-15).

Each decision in the examples is re-run through policy.engine.decide from the inputs it records,
checked by policy.legal_guard, and each draft passes llm.validator. The four demo cases of spec
section 4 are checked one by one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from apps.api import schemas as s
from llm.validator import ExpectedDecision, LlmOutput, validate
from policy.engine import (
    NAVIGLI_BASE_TERMS,
    REVIEW_DAYS,
    approve,
    decide,
    levers_for,
    quadrant_for,
)
from policy.legal_guard import LEGAL_REFUND_DAYS, check
from policy.types import (
    CaseScores,
    Decision,
    DecisionState,
    EvidenceQuality,
    Quadrant,
    ReturnRequest,
)
from policy.types import Path as ReturnPath

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples" / "api"
BASE = NAVIGLI_BASE_TERMS
CASES = ("giulia", "marco", "rossi", "rossi_card", "elena", "insufficient")
# Every other queued return has a card too (ADR-15 section 7): the mock queue opens every case.
OTHER_CASES = ("chiara", "andrea", "martina", "valentina", "simone", "francesca", "lorenzo")
ALL_CASES = CASES + OTHER_CASES


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def card(key: str) -> s.CaseCard:
    return s.CaseCard.model_validate(load(f"case_card.{key}.json"))


def engine_inputs(view: s.DecisionView) -> tuple[ReturnRequest, CaseScores]:
    inputs = view.inputs
    request = ReturnRequest(
        return_id=view.return_id,
        order_id=inputs.order_id,
        customer_id=inputs.customer_id,
        reason=inputs.reason,
        delivered_on=inputs.delivered_on,
        notice_on=inputs.notice_on,
        order_terms=inputs.order_terms.to_policy(),
    )
    return request, inputs.scores()


def rerun(view: s.DecisionView) -> Decision:
    """policy.engine.decide on the inputs recorded in the decision view."""
    request, scores = engine_inputs(view)
    assert view.inputs.base_terms.to_policy() == BASE
    rule = view.coupon_rule.rule_version if view.coupon_rule else "coupon-rule-v1"
    return decide(request, scores, BASE, view.inputs.thresholds.to_policy(), view.decided_on, rule)


def assert_engine_output(view: s.DecisionView, decision: Decision) -> None:
    assert view.path is decision.path
    assert view.evidence_quality is decision.evidence_quality
    assert view.quadrant is decision.quadrant
    assert [x.model_dump() for x in view.proposed_levers] == decision.levers.to_contract(BASE)
    assert view.refund_due_by == decision.refund_due_by
    assert view.current_return_terms.to_policy() == decision.current_return_terms
    assert view.rules_version == decision.rules_version
    if view.state in ("proposed", "approved"):
        # an override, a rejection and a lift replace the levers: then levers, requires_approval,
        # the review date and the coupon rule describe that choice, and only proposed_levers is
        # the engine's
        assert view.review_at == decision.review_on
        assert view.requires_approval == decision.requires_approval
        assert (view.coupon_rule is None) == (decision.coupon_rule is None)
        assert view.lever_ids == decision.levers.lever_ids(BASE)
        assert view.future_terms.to_policy() == decision.levers.future_terms


def decision_views() -> Iterator[tuple[str, s.DecisionView]]:
    """Every DecisionView found in the examples."""
    for path in sorted(EXAMPLES.glob("*.json")):
        name = path.name.split(".")[0]
        if name == "index":
            continue
        model = s.API_MODELS[name].model_validate_json(path.read_text(encoding="utf-8"))
        for field in ("decision",):
            view = getattr(model, field, None)
            if isinstance(view, s.DecisionView):
                yield path.name, view
        recompute = getattr(model, "recompute", None)
        if isinstance(recompute, s.RecomputeResponse):
            yield path.name, recompute.decision


ALL_VIEWS = list(decision_views())


def test_examples_hold_decisions_of_every_kind() -> None:
    states = {view.state for _, view in ALL_VIEWS}
    assert states == {"proposed", "approved", "overridden", "rejected"}
    assert {view.path for _, view in ALL_VIEWS} == set(ReturnPath)
    assert len(ALL_VIEWS) >= 10


@pytest.mark.parametrize(("name", "view"), ALL_VIEWS, ids=[n for n, _ in ALL_VIEWS])
def test_every_example_decision_is_the_engine_output(name: str, view: s.DecisionView) -> None:
    assert_engine_output(view, rerun(view))


@pytest.mark.parametrize(("name", "view"), ALL_VIEWS, ids=[n for n, _ in ALL_VIEWS])
def test_every_example_decision_passes_the_legal_guard(name: str, view: s.DecisionView) -> None:
    """The exact mapping of ADR-15 section 8 (apps.api.schemas.to_policy_decision)."""
    decision, request = s.to_policy_decision(view)
    assert check(decision, request, BASE) == []
    expected = {"proposed": "proposed", "approved": "approved", "overridden": "approved",
                "rejected": "rejected"}[view.state]  # fmt: skip
    assert decision.state.value == expected
    assert decision.quadrant is (view.quadrant if view.state in ("proposed", "approved") else None)
    if view.coupon_rule is not None and view.state in ("approved", "overridden"):
        assert decision.coupon_rule and decision.coupon_rule.confirmed_by == view.approver


# ---- The four demo cases (spec section 4, US-104) ------------------------------------------------


def test_giulia_gets_the_q1_upgrade_without_approval() -> None:
    c = card("giulia")
    d = c.decision
    assert (d.path, d.quadrant, d.requires_approval, d.in_force) == (
        ReturnPath.WITHDRAWAL,
        Quadrant.Q1,
        False,
        True,
    )
    assert d.lever_ids == ["return_window_days:60", "priority_size_exchange:true"]
    assert d.review_at is None and d.coupon_rule is None
    assert c.linked_accounts is None
    assert d.future_terms.to_policy() == levers_for(Quadrant.Q1, BASE, False).future_terms


def test_marco_levers_are_exactly_the_engine_q4_output() -> None:
    c = card("marco")
    d = c.decision
    expected = rerun(d)
    assert d.quadrant is Quadrant.Q4 and d.requires_approval and d.state == "proposed"
    assert d.lever_ids == expected.levers.lever_ids(BASE)
    assert d.lever_ids == [
        "return_window_days:14",
        "free_return_shipping:false",
        "instant_refund:false",
        "coupons_enabled:false",
        "cluster_coupons_disabled:true",
        "transparency_notice:true",
    ]
    assert d.review_at == d.decided_on + timedelta(days=REVIEW_DAYS)
    assert d.current_return_terms.to_policy() == BASE  # the order keeps the terms of its snapshot
    linked = c.linked_accounts
    assert linked is not None and linked.cluster_size == 7 and len(linked.accounts) == 7
    assert d.coupon_rule is not None
    assert sorted(d.coupon_rule.affected_customer_ids) == sorted(
        a.customer_id for a in linked.accounts
    )
    assert d.coupon_rule.confirmed_by is None  # confirmed only by the approval


def test_marco_approval_is_policy_engine_approve() -> None:
    result = s.DecisionResult.model_validate(load("decision_result.marco_approved.json"))
    request = s.DecisionRequest.model_validate(load("decision_request.marco_approve.json"))
    proposed = card("marco").decision
    req, _ = engine_inputs(proposed)
    approved = approve(rerun(proposed), req, BASE, request.approver)
    d = result.decision
    assert (d.state, d.approver) == ("approved", approved.approver)
    assert d.coupon_rule is not None and d.coupon_rule.confirmed_by == "sara"
    assert request.levers == d.proposed_levers and request.decision_id == d.decision_id
    assert result.audit_entry.actor == "sara" and result.audit_entry.action == "decision_approved"
    assert result.outbox_message.legal_clause_kind == "withdrawal"


@pytest.mark.parametrize("key", ["rossi", "rossi_card"])
def test_rossi_family_is_never_q3_or_q4(key: str) -> None:
    c = card(key)
    assert c.decision.quadrant not in (Quadrant.Q3, Quadrant.Q4)
    assert not c.decision.requires_approval and c.decision.lever_ids == []
    linked = c.linked_accounts
    assert linked is not None and linked.cluster_size == 3
    shares_card = any("card" in link.kinds for link in linked.links)
    assert shares_card == (key == "rossi_card")


def test_elena_follows_the_guarantee_path_with_a_catalogue_anomaly() -> None:
    c = card("elena")
    d = c.decision
    assert d.path is ReturnPath.GUARANTEE and d.quadrant is None
    assert d.proposed_levers == [] and not d.requires_approval
    assert d.current_return_terms.free_return_shipping
    assert c.risk is None and d.inputs.risk is None
    assert c.item.variant_id == "V-JKT-DEF"
    anomaly = c.product_anomaly
    assert anomaly is not None and anomaly.status == "task_opened" and anomaly.catalog_task_id
    assert c.return_id in anomaly.return_ids
    recommended = next(a for a in c.alternatives if a.recommended)
    assert recommended.kind == "product_report"
    assert c.draft is not None
    assert {m.legal_clause_kind for m in c.draft.messages} == {"guarantee"}


def test_override_and_legal_guard_error() -> None:
    result = s.DecisionResult.model_validate(load("decision_result.override.json"))
    assert result.decision.state == "overridden" and result.decision.levers == []
    assert result.decision.proposed_levers  # the Q3 proposal is kept for the audit
    error = s.ErrorResponse.model_validate(load("error.legal_guard.json"))
    request = s.DecisionRequest.model_validate(load("decision_request.elena_shipping.json"))
    elena = card("elena").decision
    attempt, req = s.to_policy_decision(elena)
    assert request.levers is not None
    from dataclasses import replace

    attempt = replace(
        attempt,
        levers=s.to_policy_levers(request.levers, BASE),
        requires_approval=True,
        review_on=elena.decided_on + timedelta(days=REVIEW_DAYS),
        state=DecisionState.APPROVED,
        approver=request.approver,
    )
    violations = check(attempt, req, BASE)
    assert [(v.code, v.message) for v in error.violations] == [
        (v.code, v.message) for v in violations
    ]
    assert error.code == "legal_guard"


# ---- Queue and checkout --------------------------------------------------------------------------


def test_queue_items_agree_with_the_case_cards_and_are_sorted() -> None:
    queue = s.QueueResponse.model_validate(load("queue_response.json"))
    by_id = {item.return_id: item for item in queue.items}
    cards = {card(key).return_id: card(key) for key in ALL_CASES}
    assert set(by_id) == set(cards), "every queued return has a case card, and only those"
    for return_id, c in cards.items():
        item = by_id[return_id]
        recommended = next(a for a in c.alternatives if a.recommended)
        assert item.decision_id == c.decision.decision_id
        assert (item.quadrant, item.state, item.lever_ids, item.review_at) == (
            c.decision.quadrant,
            c.decision.state,
            c.decision.lever_ids,
            c.decision.review_at,
        )
        assert (item.order_id, item.customer_id, item.path) == (c.order_id, c.customer_id, c.path)
        assert item.expected_net_benefit == recommended.net_benefit.total
        assert item.risk == (c.risk.score if c.risk else None)
        assert item.value == (c.value.value_eur if c.value else None)
        cluster = c.linked_accounts
        assert item.linked_account_count == (cluster.cluster_size - 1 if cluster else 0)
        assert item.variant_anomaly == (c.product_anomaly is not None)
    keys = [(-i.expected_net_benefit.value, i.return_id) for i in queue.items]
    assert keys == sorted(keys) and queue.sort == "-expected_net_benefit"
    thresholds = card("giulia").decision.inputs.thresholds.to_policy()
    for item in queue.items:
        if item.risk is not None and item.value is not None:
            scores = CaseScores(
                float(item.risk.value),
                Decimal(str(item.value.value)),
                item.evidence_quality,
            )
            assert item.quadrant is quadrant_for(scores, thresholds), item.return_id
    assert queue.items[0].customer_id == "C-MARCO-1"  # the largest expected net benefit


def test_checkout_terms_come_from_the_decision_in_force() -> None:
    giulia = s.CheckoutEvaluateResponse.model_validate(
        load("checkout_evaluate_response.giulia.json")
    )
    marco = s.CheckoutEvaluateResponse.model_validate(load("checkout_evaluate_response.marco.json"))
    approved = s.DecisionResult.model_validate(load("decision_result.marco_approved.json")).decision
    assert giulia.terms.to_policy() == levers_for(Quadrant.Q1, BASE, False).future_terms
    assert giulia.terms_source == card("giulia").decision.decision_id and giulia.coupon_eligible
    assert marco.terms.to_policy() == levers_for(Quadrant.Q4, BASE, True).future_terms
    assert marco.terms_source == approved.decision_id and approved.in_force
    assert not marco.coupon_eligible and marco.coupon_block is not None
    assert marco.review_at == approved.review_at
    confirm = s.CheckoutConfirmResponse.model_validate(
        load("checkout_confirm_response.giulia.json")
    )
    assert confirm.terms_snapshot.terms == giulia.terms  # E2E-02: shown = stored
    assert confirm.terms_snapshot.terms_snapshot_id == giulia.terms_snapshot_id


def test_recomputed_cases_show_before_and_after() -> None:
    evidence = s.RecomputeResponse.model_validate(load("recompute_response.rossi_card.json"))
    assert evidence.before.decision_id == card("rossi_card").decision.decision_id
    assert evidence.after.active_links < evidence.before.active_links
    assert evidence.after.risk and evidence.before.risk
    assert evidence.after.risk.value < evidence.before.risk.value
    dispute = s.LinkDisputeResponse.model_validate(load("link_dispute_response.json"))
    assert dispute.recompute is not None
    assert (dispute.recompute.before.quadrant, dispute.recompute.after.quadrant) == (
        Quadrant.Q3,
        Quadrant.Q1,
    )
    assert dispute.recompute.after.cluster_size is None


def test_audit_entry_ids_grow_with_time() -> None:
    """The audit log is append-only: a later entry never has an earlier timestamp."""
    page = s.AuditPage.model_validate(load("audit_page.json"))
    entries = sorted(page.items, key=lambda e: e.entry_id)
    assert [e.entry_id for e in entries] == list(range(1, len(entries) + 1))
    assert all(a.at <= b.at for a, b in zip(entries, entries[1:], strict=False))
    assert page.items == sorted(page.items, key=lambda e: (e.at, e.entry_id), reverse=True)


def test_refunds_are_due_within_14_days_of_the_notice() -> None:
    for _, view in ALL_VIEWS:
        assert view.refund_due_by == view.inputs.notice_on + timedelta(days=LEGAL_REFUND_DAYS)


# ---- Simulator (ECO-01) and drafts (llm/validator.py) --------------------------------------------


def test_simulator_example_reproduces_the_proposal_example() -> None:
    v1 = s.SimulatorAssumptionsResponse.model_validate(load("simulator_assumptions.v1.json"))
    totals = {x.id: x.net_benefit.total.value for x in v1.current.sensitivity}
    assert totals == {"base": 368.0, "logistics_halved": 48.0, "abandonment_doubled": 128.0,
                      "both": -192.0}  # fmt: skip
    v2 = s.SimulatorAssumptionsResponse.model_validate(load("simulator_assumptions.v2.json"))
    assert v2.current.supersedes == v1.current.version
    assert [h.version for h in v2.history] == ["assumptions-v2", "assumptions-v1"]


def drafts() -> Iterator[tuple[str, s.DraftView, s.DecisionView, Any]]:
    for key in ALL_CASES:
        c = card(key)
        assert c.draft is not None
        yield key, c.draft, c.decision, load(f"case_card.{key}.json")
    for name in ("recompute_response.rossi_card.json",):
        r = s.RecomputeResponse.model_validate(load(name))
        yield name, r.draft, r.decision, load(name)


@pytest.mark.parametrize("item", list(drafts()), ids=lambda x: x[0])
def test_drafts_pass_the_llm_validator(item: tuple[str, s.DraftView, s.DecisionView, Any]) -> None:
    _, draft, decision, case = item
    echo = draft.decision_echo
    output = LlmOutput.model_validate(
        {
            "decision_echo": {
                "path": echo.path.value,
                "quadrant": echo.quadrant.value if echo.quadrant else None,
                "lever_ids": echo.lever_ids,
            },
            "operator_explanation": " ".join(f"{x.label}: {x.text}" for x in draft.explanation),
            "customer_message": {m.lang: m.connective_text for m in draft.messages},
        }
    )
    expected = ExpectedDecision(
        decision.path.value,
        decision.quadrant.value if decision.quadrant else None,
        decision.lever_ids,
    )
    result = validate(output.model_dump(), expected, case)
    assert result.ok, result.errors


def test_insufficient_evidence_case_lists_what_is_missing() -> None:
    c = card("insufficient")
    d = c.decision
    assert d.evidence_quality is EvidenceQuality.INSUFFICIENT and d.quadrant is None
    assert d.proposed_levers == [] and not d.requires_approval and c.risk is None
    assert d.missing_evidence == c.evidence.missing_evidence and len(d.missing_evidence) == 2
    assert d.current_return_terms.to_policy() == BASE


def test_evidence_quality_insufficient_case_is_outside_the_matrix() -> None:
    queue = s.QueueResponse.model_validate(load("queue_response.json"))
    insufficient = [i for i in queue.items if i.evidence_quality is EvidenceQuality.INSUFFICIENT]
    assert insufficient and all(i.quadrant is None and i.risk is None for i in insufficient)
    matrix = s.PortfolioMatrix.model_validate(load("portfolio_matrix.json"))
    points = {p.customer_id: p for p in matrix.points}
    assert all(points[f"C-MARCO-{i}"].quadrant is Quadrant.Q4 for i in range(1, 8))
    rossi = [p for cid, p in points.items() if cid.startswith("C-ROSSI")]
    assert len(rossi) == 6 and all(p.quadrant not in (Quadrant.Q3, Quadrant.Q4) for p in rossi)
    assert points["C-GIULIA"].quadrant is Quadrant.Q1


# ---- Matrix points and case cards (A10), assumption versions (US-602) ---------------------------


def test_every_matrix_point_of_a_carded_customer_is_its_card() -> None:
    """The matrix and the case card show one risk and one value per customer (spec 8.5)."""
    matrix = s.PortfolioMatrix.model_validate(load("portfolio_matrix.json"))
    points = {p.customer_id: p for p in matrix.points}
    for key in ALL_CASES:
        c = card(key)
        p = points[c.customer_id]
        d = c.decision
        if c.risk is not None:
            assert p.risk == c.risk.score.value, key
        elif d.path is ReturnPath.WITHDRAWAL:  # insufficient evidence: no score anywhere
            assert p.risk is None and d.inputs.risk is not None, key
        else:  # the customer has a score; the defect return is never scored (rule 4)
            assert p.risk is not None and d.inputs.risk is None, key
        assert c.value is not None and p.value_eur == c.value.value_eur.value, key
        assert p.evidence_quality is d.evidence_quality, key
        assert p.cluster_id == d.inputs.cluster_id, key
        returned = sum(line.returned for o in c.orders.orders for line in o.lines)
        welcome = sum(o.coupon_kind == "welcome" for o in c.orders.orders)
        assert (p.returns_12m, p.welcome_coupons) == (returned, welcome), key
        if d.path is ReturnPath.WITHDRAWAL:
            assert p.quadrant is d.quadrant, key


def _assumption_sets() -> Iterator[s.Assumptions]:
    def walk(node: Any) -> Iterator[s.Assumptions]:
        if isinstance(node, s.Assumptions):
            yield node
        elif isinstance(node, s.BaseModel):
            for name in type(node).model_fields:
                yield from walk(getattr(node, name))
        elif isinstance(node, list | tuple):
            for item in node:
                yield from walk(item)

    for path in sorted(EXAMPLES.glob("*.json")):
        name = path.name.split(".")[0]
        if name != "index":
            model = s.API_MODELS[name].model_validate_json(path.read_text(encoding="utf-8"))
            yield from walk(model.root if isinstance(model, s.AgentStream) else model)


def test_an_assumption_version_label_identifies_its_values() -> None:
    """US-602: one label, one set of values; derived sets name the stored version whose unit
    costs they use, and use exactly those unit costs."""
    by_label: dict[str, s.Assumptions] = {}
    for a in _assumption_sets():
        assert by_label.setdefault(a.version, a) == a, a.version
    stored = {
        v.version: v.net_benefit.assumptions
        for name in ("simulator_assumptions.v1.json", "simulator_assumptions.v2.json")
        for v in s.SimulatorAssumptionsResponse.model_validate(load(name)).history
    }
    derived = [a for a in by_label.values() if a.based_on is not None]
    assert len(derived) > 10
    for a in derived:
        if a.version.startswith(f"{a.based_on}/"):
            continue  # a sensitivity scenario: unit costs changed on purpose (spec 8.10)
        s.check_unit_costs(a, stored[a.based_on])
    for a in stored.values():
        assert a.based_on is None


# ---- Mock routes (A15) ---------------------------------------------------------------------------


def test_mock_routes_are_unambiguous_and_open_every_queued_case() -> None:
    routes = load("index.json")["routes"]
    keys = [(r["method"], r["path"], r["query"], r["request"], r["scenario"]) for r in routes]
    assert len(keys) == len(set(keys)), "a route is chosen by method, path, query, body, scenario"
    defaults: dict[tuple[Any, ...], int] = {}
    for r in routes:
        if r["scenario"] == "default":
            group = (r["method"], r["path"], r["query"], r["request"])
            defaults[group] = defaults.get(group, 0) + 1
    assert all(n == 1 for n in defaults.values())
    stale = [r for r in routes if r["status"] == 409]
    assert stale and all(r["scenario"] == "stale_decision" for r in stale)
    queue = s.QueueResponse.model_validate(load("queue_response.json"))
    cards = {
        r["path"]: r["response"] for r in routes if r["endpoint"] == "/api/returns/{return_id}"
    }
    for item in queue.items:
        response = cards[f"/api/returns/{item.return_id}"]
        assert s.CaseCard.model_validate(load(response)).return_id == item.return_id
    inspections = {r["response"] for r in routes if r["endpoint"] == "/api/inspections"}
    assert inspections == {"inspection.awaiting.json", "inspection.manual_review.json"}


def test_queue_query_routes_answer_the_filtered_queue() -> None:
    full = s.QueueResponse.model_validate(load("queue_response.json"))
    for route in load("index.json")["routes"]:
        if route["endpoint"] != "/api/returns" or route["query"] is None:
            continue
        query = s.QueueQuery.model_validate(load(route["query"]))
        response = s.QueueResponse.model_validate(load(route["response"]))
        expected = [i for i in full.items if query.matches(i)]
        assert response.items == expected and response.total == len(expected), route["query"]
    due = s.QueueResponse.model_validate(load("queue_response.review_due.json"))
    assert [i.return_id for i in due.items] == [card("lorenzo").return_id]


# ---- Review of restrictions (US-607) and decision history (US-604) -------------------------------


def test_a_lapsed_restriction_is_due_for_review_and_lifted_with_a_new_version() -> None:
    lorenzo = card("lorenzo")
    v1 = lorenzo.decision
    assert v1.state == "approved" and v1.requires_approval and v1.review_at is not None
    assert v1.review_at <= lorenzo.as_of.date() and not v1.in_force
    request = s.DecisionRequest.model_validate(load("decision_request.lift.json"))
    result = s.DecisionResult.model_validate(load("decision_result.lift.json"))
    v2 = result.decision
    assert request.action == "lift" and request.decision_id == v1.decision_id
    assert (v2.version, v2.supersedes, v2.revision_trigger) == (2, v1.decision_id, "review")
    assert v2.levers == [] and v2.future_terms.to_policy() == BASE and v2.review_at is None
    assert v2.override_reason == request.override_reason and v2.approver == request.approver
    assert result.audit_entry.action == "restriction_lifted"
    before = result.audit_entry.before
    assert isinstance(before, s.DecisionAuditState) and before == s.DecisionAuditState.from_view(v1)


def test_earlier_decision_versions_keep_their_inputs_and_alternatives() -> None:
    record = s.DecisionRecord.model_validate(load("decision_record.chiara_v1.json"))
    chiara = card("chiara")
    assert record.decision.decision_id == chiara.decision.decision_id
    assert record.alternatives == chiara.alternatives
    assert record.decision.inputs == chiara.decision.inputs
    dispute = s.LinkDisputeResponse.model_validate(load("link_dispute_response.json"))
    assert dispute.recompute is not None
    assert record.decision.superseded_by == dispute.recompute.decision.decision_id
    assert not record.decision.in_force
    request = s.LinkDisputeRequest.model_validate(load("link_dispute_request.json"))
    assert (
        request.decision_id == dispute.recompute.before.decision_id == record.decision.decision_id
    )


def test_decision_audit_payloads_are_the_decision_states() -> None:
    for name in ("decision_result.marco_approved.json", "decision_result.override.json",
                 "decision_result.reject.json", "decision_result.lift.json"):  # fmt: skip
        result = s.DecisionResult.model_validate(load(name))
        after = result.audit_entry.after
        assert after == s.DecisionAuditState.from_view(result.decision), name
