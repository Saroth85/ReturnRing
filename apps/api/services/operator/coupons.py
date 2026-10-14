"""The cluster coupon application of a decision (US-402, US-607, LEG-09; ADR-15 section 4).

An application is proposed with a version whose levers block the cluster's coupons, confirmed by
the person who approves (or overrides, or renews) it, and reversed when a lift or a recompute
supersedes that version (the person who lifted, or who added the evidence or disputed the link,
with their reason). Served with as_of and in_force: in force while confirmed and before its
review date, in UTC days; computed when served, never stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import conflict
from apps.api.services.audit import record
from contracts.sql import coupon_rule_applications

_FIELDS = (
    "application_id",
    "rule_version",
    "cluster_id",
    "decision_id",
    "affected_customer_ids",
    "state",
    "proposed_at",
    "confirmed_by",
    "confirmed_at",
    "review_at",
    "reversible",
    "reversed_by",
    "reversed_at",
    "reversal_reason",
)


def served(row: Any, as_of: datetime) -> s.CouponRuleApplication:
    """The CouponRuleApplication of a coupon_rule_applications row, as served at `as_of`."""
    values = {k: row[k] for k in _FIELDS}
    values["affected_customer_ids"] = list(values["affected_customer_ids"])
    live = row["state"] == "confirmed" and s.utc_day(as_of) < row["review_at"]
    return s.CouponRuleApplication.model_validate({**values, "as_of": as_of, "in_force": live})


async def application(
    conn: AsyncConnection, application_id: str, as_of: datetime
) -> s.CouponRuleApplication:
    query = select(coupon_rule_applications).where(
        coupon_rule_applications.c.application_id == application_id
    )
    row = (await conn.execute(query)).mappings().first()
    if row is None:
        raise LookupError(f"{application_id}: coupon application missing")
    return served(row, as_of)


async def confirm(conn: AsyncConnection, application_id: str, by: str, at: datetime) -> None:
    """The person who approves the decision confirms its proposed application."""
    result = await conn.execute(
        update(coupon_rule_applications)
        .where(
            (coupon_rule_applications.c.application_id == application_id)
            & (coupon_rule_applications.c.state == "proposed")
        )
        .values(state="confirmed", confirmed_by=by, confirmed_at=at)
    )
    if result.rowcount != 1:
        raise conflict(f"{application_id} is no longer a proposed coupon application")


async def reverse(
    conn: AsyncConnection, application_id: str, *, by: str, at: datetime, reason: str
) -> bool:
    """Reverse a confirmed application (the person, the time and the reason of the lift or the
    recompute that supersedes its decision); False when it was not confirmed (still proposed, or
    reversed before)."""
    result = await conn.execute(
        update(coupon_rule_applications)
        .where(
            (coupon_rule_applications.c.application_id == application_id)
            & (coupon_rule_applications.c.state == "confirmed")
        )
        .values(state="reversed", reversed_by=by, reversed_at=at, reversal_reason=reason)
    )
    return bool(result.rowcount == 1)


async def log_reversal(
    conn: AsyncConnection,
    application_id: str,
    *,
    by: str,
    at: datetime,
    reason: str,
    return_id: str,
    decision_id: str,
    request_id: str | None,
) -> s.AuditEntry:
    """The audit entry of a reversal (coupon_rule_application_reversed: a person, a reason)."""
    return await record(
        conn,
        at=at,
        actor=by,
        action="coupon_rule_application_reversed",
        entity_type="coupon_rule_application",
        entity_id=application_id,
        return_id=return_id,
        decision_id=decision_id,
        reason=reason,
        before={"state": "confirmed"},
        after={"state": "reversed"},
        request_id=request_id,
    )
