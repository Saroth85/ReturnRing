"""The manager's area (view 3; US-208, US-304, US-402, US-601..US-607): portfolio matrix,
simulator assumptions, KPI, fairness, audit log and its CSV, outbox, product anomalies,
catalogue tasks and coupon rules.

Every route checks the role of its actor first (ADR-15 section 5: the person a body names, and
the X-ReturnRing-Actor header), then runs its service (services.manager) in one transaction and
answers the contract model; errors are ErrorResponse. The fairness report and the false
positives of the KPIs need the offline evaluation report of the serving snapshot: without it
they answer 404 (rule 12: no label is read at runtime, no rate is invented).
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from apps.api import schemas as s
from apps.api.errors import not_found, request_id_of
from apps.api.roles import authorize, endpoint
from apps.api.routes.stubs import add_stubs, response_model, responses
from apps.api.runtime import runtime
from apps.api.services.manager.assumptions import assumptions_response, update_assumptions
from apps.api.services.manager.catalog import anomalies, open_task, tasks, update_task
from apps.api.services.manager.coupons import (
    approve_rule,
    coupon_rules_response,
    reverse_application,
)
from apps.api.services.manager.evaluation import ReportUnavailable, fairness, load_report
from apps.api.services.manager.kpi import default_week, kpi
from apps.api.services.manager.matrix import portfolio_matrix
from apps.api.services.manager.records import audit_csv, audit_page, outbox_page

router = APIRouter()
GZIP_MIN_BYTES: Final = 1024
MATRIX: Final = "/api/portfolio/matrix"
ASSUMPTIONS: Final = "/api/simulator/assumptions"
TASKS: Final = "/api/catalog-tasks"
RULES: Final = "/api/coupon-rules"
REVERSE: Final = "/api/coupon-rules/applications/{application_id}/reverse"
TaskId = Annotated[str, PathParam(min_length=1, max_length=96)]
ApplicationId = Annotated[str, PathParam(min_length=1, max_length=96)]


def route(method: str, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a route with the contract's status, response model and error responses."""
    ep = endpoint(method, path)
    return router.api_route(
        path,
        methods=[method],
        status_code=ep.status,
        response_model=response_model(ep),
        responses=responses(ep),
        summary=ep.summary,
        tags=["manager"],
    )


def answer(model: BaseModel, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status)


def accepts_gzip(header: str | None) -> bool:
    """Whether Accept-Encoding allows gzip (an explicit gzip entry wins over *)."""
    weights: dict[str, float] = {}
    for part in (header or "").split(","):
        coding, *params = (x.strip() for x in part.split(";"))
        q = 1.0
        for param in params:
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if coding:
            weights[coding.lower()] = q
    weight = weights.get("gzip", weights.get("*", 0.0))
    return weight > 0


def large_answer(request: Request, model: BaseModel) -> Response:
    """A large JSON body (the matrix: about 20,000 points), gzipped when the client accepts it
    (ADR-15 section 4)."""
    data = json.dumps(model.model_dump(mode="json"), separators=(",", ":")).encode("utf-8")
    headers = {"Vary": "Accept-Encoding"}
    if len(data) >= GZIP_MIN_BYTES and accepts_gzip(request.headers.get("accept-encoding")):
        headers["Content-Encoding"] = "gzip"
        data = gzip.compress(data, compresslevel=6)
    return Response(data, media_type="application/json", headers=headers)


# ---- Portfolio, simulator, KPI and fairness (US-601..US-603, US-605) ----------------------------


@route("GET", MATRIX)
async def get_portfolio_matrix(request: Request) -> Response:
    authorize(request, "GET", MATRIX)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        body = await portfolio_matrix(conn, serving, rt.now())
    return large_answer(request, body)


@route("GET", ASSUMPTIONS)
async def get_assumptions(request: Request) -> JSONResponse:
    authorize(request, "GET", ASSUMPTIONS)
    rt = runtime(request)
    async with rt.transaction() as conn:
        return answer(await assumptions_response(conn))


@route("PUT", ASSUMPTIONS)
async def put_assumptions(
    request: Request, body: Annotated[s.SimulatorAssumptionsUpdate, Body()]
) -> JSONResponse:
    authorize(request, "PUT", ASSUMPTIONS, body)
    rt = runtime(request)
    async with rt.transaction() as conn:
        result = await update_assumptions(
            conn, body, at=rt.now(), request_id=request_id_of(request)
        )
    return answer(result)


@route("GET", "/api/kpi")
async def get_kpi(request: Request, query: Annotated[s.KpiQuery, Query()]) -> JSONResponse:
    authorize(request, "GET", "/api/kpi")
    rt = runtime(request)
    serving = rt.require_serving()
    now = rt.now()
    try:
        report = load_report(serving)
    except ReportUnavailable as exc:
        raise not_found(str(exc)) from None
    async with rt.transaction() as conn:
        body = await kpi(conn, report, query.week_start or default_week(now), now)
    return answer(body)


@route("GET", "/api/fairness")
async def get_fairness(request: Request) -> JSONResponse:
    authorize(request, "GET", "/api/fairness")
    serving = runtime(request).require_serving()
    try:
        body = fairness(load_report(serving))
    except ReportUnavailable as exc:
        raise not_found(str(exc)) from None
    return answer(body)


# ---- Audit log and outbox (US-604, US-304) -------------------------------------------------------


@route("GET", "/api/audit")
async def get_audit(request: Request, query: Annotated[s.AuditQuery, Query()]) -> Response:
    authorize(request, "GET", "/api/audit")
    rt = runtime(request)
    async with rt.transaction() as conn:
        if query.format == "csv":
            text = await audit_csv(conn, query)
            return Response(
                text,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
            )
        return answer(await audit_page(conn, query))


@route("GET", "/api/outbox")
async def get_outbox(request: Request, query: Annotated[s.OutboxQuery, Query()]) -> JSONResponse:
    authorize(request, "GET", "/api/outbox")
    rt = runtime(request)
    async with rt.transaction() as conn:
        return answer(await outbox_page(conn, query))


# ---- Product anomalies and catalogue tasks (US-208, US-606) --------------------------------------


@route("GET", "/api/products/anomalies")
async def get_anomalies(
    request: Request, query: Annotated[s.AnomaliesQuery, Query()]
) -> JSONResponse:
    authorize(request, "GET", "/api/products/anomalies")
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        return answer(await anomalies(conn, serving, query, rt.now()))


@route("GET", TASKS)
async def get_tasks(request: Request) -> JSONResponse:
    authorize(request, "GET", TASKS)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        return answer(await tasks(conn, serving))


@route("POST", TASKS)
async def post_task(request: Request, body: Annotated[s.CatalogTaskCreate, Body()]) -> JSONResponse:
    authorize(request, "POST", TASKS, body)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        task = await open_task(conn, serving, body, at=rt.now(), request_id=request_id_of(request))
    return answer(task, 201)


@route("PATCH", TASKS + "/{task_id}")
async def patch_task(
    request: Request, task_id: TaskId, body: Annotated[s.CatalogTaskUpdate, Body()]
) -> JSONResponse:
    authorize(request, "PATCH", TASKS + "/{task_id}", body)
    rt = runtime(request)
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        task = await update_task(
            conn, serving, task_id, body, at=rt.now(), request_id=request_id_of(request)
        )
    return answer(task)


# ---- Coupon rules (US-402, US-607, LEG-09) -------------------------------------------------------


@route("GET", RULES)
async def get_rules(request: Request) -> JSONResponse:
    authorize(request, "GET", RULES)
    rt = runtime(request)
    async with rt.transaction() as conn:
        return answer(await coupon_rules_response(conn, rt.now()))


@route("POST", RULES)
async def post_rule(request: Request, body: Annotated[s.CouponRuleCreate, Body()]) -> JSONResponse:
    authorize(request, "POST", RULES, body)
    rt = runtime(request)
    async with rt.transaction() as conn:
        rules = await approve_rule(conn, body, at=rt.now(), request_id=request_id_of(request))
    return answer(rules, 201)


@route("POST", REVERSE)
async def post_reverse(
    request: Request,
    application_id: ApplicationId,
    body: Annotated[s.CouponApplicationReverse, Body()],
) -> JSONResponse:
    authorize(request, "POST", REVERSE, body)
    rt = runtime(request)
    async with rt.transaction() as conn:
        application = await reverse_application(
            conn, application_id, body, at=rt.now(), request_id=request_id_of(request)
        )
    return answer(application)


add_stubs(router, "manager")
