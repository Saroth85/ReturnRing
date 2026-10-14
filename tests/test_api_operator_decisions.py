"""POST /api/returns/{return_id}/decision (US-303, US-306, US-607; ADR-15 section 4 "Decisions").

approve, override and reject act on the proposed latest version, with one audit entry and one
outbox message in the transaction of the act; lift and renew review an approved or overridden
restriction and create the next version from policy.engine.decide on the latest scores, with no
message. The legal guard refuses an override that breaks rules 3 to 5 (422 legal_guard); a
restrictive action always has a person and a review date (rule 5); a stale version answers 409
stale_decision; a cluster coupon block is confirmed by the person who approves it and reversed by
a lift (LEG-09).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from apps.api import schemas as s
from apps.api.roles import ACTOR_HEADER
from llm.legal_clause import clause
from pipeline.generator.golden import ELENA, GIULIA, MARCO_ACCOUNTS, ROSSI_ACCOUNTS
from policy.engine import REVIEW_DAYS
from tests.api_harness import Api, golden_return
from tests.test_api_operator_support import (
    audit,
    card,
    decide,
    decision_body,
    error,
    latest,
    ok,
    ring_return,
    rows,
)

REASON = "The customer explained the returns by phone; the restriction is not justified"


def _lever(lever_id: str, value: Any) -> dict[str, Any]:
    return s.Lever.model_validate(
        {"id": lever_id, "value": value, "scope": _SCOPES[lever_id]}
    ).model_dump()


_SCOPES = {
    "return_window_days": "future_orders",
    "free_return_shipping": "future_orders",
    "instant_refund": "future_orders",
    "coupons_enabled": "future_orders",
    "cluster_coupons_disabled": "future_orders_cluster",
    "transparency_notice": "communication",
}
Q3_LEVERS = [_lever("instant_refund", False), _lever("transparency_notice", True)]
# Sentences of the phrasebook for the withdrawal path only: not allowed on the guarantee path.
WITHDRAWAL_ONLY = ["OPEN_CHOICE", "HELP_SIZE", "CLOSE_HAPPY"]


def _insufficient(api: Api) -> str:
    scores = api.serving.frame("scores/request_scores")
    rows_ = scores.loc[scores["evidence_quality"] == "insufficient", "return_id"]
    return str(sorted(rows_)[0])


def _rossi(api: Api) -> str:
    scores = api.serving.frame("scores/request_scores")
    return str(sorted(scores.loc[scores["customer_id"].isin(ROSSI_ACCOUNTS), "return_id"])[0])


# ---- approve -------------------------------------------------------------------------------------


def test_approve_confirms_the_proposal_and_the_cluster_coupon_block(api: Api) -> None:
    """E2E-01 (Marco): Sara approves the Q4 proposal; she also confirms the coupon block of the
    7 linked accounts; one audit entry and one message, written at the time of the act."""
    return_id = ring_return(api)
    before = card(api, return_id)
    proposed = before["decision"]
    response = decide(api, return_id, "approve", levers=proposed["proposed_levers"])
    result = ok(response, "decision_result")
    now = "2026-10-02T09:00:00Z"
    d = result["decision"]
    assert d["decision_id"] == proposed["decision_id"] and d["version"] == 1
    assert (d["state"], d["approver"], d["approved_at"]) == ("approved", "sara", now)
    assert d["in_force"] and d["levers"] == proposed["proposed_levers"]
    assert d["review_at"] == proposed["review_at"] and d["requires_approval"]
    rule = d["coupon_rule"]
    assert (rule["state"], rule["confirmed_by"], rule["in_force"]) == ("confirmed", "sara", True)
    app = result["coupon_application"]
    assert app["application_id"] == rule["application_id"] and app["state"] == "confirmed"
    assert (app["confirmed_by"], app["confirmed_at"], app["in_force"]) == ("sara", now, True)
    assert sorted(app["affected_customer_ids"]) == list(MARCO_ACCOUNTS)
    assert app["review_at"] == d["review_at"] and app["decision_id"] == d["decision_id"]
    entry = result["audit_entry"]
    assert (entry["action"], entry["actor"], entry["actor_role"]) == (
        "decision_approved",
        "sara",
        "operator",
    )
    assert entry["before"]["state"] == "proposed" and entry["after"]["state"] == "approved"
    assert entry["request_id"] == response.headers[s.REQUEST_ID_HEADER]
    message = result["outbox_message"]
    assert (message["lang"], message["created_by"], message["created_at"]) == ("it", "sara", now)
    assert message["legal_clause_kind"] == "withdrawal"
    assert message["body"].endswith(clause("withdrawal", "it"))
    assert message["generation_id"] == proposed["generation_id"]  # the draft's selection
    assert result["superseded"] is None
    # the state in the database
    (row,) = rows(api, "decisions", decision_id=d["decision_id"])
    assert row["state"] == "approved" and row["approver"] == "sara"
    (app_row,) = rows(api, "coupon_rule_applications", application_id=app["application_id"])
    assert app_row["state"] == "confirmed" and app_row["confirmed_by"] == "sara"
    (out,) = rows(api, "outbox", return_id=return_id)
    assert out["message_id"] == message["message_id"] and out["body"] == message["body"]
    actions = [e.action for e in audit(api, return_id)]
    assert actions == [
        "decision_proposed",
        "draft_generated",
        "decision_approved",
        "message_queued",
    ]
    after = card(api, return_id)
    assert after["decision"]["state"] == "approved" and after["decision"]["in_force"]
    text = response.text.lower()
    assert "same person" not in text and "stessa persona" not in text  # rule 11


def test_approve_without_levers_and_the_upgrade_of_giulia(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    result = ok(decide(api, return_id, "approve", lang="en"), "decision_result")
    d = result["decision"]
    assert d["state"] == "approved" and d["quadrant"] == "Q1" and d["in_force"]
    assert d["review_at"] is None and result["coupon_application"] is None
    assert result["outbox_message"]["lang"] == "en"
    assert result["outbox_message"]["legal_clause_kind"] is None


def test_a_version_is_decided_once(api: Api) -> None:
    return_id = ring_return(api)
    decision_id = latest(api, return_id)
    ok(decide(api, return_id, "approve"), "decision_result")
    for action, extra in (
        ("approve", {}),
        ("reject", {"override_reason": REASON, "override_category": "customer_context"}),
        (
            "override",
            {"levers": [], "override_reason": REASON, "override_category": "customer_context"},
        ),
    ):
        body = error(
            decide(api, return_id, action, decision_id=decision_id, **extra), 409, "conflict"
        )
        assert decision_id in body["message"]
    assert [e.action for e in audit(api, return_id)].count("decision_approved") == 1


def test_approve_with_other_levers_is_a_conflict(api: Api) -> None:
    return_id = ring_return(api)
    error(decide(api, return_id, "approve", levers=Q3_LEVERS), 409, "conflict")
    assert card(api, return_id)["decision"]["state"] == "proposed"


def test_a_restriction_is_approved_before_its_review_date(api: Api) -> None:
    """ADR-15 round 4, E4: a proposal still waiting on its review day is stale (409)."""
    return_id = ring_return(api)
    review_at = date.fromisoformat(card(api, return_id)["decision"]["review_at"])
    api.clock.set(api.clock.now().replace(year=review_at.year, month=review_at.month,
                                          day=review_at.day))  # fmt: skip
    body = error(decide(api, return_id, "approve"), 409, "conflict")
    assert "review date" in body["message"]
    assert rows(api, "outbox") == []
    assert card(api, return_id)["decision"]["state"] == "proposed"


# ---- override ------------------------------------------------------------------------------------


def test_override_to_the_standard_terms(api: Api) -> None:
    """US-306: other levers with a reason and a category; the coupon block proposed with the
    version is not confirmed; the message has no clause (no restriction)."""
    return_id = ring_return(api)
    proposed = card(api, return_id)["decision"]
    result = ok(
        decide(
            api, return_id, "override", levers=[], override_reason=REASON,
            override_category="customer_context",
        ),
        "decision_result",
    )  # fmt: skip
    d = result["decision"]
    assert (d["state"], d["levers"], d["requires_approval"], d["review_at"]) == (
        "overridden",
        [],
        False,
        None,
    )
    assert d["override_reason"] == REASON and d["override_category"] == "customer_context"
    assert d["proposed_levers"] == proposed["proposed_levers"] and d["in_force"]
    assert d["coupon_rule"] is None and result["coupon_application"] is None
    assert result["outbox_message"]["legal_clause_kind"] is None
    entry = result["audit_entry"]
    assert entry["action"] == "decision_overridden" and entry["reason"] == REASON
    (app_row,) = rows(api, "coupon_rule_applications", decision_id=d["decision_id"])
    assert app_row["state"] == "proposed" and app_row["confirmed_by"] is None


def test_a_restrictive_override_has_a_person_and_a_review_date(api: Api) -> None:
    """Rule 5, LEG-03, LEG-08: Sara restricts Giulia's future orders herself: the restriction
    carries her name and the review date of the version, and the withdrawal clause."""
    return_id = golden_return(api.serving, GIULIA)
    proposed = card(api, return_id)["decision"]
    result = ok(
        decide(
            api, return_id, "override", levers=Q3_LEVERS, override_reason=REASON,
            override_category="evidence_disagreement",
        ),
        "decision_result",
    )  # fmt: skip
    d = result["decision"]
    review = date.fromisoformat(proposed["decided_on"]) + timedelta(days=REVIEW_DAYS)
    assert d["requires_approval"] and d["review_at"] == review.isoformat()
    assert d["approver"] == "sara" and d["in_force"]
    assert d["lever_ids"] == ["instant_refund:false", "transparency_notice:true"]
    assert result["outbox_message"]["legal_clause_kind"] == "withdrawal"


def test_the_legal_guard_refuses_charging_the_shipping_of_a_defect(api: Api) -> None:
    """Rule 4, LEG-07: Elena's defect follows the guarantee; no restrictive lever applies."""
    return_id = golden_return(api.serving, ELENA)
    body = error(
        decide(
            api, return_id, "override", levers=[_lever("free_return_shipping", False)],
            override_reason="Try charging the return shipping", override_category="other",
        ),
        422,
        "legal_guard",
    )  # fmt: skip
    assert "GUARANTEE_RESTRICTED" in {v["code"] for v in body["violations"]}
    assert card(api, return_id)["decision"]["state"] == "proposed"
    assert rows(api, "outbox") == []


def test_the_legal_guard_keeps_insufficient_evidence_on_the_standard_terms(api: Api) -> None:
    """US-307: no upgrade and no restriction without enough evidence."""
    return_id = _insufficient(api)
    body = error(
        decide(
            api, return_id, "override", levers=[_lever("return_window_days", 60)],
            override_reason="A loyal customer by phone", override_category="customer_context",
        ),
        422,
        "legal_guard",
    )  # fmt: skip
    assert "INSUFFICIENT_EVIDENCE_RESTRICTED" in {v["code"] for v in body["violations"]}


def test_a_coupon_block_without_a_cluster_is_refused(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    body = error(
        decide(
            api, return_id, "override", levers=[_lever("cluster_coupons_disabled", True)],
            override_reason=REASON, override_category="other",
        ),
        422,
        "legal_guard",
    )  # fmt: skip
    assert "COUPON_CLUSTER_MISSING" in {v["code"] for v in body["violations"]}


def test_an_override_cannot_add_a_cluster_coupon_block_the_engine_did_not_propose(
    api: Api,
) -> None:
    """LEG-09: the general rule applies to a cluster of a Q4 case, on the engine's proposal."""
    return_id = _rossi(api)
    assert card(api, return_id)["linked_accounts"] is not None
    body = error(
        decide(
            api, return_id, "override", levers=[_lever("cluster_coupons_disabled", True)],
            override_reason=REASON, override_category="other",
        ),
        422,
        "invalid_request",
    )  # fmt: skip
    assert "coupon" in body["message"]


def test_the_operator_chooses_reviewed_sentences_never_free_text(api: Api) -> None:
    """ADR-14 decision 8: message.sentence_ids chooses phrasebook ids; a selection not allowed
    for the decision acted on is refused (422)."""
    return_id = ring_return(api)
    chosen = ["OPEN_MESSAGE", "CONTACT_TEAM", "CLOSE_DAY"]
    result = ok(
        decide(
            api, return_id, "override", levers=[], override_reason=REASON,
            override_category="customer_context",
            message={"lang": "it", "sentence_ids": chosen},
        ),
        "decision_result",
    )  # fmt: skip
    message = result["outbox_message"]
    assert message["sentence_ids"] == chosen and message["generation_id"] is None

    other = golden_return(api.serving, ELENA)
    refused = error(
        decide(api, other, "approve", message={"lang": "it", "sentence_ids": WITHDRAWAL_ONLY}),
        422,
        "invalid_request",
    )
    assert refused["violations"]
    assert card(api, other)["decision"]["state"] == "proposed"


# ---- reject --------------------------------------------------------------------------------------


def test_reject_discards_the_proposal_and_keeps_the_order_terms(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    proposed = card(api, return_id)["decision"]
    result = ok(
        decide(
            api, return_id, "reject", lang="en",
            override_reason="The customer asked to keep the standard terms",
            override_category="customer_context",
        ),
        "decision_result",
    )  # fmt: skip
    d = result["decision"]
    assert (d["state"], d["levers"], d["in_force"], d["requires_approval"]) == (
        "rejected",
        [],
        False,
        False,
    )
    assert d["current_return_terms"] == proposed["current_return_terms"]  # rule 3
    assert d["refund_due_by"] == proposed["refund_due_by"]
    assert result["audit_entry"]["action"] == "decision_rejected"
    assert result["outbox_message"]["lang"] == "en"


def test_reject_of_a_ring_proposal_leaves_the_coupon_block_unconfirmed(api: Api) -> None:
    return_id = ring_return(api)
    result = ok(
        decide(
            api, return_id, "reject", override_reason=REASON,
            override_category="linked_accounts_explained",
        ),
        "decision_result",
    )  # fmt: skip
    assert result["coupon_application"] is None and result["decision"]["coupon_rule"] is None
    (app_row,) = rows(
        api, "coupon_rule_applications", decision_id=result["decision"]["decision_id"]
    )
    assert app_row["state"] == "proposed"


# ---- lift and renew (US-607) ---------------------------------------------------------------------


def _approved_ring(api: Api) -> tuple[str, dict[str, Any]]:
    return_id = ring_return(api)
    result = ok(decide(api, return_id, "approve"), "decision_result")
    return return_id, result["decision"]


def test_lift_ends_the_restriction_and_reverses_the_coupon_block(api: Api) -> None:
    """A rectification the customer asked for, before the review date: the next version is
    the engine's fresh proposal overridden to the standard levers; the restriction stays in
    the log, superseded; its coupon block is reversed by the person who lifted it."""
    return_id, approved = _approved_ring(api)
    api.clock.advance(timedelta(days=1))
    now = "2026-10-03T09:00:00Z"
    response = decide(
        api, return_id, "lift", approver="luca",
        override_reason="Rectification asked by the customer", override_category="customer_context",
    )  # fmt: skip
    result = ok(response, "decision_result")
    d, old = result["decision"], result["superseded"]
    assert (d["version"], d["revision_trigger"], d["supersedes"]) == (
        2,
        "review",
        approved["decision_id"],
    )
    assert (d["state"], d["levers"], d["review_at"], d["in_force"]) == (
        "overridden",
        [],
        None,
        True,
    )
    assert d["decided_on"] == "2026-10-03" and d["created_at"] == d["approved_at"] == now
    assert d["quadrant"] == "Q4" and d["proposed_levers"] == approved["proposed_levers"]
    assert d["generation_id"] is None
    assert old["superseded_by"] == d["decision_id"] and not old["in_force"]
    assert old["coupon_rule"]["state"] == "reversed" and not old["coupon_rule"]["in_force"]
    app = result["coupon_application"]
    assert (app["state"], app["reversed_by"], app["reversed_at"]) == ("reversed", "luca", now)
    assert app["reversal_reason"] == "Rectification asked by the customer"
    assert result["outbox_message"] is None
    entry = result["audit_entry"]
    assert entry["action"] == "restriction_lifted" and entry["actor_role"] == "manager"
    assert entry["before"]["decision_id"] == approved["decision_id"]
    actions = [e.action for e in audit(api, return_id)]
    assert actions[-2:] == ["restriction_lifted", "coupon_rule_application_reversed"]
    assert len(rows(api, "outbox", return_id=return_id)) == 1  # the approval's only
    shown = card(api, return_id)
    assert shown["decision"]["decision_id"] == d["decision_id"]
    assert [r["version"] for r in shown["revisions"]] == [1, 2]
    assert shown["draft_status"] == "pending"


def test_renew_keeps_the_restriction_from_its_review_date_on(api: Api) -> None:
    return_id, approved = _approved_ring(api)
    early = error(decide(api, return_id, "renew", approver="luca"), 409, "conflict")
    assert approved["review_at"] in early["message"]
    review_at = date.fromisoformat(approved["review_at"])
    api.clock.set(api.clock.now().replace(year=review_at.year, month=review_at.month,
                                          day=review_at.day))  # fmt: skip
    result = ok(decide(api, return_id, "renew", approver="luca"), "decision_result")
    d, old = result["decision"], result["superseded"]
    assert (d["state"], d["revision_trigger"], d["approver"]) == ("approved", "review", "luca")
    assert d["decided_on"] == approved["review_at"] and d["in_force"]
    assert d["review_at"] == (review_at + timedelta(days=REVIEW_DAYS)).isoformat()
    assert old["superseded_by"] == d["decision_id"] and not old["in_force"]
    app = result["coupon_application"]
    assert app["decision_id"] == d["decision_id"] and app["state"] == "confirmed"
    assert (
        app["confirmed_by"] == "luca"
        and app["application_id"] != approved["coupon_rule"]["application_id"]
    )
    assert result["audit_entry"]["action"] == "restriction_renewed"
    assert result["outbox_message"] is None


def test_renew_needs_a_fresh_restriction(api: Api) -> None:
    """Sara's own restriction of a Q1 customer: at the review the engine proposes no
    restriction, so there is nothing to renew (409); lifting it works."""
    return_id = golden_return(api.serving, GIULIA)
    ok(
        decide(
            api, return_id, "override", levers=Q3_LEVERS, override_reason=REASON,
            override_category="evidence_disagreement",
        ),
        "decision_result",
    )  # fmt: skip
    review_at = date.fromisoformat(card(api, return_id)["decision"]["review_at"])
    api.clock.set(api.clock.now().replace(year=review_at.year, month=review_at.month,
                                          day=review_at.day))  # fmt: skip
    body = error(decide(api, return_id, "renew"), 409, "conflict")
    assert "lift" in body["message"]
    lifted = ok(
        decide(
            api, return_id, "lift", override_reason="Reviewed at the due date",
            override_category="periodic_review",
        ),
        "decision_result",
    )  # fmt: skip
    assert lifted["decision"]["quadrant"] == "Q1" and lifted["coupon_application"] is None


@pytest.mark.parametrize("action", ["lift", "renew"])
def test_only_an_approved_restriction_is_reviewed(api: Api, action: str) -> None:
    extra = (
        {"override_reason": REASON, "override_category": "periodic_review"}
        if action == "lift"
        else {}
    )
    proposed = ring_return(api)
    error(decide(api, proposed, action, **extra), 409, "conflict")  # still proposed
    giulia = golden_return(api.serving, GIULIA)
    ok(decide(api, giulia, "approve"), "decision_result")
    error(decide(api, giulia, action, **extra), 409, "conflict")  # approved, no restriction


# ---- errors and atomicity ------------------------------------------------------------------------


def test_errors_of_the_decision_endpoint(api: Api) -> None:
    return_id = ring_return(api)
    path = f"/api/returns/{return_id}/decision"
    body = decision_body(api, return_id, "approve")
    error(api.client.post("/api/returns/RET-NOPE/decision", json=body), 404, "not_found")
    other = latest(api, golden_return(api.serving, GIULIA))
    error(api.client.post(path, json={**body, "decision_id": other}), 404, "not_found")
    error(api.client.post(path, json={**body, "approver": "ahmed"}), 403, "forbidden_role")
    error(api.client.post(path, json=body, headers={ACTOR_HEADER: "luca"}), 403, "forbidden_role")
    error(api.client.post(path, json={**body, "action": "lift"}), 422, "invalid_request")
    assert card(api, return_id)["decision"]["state"] == "proposed"


def test_a_failed_act_writes_nothing(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    """The decision, its coupon application, its audit entry and its message are one
    transaction: a failure in the last write leaves the case as it was."""
    from apps.api.services.operator import actions

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("the outbox is down")

    monkeypatch.setattr(actions, "queue", broken)
    return_id = ring_return(api)
    before = {t: rows(api, t) for t in ("decisions", "coupon_rule_applications", "audit_log")}
    error(decide(api, return_id, "approve"), 500, "internal")
    after = {t: rows(api, t) for t in before}
    assert after == before and rows(api, "outbox") == []
