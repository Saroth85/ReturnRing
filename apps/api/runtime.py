"""What a running API holds (app.state.runtime): its settings, clock, database engine, the
serving snapshot in memory and the draft generator. Routes reach it with runtime(request)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from apps.api.clock import Clock
from apps.api.errors import ApiError
from apps.api.services.generation import Generator
from apps.api.serving import Serving
from apps.api.settings import Settings


@dataclass
class Runtime:
    settings: Settings
    clock: Clock
    generator: Generator
    engine: AsyncEngine | None = None
    serving: Serving | None = None
    serving_error: str | None = None
    database_error: str | None = None

    def now(self) -> datetime:
        return self.clock.now()

    def require_serving(self) -> Serving:
        if self.serving is None:
            raise ApiError(500, "internal", f"serving snapshot unavailable: {self.serving_error}")
        return self.serving

    def require_engine(self) -> AsyncEngine:
        if self.engine is None:
            raise ApiError(500, "internal", f"database unavailable: {self.database_error}")
        return self.engine

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """One transaction: committed when the block ends, rolled back on an error."""
        async with self.require_engine().begin() as conn:
            yield conn


def runtime(request: Request) -> Runtime:
    value = request.app.state.runtime
    if not isinstance(value, Runtime):
        raise RuntimeError("the app has no runtime: build it with apps.api.app.create_app")
    return value
