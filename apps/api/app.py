"""The app factory (ADR-15, ADR-16).

create_app() builds the FastAPI app with the request-id and mode headers, the ErrorResponse of
every error path, and the four routers. At startup it loads the serving snapshot (SERVING_DIR,
read only, checked against its manifest) and connects to DATABASE_URL; a missing piece leaves the
app up, the startup log says why (a WARNING) and /readyz says what is missing. Tests pass their
own settings, clock, engine, serving snapshot and LLM client.

The clock (ADR-15 section 15): an injected clock wins; otherwise DEMO_CLOCK=on gives the
DemoClock (the snapshot instant plus the time since the demo reset; frozen at the snapshot
instant on the public read-only demo, section 19), off the wall clock.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api import errors
from apps.api.clock import Clock, DemoClock, SystemClock
from apps.api.db import make_engine
from apps.api.middleware import RequestContext
from apps.api.routes import health, manager, operator, other
from apps.api.runtime import LOG, Runtime
from apps.api.schemas import API_CONTRACT_VERSION
from apps.api.services.generation import Generator
from apps.api.services.manager.catalog import warm as warm_anomalies
from apps.api.services.operator.queue import warm as warm_queue
from apps.api.services.other.checkout import warm as warm_checkout
from apps.api.serving import Serving, ServingError
from apps.api.settings import Settings
from llm.client import LlmClient


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    engine: AsyncEngine | None = None,
    serving: Serving | None = None,
    llm_client: LlmClient | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    rt = Runtime(
        settings=config,
        clock=clock
        or (DemoClock(frozen=config.frozen_clock) if config.demo_clock else SystemClock()),
        generator=Generator(config, llm_client),
        engine=engine,
        serving=serving,
    )
    owns_engine = engine is None
    loads_serving = serving is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if rt.serving is None:
            try:
                rt.serving = Serving.load(config.serving_dir)
            except (ServingError, OSError, ValueError, KeyError) as exc:
                rt.serving_error = f"{type(exc).__name__}: {exc}"
                LOG.warning(
                    "the serving snapshot is not loaded, every /api route answers 500 until it "
                    "is: %s (build it with python -m pipeline.serving, set SERVING_DIR)",
                    rt.serving_error,
                )
        if rt.serving is not None:
            await asyncio.to_thread(rt.serving.warm)
            await asyncio.to_thread(warm_queue, rt.serving)
            await asyncio.to_thread(warm_anomalies, rt.serving)
            await asyncio.to_thread(warm_checkout, rt.serving)
            if loads_serving:
                # The snapshot is read-only for the life of the service: move it out of the
                # cyclic garbage collector, whose full passes over its millions of objects
                # otherwise pause a request for about 2 s every few requests (US-301). Tests
                # pass their own snapshot and keep the collector as it is.
                gc.collect()
                gc.freeze()
        if rt.engine is None:
            if config.database_url is None:
                rt.database_error = "DATABASE_URL is not set"
                LOG.warning("no database: DATABASE_URL is not set")
            else:
                rt.engine = make_engine(config.database_url)
        rt.start_clock()
        await rt.refresh_clock(force=True)
        try:
            yield
        finally:
            rt.close()
            if owns_engine and rt.engine is not None:
                await rt.engine.dispose()

    app = FastAPI(
        title="ReturnRing API",
        version=API_CONTRACT_VERSION,
        description=(
            "Return decision assistant (ADR-15). Every response carries X-ReturnRing-Mode and "
            "X-Request-Id; every error is an ErrorResponse."
        ),
        lifespan=lifespan,
    )
    app.state.runtime = rt
    errors.install(app)
    for module in (health, operator, manager, other):
        app.include_router(module.router)
    app.add_middleware(RequestContext, mode=config.demo_mode)
    return app


def configure_logging(level: int = logging.INFO) -> None:
    """One line per record with its time and level (NFR-12): the service entry point calls it;
    tests keep pytest's logging."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
