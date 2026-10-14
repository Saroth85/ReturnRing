"""GET /healthz (US-001) and GET /readyz (US-901): liveness, and readiness with the mode, the
label of cached mode ("Precomputed on AMD MI300X"), a check per component and the versions.

Contract 1.5.0 (ADR-15 section 15): /readyz shows the API's clock (the demo clock: the
snapshot instant plus the time since the reset, frozen at the snapshot instant on the public
and the cached demo, 1.6.0) and whether the public demo is read-only; the
cached label is shown only while precomputed outputs are there (H6); the status is not_ready
while the database, the scores or the models are missing, when every /api route fails (H37);
the vLLM probe follows no redirect (rule 2, H23) and checks that GEMMA_MODEL is served (H30);
no server path reaches the public body (H26)."""

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
from apps.api.clock import DemoClock
from apps.api.db import ping
from apps.api.routes.stubs import add_stubs, responses
from apps.api.runtime import SERVING_UNAVAILABLE, Runtime, runtime
from apps.api.services.other.telemetry import CACHED_NOTE, component_check
from apps.api.services.seeds import ASSUMPTIONS_V1, current_assumptions
from llm.client import _NoRedirect, check_local_url
from policy.types import Decision

router = APIRouter()
RULES_VERSION: Final = str(next(f.default for f in fields(Decision) if f.name == "rules_version"))
PROBE_TIMEOUT_S: Final = 2.0


def _check(component: s.ComponentName, status: str, detail: str | None = None) -> s.ComponentCheck:
    return s.ComponentCheck.model_validate(
        {"component": component, "status": status, "detail": detail}
    )


def probe_vllm(base_url: str | None, api_key: str | None, model: str | None = None) -> str | None:
    """None when the local vLLM lists its models, `model` among them, else why not (rule 2:
    local URLs only, no redirect followed: a 3xx is an unreachable vLLM)."""
    if not base_url:
        return "VLLM_BASE_URL is not set"
    try:
        url = check_local_url(base_url).removesuffix("/chat/completions") + "/models"
    except ValueError as exc:
        return str(exc)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key or ''}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=PROBE_TIMEOUT_S) as response:
            listed = json.loads(response.read(100_000))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return f"vLLM unreachable: {type(exc).__name__}"
    if model is None:
        return None
    data = listed.get("data") if isinstance(listed, dict) else None
    served = (
        [str(m.get("id")) for m in data if isinstance(m, dict)] if isinstance(data, list) else []
    )
    if model not in served:
        return (
            f"model {model} not served by vLLM (GEMMA_MODEL; served: {', '.join(served) or 'none'})"
        )
    return None


def clock_view(rt: Runtime) -> s.ClockView:
    """The API's clock as /readyz shows it (ADR-15 section 15)."""
    now = rt.now()
    clock = rt.clock
    if isinstance(clock, DemoClock) and clock.anchored is not None:
        anchor, started = clock.anchored
        return s.ClockView(
            kind="demo",
            now=now,
            snapshot_as_of=anchor,
            started_at=started,
            frozen=clock.frozen,
            note=s.FROZEN_CLOCK_NOTE if clock.frozen else s.DEMO_CLOCK_NOTE,
        )
    if rt.clock_kind() == "fixed":
        return s.ClockView(
            kind="fixed",
            now=now,
            snapshot_as_of=None,
            started_at=None,
            frozen=False,
            note="A fixed clock",
        )
    return s.ClockView(
        kind="system",
        now=now,
        snapshot_as_of=None,
        started_at=None,
        frozen=False,
        note="The wall clock, UTC",
    )


async def telemetry_check(rt: Runtime, *, database_ok: bool) -> s.ComponentCheck:
    """The amd-smi samples of the telemetry sampler (US-905): ok when one is fresh, disabled in
    cached mode and on a CPU without samples, unavailable on a GPU whose sampler is silent."""
    settings = rt.settings
    if settings.cached:
        return _check("telemetry", "disabled", CACHED_NOTE)
    if rt.engine is None or not database_ok:
        status = "unavailable" if settings.device == "gpu" else "disabled"
        return _check("telemetry", status, "no database: the samples are stored there")
    async with rt.engine.connect() as conn:
        return await component_check(conn, settings, rt.wall_now())


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
        detail = SERVING_UNAVAILABLE  # the reason is in the server log, never a server path
        checks += [_check("scores", "unavailable", detail), _check("models", "unavailable", detail)]
    else:
        checks += [_check("scores", "ok"), _check("models", "ok")]
    if settings.cached:
        checks.append(_check("vllm", "disabled", "GPU off in cached mode"))
    elif settings.llm_mode == "mock":
        checks.append(_check("vllm", "disabled", "LLM_MODE=mock: the deterministic mock model"))
    else:
        problem = await asyncio.to_thread(
            probe_vllm, settings.vllm_base_url, settings.vllm_api_key, settings.gemma_model
        )
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
    checks.append(await telemetry_check(rt, database_ok=checks[0].status == "ok"))
    statuses = {c.component: c.status for c in checks}
    required: tuple[s.ComponentName, ...] = ("database", "scores", "models")
    status: Literal["ready", "degraded", "not_ready"] = (
        "not_ready"
        if any(statuses[c] != "ok" for c in required)
        else ("degraded" if "unavailable" in statuses.values() else "ready")
    )
    await rt.refresh_clock()
    return s.ReadyzResponse(
        status=status,
        mode=settings.demo_mode,
        llm_mode=settings.served_llm_mode,
        device=settings.device,
        label=s.CACHED_LABEL if settings.cached and statuses["demo_cache"] == "ok" else None,
        checks=checks,
        contract_version=s.API_CONTRACT_VERSION,
        rules_version=RULES_VERSION,
        assumptions_version=assumptions,
        model_versions=serving.model_versions() if serving is not None else {},
        clock=clock_view(rt),
        public_read_only=settings.public_read_only,
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
