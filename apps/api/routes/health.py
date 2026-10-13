"""GET /healthz (US-001) and GET /readyz (US-901): liveness, and readiness with the mode, the
label of cached mode ("Precomputed on AMD MI300X"), a check per component and the versions."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import fields
from typing import Final, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api import schemas as s
from apps.api.db import ping
from apps.api.routes.stubs import add_stubs, responses
from apps.api.runtime import Runtime, runtime
from apps.api.services.seeds import ASSUMPTIONS_V1, current_assumptions
from llm.client import check_local_url
from policy.types import Decision

router = APIRouter()
RULES_VERSION: Final = str(next(f.default for f in fields(Decision) if f.name == "rules_version"))
PROBE_TIMEOUT_S: Final = 2.0


def _check(component: s.ComponentName, status: str, detail: str | None = None) -> s.ComponentCheck:
    return s.ComponentCheck.model_validate(
        {"component": component, "status": status, "detail": detail}
    )


def probe_vllm(base_url: str | None, api_key: str | None) -> str | None:
    """None when the local vLLM lists its models, else why not (rule 2: local URLs only)."""
    if not base_url:
        return "VLLM_BASE_URL is not set"
    try:
        url = check_local_url(base_url).removesuffix("/chat/completions") + "/models"
    except ValueError as exc:
        return str(exc)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key or ''}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=PROBE_TIMEOUT_S) as response:
            json.loads(response.read(100_000))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return f"vLLM unreachable: {type(exc).__name__}"
    return None


async def readiness(rt: Runtime) -> s.ReadyzResponse:
    settings = rt.settings
    checks: list[s.ComponentCheck] = []
    assumptions = ASSUMPTIONS_V1
    if rt.engine is None:
        checks.append(_check("database", "unavailable", rt.database_error or "no database"))
    else:
        problem = await ping(rt.engine)
        checks.append(_check("database", "unavailable" if problem else "ok", problem))
        if problem is None:
            try:
                async with rt.engine.connect() as conn:
                    assumptions = (await current_assumptions(conn)).version
            except LookupError:
                checks[-1] = _check(
                    "database", "unavailable", "empty: run python -m apps.api.reset"
                )
    serving = rt.serving
    if serving is None:
        detail = rt.serving_error or "not loaded"
        checks += [_check("scores", "unavailable", detail), _check("models", "unavailable", detail)]
    else:
        checks += [_check("scores", "ok"), _check("models", "ok")]
    if settings.cached:
        checks.append(_check("vllm", "disabled", "GPU off in cached mode"))
    elif settings.llm_mode == "mock":
        checks.append(_check("vllm", "disabled", "LLM_MODE=mock: the deterministic mock model"))
    else:
        problem = await asyncio.to_thread(probe_vllm, settings.vllm_base_url, settings.vllm_api_key)
        checks.append(_check("vllm", "unavailable" if problem else "ok", problem))
    if settings.cached:
        cache = rt.generator.cache_dir
        entries = any(cache.glob("*.json")) if cache.is_dir() else False
        checks.append(
            _check(
                "demo_cache",
                "ok" if entries else "unavailable",
                None if entries else "no precomputed output: drafts use the template",
            )
        )
    else:
        checks.append(_check("demo_cache", "disabled"))
    checks.append(_check("telemetry", "disabled", "not collected by the API core"))
    statuses = {c.component: c.status for c in checks}
    status: Literal["ready", "degraded", "not_ready"] = (
        "not_ready"
        if statuses["database"] != "ok"
        else ("degraded" if "unavailable" in statuses.values() else "ready")
    )
    return s.ReadyzResponse(
        status=status,
        mode=settings.demo_mode,
        llm_mode=settings.llm_mode,
        device=settings.device,
        label=s.CACHED_LABEL if settings.cached else None,
        checks=checks,
        contract_version=s.API_CONTRACT_VERSION,
        rules_version=RULES_VERSION,
        assumptions_version=assumptions,
        model_versions=serving.model_versions() if serving is not None else {},
    )


@router.get(
    "/healthz",
    response_model=s.HealthzResponse,
    summary="Liveness",
    tags=["health"],
)
async def healthz() -> s.HealthzResponse:
    return s.HealthzResponse(status="ok")


@router.get(
    "/readyz",
    response_model=s.ReadyzResponse,
    responses=responses(next(ep for ep in s.ENDPOINTS if ep.path == "/readyz")),
    summary="Readiness and mode (live, cached)",
    tags=["health"],
)
async def readyz(request: Request) -> JSONResponse:
    body = await readiness(runtime(request))
    return JSONResponse(body.model_dump(mode="json"))


add_stubs(router, "health")
