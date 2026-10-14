"""Round 4 of the API contract review (ADR-15 section 12, contract 1.4.0): each probe of the
review is a mutation of the examples that the models must refuse, and each fix has a positive
check on the examples. The finding ids (E1..E27) are those of the review."""

from __future__ import annotations

import copy
import functools
import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from apps.api import schemas as s
from contracts import sql
from contracts.vocab import DEMO_USERS, LANGS
from llm.messages import format_amount
from llm.phrasebook import COUPON_BLOCK_TEXT, size_advice_text, subject
from llm.validator import validate
from scripts import build_api_examples as bae

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples" / "api"
SCHEMAS = ROOT / "contracts" / "json" / "api"


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def refused(model: type[s.BaseModel], doc: Any, match: str | None = None) -> None:
    with pytest.raises(ValidationError, match=match):
        model.model_validate(doc)


def routes() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = load("index.json")["routes"]
    return out


def metric(value: float, provenance: dict[str, Any]) -> dict[str, Any]:
    return {"value": value, "unit": "eur", "provenance": provenance}


# ---- E1: no message announces a restriction no person has approved (rule 5) -----------------


def _marco_inspection(with_message: bool) -> dict[str, Any]:
    """Marco's proposed Q4 (not approved yet) gets a diminished-value claim at the warehouse."""
    confirm = s.InspectionConfirmResponse.model_validate(load("inspection_confirm_response.json"))
    marco = s.CaseCard.model_validate(load("case_card.marco.json"))
    assert confirm.claim is not None and confirm.inspection.confirmed_at is not None
    when = confirm.inspection.confirmed_at
    claim = {
        **confirm.claim.model_dump(mode="json"),
        "amount": metric(19.8, confirm.claim.amount.provenance.model_dump(mode="json")),
        "line_price": metric(marco.item.price_eur, marco.provenance.model_dump(mode="json")),
    }
    view = s.DecisionView.model_validate(
        {**marco.decision.model_dump(mode="json"), "diminished_value_claim": claim, "as_of": when}
    )
    assert view.state == "proposed" and view.requires_approval and view.quadrant is not None
    inspection = confirm.inspection.model_dump(mode="json")
    inspection.update(return_id=view.return_id, decision_id=view.decision_id)
    message = None
    if with_message:
        refund = s.Metric(value=79.2, unit="eur", provenance=confirm.claim.amount.provenance)
        message = bae.outbox_message("MSG-000102-9", view, "it", "ahmed", when, refund=refund)
        assert "I tuoi prossimi ordini avranno queste condizioni" in message.body
        message = message.model_dump(mode="json")
    return {
        "inspection": inspection,
        "claim": claim,
        "decision": view.model_dump(mode="json"),
        "outbox_message": message,
    }


def test_e1_an_unapproved_restriction_is_never_announced_to_the_customer() -> None:
    """E1: Ahmed's confirmation would tell Marco the restrictive terms before Sara approves
    them; the refund and the deduction wait for the message of her act."""
    s.InspectionConfirmResponse.model_validate(_marco_inspection(with_message=False))
    refused(s.InspectionConfirmResponse, _marco_inspection(with_message=True), "never announced")
    valentina = load("inspection_confirm_response.json")  # Q2: nothing awaits a person
    assert valentina["decision"]["requires_approval"] is False
    refused(s.InspectionConfirmResponse, {**valentina, "outbox_message": None}, "claim")


# ---- E2: a cluster coupon block belongs to the case cluster and its linked accounts ---------


def test_e2_a_coupon_block_is_tied_to_the_cluster_of_the_case() -> None:
    card = load("case_card.marco.json")
    s.CaseCard.model_validate(card)
    rule = ("decision", "coupon_rule")
    accounts = card["decision"]["coupon_rule"]["affected_customer_ids"]
    probes: list[tuple[tuple[Any, ...], Any, str]] = [
        ((*rule, "cluster_id"), "CL-999999", "cluster given to the engine"),  # L1
        ((*rule, "affected_customer_ids"), [*accounts, "C-OUTSIDER-1"], "linked accounts"),  # L2
        ((*rule, "affected_customer_ids"), accounts[1:], "case customer"),  # L3
    ]
    for path, value, match in probes:
        refused(s.CaseCard, mutated(card, path, value), match)
    result = load("decision_result.marco_approved.json")
    s.DecisionResult.model_validate(result)
    both = copy.deepcopy(result)  # L4: the rule and the confirmed application on another cluster
    both["decision"]["coupon_rule"]["cluster_id"] = "CL-999999"
    both["coupon_application"]["cluster_id"] = "CL-999999"
    refused(s.DecisionResult, both, "cluster given to the engine")
    refused(s.DecisionResult, mutated(result, ("decision", "inputs", "cluster_id"), None))  # L5


def test_e2_the_legal_guard_and_the_tables_see_the_cluster_of_the_block() -> None:
    view = s.DecisionResult.model_validate(load("decision_result.marco_approved.json")).decision
    decision, _ = s.to_policy_decision(view)
    assert decision.coupon_rule is not None and decision.coupon_rule.cluster_id == "CL-000417"
    applications = sql.coupon_rule_applications
    keys = [
        [e.target_fullname for e in fk.elements]
        for fk in applications.foreign_key_constraints
        if fk.referred_table is sql.decisions
    ]
    assert keys == [["decisions.decision_id", "decisions.cluster_id"]]


# ---- E3 and E10: the facts of a draft are the facts llm.case writes for its version ---------


def _set_fact(card: dict[str, Any], metric_name: str, **changes: Any) -> dict[str, Any]:
    """The card with a fact of the llm_case changed, in the case and in every statement."""
    out = copy.deepcopy(card)
    draft = out["draft"]
    for fact in draft["case_facts"] + [f for x in draft["explanation"] for f in x["facts"]]:
        if fact["metric"] == metric_name:
            fact.update(changes)
    return out


def _replace_text(card: dict[str, Any], old: str, new: str) -> dict[str, Any]:
    for statement in card["draft"]["explanation"]:
        statement["text"] = statement["text"].replace(old, new)
    return card


def test_e3_no_cluster_is_stated_for_a_customer_without_one() -> None:
    """E3: the regenerated draft of Giulia and the draft after the dispute stated "Linked
    accounts in the cluster: 1" for customers in no cluster."""
    for name, holder in (
        ("regenerate_response.json", lambda d: d),
        ("link_dispute_response.json", lambda d: d["recompute"]),
        ("case_card.giulia.json", lambda d: d),
    ):
        doc = holder(load(name))
        assert doc["decision"]["inputs"]["cluster_id"] is None
        texts = [x["text"] for x in doc["draft"]["explanation"]]
        assert not any("in the cluster" in text for text in texts), (name, texts)
        alone = [f for f in doc["draft"]["case_facts"] if f["metric"] == "cluster_size"]
        assert all(f["entity"] == "customer" for f in alone), name
    giulia = load("case_card.giulia.json")
    refused(s.CaseCard, _set_fact(giulia, "cluster_size", entity="cluster"))


@pytest.mark.parametrize(
    ("metric_name", "changes", "old", "new"),
    [
        ("cluster_size", {"value": 70}, "has 7 linked", "has 70 linked"),  # P1 of E3, C1 of E10
        ("creation_spread_days", {"value": 90}, "within 9 days", "within 90 days"),  # P2 of E3
        ("cluster_size", {"period": "as of 2020-01-01"}, "", ""),  # P3 of E3
        ("cluster_size", {"entity": "customer"}, "", ""),  # P4 of E3
        ("risk_score", {"period": "as of 2020-01-01"}, "", ""),  # P4 of E10
        ("risk_score", {"entity": "cluster"}, "", ""),  # P5 of E10
    ],
)
def test_e3_e10_a_fact_is_written_as_llm_case_writes_it(
    metric_name: str, changes: dict[str, Any], old: str, new: str
) -> None:
    card = load("case_card.marco.json")
    s.CaseCard.model_validate(card)
    refused(s.CaseCard, _replace_text(_set_fact(card, metric_name, **changes), old, new))


@pytest.mark.parametrize("entity", ["customer", "policy"])
def test_e10_a_lever_is_never_a_fact(entity: str) -> None:
    """P6 of E10 checked the window fact of the proposal; since ADR-14 decision 9 (ADR-15
    section 14) a lever is never a fact and the entity "policy" is gone: a draft with the
    window of the proposal as a fact is refused, whatever its value."""
    card = load("case_card.marco.json")
    window = next(
        x["value"] for x in card["decision"]["proposed_levers"] if x["id"] == "return_window_days"
    )
    facts = card["draft"]["case_facts"]
    assert all(f["metric"] != "return_window_days" for f in facts)
    probe = copy.deepcopy(card)
    probe["draft"]["case_facts"].append(
        {"id": f"F{len(facts) + 1}", "entity": entity, "metric": "return_window_days",
         "value": window, "period": "orders from 2026-09-28", "nature": "synthetic"}
    )  # fmt: skip
    refused(s.CaseCard, probe)


def test_e10_a_draft_cannot_cite_invented_renamed_or_forbidden_facts() -> None:
    andrea = load("case_card.andrea.json")
    s.CaseCard.model_validate(andrea)
    # P1: tenure_days 3260 while the card's score factors say 326
    probe = _replace_text(_set_fact(andrea, "tenure_days", value=3260), "326", "3260")
    refused(s.CaseCard, probe, "build_case writes")
    # P8: the days fact renamed, with another value: the check of B3 is not bypassed
    probe = _replace_text(
        _set_fact(andrea, "days_since_delivery", metric="days_from_delivery", value=25),
        "return notice: 5", "return notice: 25",
    )  # fmt: skip
    refused(s.CaseCard, probe)
    # P2: a fact build_case never writes, cited by a new statement
    probe = copy.deepcopy(andrea)
    draft = probe["draft"]
    fact = {"id": "F8", "entity": "customer", "metric": "returns_12m", "value": 9,
            "period": draft["case_facts"][0]["period"], "nature": "synthetic"}  # fmt: skip
    draft["case_facts"].append(fact)
    draft["explanation"].append(
        {
            "label": "OBSERVED",
            "fact_ids": ["F8"],
            "facts": [fact],
            "text": "The customer sent back 9 items in the last year.",
        }
    )
    refused(s.CaseCard, probe)
    # P3: forbidden features (rule 6) as score facts
    for name in ("customer_age", "gender_code"):
        probe = _replace_text(_set_fact(andrea, "tenure_days", metric=name, value=23),
                              "tenure_days is 326", f"{name} is 23")  # fmt: skip
        refused(s.CaseCard, probe)


def test_e10_a_case_on_the_guarantee_path_cites_no_score_fact() -> None:
    """R4: Elena's guarantee case with a hypothesis citing the residual (rule 4)."""
    elena = load("case_card.elena.json")
    s.CaseCard.model_validate(elena)
    draft = elena["draft"]
    fact = {**draft["case_facts"][0], "id": "F2", "entity": "customer", "metric": "residual_z",
            "value": 2.4, "period": f"as of {elena['decision']['decided_on']}"}  # fmt: skip
    draft["case_facts"].append(fact)
    draft["explanation"].append(
        {
            "label": "HYPOTHESIS",
            "fact_ids": ["F2"],
            "facts": [fact],
            "text": "The residual_z of 2.4 may point to a batch problem.",
        }
    )
    refused(s.CaseCard, elena, "not scored")


def test_e10_the_draft_stores_every_fact_of_its_llm_case() -> None:
    sources = draft_sources()
    for path in sorted(EXAMPLES.glob("*.json")):
        if path.name == "index.json":
            continue
        for found in _drafts(json.loads(path.read_text(encoding="utf-8"))):
            source = sources[found["generation"]["generation_id"]]
            assert found["case_facts"] == source.case["facts"], path.name


def _drafts(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if "case_facts" in node and "explanation" in node:
            yield node
        for value in node.values():
            yield from _drafts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _drafts(value)


@functools.cache
def draft_sources() -> dict[str, Any]:
    bae.build()
    return dict(bae.DRAFT_SOURCES)


def test_e11_the_stored_draft_has_the_verdict_llm_generate_had() -> None:
    """E11: an output llm.validator accepts on the whole llm_case (an uncited metric name, which
    is an identifier of the case) is accepted as a stored draft too: one validator, one case."""
    andrea = s.CaseCard.model_validate(load("case_card.andrea.json"))
    assert andrea.draft is not None
    source = draft_sources()[andrea.draft.generation.generation_id]
    output = andrea.draft.llm_output()
    statements = output["operator_explanation"]
    value = next(i for i, x in enumerate(statements) if x["fact_ids"] == ["F2"])
    del statements[value]
    statements[-1]["text"] += " value_eur_12m is shown with the value view."
    assert validate(output, source.expected, source.case).ok
    draft = andrea.draft.model_dump(mode="json")
    draft["explanation"] = [
        {**x, "facts": [f for f in draft["case_facts"] if f["id"] in x["fact_ids"]]}
        for x in statements
    ]
    s.CaseCard.model_validate({**load("case_card.andrea.json"), "draft": draft})


# ---- E4: no approval after the review date; later versions decided on their own day --------


def test_e4_a_restriction_is_approved_and_confirmed_before_its_review_date() -> None:
    result = load("decision_result.marco_approved.json")
    late = "2027-01-06T10:00:00Z"  # review_at 2026-12-27: a stale proposal (D1.v2)
    probe = copy.deepcopy(result)
    probe["decision"].update(approved_at=late, as_of=late)
    probe["decision"]["in_force"] = probe["decision"]["coupon_rule"]["in_force"] = False
    probe["audit_entry"]["at"] = probe["outbox_message"]["created_at"] = late
    probe["coupon_application"].update(confirmed_at=late, as_of=late, in_force=False)
    refused(s.DecisionResult, probe, "approved before its review date")
    app = load("coupon_rules.json")["applications"][0]
    s.CouponRuleApplication.model_validate(app)
    refused(
        s.CouponRuleApplication,
        {**app, "confirmed_at": late, "as_of": late, "in_force": False},
        "confirmed before its review date",
    )  # D2.v3
    refused(
        s.CouponRuleApplication,
        {**app, "review_at": app["proposed_at"][:10], "in_force": False},
        "after the day it is proposed",
    )  # D2.v4


def test_e4_a_recompute_version_is_decided_on_the_day_it_is_created() -> None:
    doc = load("recompute_response.rossi_card.json")
    s.RecomputeResponse.model_validate(doc)
    assert doc["decision"]["decided_on"] == doc["decision"]["created_at"][:10]
    refused(s.RecomputeResponse, mutated(doc, ("decision", "decided_on"), "2026-01-01"),
            "decided_on")  # fmt: skip


# ---- E5: standalone audit entries and the superseded version of a review -------------------


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"approver": None}, "person acted"),  # A1: approved with no approver
        ({"override_category": "other"}, "category"),  # A3
    ],
)
def test_e5_an_audit_state_is_a_state_a_view_can_have(changes: dict[str, Any], match: str) -> None:
    state = load("decision_result.marco_approved.json")["audit_entry"]["after"]
    s.DecisionAuditState.model_validate(state)
    refused(s.DecisionAuditState, {**state, **changes}, match)
    proposed = load("decision_result.marco_approved.json")["audit_entry"]["before"]
    refused(s.DecisionAuditState, {**proposed, "approver": "sara"}, "person acted")  # A2
    rejected = load("decision_result.reject.json")["audit_entry"]["after"]
    s.DecisionAuditState.model_validate(rejected)
    refused(s.DecisionAuditState, {**rejected, "lever_ids": ["instant_refund:false"]})  # A10


def test_e5_a_decision_entry_is_checked_on_its_own() -> None:
    entry = load("decision_result.marco_approved.json")["audit_entry"]
    s.AuditEntry.model_validate(entry)
    refused(s.AuditEntry, mutated(entry, ("after", "approver"), "luca"), "approver")  # A4
    far = mutated(entry, ("after", "review_at"), "2099-01-01")
    refused(s.AuditEntry, far, "review")  # A5
    moved = mutated(entry, ("after", "version"), 3)
    refused(s.AuditEntry, moved, "keeps its number")  # A8
    lift = load("decision_result.lift.json")["audit_entry"]
    s.AuditEntry.model_validate(lift)
    restricted = copy.deepcopy(lift)  # A7: a lift that still needs approval and a review date
    restricted["after"].update(requires_approval=True, review_at="2026-12-29")
    refused(s.AuditEntry, restricted, "standard levers")


def _marco_lift() -> dict[str, Any]:
    approved = s.DecisionResult.model_validate(load("decision_result.marco_approved.json"))
    _, result = bae.review_result(
        bae.marco().engine, approved.decision, "lift", "luca",
        datetime(2026, 10, 2, 9, 30, tzinfo=UTC), entry_id=30,
        reason="Rectification asked by the customer", category="customer_context",
    )  # fmt: skip
    return result.model_dump(mode="json")


def test_e5_a_lift_and_a_renewal_carry_the_version_they_supersede() -> None:
    lift = _marco_lift()
    s.DecisionResult.model_validate(lift)
    assert lift["superseded"]["decision_id"] == lift["decision"]["supersedes"]
    assert lift["superseded"]["coupon_rule"]["state"] == "reversed"
    refused(s.DecisionResult, {**lift, "superseded": None}, "supersedes")
    # D4.v1: a before that drops the cluster block and its application, with no reversal
    dropped = copy.deepcopy(lift)
    before = dropped["audit_entry"]["before"]
    before["lever_ids"].remove("cluster_coupons_disabled:true")
    before["coupon_application_id"] = None
    dropped["coupon_application"] = None
    refused(s.DecisionResult, dropped)
    # D6.v4: a before with another quadrant, risk and review date
    other = copy.deepcopy(lift)
    other["audit_entry"]["before"].update(quadrant="Q3", review_at="2026-12-30")
    other["audit_entry"]["before"]["risk"]["value"] = 0.99
    refused(s.DecisionResult, other, "version it supersedes")
    approve = load("decision_result.marco_approved.json")
    refused(s.DecisionResult, {**approve, "superseded": lift["superseded"]}, "only a lift")


# ---- E6: the after panel of a recompute shows what the engine used -------------------------


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("after", "risk", "value"), 0.99),
        (("after", "requires_approval"), True),
        (("after", "evidence_quality"), "insufficient"),
        (("after", "active_links"), 6),
        (("after", "cluster_size"), 8),
        (("after", "risk", "provenance", "origin"), "Contract example: another model"),
    ],
)
def test_e6_the_after_snapshot_is_the_new_decision(path: tuple[str, ...], value: Any) -> None:
    doc = load("link_dispute_response.json")
    s.LinkDisputeResponse.model_validate(doc)
    refused(s.LinkDisputeResponse, mutated(doc, ("recompute", *path), value))


# ---- E7: no euro amount without provenance in an untyped payload -----------------------------


@pytest.mark.parametrize("amount", [Decimal("17.98"), "17.98", " 17,98 ", 17.98])
def test_e7_a_payload_number_is_a_metric_whatever_its_type(amount: Any) -> None:
    items = load("audit_page.json")["items"]
    entry = next(e for e in items if e["action"] == "inspection_confirmed")
    s.AuditEntry.model_validate(entry)
    probe = copy.deepcopy(entry)
    probe["after"]["compensation_eur"] = amount
    refused(s.AuditEntry, probe, "Metric")
    s.AuditEntry.model_validate(mutated(entry, ("after", "rule_id"), "DV-RULE-1"))


# ---- E12, E17: the refund and the deduction stated to the customer ----------------------------


def test_e17_the_deduction_is_stated_with_its_reason() -> None:
    """E17 (spec 9 F3: the message leaves with the reason of the deduction)."""
    confirm = s.InspectionConfirmResponse.model_validate(load("inspection_confirm_response.json"))
    message, claim = confirm.outbox_message, confirm.claim
    assert message is not None and claim is not None and message.refund is not None
    deduction = format_amount(Decimal(str(claim.amount.value)), message.lang)
    assert message.fields.deduction == deduction == "17,98 €"
    assert f"Abbiamo trattenuto {deduction} per il minor valore rilevato" in message.body
    assert "taglia" not in message.body  # no other size is offered for a damaged item
    assert Decimal(str(message.refund.value)) == claim.refund_eur() == Decimal("71.92")
    outbox = s.OutboxResponse.model_validate(load("outbox.json"))
    assert message in outbox.items


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("outbox_message", "refund", "value"), 89.9),  # O1: the price, not the refund
        (("outbox_message", "refund", "value"), 4999.0),  # O2
        (("outbox_message", "refund", "value"), 0.0),  # O3
        (("outbox_message", "refund", "provenance", "origin"), "whatever"),  # O6
        (("outbox_message", "refund", "provenance", "nature"), "real"),  # O6
        (("claim", "line_price", "value"), 120.0),  # the claim is on the price of the line
    ],
)
def test_e12_the_refund_is_the_price_less_the_claim(path: tuple[str, ...], value: Any) -> None:
    doc = load("inspection_confirm_response.json")
    s.InspectionConfirmResponse.model_validate(doc)
    refused(s.InspectionConfirmResponse, mutated(doc, path, value))


def test_e12_a_refund_is_stated_only_after_a_claim() -> None:
    doc = load("inspection_confirm_response.json")
    probe = copy.deepcopy(doc)  # O5: no claim anywhere, the message still states a refund
    probe["claim"] = probe["decision"]["diminished_value_claim"] = None
    refused(s.InspectionConfirmResponse, probe)
    result = load("decision_result.marco_approved.json")  # O4: a refund with no claim
    provenance = result["decision"]["inputs"]["provenance"]
    refund = metric(1.0, provenance)
    refused(s.DecisionResult, mutated(result, ("outbox_message", "refund"), refund))


# ---- E13: the subject comes from llm/ ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Reso rifiutato: nessun rimborso, stesso cliente di altri 3 account",
        "Your refund of 500 EUR within 90 days",
    ],
)
def test_e13_the_subject_is_the_fixed_subject(text: str) -> None:
    result = load("decision_result.marco_approved.json")
    message = result["outbox_message"]
    assert message["subject"] == subject(message["return_id"], message["lang"])
    refused(s.DecisionResult, mutated(result, ("outbox_message", "subject"), text), "subject")


# ---- E14: no tone that llm/ does not apply ----------------------------------------------------


def test_e14_there_is_no_tone_anywhere() -> None:
    import contracts.vocab as vocab

    assert "tone" not in s.RegenerateRequest.model_fields
    assert "tone" not in s.DraftView.model_fields
    assert "tone" not in sql.llm_generations.c
    assert not hasattr(vocab, "Tone") and not hasattr(s, "Tone")
    for path in list(SCHEMAS.glob("*.json")) + list(EXAMPLES.glob("*.json")):
        assert '"tone"' not in path.read_text(encoding="utf-8"), path.name


# ---- E16: every declared error status has an example and a mock scenario --------------------


def test_e16_every_error_has_an_example_and_a_route() -> None:
    declared = {status for ep in s.ENDPOINTS for status in ep.errors}
    served = {r["status"] for r in routes() if r["status"] >= 400}
    assert declared <= served, sorted(declared - served)
    codes = {s.ErrorResponse.model_validate(load(p.name)).code for p in EXAMPLES.glob("error.*")}
    assert codes == set(get_args(s.ErrorCode))
    for r in routes():
        if r["status"] >= 400:
            error = s.ErrorResponse.model_validate(load(r["response"]))
            ep = next(e for e in s.ENDPOINTS if (e.method, e.path) == (r["method"], r["endpoint"]))
            assert r["status"] in ep.errors, r
            assert r["media_type"] == "application/json", r
            expected = {403: "forbidden_role", 404: "not_found", 429: "rate_limited",
                        503: "llm_unavailable"}.get(r["status"])  # fmt: skip
            assert expected is None or error.code == expected, r


def _route(**key: Any) -> dict[str, Any]:
    found = [r for r in routes() if all(r[k] == v for k, v in key.items())]
    assert len(found) == 1, (key, found)
    return found[0]


def test_e16_the_scenarios_of_the_review_are_served() -> None:
    decide = "/api/returns/{return_id}/decision"
    forbidden = _route(endpoint=decide, status=403)
    request = s.DecisionRequest.model_validate(load(forbidden["request"]))
    assert DEMO_USERS[request.approver] == "warehouse"
    again = _route(endpoint=decide, status=409, scenario="conflict")
    assert again["request"] == "decision_request.marco_approve.json"
    early = _route(endpoint=decide, request="decision_request.renew_early.json")
    assert early["status"] == 409 and load(early["response"])["code"] == "conflict"
    lapsed = _route(endpoint="/api/checkout/confirm", status=409)
    assert lapsed["scenario"] == "conflict"
    for endpoint in ("/api/returns/{return_id}/regenerate", "/api/agent/ask"):
        unavailable = [r for r in routes() if r["endpoint"] == endpoint and r["status"] == 503]
        assert unavailable and all(r["scenario"] == "cached" for r in unavailable)
    assert _route(endpoint="/api/agent/ask", status=429)["scenario"] == "rate_limited"


def test_e16_the_renewal_is_served_under_its_own_scenario() -> None:
    post = _route(endpoint="/api/returns/{return_id}/decision", scenario="renewed")
    result = s.DecisionResult.model_validate(load(post["response"]))
    request = s.DecisionRequest.model_validate(load(post["request"]))
    assert request.action == "renew" and result.audit_entry.action == "restriction_renewed"
    record = _route(path=f"/api/decisions/{result.decision.decision_id}", scenario="renewed")
    served = s.DecisionRecord.model_validate(load(record["response"])).decision
    assert (served.decision_id, served.state) == (result.decision.decision_id, "approved")
    lifted = _route(path=f"/api/decisions/{result.decision.decision_id}", scenario="default")
    assert s.DecisionRecord.model_validate(load(lifted["response"])).decision.state == "overridden"
    lift = s.DecisionResult.model_validate(load("decision_result.lift.json"))
    assert result.audit_entry.entry_id == lift.audit_entry.entry_id  # the same slot of the log


# ---- E18: every decision the examples name is served ----------------------------------------

_DECISION_ID = re.compile(r"DEC-[0-9]{6}-[0-9]+")


def test_e18_every_decision_id_of_the_examples_is_served() -> None:
    served = {
        r["path"].removeprefix("/api/decisions/"): r["response"]
        for r in routes()
        if r["endpoint"] == "/api/decisions/{decision_id}" and r["scenario"] == "default"
    }
    named: set[str] = set()
    for path in EXAMPLES.iterdir():
        if path.name != "index.json":
            named |= set(_DECISION_ID.findall(path.read_text(encoding="utf-8")))
    assert named <= set(served), sorted(named - set(served))
    cards = {r["path"] for r in routes() if r["endpoint"] == "/api/returns/{return_id}"}
    for name in served.values():
        record = s.DecisionRecord.model_validate(load(name))
        assert f"/api/returns/{record.decision.return_id}" in cards, name


# ---- E19: the matrix holds every customer of a decision in force ----------------------------


def test_e19_every_customer_of_a_decision_in_force_is_in_the_matrix() -> None:
    matrix = s.PortfolioMatrix.model_validate(load("portfolio_matrix.json"))
    points = {p.customer_id: p for p in matrix.points}
    views = [s.CaseCard.model_validate(load(p.name)).decision
             for p in EXAMPLES.glob("case_card.*.json")]  # fmt: skip
    views += [
        s.DecisionRecord.model_validate(load(p.name)).decision
        for p in EXAMPLES.glob("decision_record.*.json")
    ]
    checked = 0
    for view in views:
        standing = view.superseded_by is None and view.created_at <= matrix.as_of
        if standing and view.quadrant is not None and view.state in ("proposed", "approved"):
            point = points[view.inputs.customer_id]
            assert point.quadrant is view.quadrant, view.decision_id
            checked += 1
    assert checked >= 10
    earlier = [p for p in matrix.points if p.cluster_id == "CL-000655"]
    assert {p.customer_id for p in earlier} == {"C-12007", "C-12930", "C-15561"}
    assert all(p.quadrant is not None and p.quadrant.value == "Q4" for p in earlier)


# ---- E20: the audit log holds every act the examples show -----------------------------------


def _models(prefix: str, model: type[s.BaseModel]) -> list[Any]:
    return [model.model_validate(load(p.name)) for p in sorted(EXAMPLES.glob(f"{prefix}*.json"))]


def test_e20_the_audit_page_is_the_whole_log() -> None:
    page = s.AuditPage.model_validate(load("audit_page.json"))
    assert page.total == len(page.items) and len(page.items) <= page.page_size
    logged = {(e.action, e.entity_id) for e in page.items}
    ids = {e.entity_id for e in page.items}
    for card in _models("case_card.", s.CaseCard):
        assert ("decision_proposed", card.decision.decision_id) in logged
        assert card.draft is None or card.draft.generation.generation_id in ids
        if card.decision.state != "proposed":
            assert card.decision.decision_id in ids
    for result in _models("decision_result.", s.DecisionResult):
        if result.audit_entry.action != "restriction_renewed":  # another scenario
            assert result.audit_entry in page.items
    for message in s.OutboxResponse.model_validate(load("outbox.json")).items:
        assert ("message_queued", message.message_id) in logged
    for recompute in [load("recompute_response.rossi_card.json"),
                      load("link_dispute_response.json")["recompute"]]:  # fmt: skip
        by_id = {e.entry_id: e for e in page.items}
        entries = [by_id[i] for i in recompute["audit_entry_ids"]]
        assert [e.action for e in entries][1:] == ["case_recomputed", "draft_generated"]
    for name, action in (
        ("inspection.awaiting.json", "inspection_created"),
        ("inspection.manual_review.json", "inspection_created"),
    ):
        assert (action, load(name)["inspection_id"]) in logged
    assert ("inspection_confirmed", "INS-000014") in logged
    assert ("coupon_rule_application_reversed", "CRA-000027") in logged
    for rule in load("coupon_rules.after_create.json")["history"]:
        assert ("coupon_rule_approved", rule["rule_version"]) in logged
    for task in load("catalog_tasks.json")["items"]:
        assert ("catalog_task_opened", task["task_id"]) in logged
    for snapshot in ("TS-CHK-000981", "TS-CHK-000982"):
        assert ("checkout_terms_shown", snapshot) in logged
        assert ("checkout_confirmed", snapshot) in logged
    assert ("draft_regenerated", "GEN-000101-2") in logged


# ---- E21: every agent answer is served framed --------------------------------------------------


def test_e21_every_agent_route_serves_the_framed_stream() -> None:
    agent = [r for r in routes() if r["endpoint"] == "/api/agent/ask" and r["status"] == 200]
    assert len(agent) == 4
    for r in agent:
        assert r["response"].endswith(".sse") and r["media_type"] == "text/event-stream"
        events = s.AgentStream.model_validate(load(r["response"].replace(".sse", ".json"))).root
        text = (EXAMPLES / r["response"]).read_text(encoding="utf-8")
        assert text == "".join(s.sse_frame(e) for e in events), r["response"]


# ---- E22: the Italian date article in the examples --------------------------------------------


def test_e22_no_example_says_per_il_before_a_vowel() -> None:
    for path in EXAMPLES.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\bil (1|8|11) [a-z]", text), path.name
    giulia = load("case_card.giulia.json")
    assert (
        "previsto al più tardi per l'11 ottobre 2026"
        in giulia["draft"]["message"]["texts"][0]["text"]
    )


# ---- E24: an answer cites what it names --------------------------------------------------------

_RECORD_ID = re.compile(r"\b(?:CL|C|RET|ORD|TS)-[A-Z0-9-]*[0-9]\b")


def test_e24_every_record_an_answer_names_is_among_its_refs() -> None:
    for path in sorted(EXAMPLES.glob("agent_stream.*.json")):
        for event in load(path.name):
            if event["type"] == "answer":
                # a link names its two accounts: it is the evidence of both
                refs = {x for ref in event["refs"] for x in [ref["id"], *ref["id"].split("~")]}
                for found in _RECORD_ID.findall(event["text"]):
                    assert found in refs, (path.name, found)
    rossi = next(e for e in load("agent_stream.marco_rossi.json") if e["type"] == "answer")
    for key in ("refs", "evidence_consulted"):
        assert {"kind": "cluster", "id": "CL-000233"} in rossi[key]
    calls = [e for e in load("agent_stream.marco_rossi.json") if e["type"] == "tool_call"]
    assert {"tool": "get_cluster", "customer_id": "C-ROSSI-2"} in [
        {"tool": c["tool"], **c["arguments"]} for c in calls
    ]


# ---- E25: the checkout texts are fixed, reviewed and in both languages ----------------------


def test_e25_checkout_texts_come_from_llm_in_italian_and_english() -> None:
    marco = load("checkout_evaluate_response.marco.json")
    assert marco["coupon_block"]["text"] == COUPON_BLOCK_TEXT
    giulia = load("checkout_evaluate_response.giulia.json")
    advice = next(x["size_advice"] for x in giulia["lines"] if x["size_advice"])
    # the size guide: the live checkout never suggests a size (ADR-15 section 16)
    assert advice["text"] == {lang: size_advice_text("measurement_guide", lang) for lang in LANGS}
    s.CheckoutEvaluateResponse.model_validate(marco)
    english = {**COUPON_BLOCK_TEXT, "en": "Coupons are paused for the linked accounts"}
    refused(s.CheckoutEvaluateResponse, mutated(marco, ("coupon_block", "text"), english))
    refused(s.CheckoutEvaluateResponse,
            mutated(marco, ("coupon_block", "text"), "Coupons are paused"))  # fmt: skip
    line = next(i for i, x in enumerate(giulia["lines"]) if x["size_advice"])
    refused(s.CheckoutEvaluateResponse,
            mutated(giulia, ("lines", line, "size_advice", "suggested_size"), "M"))  # fmt: skip
    english = {**advice["text"], "it": advice["text"]["en"]}
    refused(s.CheckoutEvaluateResponse,
            mutated(giulia, ("lines", line, "size_advice", "text"), english))  # fmt: skip


# ---- E26: a decision record shows the state of its coupon application -----------------------


def test_e26_a_reversed_coupon_application_is_shown_with_its_decision() -> None:
    record = load("decision_record.earlier_q4_v1.json")
    view = s.DecisionRecord.model_validate(record).decision
    assert view.in_force and view.coupon_rule is not None
    assert (view.coupon_rule.state, view.coupon_rule.in_force) == ("reversed", False)
    reversed_app = s.CouponRuleApplication.model_validate(
        load("coupon_rule_application.reversed.json")
    )
    assert reversed_app.application_id == view.coupon_rule.application_id
    refused(s.DecisionRecord, mutated(record, ("decision", "coupon_rule", "in_force"), True))
    card = load("case_card.earlier.json")
    assert card["decision"]["coupon_rule"]["state"] == "reversed"
    proposed = load("case_card.marco.json")
    assert proposed["decision"]["coupon_rule"]["state"] == "proposed"
    refused(s.CaseCard, mutated(proposed, ("decision", "coupon_rule", "state"), "confirmed"))


# ---- helpers ------------------------------------------------------------------------------------


def mutated(doc: Any, path: tuple[Any, ...], value: Any) -> Any:
    out = copy.deepcopy(doc)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


Mutation = Callable[[dict[str, Any]], None]


def test_e10_a_regenerated_draft_is_checked_against_its_version_too() -> None:
    """Without the score factors in the response, the facts of a regenerated draft are still
    the facts llm.case writes: forbidden features, periods and fixed values are refused."""
    doc = load("regenerate_response.json")
    s.RegenerateResponse.model_validate(doc)
    probe = _replace_text(_set_fact(doc, "residual_z", metric="customer_age", value=23),
                          "residual_z is 0.4", "customer_age is 23")  # fmt: skip
    refused(s.RegenerateResponse, probe, "rule 6")
    refused(s.RegenerateResponse, _set_fact(doc, "risk_score", period="as of 2020-01-01"))
    refused(s.RegenerateResponse, _set_fact(doc, "cluster_size", entity="cluster"))
