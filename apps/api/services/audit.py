"""The audit log writer and reader (US-604; rules 5 and 11; NFR-12).

record() validates an entry with the contract model AuditEntry before it inserts it, so the rules
of the log hold in code as well as in the CHECKs: a human-only action is never the system's, a
reason where the log needs one, typed payloads for decisions and generations, and Metrics for
every number of an untyped payload. The actor's role comes from DEMO_USERS; "system" is the only
actor with the system role. audit_log is append-only: the database refuses UPDATE and DELETE.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from contracts.sql import audit_log
from contracts.vocab import DEMO_USERS, SYSTEM_ACTOR, AuditAction, AuditEntityType, Role

Payload = s.DecisionAuditState | s.GenerationAuditState | Mapping[str, Any] | None


def role_of(actor: str) -> Role:
    if actor == SYSTEM_ACTOR:
        return "system"
    try:
        return DEMO_USERS[actor]
    except KeyError:
        raise ValueError(f"{actor} is not a demo user (ADR-15 section 5)") from None


def _json(payload: Payload) -> Any:
    if payload is None:
        return None
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    return dict(payload)


async def record(
    conn: AsyncConnection,
    *,
    at: datetime,
    actor: str,
    action: AuditAction,
    entity_type: AuditEntityType,
    entity_id: str,
    return_id: str | None = None,
    decision_id: str | None = None,
    reason: str | None = None,
    before: Payload = None,
    after: Payload = None,
    rules_version: str | None = None,
    model_versions: Mapping[str, str] | None = None,
    assumptions_version: str | None = None,
    request_id: str | None = None,
) -> s.AuditEntry:
    """Append one entry; returns it with its entry_id."""
    fields: dict[str, Any] = {
        "at": at,
        "actor": actor,
        "actor_role": role_of(actor),
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "return_id": return_id,
        "decision_id": decision_id,
        "reason": reason,
        "before": _json(before),
        "after": _json(after),
        "rules_version": rules_version,
        "model_versions": dict(model_versions) if model_versions is not None else None,
        "assumptions_version": assumptions_version,
        "request_id": request_id,
    }
    s.AuditEntry.model_validate({"entry_id": 1, **fields})  # the contract, before the insert
    result = await conn.execute(insert(audit_log).values(**fields).returning(audit_log.c.entry_id))
    entry_id = int(result.scalar_one())
    return s.AuditEntry.model_validate({"entry_id": entry_id, **fields})


def entry_of(row: Mapping[str, Any]) -> s.AuditEntry:
    """The API view of an audit_log row."""
    return s.AuditEntry.model_validate(dict(row))


async def entries(
    conn: AsyncConnection, *, return_id: str | None = None, decision_id: str | None = None
) -> list[s.AuditEntry]:
    """The entries about a return or a decision, in time order (entry_id breaks ties)."""
    query = select(audit_log).order_by(audit_log.c.at, audit_log.c.entry_id)
    if return_id is not None:
        query = query.where(audit_log.c.return_id == return_id)
    if decision_id is not None:
        query = query.where(audit_log.c.decision_id == decision_id)
    rows = (await conn.execute(query)).mappings().all()
    return [entry_of(dict(row)) for row in rows]
