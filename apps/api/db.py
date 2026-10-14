"""The database the API writes: the tables of contracts/sql.py on PostgreSQL (asyncpg), or on
SQLite (aiosqlite) in tests. create_all builds every table, CHECK and append-only trigger
(ADR-15 section 6); the demo reset drops and recreates them (US-903)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from contracts.sql import create_all, enable_sqlite_foreign_keys, metadata


def make_engine(url: str) -> AsyncEngine:
    """An async engine on DATABASE_URL; an in-memory SQLite database is shared by every
    connection of the engine (one StaticPool connection), so tests see one database. Errors
    never log the parameters of a statement (H27): message bodies, reasons and customer ids."""
    kwargs: dict[str, Any] = {"hide_parameters": True}
    if url.startswith("sqlite") and (
        ":memory:" in url or url.rstrip("/").endswith("sqlite+aiosqlite:")
    ):
        kwargs.update(poolclass=StaticPool, connect_args={"check_same_thread": False})
    engine = create_async_engine(url, **kwargs)
    enable_sqlite_foreign_keys(engine)
    return engine


async def create_schema(engine: AsyncEngine) -> None:
    await create_all(engine)


async def drop_schema(engine: AsyncEngine) -> None:
    """Drop every table of the contract (and, on PostgreSQL, the append-only function)."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        if conn.dialect.name == "postgresql":
            await conn.execute(text("DROP FUNCTION IF EXISTS returnring_append_only() CASCADE"))


async def ping(engine: AsyncEngine) -> str | None:
    """None when the database answers and holds the contract tables, else why not."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            names = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    except Exception as exc:  # noqa: BLE001 - readiness reports any failure
        return f"{type(exc).__name__}"
    missing = sorted(set(metadata.tables) - names)
    return f"missing tables: {', '.join(missing[:3])}" if missing else None
