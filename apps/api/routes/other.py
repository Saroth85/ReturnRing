"""The other areas (view 4 and the shop; US-401..US-403, US-501..US-503, US-701, US-702, US-902,
US-905): simulated checkout, warehouse inspections, the read-only agent, benchmarks and
telemetry. The services are in apps/api/services/other/.

- POST /api/checkout/evaluate and /confirm: called by the simulated shop, no role (ADR-15 5);
- POST /api/inspections and /{inspection_id}/confirm: Ahmed (warehouse) only;
- POST /api/agent/ask: Sara or Luca; text/event-stream framed by sse_frame; 429 past the rate
  limit, 503 when no model can answer;
- GET /api/benchmarks and /api/telemetry: every role;
- GET /api/demo/cases: the golden cases of the demo reset with their live ids (1.5.0, H9).

No model call runs inside a database transaction (H20): the agent and the vision checklist
read the case in one transaction, call the model on the runtime's model-call pool and store
the result in a second one. On the public read-only demo (PUBLIC_READ_ONLY) the agent and the
checkout evaluation are rate limited per client (roles.check_public_rate).

Every endpoint of the group that has no route above add_stubs() is a 501 stub.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, StreamingResponse

from apps.api import schemas as s
from apps.api.errors import ApiError, not_found, request_id_of
from apps.api.roles import authorize, check_public_rate, client_key, public_read_only
from apps.api.routes.stubs import add_stubs, response_model, responses
from apps.api.runtime import Runtime, runtime
from apps.api.services import demo
from apps.api.services.other import agent, benchmarks, checkout, inspections, telemetry

router = APIRouter()
SSE_HEADERS: dict[str, str] = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _ep(method: str, path: str) -> s.Endpoint:
    return next(ep for ep in s.ENDPOINTS if (ep.method, ep.path) == (method, path))


EVALUATE = _ep("POST", "/api/checkout/evaluate")
CONFIRM = _ep("POST", "/api/checkout/confirm")
INSPECT = _ep("POST", "/api/inspections")
INSPECT_CONFIRM = _ep("POST", "/api/inspections/{inspection_id}/confirm")
ASK = _ep("POST", "/api/agent/ask")
BENCHMARKS = _ep("GET", "/api/benchmarks")
TELEMETRY = _ep("GET", "/api/telemetry")
DEMO_CASES = _ep("GET", "/api/demo/cases")


def _client(rt: Runtime) -> object | None:
    """The model client of the settings (an unavailable one when vLLM is not set up)."""
    return rt.generator.client()


@router.post(
    EVALUATE.path,
    status_code=EVALUATE.status,
    response_model=response_model(EVALUATE),
    responses=responses(EVALUATE),
    summary=EVALUATE.summary,
    tags=["other"],
)
async def checkout_evaluate(
    request: Request, body: Annotated[s.CheckoutEvaluateRequest, Body()]
) -> JSONResponse:
    """US-401..US-403: line probabilities, size advice, the terms of the order stored as shown."""
    authorize(request, EVALUATE.method, EVALUATE.path, body)
    check_public_rate(request)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        out = await checkout.evaluate(conn, serving, body, rt.now(), request_id_of(request))
    return JSONResponse(out.model_dump(mode="json"), status_code=EVALUATE.status)


@router.post(
    CONFIRM.path,
    status_code=CONFIRM.status,
    response_model=response_model(CONFIRM),
    responses=responses(CONFIRM),
    summary=CONFIRM.summary,
    tags=["other"],
)
async def checkout_confirm(
    request: Request, body: Annotated[s.CheckoutConfirmRequest, Body()]
) -> JSONResponse:
    """US-403, ADR-05: the snapshot shown, bound to a new order (201), or 409."""
    authorize(request, CONFIRM.method, CONFIRM.path, body)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        out = await checkout.confirm(conn, serving, body, rt.now(), request_id_of(request))
    return JSONResponse(out.model_dump(mode="json"), status_code=CONFIRM.status)


@router.post(
    INSPECT.path,
    status_code=INSPECT.status,
    response_model=response_model(INSPECT),
    responses=responses(INSPECT),
    summary=INSPECT.summary,
    tags=["other"],
)
async def inspection_create(
    request: Request, body: Annotated[s.InspectionCreateRequest, Body()]
) -> JSONResponse:
    """US-501, US-503: the suggested checklist of the photo, or manual review (201)."""
    authorize(request, INSPECT.method, INSPECT.path, body)
    rt = runtime(request)
    serving = rt.require_serving()
    photo = inspections.decode(body.photo)
    async with rt.transaction() as conn:
        await inspections.latest_case(conn, serving, body.return_id, rt.now())
    vision = inspections.vision_model(rt.settings, _client(rt))
    result = await rt.model_call(inspections.checklist, vision, rt.settings, photo, body.return_id)
    async with rt.transaction() as conn:
        out = await inspections.create(
            conn, serving, rt.settings, body, photo, result, rt.now(), request_id_of(request)
        )
    return JSONResponse(out.model_dump(mode="json"), status_code=INSPECT.status)


@router.post(
    INSPECT_CONFIRM.path,
    status_code=INSPECT_CONFIRM.status,
    response_model=response_model(INSPECT_CONFIRM),
    responses=responses(INSPECT_CONFIRM),
    summary=INSPECT_CONFIRM.summary,
    tags=["other"],
)
async def inspection_confirm(
    request: Request,
    inspection_id: Annotated[str, PathParam(max_length=96)],
    body: Annotated[s.InspectionConfirmRequest, Body()],
) -> JSONResponse:
    """US-502: Ahmed's observations, the claim of an approved rule when they hold it."""
    authorize(request, INSPECT_CONFIRM.method, INSPECT_CONFIRM.path, body)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        out = await inspections.confirm(
            conn, serving, inspection_id, body, rt.now(), request_id_of(request)
        )
    return JSONResponse(out.model_dump(mode="json"), status_code=INSPECT_CONFIRM.status)


@router.post(
    ASK.path,
    status_code=ASK.status,
    response_model=response_model(ASK),
    responses=responses(ASK),
    summary=ASK.summary,
    tags=["other"],
)
async def agent_ask(
    request: Request, body: Annotated[s.AgentAskRequest, Body()]
) -> StreamingResponse:
    """US-701, US-702: the read-only agent's answer as server-sent events (sse_frame)."""
    authorize(request, ASK.method, ASK.path, body)
    # The public limit first: a client past it spends no question of the agent's budget, which
    # the public demo counts per client and person (one client cannot lock the agent for all).
    check_public_rate(request)
    public = public_read_only(request)
    agent.check_rate(request.app, body.asked_by, client_key(request) if public else None)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        data = await agent.case_data(conn, serving, body.return_id, rt.now())
    if data is None:
        raise not_found(f"return {body.return_id}")
    if rt.settings.llm_mode == "vllm":
        rt.generator.client()  # a configuration the client refuses is a 503, never a 500
    agent.check_available(rt.settings, rt.generator.client_error)
    answered = await agent.run(rt.settings, rt.generator.client, rt.model_call, data, body.question)
    async with rt.transaction() as conn:
        events = await agent.save(conn, body, data, answered, rt.now())
    frames = [s.sse_frame(e) for e in events.root]
    return StreamingResponse(
        iter(frames), status_code=ASK.status, media_type=s.SSE, headers=SSE_HEADERS
    )


@router.get(
    BENCHMARKS.path,
    response_model=response_model(BENCHMARKS),
    responses=responses(BENCHMARKS),
    summary=BENCHMARKS.summary,
    tags=["other"],
)
async def get_benchmarks(request: Request) -> JSONResponse:
    """US-902: B1-B4 of the run on file, measured or pending, each figure with its provenance."""
    authorize(request, BENCHMARKS.method, BENCHMARKS.path)
    out = benchmarks.load(benchmarks.benchmarks_dir())
    return JSONResponse(out.model_dump(mode="json"))


@router.get(
    TELEMETRY.path,
    response_model=response_model(TELEMETRY),
    responses=responses(TELEMETRY),
    summary=TELEMETRY.summary,
    tags=["other"],
)
async def get_telemetry(request: Request) -> JSONResponse:
    """US-905: GPU samples of amd-smi, vLLM latency and throughput; empty and labelled on CPU."""
    authorize(request, TELEMETRY.method, TELEMETRY.path)
    rt = runtime(request)
    async with rt.transaction() as conn:
        out = await telemetry.telemetry(conn, rt.settings, rt.now(), rt.wall_now())
    return JSONResponse(out.model_dump(mode="json"))


@router.get(
    DEMO_CASES.path,
    response_model=response_model(DEMO_CASES),
    responses=responses(DEMO_CASES),
    summary=DEMO_CASES.summary,
    tags=["other"],
)
async def get_demo_cases(request: Request) -> JSONResponse:
    """US-903: the golden cases of the reset with their live ids; 404 before any reset."""
    authorize(request, DEMO_CASES.method, DEMO_CASES.path)
    rt = runtime(request)
    async with rt.transaction() as conn:
        row = await demo.reset_row(conn)
    if row is None:
        raise ApiError(404, "not_found", "no demo reset yet: run python -m apps.api.reset")
    return JSONResponse(demo.cases_response(row, rt.now()).model_dump(mode="json"))


add_stubs(router, "other")
