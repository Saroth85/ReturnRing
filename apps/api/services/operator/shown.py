"""A case as the person saw it (ADR-15 section 2, "Concurrency"): the return, its versions and
the latest one served now. Every action names the decision_id it was shown; only the latest
version of a return can be acted on.

- an unknown return, or a decision_id that is no version of it: 404 not_found;
- an earlier version: 409 stale_decision, naming the latest.

A write locks the return row first (SELECT ... FOR UPDATE on PostgreSQL; SQLite serializes its
writes), so two acts on one case run one after the other and the second sees the first.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import not_found, stale_decision
from apps.api.services import decisions as store
from apps.api.services.cases import record_of
from apps.api.services.prices import paid_price
from apps.api.serving import ReturnRecord, Serving
from contracts.sql import return_requests


@dataclass(frozen=True)
class Shown:
    ret: Mapping[str, Any]  # the return_requests row
    record: ReturnRecord
    rows: list[dict[str, Any]]  # every version, in order
    view: s.DecisionView  # the latest version, served at the time of the act
    price: Decimal  # the price of the returned line (the base of a diminished-value claim)

    @property
    def row(self) -> dict[str, Any]:
        return self.rows[-1]

    def line_price(self, _return_id: str) -> Decimal:
        return self.price


def line_price(serving: Serving, line_id: str) -> Decimal:
    """The price paid for the returned line (services.prices, H2)."""
    return paid_price(serving, line_id)


async def return_of(
    conn: AsyncConnection, return_id: str, *, lock: bool = False
) -> Mapping[str, Any]:
    query = select(return_requests).where(return_requests.c.return_id == return_id)
    if lock:
        query = query.with_for_update()
    row = (await conn.execute(query)).mappings().first()
    if row is None:
        raise not_found(f"return {return_id}")
    return dict(row)


async def shown(
    conn: AsyncConnection,
    serving: Serving,
    return_id: str,
    decision_id: str | None,
    as_of: datetime,
    *,
    lock: bool = False,
) -> Shown:
    """The case of `return_id` with its latest version served at `as_of`; `decision_id` (the
    version the person saw) must be that version. None takes the latest (a recompute of every
    case of a cluster)."""
    ret = await return_of(conn, return_id, lock=lock)
    rows = await store.decision_rows(conn, return_id)
    if not rows:
        raise not_found(f"decision of return {return_id}")
    ids = [r["decision_id"] for r in rows]
    if decision_id is not None and decision_id not in ids:
        raise not_found(f"decision {decision_id} of return {return_id}")
    if decision_id is not None and decision_id != ids[-1]:
        raise stale_decision(decision_id, ids[-1])
    record = record_of(serving, ret)
    price = line_price(serving, record.line_id)
    view = await store.view_of(conn, rows[-1], as_of, line_price=lambda _: price)
    return Shown(ret, record, rows, view, price)
