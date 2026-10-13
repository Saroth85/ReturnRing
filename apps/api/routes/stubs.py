"""501 stubs for the endpoints of ADR-15 a route builder has not implemented yet.

A stub has the endpoint's contract: its path parameters, query model and body model (so FastAPI
answers 422 invalid_request exactly as the real route will), its role check (403
forbidden_role), its response model in the OpenAPI document, and answers 501 with an
ErrorResponse. To implement an endpoint, add the route to the module of its group above the
add_stubs() call: the stub of that method and path is then not registered.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Sequence
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Query, Request
from fastapi import Path as PathParam
from fastapi.routing import APIRoute
from pydantic import BaseModel

from apps.api.errors import not_implemented
from apps.api.roles import authorize
from apps.api.schemas import API_MODELS, CSV, ENDPOINTS, SSE, Endpoint, ErrorResponse

# The three groups of the route builders (and health), by path prefix.
GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "health": ("/healthz", "/readyz"),
    "operator": ("/api/returns", "/api/decisions", "/api/links"),
    "manager": (
        "/api/portfolio",
        "/api/simulator",
        "/api/kpi",
        "/api/fairness",
        "/api/audit",
        "/api/outbox",
        "/api/products",
        "/api/catalog-tasks",
        "/api/coupon-rules",
    ),
    "other": (
        "/api/checkout",
        "/api/inspections",
        "/api/agent",
        "/api/benchmarks",
        "/api/telemetry",
    ),
}


def group_of(ep: Endpoint) -> str:
    for group, prefixes in GROUPS.items():
        if any(ep.path == p or ep.path.startswith(p + "/") for p in prefixes):
            return group
    raise KeyError(f"{ep.method} {ep.path} belongs to no group")


def endpoints_of(group: str) -> tuple[Endpoint, ...]:
    return tuple(ep for ep in ENDPOINTS if group_of(ep) == group)


def responses(ep: Endpoint) -> dict[int | str, dict[str, Any]]:
    """The error responses of an endpoint (and its stream or CSV body) for the OpenAPI document."""
    out: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (*ep.errors, 422, 501)
    }
    if ep.media_type == SSE:
        out[ep.status] = {
            "description": "Server-sent events; each data line is one event of agent_stream",
            "content": {SSE: {"schema": {"type": "string"}}},
        }
    elif CSV in ep.alt_media_types:
        out[ep.status] = {"content": {CSV: {"schema": {"type": "string"}}}}
    return out


def response_model(ep: Endpoint) -> Any:
    return None if ep.media_type == SSE else API_MODELS[ep.response]


def _stub(ep: Endpoint) -> Callable[..., Any]:
    params = [
        inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY, annotation=Request),
        *(
            inspect.Parameter(
                name, inspect.Parameter.KEYWORD_ONLY, annotation=Annotated[str, PathParam()]
            )
            for name in re.findall(r"{(\w+)}", ep.path)
        ),
    ]
    if ep.query is not None:
        params.append(
            inspect.Parameter(
                "query",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Annotated[API_MODELS[ep.query], Query()],
            )
        )
    if ep.request is not None:
        params.append(
            inspect.Parameter(
                "body",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Annotated[API_MODELS[ep.request], Body()],
            )
        )

    async def route(**kwargs: Any) -> Any:
        body = kwargs.get("body")
        authorize(
            kwargs["request"], ep.method, ep.path, body if isinstance(body, BaseModel) else None
        )
        raise not_implemented(ep.method, ep.path)

    route.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    route.__name__ = "stub_" + re.sub(r"\W+", "_", f"{ep.method.lower()}_{ep.path}").strip("_")
    route.__doc__ = f"{ep.summary}. Stories: {', '.join(ep.stories)}. Not implemented yet (501)."
    return route


def registered(router: APIRouter) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for route in router.routes:
        if isinstance(route, APIRoute):
            found |= {(m, route.path) for m in route.methods or ()}
    return found


def add_stubs(router: APIRouter, group: str) -> list[Endpoint]:
    """Register a 501 stub for every endpoint of `group` the router does not implement; returns
    the stubbed endpoints."""
    done = registered(router)
    stubbed: list[Endpoint] = []
    for ep in endpoints_of(group):
        if (ep.method, ep.path) in done:
            continue
        router.add_api_route(
            ep.path,
            _stub(ep),
            methods=[ep.method],
            status_code=ep.status,
            response_model=response_model(ep),
            responses=responses(ep),
            summary=ep.summary,
            tags=[group],
        )
        stubbed.append(ep)
    return stubbed


def implemented(group: str, router: APIRouter) -> Sequence[Endpoint]:
    """The endpoints of `group` with a real route (not a stub) in `router`."""
    real = {
        (m, r.path)
        for r in router.routes
        if isinstance(r, APIRoute) and not r.name.startswith("stub_")
        for m in r.methods or ()
    }
    return [ep for ep in endpoints_of(group) if (ep.method, ep.path) in real]
