"""What a running API holds (app.state.runtime): its settings, clock, database engine, the
serving snapshot in memory, the draft generator and the executor of model calls. Routes reach
it with runtime(request).

- The clock (ADR-15 section 15): with DEMO_CLOCK=on it is a DemoClock anchored at the demo
  reset (the row of demo_reset), read again at most every CLOCK_REFRESH_S seconds, so a reset
  made while the API runs restarts its clock; before any reset it starts at the snapshot
  instant when the service starts. A frozen clock (the read-only demo) is anchored with the
  latest act the store recorded (latest_act, at the start and at every new anchor), so it never
  shows a 'now' before an act made with writes on (ADR-15 section 21).
- Model calls (H20, H36) run on their own bounded thread pool (LLM_CONCURRENCY workers), never
  inside an open database transaction, and never on asyncio's default executor, which the
  readiness probe and the CPU recomputes use.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, TypeVar

from fastapi import Request
from sqlalchemy import Column, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from apps.api.clock import Clock, DemoClock, kind_of, wall_now
from apps.api.errors import ApiError
from apps.api.services.generation import Generator
from apps.api.serving import Serving
from apps.api.settings import Settings
from contracts.sql import TABLES, UtcDateTime, demo_reset

LOG: Final = logging.getLogger("returnring.api")
CLOCK_REFRESH_S: Final = 5.0
SERVING_UNAVAILABLE: Final = "serving snapshot not loaded: see the server log"
DATABASE_UNAVAILABLE: Final = "database unavailable: see the server log"
T = TypeVar("T")

# The timestamps of contracts/sql.py that do not date an act on the API's clock (ADR-15 section
# 21): the snapshot instant and the wall time the demo clock started, the wall time of a GPU
# sample, and the time the shop says the customer asked (data of the store, not the API's act:
# the API's receipt is received_at). Every other timestamp is an act, written at the clock's
# 'now': a decision, an approval, a lift, a reversal, a message, a shown terms snapshot, an
# inspection, a draft, an audit entry...
NOT_ACTS: Final = frozenset(
    {
        ("demo_reset", "snapshot_as_of"),
        ("demo_reset", "started_at"),
        ("telemetry_samples", "sampled_at"),
        ("return_requests", "requested_at"),
    }
)


def act_columns() -> tuple[Column[Any], ...]:
    """Every timestamp column that dates an act on the API's clock (NOT_ACTS aside)."""
    return tuple(
        c
        for t in TABLES
        for c in t.columns
        if isinstance(c.type, UtcDateTime) and (t.name, c.name) not in NOT_ACTS
    )


async def latest_act(conn: AsyncConnection) -> datetime | None:
    """The latest act the store recorded, None in an empty store: one row of the maxima of
    every act column (the reset dates its rows at the snapshot instant)."""
    found = (
        await conn.execute(select(*(select(func.max(c)).scalar_subquery() for c in act_columns())))
    ).one()
    return max((at for at in found if at is not None), default=None)


@dataclass
class Runtime:
    settings: Settings
    clock: Clock
    generator: Generator
    engine: AsyncEngine | None = None
    serving: Serving | None = None
    serving_error: str | None = None
    database_error: str | None = None
    _pool: ThreadPoolExecutor | None = field(default=None, repr=False)
    _clock_checked: float = field(default=float("-inf"), repr=False)
    # The last Token Factory probe of /readyz: (time.monotonic(), problem or None).
    provider_probe: tuple[float, str | None] | None = field(default=None, repr=False)
    provider_probe_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def now(self) -> datetime:
        return self.clock.now()

    def wall_now(self) -> datetime:
        """The wall clock (what is measured in real time: GPU samples)."""
        return wall_now(self.clock)

    def require_serving(self) -> Serving:
        if self.serving is None:
            raise ApiError(500, "internal", SERVING_UNAVAILABLE)
        return self.serving

    def require_engine(self) -> AsyncEngine:
        if self.engine is None:
            raise ApiError(500, "internal", DATABASE_UNAVAILABLE)
        return self.engine

    # ---- the demo clock -----------------------------------------------------------------------

    @property
    def demo_clock(self) -> DemoClock | None:
        return self.clock if isinstance(self.clock, DemoClock) else None

    def start_clock(self) -> None:
        """Before any reset is read: the snapshot instant, as of the start of the service."""
        clock = self.demo_clock
        if clock is not None and clock.anchored is None and self.serving is not None:
            clock.anchor(self.serving.as_of, clock.wall())

    async def refresh_clock(self, *, force: bool = False) -> None:
        """Anchor the demo clock at the demo reset of the database (at most every
        CLOCK_REFRESH_S seconds); a database without a reset keeps the clock as it is. A frozen
        clock is anchored with the latest act of the store when the anchor changes and at the
        start (force), so it never stands before an act (ADR-15 section 21)."""
        clock = self.demo_clock
        if clock is None or self.engine is None:
            return
        tick = time.monotonic()
        if not force and tick - self._clock_checked < CLOCK_REFRESH_S:
            return
        self._clock_checked = tick
        try:
            async with self.engine.connect() as conn:
                row = (
                    await conn.execute(select(demo_reset.c.snapshot_as_of, demo_reset.c.started_at))
                ).first()
                anchor = (row[0], row[1]) if row is not None else clock.anchored
                if anchor is None or (anchor == clock.anchored and not (force and clock.frozen)):
                    return
                latest = await latest_act(conn) if clock.frozen else None
        except Exception:  # noqa: BLE001 - no table yet, or no database: keep the clock
            return
        clock.anchor(anchor[0], anchor[1], latest_act=latest)

    def clock_kind(self) -> str:
        return kind_of(self.clock)

    # ---- model calls --------------------------------------------------------------------------

    @property
    def pool(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=self.settings.llm_concurrency, thread_name_prefix="model-call"
            )
        return self._pool

    async def model_call(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """Run a blocking model call on the model-call pool (no transaction may be open)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.pool, functools.partial(fn, *args, **kwargs))

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # ---- transactions -------------------------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """One transaction: committed when the block ends, rolled back on an error."""
        engine = self.require_engine()
        await self.refresh_clock()
        async with engine.begin() as conn:
            yield conn


def runtime(request: Request) -> Runtime:
    value = request.app.state.runtime
    if not isinstance(value, Runtime):
        raise RuntimeError("the app has no runtime: build it with apps.api.app.create_app")
    return value
