"""Warehouse inspections (US-501..US-503, flow F3, spec 8.8; ADR-15 section 4 "Inspection";
rules 1, 3, 5, 10 and 11; LEG-05).

POST /api/inspections: Ahmed's photo in, a suggested checklist out (tag attached, visible
damage, photo quality; "not observable" is a valid answer). The checklist comes from:

- LLM_MODE=mock: MockVision, a deterministic stand-in that never looks at the picture: a file
  that is not an image of its declared type reads as an unusable photo, and any other photo
  gets one of three fixed checklists chosen by its sha256 (the same photo, the same answer);
- LLM_MODE=vllm: VllmVision, the vision part of Gemma through the same llm.client.VllmClient
  (local vLLM only, rule 2), with guided JSON; an answer that fails the checks is asked once
  more, then replaced by the template (spec 8.7);
- DEMO_MODE=cached, or vLLM down: the template, every item "not observable", labelled with its
  path and fallback reason.

The model's confidence is declared, never calibrated (confidence_is_calibrated false): it is
only a gate. The system stops with manual_review and its reason, and makes no proposal, when
the photo is not usable, an item is not observable, a declared confidence is below
CONFIDENCE_GATE, or no model answered (US-503). Otherwise a compensation is proposed only from
an approved rule (APPROVED_RULES: DV-RULE-1, visible damage on the withdrawal path) on the price
of the returned line, never on a defect (rule 4).

POST /api/inspections/{inspection_id}/confirm: Ahmed confirms every observation himself. A
claim needs the proposal of an approved rule and Ahmed's "yes" on every observation it rests
on, visible damage included: the vision output never sets an amount on its own (US-502). The
claim is recomputed from the confirmed observations and the rule, checked by policy.legal_guard,
attached to the decision (the inspections row), and the customer is told the refund it leaves
and the amount withheld with its reason, exactly when the decision awaits no person's approval:
an unapproved Q3 or Q4 proposal is never announced (rule 5, ADR-15 E1).

Contract 1.5.0 (ADR-15 section 15):

- the vision call runs with no database transaction open (H20): the case is read in one
  transaction, the checklist made on the runtime's model-call pool, the inspection stored in a
  second one;
- the confirmation locks the return first (SELECT ... FOR UPDATE, as the decision acts do),
  confirms the inspection only once (a conditional UPDATE) and makes at most one claim per
  return (uq_inspections_one_claim_per_return), so concurrent confirmations and approvals
  cannot both act (H19, H13); the claim is on the price paid for the line (H2);
- a message that would state a refund deadline already past is refused (409, H1).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Literal, Protocol

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import conflict, invalid_request, legal_guard, not_found
from apps.api.services import decisions as store
from apps.api.services import outbox
from apps.api.services.audit import record as audit
from apps.api.services.generation import info_of, load_draft
from apps.api.services.operator.shown import return_of
from apps.api.services.other.common import line_prices, next_number
from apps.api.services.provenance import provenance
from apps.api.serving import Serving
from apps.api.settings import Settings
from contracts.sql import inspections, llm_generations
from contracts.vocab import (
    CHECKLIST_ITEMS,
    ChecklistItemId,
    FallbackReason,
    Observation,
    PhotoMediaType,
    ValidatorCode,
)
from llm.client import TEMPERATURE, LlmUnavailable, VllmClient, completion_token_count
from llm.text import banned_terms, foreign_characters
from policy.legal_guard import check
from policy.types import DiminishedValueClaim
from policy.types import Path as ReturnPath

CONFIDENCE_GATE: Final = 0.6  # below this declared confidence the system stops (US-503)
MESSAGE_LANG: Final = "it"  # the merchant's language: the customer has no other on file
MOCK_MODEL: Final = "mock"
TEMPLATE_MODEL: Final = "template"  # no model call: the template checklist
VISION_TOKENS: Final = 400
MAX_VISION_CALLS: Final = 2  # one more call after a refused answer, then the template (8.7)
JPEG_MAGIC: Final = b"\xff\xd8\xff"
_MAGIC: Final[dict[str, Callable[[bytes], bool]]] = {
    "image/jpeg": lambda b: b.startswith(JPEG_MAGIC),
    "image/png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/webp": lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP",
}
VISION_PROMPT: Final = (
    "You look at one photo of a returned fashion item for the warehouse of an online shop. For "
    "each item of the checklist answer yes, no or not_observable, with your confidence between "
    "0 and 1 and at most one short note in English: tag_attached (the original tag is attached), "
    "visible_damage (stains, tears or wear can be seen), photo_quality (the photo is sharp and "
    "lit enough to judge the item). Answer not_observable when the photo does not show it. "
    "Describe only what the photo shows: never the customer, the cause or the time of a damage."
)
PROMPT_VERSION: Final = (
    "vision-v1-" + hashlib.sha256(VISION_PROMPT.encode("utf-8")).hexdigest()[:12]
)
CHECKLIST_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "minItems": len(CHECKLIST_ITEMS),
            "maxItems": len(CHECKLIST_ITEMS),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item", "suggestion", "confidence", "note"],
                "properties": {
                    "item": {"enum": list(CHECKLIST_ITEMS)},
                    "suggestion": {"enum": ["yes", "no", "not_observable"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "note": {"type": ["string", "null"], "maxLength": 120},
                },
            },
        }
    },
}


# ---- The approved compensation rules (US-502, art. 14(2) of Directive 2011/83/EU) --------------


@dataclass(frozen=True)
class CompensationRule:
    """A rule the manager approved: the observations it rests on and the share of the price of
    the returned line it withholds."""

    rule_id: str
    basis: tuple[ChecklistItemId, ...]
    share: Decimal
    text: str

    def amount(self, line_price: Decimal) -> Decimal:
        return (line_price * self.share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


APPROVED_RULES: Final[dict[str, CompensationRule]] = {
    "DV-RULE-1": CompensationRule(
        rule_id="DV-RULE-1",
        basis=("visible_damage",),
        share=Decimal("0.20"),
        text="Visible damage, not resellable as new: 20% of the line price (rule DV-RULE-1)",
    )
}
DAMAGE_RULE: Final = APPROVED_RULES["DV-RULE-1"]


# ---- Vision -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Photo:
    data: bytes
    media_type: PhotoMediaType

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def readable(self) -> bool:
        return _MAGIC[self.media_type](self.data)


@dataclass(frozen=True)
class VisionResult:
    """A checklist with the path that produced it (the GenerationInfo fields of spec 8.7)."""

    items: list[s.ChecklistSuggestion]
    path: Literal["model", "regenerated", "template"]
    fallback_reason: FallbackReason | None
    model_calls: int
    validator_codes: list[ValidatorCode]
    model: str
    llm_mode: Literal["mock", "vllm", "tokenfactory"]
    latency_ms: int | None = None
    completion_tokens: int | None = None
    answers: list[Any] = field(default_factory=list)


class VisionModel(Protocol):
    def checklist(self, photo: Photo, return_id: str) -> VisionResult: ...


def _item(
    item: ChecklistItemId, suggestion: Observation, confidence: float, note: str | None
) -> s.ChecklistSuggestion:
    return s.ChecklistSuggestion(
        item=item, suggestion=suggestion, model_confidence=confidence, note=note
    )


MockOutcome = Literal["clean", "damaged", "unclear", "unreadable"]
_MOCK: Final[dict[MockOutcome, tuple[tuple[Observation, float, str | None], ...]]] = {
    "clean": (("yes", 0.91, "Tag attached"), ("no", 0.86, None), ("yes", 0.94, None)),
    "damaged": (
        ("no", 0.78, "No tag visible"),
        ("yes", 0.71, "Stain near the collar"),
        ("yes", 0.93, None),
    ),
    "unclear": (
        ("not_observable", 0.35, None),
        ("not_observable", 0.3, None),
        ("no", 0.88, "Too dark"),
    ),
    "unreadable": (
        ("not_observable", 0.0, None),
        ("not_observable", 0.0, None),
        ("no", 0.99, "Not a readable image of its declared type"),
    ),
}
_MOCK_CYCLE: Final[tuple[MockOutcome, ...]] = ("clean", "damaged", "unclear")


def mock_outcome(data: bytes, media_type: PhotoMediaType = "image/jpeg") -> MockOutcome:
    """The fixed checklist the mock gives a photo: unreadable when the bytes are not an image of
    the declared type, otherwise chosen by the first byte of its sha256."""
    photo = Photo(data, media_type)
    if not photo.readable:
        return "unreadable"
    return _MOCK_CYCLE[hashlib.sha256(data).digest()[0] % len(_MOCK_CYCLE)]


class MockVision:
    """LLM_MODE=mock: deterministic, no model call, no latency recorded (like the mock drafts)."""

    def checklist(self, photo: Photo, return_id: str) -> VisionResult:
        answers = _MOCK[mock_outcome(photo.data, photo.media_type)]
        items = [
            _item(item, *answer) for item, answer in zip(CHECKLIST_ITEMS, answers, strict=True)
        ]
        return VisionResult(items, "model", None, 1, [], MOCK_MODEL, "mock")


def template_result(
    reason: FallbackReason,
    llm_mode: Literal["mock", "vllm", "tokenfactory"],
    model: str,
    calls: int = 0,
    codes: Sequence[ValidatorCode] = (),
) -> VisionResult:
    """No model answer: every item not observable (the gate then stops at manual review). With
    no model call the label is the template, never a model that did not run (H30)."""
    items = [_item(item, "not_observable", 0.0, None) for item in CHECKLIST_ITEMS]
    label = model if calls else TEMPLATE_MODEL
    return VisionResult(items, "template", reason, calls, list(codes), label, llm_mode)


def parse_checklist(
    content: str,
) -> tuple[list[s.ChecklistSuggestion] | None, list[ValidatorCode]]:
    """The checklist of a model answer, or None with the llm.validator codes found: SCHEMA for
    an answer off the guided schema, BANNED_TERM and SCRIPT for a note (rule 11)."""
    try:
        data = json.loads(content)
        rows = data["items"]
        if not isinstance(rows, list) or set(data) != {"items"}:
            raise ValueError("not the checklist object")
        by_item = {str(r["item"]): r for r in rows}
        if [str(r["item"]) for r in rows] != list(CHECKLIST_ITEMS) or len(by_item) != 3:
            raise ValueError("the three items, in order")
        items = [
            s.ChecklistSuggestion(
                item=r["item"],
                suggestion=r["suggestion"],
                model_confidence=float(r["confidence"]),
                note=(str(r["note"]).strip() or None) if r.get("note") is not None else None,
            )
            for r in rows
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, ["SCHEMA"]
    codes: list[ValidatorCode] = []
    notes = " ".join(i.note for i in items if i.note)
    if banned_terms(notes):
        codes.append("BANNED_TERM")
    if foreign_characters(notes):
        codes.append("SCRIPT")
    return (None, codes) if codes else (items, [])


Post = Callable[[Mapping[str, Any]], tuple[dict[str, Any], float]]


class VllmVision:
    """LLM_MODE=vllm: the photo and the checklist schema to Gemma on the local vLLM, through the
    transport of llm.client.VllmClient (allow-listed local URL, no proxy, no redirect)."""

    def __init__(self, client: VllmClient, post: Post | None = None) -> None:
        self._client = client
        self._post: Post = post or client._post  # the client's own transport (rule 2)

    def _body(self, photo: Photo, return_id: str, retry: Sequence[str]) -> dict[str, Any]:
        url = f"data:{photo.media_type};base64,{base64.b64encode(photo.data).decode('ascii')}"
        text = f"Checklist for the returned item of {return_id}."
        if retry:
            text += f" Your previous answer was refused ({', '.join(retry)}): follow the schema."
        return {
            "model": self._client.config.model,
            "messages": [
                {"role": "system", "content": VISION_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                },
            ],
            "temperature": TEMPERATURE,
            "max_tokens": VISION_TOKENS,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "inspection_checklist",
                    "schema": CHECKLIST_SCHEMA,
                    "strict": True,
                },
            },
        }

    def checklist(self, photo: Photo, return_id: str) -> VisionResult:
        model = self._client.config.model
        codes: list[ValidatorCode] = []
        calls = 0
        tokens: int | None = None
        started = time.perf_counter()
        for _ in range(MAX_VISION_CALLS):
            try:
                data, _ = self._post(self._body(photo, return_id, codes))
            except LlmUnavailable:
                return template_result("unavailable", "vllm", model, calls, _unique(codes))
            calls += 1
            used = completion_token_count(data)
            if used is not None:
                tokens = (tokens or 0) + used
            content = _content(data)
            found: list[ValidatorCode] = ["SCHEMA"]
            items = None
            if content is not None:
                items, found = parse_checklist(content)
            if items is not None:
                return VisionResult(
                    items,
                    "model" if calls == 1 else "regenerated",
                    None,
                    calls,
                    _unique(codes),
                    model,
                    "vllm",
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    completion_tokens=tokens,
                )
            codes += found
        return template_result("invalid_twice", "vllm", model, calls, _unique(codes))


def _unique(codes: Sequence[ValidatorCode]) -> list[ValidatorCode]:
    return list(dict.fromkeys(codes))


def _content(data: Mapping[str, Any]) -> str | None:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def vision_model(settings: Settings, client: object) -> VisionModel | None:
    """The vision model of the settings; None when no model can answer (cached mode, or a
    vLLM client that cannot be built): the template then answers."""
    if settings.cached:
        return None
    if settings.llm_mode == "mock":
        return MockVision()
    if settings.llm_mode == "tokenfactory":
        return None  # shared Gemma is text-only: every observation needs manual review
    return VllmVision(client) if isinstance(client, VllmClient) else None


# ---- The gate (US-503) and the proposal (US-502) -------------------------------------------------

_LABELS: Final[dict[str, str]] = {
    "tag_attached": "the tag",
    "visible_damage": "visible damage",
    "photo_quality": "the photo quality",
}


@dataclass(frozen=True)
class Assessment:
    state: Literal["awaiting_confirmation", "manual_review"]
    manual_review_reason: str | None
    proposal: s.CompensationProposal | None


def assess(
    result: VisionResult,
    path: ReturnPath,
    line_price: Decimal,
    claim_provenance: s.Provenance,
) -> Assessment:
    """Stop when unsure (no proposal), else propose the approved rule the checklist meets."""
    if result.path == "template":
        reason = {
            "unavailable": "No vision model answered (GPU off or model unavailable)",
            "invalid_twice": "The vision model gave no valid checklist",
            "decision_echo": "The vision model gave no valid checklist",
        }[result.fallback_reason or "unavailable"]
        return Assessment("manual_review", f"{reason}: verify by hand", None)
    by_item = {i.item: i for i in result.items}
    if by_item["photo_quality"].suggestion != "yes":
        return Assessment(
            "manual_review", "The photo is not clear enough to check the item: verify by hand", None
        )
    unsure = [
        i.item
        for i in result.items
        if i.suggestion == "not_observable" or i.model_confidence < CONFIDENCE_GATE
    ]
    if unsure:
        what = ", ".join(_LABELS[i] for i in unsure)
        return Assessment("manual_review", f"Not sure about {what}: verify by hand", None)
    proposal = None
    meets = all(by_item[i].suggestion == "yes" for i in DAMAGE_RULE.basis)
    amount = DAMAGE_RULE.amount(line_price)
    if path is ReturnPath.WITHDRAWAL and meets and amount > 0:
        proposal = s.CompensationProposal(
            rule_id=DAMAGE_RULE.rule_id,
            amount=s.Metric(value=float(amount), unit="eur", provenance=claim_provenance),
            basis=list(DAMAGE_RULE.basis),
            text=DAMAGE_RULE.text,
        )
    return Assessment("awaiting_confirmation", None, proposal)


# ---- Storage and views --------------------------------------------------------------------------


def decode(photo: s.PhotoUpload) -> Photo:
    try:
        data = base64.b64decode(photo.data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise invalid_request(
            "the photo is not valid base64", [("invalid_base64", "body.photo.data_base64")]
        ) from None
    if not data:
        raise invalid_request("the photo is empty", [("empty", "body.photo.data_base64")])
    return Photo(data, photo.media_type)


def _vision_provenance(result: VisionResult, photo_id: str, at: datetime) -> s.Provenance:
    day = s.utc_day(at)
    if result.path == "template":
        origin = f"template checklist for photo {photo_id}: no model answer, nothing observed"
        return provenance(origin, day, day, "synthetic")
    if result.llm_mode == "mock":
        origin = (
            f"vision checklist of the deterministic mock on photo {photo_id} (declared "
            "confidences, not calibrated)"
        )
        return provenance(origin, day, day, "synthetic")
    origin = (
        f"vision checklist of {result.model} on photo {photo_id} (declared confidences, not "
        "calibrated)"
    )
    return provenance(origin, day, day, "real")


def generation_of(result: VisionResult, generation_id: str, at: datetime) -> s.GenerationInfo:
    """The generation record of a checklist, as served (the contract examples use it too)."""
    return s.GenerationInfo(
        generation_id=generation_id,
        generation_path=result.path,
        fallback_reason=result.fallback_reason,
        llm_mode=result.llm_mode,
        served_from="live",
        model=result.model,
        model_calls=result.model_calls,
        validator_codes=result.validator_codes,
        created_at=at,
        latency_ms=result.latency_ms,
    )


def proposal_provenance(rule_id: str, inspection_id: str, at: datetime) -> s.Provenance:
    day = s.utc_day(at)
    origin = (
        f"diminished-value proposal: approved rule {rule_id} on the price of the returned line, "
        f"inspection {inspection_id}"
    )
    return provenance(origin, day, day, "synthetic")


def inspection_view(row: Mapping[str, Any], generation: Mapping[str, Any]) -> s.InspectionView:
    """The API view of an inspections row and its llm_generations row."""
    observed = row["confirmed_observations"]
    proposal = row["proposal"]
    return s.InspectionView(
        inspection_id=row["inspection_id"],
        return_id=row["return_id"],
        decision_id=row["decision_id"],
        path=row["path"],
        inspector=row["inspector"],
        photo_id=row["photo_id"],
        created_at=row["created_at"],
        state=row["state"],
        checklist=[s.ChecklistSuggestion.model_validate(c) for c in row["checklist"]],
        manual_review_reason=row["manual_review_reason"],
        proposal=None if proposal is None else s.CompensationProposal.model_validate(proposal),
        confirmed_observations=(
            None
            if observed is None
            else [s.ConfirmedObservation.model_validate(o) for o in observed]
        ),
        confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"],
        generation=info_of(generation),
        provenance=s.Provenance.model_validate(generation["llm_case"]["provenance"]),
    )


async def _rows(
    conn: AsyncConnection, inspection_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    row = (
        (
            await conn.execute(
                select(inspections).where(inspections.c.inspection_id == inspection_id)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    generation = (
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
    return dict(row), dict(generation)


def _with_path(row: Mapping[str, Any], path: ReturnPath) -> dict[str, Any]:
    return {**row, "path": path.value}


# ---- POST /api/inspections ----------------------------------------------------------------------


async def latest_case(
    conn: AsyncConnection, serving: Serving, return_id: str, at: datetime
) -> tuple[s.DecisionView, Decimal]:
    """The latest decision version of a queued return and the price paid for its line (404)."""
    prices = await line_prices(conn, serving, [return_id])
    found = await store.latest_view(conn, return_id, at, line_price=prices.get)
    if found is None or return_id not in prices:
        raise not_found(f"return {return_id}")
    return found[0], prices[return_id]


def checklist(
    vision: VisionModel | None, settings: Settings, photo: Photo, return_id: str
) -> VisionResult:
    """The checklist of the photo (a blocking model call in vllm mode: run it on the model-call
    pool, with no transaction open), or the template when no model can answer."""
    if vision is None:
        model = settings.gemma_model if settings.served_llm_mode != "mock" else MOCK_MODEL
        return template_result("unavailable", settings.served_llm_mode, model)
    return vision.checklist(photo, return_id)


async def create(
    conn: AsyncConnection,
    serving: Serving,
    settings: Settings,
    body: s.InspectionCreateRequest,
    photo: Photo,
    result: VisionResult,
    at: datetime,
    request_id: str | None = None,
) -> s.InspectionView:
    """Store the photo's checklist for the latest decision version of the return."""
    view, price = await latest_case(conn, serving, body.return_id, at)
    number = await next_number(conn, "INS", inspections)
    inspection_id, photo_id = f"INS-{number:06d}", f"PH-{number:06d}"
    generation_id = f"GEN-INS-{number:06d}"
    claim_prov = proposal_provenance(DAMAGE_RULE.rule_id, inspection_id, at)
    verdict = assess(result, view.path, price, claim_prov)
    vision_prov = _vision_provenance(result, photo_id, at)
    info = generation_of(result, generation_id, at)
    inspection = s.InspectionView(
        inspection_id=inspection_id,
        return_id=body.return_id,
        decision_id=view.decision_id,
        path=view.path,
        inspector=body.inspector,
        photo_id=photo_id,
        created_at=at,
        state=verdict.state,
        checklist=result.items,
        manual_review_reason=verdict.manual_review_reason,
        proposal=verdict.proposal,
        confirmed_observations=None,
        confirmed_by=None,
        confirmed_at=None,
        generation=info,
        provenance=vision_prov,
    )
    suggested = [i.model_dump(mode="json") for i in result.items]
    tokens = result.completion_tokens
    latency = result.latency_ms
    await conn.execute(
        insert(llm_generations).values(
            generation_id=generation_id,
            kind="inspection_checklist",
            return_id=body.return_id,
            decision_id=view.decision_id,
            model=info.model,
            llm_mode=info.llm_mode,
            served_from=info.served_from,
            generation_path=info.generation_path,
            fallback_reason=info.fallback_reason,
            model_calls=info.model_calls,
            prompt_version=PROMPT_VERSION,
            input_digest=photo.sha256,
            llm_case={
                "kind": "inspection_checklist",
                "return_id": body.return_id,
                "photo_id": photo_id,
                "photo_sha256": photo.sha256,
                "media_type": photo.media_type,
                "provenance": vision_prov.model_dump(mode="json"),
            },
            output={"items": suggested},
            validator_codes=list(info.validator_codes),
            latency_ms=latency,
            prompt_tokens=None,
            completion_tokens=tokens,
            tokens_per_second=(tokens / (latency / 1000)) if tokens and latency else None,
            created_at=at,
        )
    )
    await conn.execute(
        insert(inspections).values(
            inspection_id=inspection_id,
            return_id=body.return_id,
            decision_id=view.decision_id,
            inspector=body.inspector,
            photo_id=photo_id,
            photo_media_type=photo.media_type,
            photo_sha256=photo.sha256,
            state=verdict.state,
            checklist=suggested,
            min_model_confidence=min(i.model_confidence for i in result.items),
            manual_review_reason=verdict.manual_review_reason,
            proposal=None if verdict.proposal is None else verdict.proposal.model_dump(mode="json"),
            confirmed_observations=None,
            confirmed_by=None,
            confirmed_at=None,
            compensation_applied=False,
            compensation_eur=None,
            compensation_rule_id=None,
            generation_id=generation_id,
            created_at=at,
        )
    )
    await audit(
        conn,
        at=at,
        actor=body.inspector,
        action="inspection_created",
        entity_type="inspection",
        entity_id=inspection_id,
        return_id=body.return_id,
        decision_id=view.decision_id,
        after={"state": verdict.state},
        rules_version=view.rules_version,
        model_versions=view.model_versions,
        assumptions_version=view.assumptions_version,
        request_id=request_id,
    )
    return inspection


# ---- POST /api/inspections/{inspection_id}/confirm ----------------------------------------------


def _claim_amount(
    row: Mapping[str, Any], body: s.InspectionConfirmRequest, path: ReturnPath, price: Decimal
) -> tuple[CompensationRule, Decimal]:
    """The rule and the amount of the claim Ahmed applies, recomputed from his observations."""
    if row["proposal"] is None:
        raise invalid_request(
            "no compensation was proposed for this inspection (manual review or nothing to "
            "claim): a claim needs the proposal of an approved rule",
            [("no_proposal", "body.apply_compensation")],
        )
    proposal = s.CompensationProposal.model_validate(row["proposal"])
    rule = APPROVED_RULES.get(proposal.rule_id)
    if rule is None or path is not ReturnPath.WITHDRAWAL:
        raise invalid_request(
            f"{proposal.rule_id} is not an approved rule for this return",
            [("no_rule", "body.apply_compensation")],
        )
    observed = {o.item: o.observation for o in body.observations}
    missing = [item for item in rule.basis if observed[item] != "yes"]
    if missing:
        raise invalid_request(
            f"the claim rests on observations Ahmed did not confirm: {', '.join(missing)} "
            "(never on vision alone, US-502)",
            [("observation_not_confirmed", f"body.observations.{m}") for m in missing],
        )
    return rule, rule.amount(price)


async def confirm(
    conn: AsyncConnection,
    serving: Serving,
    inspection_id: str,
    body: s.InspectionConfirmRequest,
    at: datetime,
    request_id: str | None = None,
) -> s.InspectionConfirmResponse:
    """Confirm the observations and, when it holds, the claim (see the module docstring)."""
    found = await _rows(conn, inspection_id)
    if found is None:
        raise not_found(f"inspection {inspection_id}")
    return_id = str(found[0]["return_id"])
    await return_of(conn, return_id, lock=True)  # the lock of the decision acts (H19)
    found = await _rows(conn, inspection_id)  # read again under the lock
    assert found is not None
    row, generation = found
    if row["state"] == "confirmed":
        raise conflict(f"{inspection_id} is already confirmed")
    prices = await line_prices(conn, serving, [return_id])
    latest = await store.latest_view(conn, return_id, at, line_price=prices.get)
    if latest is None or return_id not in prices:
        raise not_found(f"return {return_id}")
    view, decision_row = latest
    if view.decision_id != row["decision_id"]:
        raise conflict(
            f"{inspection_id} was made for {row['decision_id']}; the case is now at "
            f"{view.decision_id}: inspect the item again"
        )
    earlier = view.diminished_value_claim
    if earlier is not None:  # one claim per return, confirmed with its own inspection (H13)
        raise conflict(
            f"{return_id} already carries the diminished-value claim of "
            f"{earlier.inspection_id}: no other inspection is confirmed for it"
        )
    rule: CompensationRule | None = None
    amount: Decimal | None = None
    if body.apply_compensation:
        rule, amount = _claim_amount(row, body, view.path, prices[return_id])
        decision, request = s.to_policy_decision(view)
        claimed = replace(
            decision,
            diminished_value_claim=DiminishedValueClaim(
                amount_eur=amount,
                photo_evidence_ids=(str(row["photo_id"]),),
                confirmed_by=body.inspector,
                rule_id=rule.rule_id,
            ),
        )
        violations = check(claimed, request, view.inputs.base_terms.to_policy())
        if violations:
            raise legal_guard(violations)
        awaiting = view.state == "proposed" and view.requires_approval
        if not awaiting:  # a message will state the refund: never a past deadline (H1)
            outbox.check_refund_due(view, at)
    observations = [o.model_dump(mode="json") for o in body.observations]
    try:
        result = await conn.execute(
            update(inspections)
            .where(
                (inspections.c.inspection_id == inspection_id)
                & (inspections.c.state != "confirmed")
            )
            .values(
                state="confirmed",
                confirmed_observations=observations,
                confirmed_by=body.inspector,
                confirmed_at=at,
                compensation_applied=rule is not None,
                compensation_eur=amount,
                compensation_rule_id=None if rule is None else rule.rule_id,
            )
        )
    except IntegrityError:
        raise conflict(f"{return_id} already carries a diminished-value claim") from None
    if result.rowcount != 1:
        raise conflict(f"{inspection_id} was confirmed meanwhile")
    after = await store.view_of(conn, decision_row, at, line_price=prices.get)
    claim = after.diminished_value_claim
    confirmed = {
        **row,
        "state": "confirmed",
        "confirmed_observations": observations,
        "confirmed_by": body.inspector,
        "confirmed_at": at,
    }
    inspection = inspection_view(_with_path(confirmed, view.path), generation)
    payload: dict[str, Any] = {"state": "confirmed"}
    if claim is not None:
        payload.update(compensation=claim.amount.model_dump(mode="json"), rule_id=claim.rule_id)
    note = (body.note or "").strip()
    await audit(
        conn,
        at=at,
        actor=body.inspector,
        action="inspection_confirmed",
        entity_type="inspection",
        entity_id=inspection_id,
        return_id=return_id,
        decision_id=view.decision_id,
        reason=note if len(note) >= 3 else None,
        before={"state": row["state"]},
        after=payload,
        rules_version=view.rules_version,
        model_versions=view.model_versions,
        assumptions_version=view.assumptions_version,
        request_id=request_id,
    )
    message = None
    awaiting = after.state == "proposed" and after.requires_approval
    if claim is not None and not awaiting:
        draft = await load_draft(conn, after)
        refund = claim.refund()  # its own provenance, not the claim's (rule 11)
        composed = await outbox.compose(
            conn,
            after,
            lang=MESSAGE_LANG,
            created_by=body.inspector,
            created_at=at,
            preferred=None if draft is None else draft.message.sentence_ids,
            refund=refund,
        )
        message = await outbox.queue(conn, composed, after, request_id)
    return s.InspectionConfirmResponse(
        inspection=inspection, claim=claim, decision=after, outbox_message=message
    )
