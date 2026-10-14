"""Small reads shared by the services of the other areas: the price of a returned line, the
latest decision version of a return as served, and sequential ids.

Every read goes through the serving snapshot (the store, read only) or the tables of
contracts/sql.py; nothing here decides (rule 1).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services import decisions as store
from apps.api.serving import Serving
from contracts.sql import return_requests


async def line_prices(
    conn: AsyncConnection, serving: Serving, return_ids: Iterable[str]
) -> dict[str, Decimal]:
    """The price of the returned line of each return (EUR), from the store line it names; the
    base of a diminished-value claim (ClaimView.line_price)."""
    wanted = sorted(set(return_ids))
    if not wanted:
        return {}
    rows = (
        await conn.execute(
            select(return_requests.c.return_id, return_requests.c.line_id).where(
                return_requests.c.return_id.in_(wanted)
            )
        )
    ).all()
    prices: dict[str, Decimal] = {}
    for return_id, line_id in rows:
        line = serving.by_key("store/order_lines", str(line_id))
        if line is not None:
            prices[str(return_id)] = Decimal(str(line["price_eur"]))
    return prices


async def latest(
    conn: AsyncConnection, serving: Serving, return_id: str, at: datetime
) -> tuple[s.DecisionView, Mapping[str, Any]] | None:
    """The latest version of a return's decision as served at `at`, with its row."""
    prices = await line_prices(conn, serving, [return_id])
    return await store.latest_view(conn, return_id, at, line_price=prices.get)


async def view_at(
    conn: AsyncConnection, serving: Serving, row: Mapping[str, Any], at: datetime
) -> s.DecisionView:
    """The DecisionView of a decisions row as served at `at` (in_force computed then)."""
    prices = await line_prices(conn, serving, [str(row["return_id"])])
    return await store.view_of(conn, row, at, line_price=prices.get)


async def next_number(conn: AsyncConnection, table: Table, *where: Any) -> int:
    """1 + the number of rows of `table` (matching `where`): the next sequential id number.
    Deterministic, like the ids of the API core (apps.api.services.ids)."""
    query = select(func.count()).select_from(table)
    for clause in where:
        query = query.where(clause)
    return int(await conn.scalar(query) or 0) + 1
