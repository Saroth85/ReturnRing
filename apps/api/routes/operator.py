"""The operator's area (view 1; E3: US-301..US-310, US-604, US-607; ADR-15 sections 3 and 4).

- GET /api/returns: the queue, sorted by expected net benefit, with filters, pages and review_due;
- GET /api/returns/{return_id}: the case card (services.cases.case_card) on the account graph
  its latest version was decided on (services.operator.graph);
- POST /api/returns/{return_id}/decision: approve, override, reject, lift, renew;
- GET /api/decisions/{decision_id}: any version of a decision (US-604);
- POST /api/returns/{return_id}/regenerate: a new draft through llm/;
- POST /api/returns/{return_id}/evidence and POST /api/links/{link_id}/dispute: a recompute.

Every route checks the role of the actor first (403 forbidden_role, ADR-15 section 5), then runs
its service in one transaction: the decision, its audit entries and its outbox message are
written together or not at all. A draft is made with no transaction open, on the runtime's
model-call pool, and stored in a second transaction (1.5.0, H20): regenerate, the recompute of
evidence and disputes, and the recompute of a stale restriction proposal, which answers 409
once the case is decided again (H16). Every error is an ErrorResponse (apps.api.errors).
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from apps.api import schemas as s
from apps.api.errors import not_found, request_id_of
from apps.api.roles import authorize
from apps.api.routes.stubs import add_stubs, responses
from apps.api.runtime import runtime
from apps.api.services import decisions as store
from apps.api.services.cases import case_card
from apps.api.services.operator.actions import StaleProposal, act, recompute_stale
from apps.api.services.operator.drafts import regenerate
from apps.api.services.operator.graph import view_for
from apps.api.services.operator.queue import decision_record, queue
from apps.api.services.operator.recompute import (
    add_evidence,
    dispute,
    finish,
    finish_dispute,
    make_drafts,
    store_draft,
)

router = APIRouter()
_BY_ROUTE: Final = {(ep.method, ep.path): ep for ep in s.ENDPOINTS}
QUEUE = _BY_ROUTE[("GET", "/api/returns")]
CASE_CARD = _BY_ROUTE[("GET", "/api/returns/{return_id}")]
DECISION = _BY_ROUTE[("POST", "/api/returns/{return_id}/decision")]
RECORD = _BY_ROUTE[("GET", "/api/decisions/{decision_id}")]
REGENERATE = _BY_ROUTE[("POST", "/api/returns/{return_id}/regenerate")]
EVIDENCE = _BY_ROUTE[("POST", "/api/returns/{return_id}/evidence")]
DISPUTE = _BY_ROUTE[("POST", "/api/links/{link_id}/dispute")]

ReturnId = Annotated[str, PathParam(max_length=96)]


def _json(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status)


def _route(ep: s.Endpoint) -> dict[str, Any]:
    return {
        "response_model": s.API_MODELS[ep.response],
        "responses": responses(ep),
        "summary": ep.summary,
        "status_code": ep.status,
        "tags": ["operator"],
    }


@router.get(QUEUE.path, **_route(QUEUE))
async def get_queue(request: Request, query: Annotated[s.QueueQuery, Query()]) -> JSONResponse:
    authorize(request, QUEUE.method, QUEUE.path)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        body = await queue(conn, serving, query, rt.now())
    return _json(body)


@router.get(CASE_CARD.path, **_route(CASE_CARD))
async def get_case_card(request: Request, return_id: ReturnId) -> JSONResponse:
    authorize(request, CASE_CARD.method, CASE_CARD.path)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        rows = await store.decision_rows(conn, return_id)
        if not rows:
            raise not_found(f"return {return_id}")
        graph = await view_for(conn, serving, rows[-1])
        card = await case_card(conn, graph, return_id, rt.now())
    if card is None:
        raise not_found(f"return {return_id}")
    return _json(card)


@router.post(DECISION.path, **_route(DECISION))
async def post_decision(
    request: Request, return_id: ReturnId, body: Annotated[s.DecisionRequest, Body()]
) -> JSONResponse:
    authorize(request, DECISION.method, DECISION.path, body)
    rt = runtime(request)
    serving = rt.require_serving()
    request_id = request_id_of(request)
    try:
        async with rt.transaction() as conn:
            result = await act(conn, serving, return_id, body, rt.now(), request_id)
    except StaleProposal as stale:
        # The proposal reached its review date before anyone acted on it: decide the case again
        # (trigger stale_proposal), store its draft, then answer the 409 (H16).
        now = rt.now()
        async with rt.transaction() as conn:
            pending = await recompute_stale(
                conn, serving, return_id, stale.decision_id, now, request_id
            )
        if pending is not None:
            outcomes = await rt.model_call(make_drafts, rt.generator, [pending], now)
            async with rt.transaction() as conn:
                await store_draft(conn, pending, outcomes[0], now, request_id)
            stale.recomputed(pending.new.decision_id)
        raise stale from None
    return _json(result)


@router.get(RECORD.path, **_route(RECORD))
async def get_decision(
    request: Request, decision_id: Annotated[str, PathParam(max_length=96)]
) -> JSONResponse:
    authorize(request, RECORD.method, RECORD.path)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        found = await decision_record(conn, serving, decision_id, rt.now())
    if found is None:
        raise not_found(f"decision {decision_id}")
    return _json(found)


@router.post(REGENERATE.path, **_route(REGENERATE))
async def post_regenerate(
    request: Request, return_id: ReturnId, body: Annotated[s.RegenerateRequest, Body()]
) -> JSONResponse:
    authorize(request, REGENERATE.method, REGENERATE.path, body)
    rt = runtime(request)
    result = await regenerate(rt, return_id, body, rt.now(), request_id_of(request))
    return _json(result)


@router.post(EVIDENCE.path, **_route(EVIDENCE))
async def post_evidence(
    request: Request, return_id: ReturnId, body: Annotated[s.EvidenceRequest, Body()]
) -> JSONResponse:
    authorize(request, EVIDENCE.method, EVIDENCE.path, body)
    rt = runtime(request)
    serving = rt.require_serving()
    now, request_id = rt.now(), request_id_of(request)
    async with rt.transaction() as conn:
        pending = await add_evidence(conn, serving, return_id, body, now, request_id)
    outcomes = await rt.model_call(make_drafts, rt.generator, [pending], now)
    async with rt.transaction() as conn:
        result = await finish(conn, serving, pending, outcomes[0], now, request_id)
    return _json(result)


@router.post(DISPUTE.path, **_route(DISPUTE))
async def post_dispute(
    request: Request,
    link_id: Annotated[str, PathParam(max_length=96)],
    body: Annotated[s.LinkDisputeRequest, Body()],
) -> JSONResponse:
    authorize(request, DISPUTE.method, DISPUTE.path, body)
    rt = runtime(request)
    serving = rt.require_serving()
    now, request_id = rt.now(), request_id_of(request)
    async with rt.transaction() as conn:
        pending = await dispute(conn, serving, link_id, body, now, request_id)
    outcomes = await rt.model_call(make_drafts, rt.generator, pending.recomputes, now)
    async with rt.transaction() as conn:
        result = await finish_dispute(conn, serving, pending, outcomes, now, request_id)
    return _json(result)


add_stubs(router, "operator")
