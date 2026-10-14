"""Identifiers the API writes (ADR-16). Deterministic: the same reset gives the same ids.

Sequential numbers come from the id_counters table (ADR-15 section 15, H10): allocate() takes
the next number of a kind in the caller's transaction. Its row stays locked until the
transaction ends, so two concurrent writers of one kind never take the same number on
PostgreSQL, and a rolled-back transaction gives its number back. A database whose counter row
is missing (written before the counters existed) starts the kind after its existing rows.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy import Table, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from contracts.sql import id_counters

RETURN_PREFIX: Final = "RET-"


def return_number(return_id: str) -> str:
    """The number of a return id: 0003008 of RET-0003008 (another id is kept whole)."""
    return return_id.removeprefix(RETURN_PREFIX)


def decision_id(return_id: str, version: int) -> str:
    """DEC-<return>-<version>, as in the contract examples (DEC-000102-1)."""
    return f"DEC-{return_number(return_id)}-{version}"


def application_id(return_id: str, version: int) -> str:
    """The cluster coupon application proposed with a decision version."""
    return f"CRA-{return_number(return_id)}-{version}"


def generation_id(return_id: str, seq: int) -> str:
    """The seq-th generation of a return's drafts (GEN-000101-1)."""
    return f"GEN-{return_number(return_id)}-{seq}"


def message_id(seq: int) -> str:
    return f"MSG-{seq:06d}"


async def allocate(
    conn: AsyncConnection, kind: str, table: Table | None = None, *where: Any
) -> int:
    """The next number of `kind` (1, 2, ...), in the caller's transaction. A missing counter
    starts after the rows of `table` matching `where` (the ids written before the counters)."""
    found = await conn.scalar(select(id_counters.c.kind).where(id_counters.c.kind == kind))
    if found is None:
        seed = 0
        if table is not None:
            query = select(func.count()).select_from(table)
            for clause in where:
                query = query.where(clause)
            seed = int(await conn.scalar(query) or 0)
        make = pg_insert if conn.dialect.name == "postgresql" else sqlite_insert
        await conn.execute(make(id_counters).values(kind=kind, value=seed).on_conflict_do_nothing())
    value = await conn.scalar(
        update(id_counters)
        .where(id_counters.c.kind == kind)
        .values(value=id_counters.c.value + 1)
        .returning(id_counters.c.value)
    )
    return int(value)
