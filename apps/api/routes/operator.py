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
written together or not at all. Every error is an ErrorResponse (apps.api.errors).
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
from apps.api.services.operator.actions import act
from apps.api.services.operator.drafts import regenerate
from apps.api.services.operator.graph import view_for
from apps.api.services.operator.queue import decision_record, queue
from apps.api.services.operator.recompute import add_evidence, dispute

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
    async with rt.transaction() as conn:
        result = await act(conn, serving, return_id, body, rt.now(), request_id_of(request))
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
    async with rt.transaction() as conn:
        result = await add_evidence(
            conn, serving, rt.generator, return_id, body, rt.now(), request_id_of(request)
        )
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
    async with rt.transaction() as conn:
        result = await dispute(
            conn, serving, rt.generator, link_id, body, rt.now(), request_id_of(request)
        )
    return _json(result)


add_stubs(router, "operator")
