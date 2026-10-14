"""End-to-end flows of the operator on the golden cases (spec 12: E2E-01, E2E-03, E2E-04,
E2E-05; MOD-11, US-307), through the HTTP API only, CPU and LLM_MODE=mock unless said.

- E2E-01: Giulia and Marco, return to outbox: the queue, the card, the approval (with the
  cluster coupon block confirmed for Marco), the message, the audit trail, the history.
- E2E-03: vLLM off in live vLLM mode: the queue, the cards and every act still work; a
  recompute falls back to the template, labelled; only an explicit regeneration says 503.
- E2E-04: Elena's defect follows the guarantee: no score, no restriction (an override that
  charges the shipping is refused), the anomaly of the variant shown, the guarantee clause.
- E2E-05: the Rossi family with a shared card: never Q3 or Q4, before and after evidence.
- US-307: a case with insufficient evidence keeps the standard terms, shown as such.

Every response passes its JSON Schema and its model; every number is synthetic.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.api.serving import Serving
from llm.legal_clause import clause
from pipeline.generator.golden import (
    ELENA,
    GIULIA,
    MARCO_ACCOUNTS,
    ROSSI_ACCOUNTS,
    ROSSI_CARD_ACCOUNTS,
)
from tests.api_harness import Api, golden_return, open_api, settings_for
from tests.test_api_operator_support import (
    audit,
    by_id,
    card,
    decide,
    error,
    latest,
    ok,
    ring_return,
    rows,
)


def _queue(api: Api, **params: Any) -> dict[str, Any]:
    return ok(api.client.get("/api/returns", params={"page_size": 100, **params}), "queue_response")


def _owned(api: Api, accounts: tuple[str, ...]) -> list[str]:
    scores = api.serving.frame("scores/request_scores")
    return sorted(str(r) for r in scores.loc[scores["customer_id"].isin(accounts), "return_id"])


@pytest.fixture
def vllm_down_api(
    serving: Serving, serving_dir: Path, seeded_db: tuple[Path, Any], tmp_path: Path
) -> Iterator[Api]:
    database = tmp_path / "vllm-down.sqlite"
    shutil.copyfile(seeded_db[0], database)
    settings = settings_for(
        serving_dir, database, llm_mode="vllm", vllm_base_url="http://127.0.0.1:9",
        vllm_api_key="test-key",
    )  # fmt: skip
    yield from open_api(serving, settings, database)


def test_e2e_01_golden_path_of_giulia_and_marco(api: Api) -> None:
    giulia, marco = golden_return(api.serving, GIULIA), ring_return(api)
    pending = _queue(api, state=["proposed"], quadrant=["Q3", "Q4"], requires_approval=True)
    assert marco in [i["return_id"] for i in pending["items"]]
    assert giulia not in [i["return_id"] for i in pending["items"]]
    # Giulia: the Q1 upgrade applies by itself; Sara confirms it and the customer is told
    g = card(api, giulia)
    assert g["decision"]["quadrant"] == "Q1" and g["decision"]["in_force"]
    g_result = ok(decide(api, giulia, "approve", lang="it"), "decision_result")
    assert g_result["outbox_message"]["legal_clause_kind"] is None
    # Marco: Q4 with the cluster coupon block; Sara approves and confirms the block
    m = card(api, marco)
    assert m["decision"]["quadrant"] == "Q4" and not m["decision"]["in_force"]
    assert m["linked_accounts"]["cluster_size"] == 7
    m_result = ok(decide(api, marco, "approve", lang="it"), "decision_result")
    d = m_result["decision"]
    assert d["in_force"] and d["approver"] == "sara" and d["review_at"] is not None  # rule 5
    assert sorted(m_result["coupon_application"]["affected_customer_ids"]) == list(MARCO_ACCOUNTS)
    assert m_result["outbox_message"]["body"].endswith(clause("withdrawal", "it"))
    # the outbox, the log, the queue and the history
    sent = rows(api, "outbox")
    assert {r["return_id"] for r in sent} == {giulia, marco} and len(sent) == 2
    for return_id in (giulia, marco):
        actions = [e.action for e in audit(api, return_id)]
        assert actions == [
            "decision_proposed",
            "draft_generated",
            "decision_approved",
            "message_queued",
        ]
    items = by_id(_queue(api)["items"])
    assert items[giulia]["state"] == items[marco]["state"] == "approved"
    record = ok(api.client.get(f"/api/decisions/{d['decision_id']}"), "decision_record")
    assert record["decision"]["coupon_rule"]["state"] == "confirmed"
    assert record["alternatives"] == m["alternatives"]


def test_e2e_03_the_demo_works_with_vllm_off(vllm_down_api: Api) -> None:
    api = vllm_down_api
    assert _queue(api)["total"] == len(api.serving.open_return_ids())
    marco = ring_return(api)
    m = card(api, marco)
    assert m["draft_status"] == "ready"
    ok(decide(api, marco, "approve"), "decision_result")
    rossi = _owned(api, ROSSI_CARD_ACCOUNTS)[0]
    body = {
        "decision_id": latest(api, rossi),
        "kind": "customer_contact",
        "note": "The customer called: the card is the parents' card",
        "author": "sara",
    }
    result = ok(api.client.post(f"/api/returns/{rossi}/evidence", json=body), "recompute_response")
    info = result["draft"]["generation"]
    assert (info["generation_path"], info["fallback_reason"]) == ("template", "unavailable")
    assert info["llm_mode"] == "vllm" and info["served_from"] == "live"
    card(api, rossi)
    regenerate = {"decision_id": latest(api, rossi), "requested_by": "sara"}
    error(
        api.client.post(f"/api/returns/{rossi}/regenerate", json=regenerate), 503, "llm_unavailable"
    )


def test_e2e_04_elena_follows_the_guarantee(api: Api) -> None:
    elena = golden_return(api.serving, ELENA)
    item = by_id(_queue(api, path=["guarantee"])["items"])[elena]
    assert item["risk"] is None and item["quadrant"] is None and item["variant_anomaly"]
    c = card(api, elena)
    assert c["risk"] is None and c["product_anomaly"] is not None
    assert any(a["kind"] == "product_report" for a in c["alternatives"])
    shipping = [{"id": "free_return_shipping", "value": False, "scope": "future_orders"}]
    refused = error(
        decide(
            api, elena, "override", levers=shipping, override_reason="Charge the shipping",
            override_category="other",
        ),
        422,
        "legal_guard",
    )  # fmt: skip
    assert refused["violations"]
    result = ok(decide(api, elena, "approve", lang="it"), "decision_result")
    d = result["decision"]
    assert d["path"] == "guarantee" and d["levers"] == [] and not d["requires_approval"]
    assert d["current_return_terms"]["free_return_shipping"]
    message = result["outbox_message"]
    assert message["legal_clause_kind"] == "guarantee"
    assert message["body"].endswith(clause("guarantee", "it"))


def test_e2e_05_the_rossi_family_is_never_restricted(api: Api) -> None:
    """MOD-11, FAIR-03: a family sharing a device, an address and a card is linked, never in
    Q3 or Q4, before and after the shared card is explained."""
    family = _owned(api, ROSSI_ACCOUNTS + ROSSI_CARD_ACCOUNTS)
    assert family
    for return_id in family:
        c = card(api, return_id)
        assert c["decision"]["quadrant"] not in ("Q3", "Q4")
        assert c["linked_accounts"] is not None and not c["decision"]["requires_approval"]
    shared = _owned(api, ROSSI_CARD_ACCOUNTS)[0]
    body = {
        "decision_id": latest(api, shared),
        "kind": "shared_payment_explained",
        "note": "Mother and daughter share the family card",
        "target_link_ids": ["C-ROSSI-CARD-1~C-ROSSI-CARD-2"],
        "author": "sara",
    }
    result = ok(api.client.post(f"/api/returns/{shared}/evidence", json=body), "recompute_response")
    for snapshot in (result["before"], result["after"]):
        assert snapshot["quadrant"] not in ("Q3", "Q4") and not snapshot["requires_approval"]
    ok(decide(api, shared, "approve", lang="en"), "decision_result")
    assert card(api, shared)["decision"]["state"] == "approved"


def test_insufficient_evidence_keeps_the_standard_terms(api: Api) -> None:
    """US-307: no risk and no quadrant shown; the missing evidence is listed; approving keeps
    the standard terms, never an upgrade or a restriction."""
    items = _queue(api, evidence_quality="insufficient")["items"]
    assert items
    return_id = items[0]["return_id"]
    c = card(api, return_id)
    assert c["risk"] is None and c["evidence"]["missing_evidence"]
    assert c["decision"]["quadrant"] is None and c["decision"]["proposed_levers"] == []
    result = ok(decide(api, return_id, "approve", lang="en"), "decision_result")
    assert result["decision"]["levers"] == [] and result["outbox_message"]["lang"] == "en"
    assert result["coupon_application"] is None


# ---- the meaning of the contract examples ------------------------------------------------------

EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples" / "api"


def _example(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    return body


def _result(r: dict[str, Any], blocks: bool = True) -> dict[str, Any]:
    """What a decision result means, without its ids, times and figures (the examples are of
    other customers): the state, the version chain, the restriction, the message, the audit
    entry, the version superseded and the coupon block."""
    d, entry, message = r["decision"], r["audit_entry"], r["outbox_message"]
    old = r["superseded"]
    meaning: dict[str, Any] = {
        "decision": (d["state"], d["revision_trigger"], d["in_force"], d["requires_approval"]),
        "review": d["review_at"] is not None,
        "message": None if message is None else (message["lang"], message["legal_clause_kind"]),
        "audit": (entry["action"], entry["actor_role"], entry["before"]["state"]),
        "superseded": None if old is None else (old["state"], old["in_force"]),
    }
    if blocks:
        rule, app = d["coupon_rule"], r["coupon_application"]
        meaning["coupon"] = (rule and rule["state"], app and app["state"])
    return meaning


def _recompute(r: dict[str, Any], log: dict[int, str]) -> dict[str, Any]:
    b, a = r["before"], r["after"]
    return {
        "trigger": r["trigger"],
        "versions": a["version"] - b["version"],
        "cluster": (b["cluster_size"] is None, a["cluster_size"] is None),
        "links": (a["active_links"] < b["active_links"], a["active_links"] == 0),
        "coupon": r["coupon_application"],
        "audit": [log[i] for i in r["audit_entry_ids"]],
        "decision": (r["decision"]["state"], r["decision"]["revision_trigger"]),
    }


def test_acts_mean_what_the_contract_examples_mean(api: Api) -> None:
    """ADR-15 section 7: the golden cases of the tiny store give the results the examples show
    for the same acts: Marco's approval, a rejection in English, a lift by the manager at the
    review (the examples lift a Q3 restriction, without a coupon block: Marco's Q4 block is
    reversed besides), and an override to the standard terms."""
    marco, giulia = ring_return(api), golden_return(api.serving, GIULIA)
    approved = ok(decide(api, marco, "approve"), "decision_result")
    assert _result(approved) == _result(_example("decision_result.marco_approved"))
    reason = {"override_reason": "The customer explained the returns by phone"}
    rejected = ok(
        decide(api, giulia, "reject", lang="en", override_category="customer_context", **reason),
        "decision_result",
    )
    assert _result(rejected) == _result(_example("decision_result.reject"))
    rossi = _owned(api, ROSSI_CARD_ACCOUNTS)[0]  # a proposal of the standard levers
    overridden = ok(
        decide(api, rossi, "override", levers=[], override_category="other", **reason),
        "decision_result",
    )  # before the refund deadline: a message never states a past one (1.5.0, H1)
    assert _result(overridden) == _result(_example("decision_result.override"))
    api.clock.advance(timedelta(days=200))  # Marco's restriction is due for review
    lift = {"override_reason": "Reviewed at the due date", "override_category": "periodic_review"}
    lifted = ok(decide(api, marco, "lift", approver="luca", **lift), "decision_result")
    assert _result(lifted, blocks=False) == _result(_example("decision_result.lift"), False)
    assert lifted["coupon_application"]["state"] == "reversed"


def test_reviews_and_recomputes_mean_what_the_contract_examples_mean(api: Api) -> None:
    """A renewal at the review, a regeneration in English, evidence on the Rossi shared card
    and the dispute of the last card link of Marco's account, as the examples show them."""
    marco = ring_return(api)
    ok(decide(api, marco, "approve"), "decision_result")
    api.clock.advance(timedelta(days=200))
    renewed = ok(decide(api, marco, "renew", approver="luca"), "decision_result")
    assert _result(renewed, blocks=False) == _result(_example("decision_result.renew"), False)
    assert renewed["coupon_application"]["state"] == "confirmed"

    giulia = golden_return(api.serving, GIULIA)
    body = {"decision_id": latest(api, giulia), "languages": ["en"], "requested_by": "sara"}
    path = f"/api/returns/{giulia}/regenerate"
    mine = ok(api.client.post(path, json=body), "regenerate_response")
    for r in (mine, _example("regenerate_response")):
        assert r["draft"]["generation"]["generation_path"] == "model"
        assert [t["lang"] for t in r["draft"]["message"]["texts"]] == ["en"]
        assert r["decision"]["state"] == "proposed"

    actions = {e["entry_id"]: e["action"] for e in _example("audit_page")["items"]}
    rossi = _owned(api, ROSSI_CARD_ACCOUNTS)[0]
    evidence = {
        "decision_id": latest(api, rossi),
        "kind": "shared_payment_explained",
        "note": "Mother and daughter share the family card",
        "target_link_ids": ["C-ROSSI-CARD-1~C-ROSSI-CARD-2"],
        "author": "sara",
    }
    recomputed = ok(
        api.client.post(f"/api/returns/{rossi}/evidence", json=evidence), "recompute_response"
    )
    log = {e.entry_id: e.action for e in audit(api, rossi)}
    theirs = _example("recompute_response.rossi_card")
    assert _recompute(recomputed, log) == _recompute(theirs, actions)
    assert recomputed["after"]["quadrant"] == theirs["after"]["quadrant"] == "Q2"

    # the first dispute reverses the renewed block; the second, on a proposal, matches the
    # example (Chiara's account left alone by the dispute)
    for link in ("C-MARCO-3~C-MARCO-7", "C-MARCO-5~C-MARCO-7"):
        api.clock.advance(timedelta(minutes=5))
        body = {"reason": "Card used once for a gift", "author": "sara", "return_id": marco}
        disputed = ok(
            api.client.post(
                f"/api/links/{link}/dispute", json={**body, "decision_id": latest(api, marco)}
            ),
            "link_dispute_response",
        )
    example = _example("link_dispute_response")
    log = {e.entry_id: e.action for e in audit(api, marco)}
    assert disputed["link"]["disputed"] and example["link"]["disputed"]
    assert disputed["affected_return_ids"] == [marco]
    assert _recompute(disputed["recompute"], log) == _recompute(example["recompute"], actions)
