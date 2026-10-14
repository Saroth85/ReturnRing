"""The audit log and the simulated outbox as the manager reads them (US-604, US-304; NFR-12).

GET /api/audit filters the append-only log (return, decision, actor, actions, entity type, and
the time window since <= at < until), newest first, in pages; format=csv exports every entry
the filters select (pages do not apply) with AUDIT_CSV_COLUMNS, before, after and model_versions
as JSON cells. A free-text cell a spreadsheet would run as a formula (=, +, -, @, tab or carriage
return first) starts with a quote in the CSV; the JSON keeps the text as written.

GET /api/outbox lists the messages people's acts sent (spec 4: no real email), newest first.
Both read what the writers stored and validated (services.audit, services.outbox).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.audit import entry_of
from apps.api.services.outbox import message_of
from contracts.sql import audit_log, outbox

FORMULA_STARTS: Final = ("=", "+", "-", "@", "\t", "\r")
JSON_COLUMNS: Final = frozenset({"before", "after", "model_versions"})


def _filtered(query: Select[Any], q: s.AuditQuery) -> Select[Any]:
    c = audit_log.c
    if q.return_id is not None:
        query = query.where(c.return_id == q.return_id)
    if q.decision_id is not None:
        query = query.where(c.decision_id == q.decision_id)
    if q.actor is not None:
        query = query.where(c.actor == q.actor)
    if q.action:
        query = query.where(c.action.in_(list(q.action)))
    if q.entity_type is not None:
        query = query.where(c.entity_type == q.entity_type)
    if q.since is not None:
        query = query.where(c.at >= q.since)
    if q.until is not None:
        query = query.where(c.at < q.until)
    return query


def _newest_first(query: Select[Any]) -> Select[Any]:
    return query.order_by(audit_log.c.at.desc(), audit_log.c.entry_id.desc())


async def audit_page(conn: AsyncConnection, q: s.AuditQuery) -> s.AuditPage:
    total = await conn.scalar(_filtered(select(func.count()).select_from(audit_log), q))
    query = _newest_first(_filtered(select(audit_log), q))
    rows = (
        await conn.execute(query.limit(q.page_size).offset((q.page - 1) * q.page_size))
    ).mappings()
    return s.AuditPage(
        items=[entry_of(dict(r)) for r in rows.all()],
        total=int(total or 0),
        page=q.page,
        page_size=q.page_size,
    )


def _cell(column: str, value: Any) -> str:
    if value is None:
        return ""
    if column in JSON_COLUMNS:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = str(value)
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def csv_row(entry: Mapping[str, Any]) -> list[str]:
    return [_cell(column, entry[column]) for column in s.AUDIT_CSV_COLUMNS]


async def audit_csv(conn: AsyncConnection, q: s.AuditQuery) -> str:
    """Every entry the filters select, in the order of the pages, as RFC 4180 CSV."""
    rows = (await conn.execute(_newest_first(_filtered(select(audit_log), q)))).mappings()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(s.AUDIT_CSV_COLUMNS)
    for row in rows.all():
        writer.writerow(csv_row(entry_of(dict(row)).model_dump(mode="json")))
    return out.getvalue()


async def outbox_page(conn: AsyncConnection, q: s.OutboxQuery) -> s.OutboxResponse:
    count = select(func.count()).select_from(outbox)
    query = select(outbox).order_by(outbox.c.created_at.desc(), outbox.c.message_id.desc())
    if q.return_id is not None:
        count = count.where(outbox.c.return_id == q.return_id)
        query = query.where(outbox.c.return_id == q.return_id)
    rows = await conn.execute(query.limit(q.page_size).offset((q.page - 1) * q.page_size))
    return s.OutboxResponse(
        items=[message_of(dict(r)) for r in rows.mappings().all()],
        total=int(await conn.scalar(count) or 0),
    )
