"""POST /api/inspections and /api/inspections/{inspection_id}/confirm (US-501..US-503, flow F3,
spec 8.8; ADR-15 section 4 "Inspection"; LEG-05; rules 4, 5 and 11).

The checklist is a suggestion with declared confidences (never calibrated); the system stops at
manual review, with no proposal, when it is unsure; a claim needs the proposal of an approved
rule and Ahmed's own "yes" on what it rests on; the customer hears of the claim, with the refund
it leaves and the amount withheld, only when no person's approval is awaited.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api.app import create_app
from apps.api.services.audit import entries
from apps.api.services.other.inspections import (
    CHECKLIST_SCHEMA,
    CONFIDENCE_GATE,
    DAMAGE_RULE,
    MockVision,
    Photo,
    VllmVision,
    mock_outcome,
    parse_checklist,
)
from contracts.sql import inspections, llm_generations, outbox
from llm.client import LlmUnavailable, VllmClient, VllmConfig
from pipeline.generator.golden import ELENA, GIULIA
from tests.api_harness import Api, golden_return, open_api, settings_for, validate_body
from tests.test_api_other_support import approve_in_db, decision_of, marco_return, photo_for

CONFIRM_ALL = {"tag_attached": "no", "visible_damage": "yes", "photo_quality": "yes"}


def _create(api: Api, return_id: str, photo: dict[str, str], who: str = "ahmed") -> Any:
    body = {"return_id": return_id, "inspector": who, "photo": photo}
    return api.client.post("/api/inspections", json=body)


def _created(api: Api, return_id: str, outcome: str) -> dict[str, Any]:
    response = _create(api, return_id, photo_for(outcome))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    validate_body("inspection", body)
    return body


def _confirm(
    api: Api, inspection_id: str, observed: Mapping[str, str], apply: bool, note: str | None = None
) -> Any:
    body = {
        "inspector": "ahmed",
        "observations": [{"item": k, "observation": v} for k, v in observed.items()],
        "apply_compensation": apply,
        "note": note,
    }
    return api.client.post(f"/api/inspections/{inspection_id}/confirm", json=body)


def _error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    validate_body("error", body)
    assert body["code"] == code
    return body


def _price(api: Api, return_id: str) -> Decimal:
    record = api.serving.store_return(return_id)
    assert record is not None
    line = api.serving.by_key("store/order_lines", record.line_id)
    assert line is not None
    return Decimal(str(line["price_eur"]))


# ---- The mock vision and the checks of a model answer --------------------------------------------


def test_the_mock_is_deterministic_and_reads_the_declared_type() -> None:
    jpeg = base64.b64decode(photo_for("damaged")["data_base64"])
    assert mock_outcome(jpeg) == mock_outcome(jpeg) == "damaged"
    assert mock_outcome(jpeg, "image/png") == "unreadable"
    assert mock_outcome(b"not an image") == "unreadable"
    result = MockVision().checklist(Photo(jpeg, "image/jpeg"), "RET-1")
    assert [i.item for i in result.items] == ["tag_attached", "visible_damage", "photo_quality"]
    assert (result.path, result.model_calls, result.llm_mode, result.latency_ms) == (
        "model", 1, "mock", None
    )  # fmt: skip


def test_a_model_answer_is_checked_before_use() -> None:
    good = {"items": [
        {"item": "tag_attached", "suggestion": "yes", "confidence": 0.9, "note": None},
        {"item": "visible_damage", "suggestion": "no", "confidence": 0.8, "note": "Clean fabric"},
        {"item": "photo_quality", "suggestion": "yes", "confidence": 0.95, "note": None},
    ]}  # fmt: skip
    items, codes = parse_checklist(json.dumps(good))
    assert items is not None and not codes and items[1].note == "Clean fabric"
    assert parse_checklist("{")[1] == ["SCHEMA"]
    swapped = {"items": [good["items"][1], good["items"][0], good["items"][2]]}
    assert parse_checklist(json.dumps(swapped))[1] == ["SCHEMA"]
    accusing = json.loads(json.dumps(good))
    accusing["items"][1]["note"] = "Looks like fraud"
    assert parse_checklist(json.dumps(accusing)) == (None, ["BANNED_TERM"])
    assert CHECKLIST_SCHEMA["properties"]["items"]["minItems"] == 3


def _vllm(post: Any) -> VllmVision:
    client = VllmClient(VllmConfig(base_url="http://localhost:8001", api_key="test-key"))
    return VllmVision(client, post=post)


def _answer(content: str, tokens: Any = 40) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": tokens}}


def test_vllm_vision_sends_the_photo_with_guided_json() -> None:
    sent: list[Mapping[str, Any]] = []
    good = json.dumps({"items": [
        {"item": "tag_attached", "suggestion": "no", "confidence": 0.8, "note": None},
        {"item": "visible_damage", "suggestion": "yes", "confidence": 0.75, "note": "Stain"},
        {"item": "photo_quality", "suggestion": "yes", "confidence": 0.9, "note": None},
    ]})  # fmt: skip

    def post(body: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        sent.append(body)
        return _answer(good), 0.1

    jpeg = base64.b64decode(photo_for("damaged")["data_base64"])
    result = _vllm(post).checklist(Photo(jpeg, "image/jpeg"), "RET-1")
    assert result.path == "model" and result.llm_mode == "vllm" and result.model_calls == 1
    assert result.completion_tokens == 40 and result.latency_ms is not None
    body = sent[0]
    assert body["response_format"]["json_schema"]["schema"] == CHECKLIST_SCHEMA
    image = body["messages"][1]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ([True], None),
        ([False], None),
        ([-1], None),
        ([1.5], None),
        ([None], None),
        ([0], 0),
        ([12], 12),
        ([3, 5], 8),
        ([3, -1], 3),
        ([-1, 5], 5),
        ([0, 0], 0),
    ],
)
def test_vision_counts_only_valid_completion_tokens_across_attempts(
    counts: list[Any], expected: int | None
) -> None:
    good = json.dumps({"items": [
        {"item": "tag_attached", "suggestion": "yes", "confidence": 0.9, "note": None},
        {"item": "visible_damage", "suggestion": "no", "confidence": 0.8, "note": None},
        {"item": "photo_quality", "suggestion": "yes", "confidence": 0.9, "note": None},
    ]})  # fmt: skip
    answers = iter(
        _answer(good if i == len(counts) - 1 else "not JSON", tokens)
        for i, tokens in enumerate(counts)
    )
    result = _vllm(lambda _: (next(answers), 0.1)).checklist(
        Photo(b"\xff\xd8\xff", "image/jpeg"), "RET-1"
    )
    assert result.path == ("model" if len(counts) == 1 else "regenerated")
    assert result.completion_tokens == expected


def test_vllm_vision_asks_once_more_then_falls_back() -> None:
    answers = iter([_answer("not json"), _answer("{}")])
    result = _vllm(lambda _: (next(answers), 0.1)).checklist(
        Photo(b"\xff\xd8\xff", "image/jpeg"), "R"
    )
    assert (result.path, result.fallback_reason, result.model_calls) == (
        "template",
        "invalid_twice",
        2,
    )
    assert result.validator_codes == ["SCHEMA"]
    assert {i.suggestion for i in result.items} == {"not_observable"}

    def down(_: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        raise LlmUnavailable("vLLM unreachable")

    off = _vllm(down).checklist(Photo(b"\xff\xd8\xff", "image/jpeg"), "R")
    assert (off.path, off.fallback_reason, off.model_calls, off.validator_codes) == (
        "template", "unavailable", 0, []
    )  # fmt: skip


# ---- POST /api/inspections -----------------------------------------------------------------------


def test_damage_seen_with_confidence_gets_the_approved_rule_proposal(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    body = _created(api, return_id, "damaged")
    decision = api.run(lambda conn: decision_of(conn, return_id))
    assert body["decision_id"] == decision["decision_id"] and body["path"] == "withdrawal"
    assert body["state"] == "awaiting_confirmation" and body["manual_review_reason"] is None
    assert body["confidence_is_calibrated"] is False
    assert [c["item"] for c in body["checklist"]] == [
        "tag_attached",
        "visible_damage",
        "photo_quality",
    ]
    proposal = body["proposal"]
    assert proposal["rule_id"] == DAMAGE_RULE.rule_id and proposal["basis"] == ["visible_damage"]
    expected = (_price(api, return_id) * Decimal("0.20")).quantize(Decimal("0.01"))
    assert Decimal(str(proposal["amount"]["value"])) == expected
    assert body["generation"]["llm_mode"] == "mock" and body["generation"]["model"] == "mock"
    assert body["inspection_id"] == "INS-000001" and body["photo_id"] == "PH-000001"

    async def stored(conn: AsyncConnection) -> tuple[dict[str, Any], dict[str, Any]]:
        row = (await conn.execute(select(inspections))).mappings().one()
        gen = (
            (
                await conn.execute(
                    select(llm_generations).where(
                        llm_generations.c.generation_id == row["generation_id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        return dict(row), dict(gen)

    row, gen = api.run(stored)
    assert row["photo_sha256"] == gen["input_digest"] and len(row["photo_sha256"]) == 64
    assert gen["kind"] == "inspection_checklist" and row["state"] == "awaiting_confirmation"
    assert row["min_model_confidence"] == min(c["model_confidence"] for c in body["checklist"])
    created = [e for e in api.run(lambda conn: entries(conn)) if e.action == "inspection_created"]
    assert created[0].actor == "ahmed" and created[0].after == {"state": "awaiting_confirmation"}


def test_the_system_stops_when_it_is_unsure(api: Api) -> None:
    """US-503: a dark photo, items not observable or a low declared confidence: manual review,
    with its reason, and no proposal."""
    return_id = golden_return(api.serving, GIULIA)
    unclear = _created(api, return_id, "unclear")
    assert unclear["state"] == "manual_review" and unclear["proposal"] is None
    assert unclear["manual_review_reason"].endswith("verify by hand")
    unreadable = _created(api, return_id, "unreadable")
    assert unreadable["state"] == "manual_review" and unreadable["proposal"] is None
    clean = _created(api, return_id, "clean")
    assert clean["state"] == "awaiting_confirmation" and clean["proposal"] is None
    assert all(c["model_confidence"] >= CONFIDENCE_GATE for c in clean["checklist"])


def test_no_proposal_on_the_guarantee_path(api: Api) -> None:
    """Rule 4: a defect follows the guarantee; no diminished-value proposal."""
    return_id = golden_return(api.serving, ELENA)
    body = _created(api, return_id, "damaged")
    assert body["path"] == "guarantee" and body["proposal"] is None
    response = _confirm(api, body["inspection_id"], CONFIRM_ALL, apply=True)
    _error(response, 422, "invalid_request")


def test_inspections_are_for_the_warehouse_and_known_returns(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    _error(_create(api, return_id, photo_for("clean"), who="sara"), 403, "forbidden_role")
    _error(_create(api, "RET-NOPE", photo_for("clean")), 404, "not_found")
    bad = {"media_type": "image/jpeg", "data_base64": "@@@@not base64"}
    _error(_create(api, return_id, bad), 422, "invalid_request")


def test_cached_mode_answers_with_the_template_and_stops(api: Api) -> None:
    settings = settings_for(api.settings.serving_dir, api.database, demo_mode="cached")
    for cached in open_api(api.serving, settings, api.database, clock=api.clock):
        return_id = golden_return(cached.serving, GIULIA)
        body = _created(cached, return_id, "damaged")
        assert body["generation"]["generation_path"] == "template"
        assert body["generation"]["fallback_reason"] == "unavailable"
        assert body["state"] == "manual_review" and body["proposal"] is None


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [(40, 40), (-1, None), (True, None), (False, None), (1.5, None), (0, 0), (None, None)],
)
def test_vllm_mode_goes_through_the_client(
    api: Api, monkeypatch: pytest.MonkeyPatch, tokens: Any, expected: int | None
) -> None:
    good = json.dumps({"items": [
        {"item": "tag_attached", "suggestion": "yes", "confidence": 0.9, "note": None},
        {"item": "visible_damage", "suggestion": "yes", "confidence": 0.85, "note": "Torn seam"},
        {"item": "photo_quality", "suggestion": "yes", "confidence": 0.95, "note": None},
    ]})  # fmt: skip
    client = VllmClient(VllmConfig(base_url="http://localhost:8001", api_key="test-key"))
    monkeypatch.setattr(client, "_post", lambda body: (_answer(good, tokens), 0.2))
    settings = settings_for(
        api.settings.serving_dir, api.database, llm_mode="vllm",
        vllm_base_url="http://localhost:8001", vllm_api_key="test-key",
    )  # fmt: skip
    for live in open_api(api.serving, settings, api.database, clock=api.clock, llm_client=client):
        return_id = golden_return(live.serving, GIULIA)
        body = _created(live, return_id, "clean")
        assert body["generation"]["llm_mode"] == "vllm" and body["proposal"] is not None
        assert body["provenance"]["nature"] == "real"
        stored_tokens = live.run(
            lambda conn, generation_id=body["generation"]["generation_id"]: conn.scalar(
                select(llm_generations.c.completion_tokens).where(
                    llm_generations.c.generation_id == generation_id
                )
            )
        )
        assert stored_tokens == expected


# ---- POST /api/inspections/{inspection_id}/confirm -----------------------------------------------


def test_a_confirmed_claim_is_attached_and_told_to_the_customer(api: Api) -> None:
    """Giulia's Q1 needs no approval: the customer hears the refund and the deduction (E17)."""
    return_id = golden_return(api.serving, GIULIA)
    created = _created(api, return_id, "damaged")
    response = _confirm(
        api, created["inspection_id"], CONFIRM_ALL, apply=True, note="Checked by hand"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    validate_body("inspection_confirm_response", body)
    claim, decision, message = body["claim"], body["decision"], body["outbox_message"]
    price = _price(api, return_id)
    amount = (price * Decimal("0.20")).quantize(Decimal("0.01"))
    assert Decimal(str(claim["amount"]["value"])) == amount
    assert Decimal(str(claim["line_price"]["value"])) == price
    assert claim["photo_evidence_ids"] == [created["photo_id"]] and claim["confirmed_by"] == "ahmed"
    assert decision["diminished_value_claim"] == claim
    assert body["inspection"]["state"] == "confirmed"
    assert message is not None and message["lang"] == "it" and message["created_by"] == "ahmed"
    assert Decimal(str(message["refund"]["value"])) == price - amount
    assert message["fields"]["deduction"] is not None
    logged = api.run(lambda conn: entries(conn, return_id=return_id))
    confirmed = next(e for e in logged if e.action == "inspection_confirmed")
    assert confirmed.reason == "Checked by hand" and confirmed.after["rule_id"] == "DV-RULE-1"
    assert confirmed.before == {"state": "awaiting_confirmation"}
    assert any(e.action == "message_queued" for e in logged)
    count = api.run(lambda conn: conn.scalar(select(outbox.c.message_id).limit(1)))
    assert count == message["message_id"]


def test_a_claim_awaiting_approval_is_not_announced(api: Api) -> None:
    """Rule 5, E1: Marco's Q4 proposal waits for Sara: no message, the claim stays on the
    decision (the approval message will state it)."""
    return_id, _ = marco_return(api)
    created = _created(api, return_id, "damaged")
    response = _confirm(api, created["inspection_id"], CONFIRM_ALL, apply=True)
    assert response.status_code == 200, response.text
    body = response.json()
    validate_body("inspection_confirm_response", body)
    assert body["claim"] is not None and body["outbox_message"] is None
    assert body["decision"]["requires_approval"] and body["decision"]["state"] == "proposed"


def test_after_the_approval_the_claim_is_announced(api: Api) -> None:
    return_id, _ = marco_return(api)
    created = _created(api, return_id, "damaged")

    async def approve(conn: AsyncConnection) -> None:
        row = await decision_of(conn, return_id)
        await approve_in_db(conn, row["decision_id"], "sara", api.clock.now())

    api.run(approve)
    response = _confirm(api, created["inspection_id"], CONFIRM_ALL, apply=True)
    assert response.status_code == 200, response.text
    body = response.json()
    validate_body("inspection_confirm_response", body)
    message = body["outbox_message"]
    assert message is not None and message["legal_clause_kind"] == "withdrawal"


def test_no_claim_without_confirmed_damage_or_proposal(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    created = _created(api, return_id, "damaged")
    contradicted = {**CONFIRM_ALL, "visible_damage": "no"}
    body = _error(
        _confirm(api, created["inspection_id"], contradicted, apply=True), 422, "invalid_request"
    )
    assert "visible_damage" in body["message"]
    unclear = _created(api, return_id, "unclear")
    _error(_confirm(api, unclear["inspection_id"], CONFIRM_ALL, apply=True), 422, "invalid_request")
    response = _confirm(api, unclear["inspection_id"], CONFIRM_ALL, apply=False)
    assert response.status_code == 200, response.text
    plain = response.json()
    validate_body("inspection_confirm_response", plain)
    assert plain["claim"] is None and plain["outbox_message"] is None
    assert plain["inspection"]["confirmed_observations"][1] == {
        "item": "visible_damage", "observation": "yes"
    }  # fmt: skip


def test_confirm_conflicts_and_roles(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    created = _created(api, return_id, "damaged")
    iid = created["inspection_id"]
    sara = {"inspector": "sara", "observations": [
        {"item": k, "observation": v} for k, v in CONFIRM_ALL.items()
    ], "apply_compensation": False}  # fmt: skip
    _error(api.client.post(f"/api/inspections/{iid}/confirm", json=sara), 403, "forbidden_role")
    _error(_confirm(api, "INS-NOPE", CONFIRM_ALL, apply=False), 404, "not_found")
    assert _confirm(api, iid, CONFIRM_ALL, apply=True).status_code == 200
    _error(_confirm(api, iid, CONFIRM_ALL, apply=True), 409, "conflict")
    second = _created(api, return_id, "damaged")
    body = _error(_confirm(api, second["inspection_id"], CONFIRM_ALL, apply=True), 409, "conflict")
    assert "already carries" in body["message"] and iid in body["message"]
    plain = _confirm(api, second["inspection_id"], {**CONFIRM_ALL, "visible_damage": "no"}, False)
    _error(plain, 409, "conflict")  # one claim per decision: no second confirmation, no message


def test_the_app_factory_needs_no_vision_setup(api: Api) -> None:
    """vllm mode without a reachable configuration: the template answers, no 500."""
    settings = settings_for(api.settings.serving_dir, api.database, llm_mode="vllm")
    app = create_app(settings, clock=api.clock, serving=api.serving)
    assert app.state.runtime.settings.llm_mode == "vllm"
    for live in open_api(api.serving, settings, api.database, clock=api.clock):
        body = _created(live, golden_return(live.serving, GIULIA), "damaged")
        assert body["generation"]["fallback_reason"] == "unavailable"
