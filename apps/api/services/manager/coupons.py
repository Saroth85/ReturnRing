"""The general coupon rule and its cluster applications (US-402, US-607, LEG-09; rule 5).

The rule is a versioned text the manager approves (coupon_rules, append-only, a seq chain): a
new version supersedes the current one, which stays in the history, inactive. The engine applies
the current version to a Q4 case in a cluster as a proposal (services.decisions), which the
person who approves or overrides the decision confirms. An application is always reversible: a
person reverses a confirmed one on request, with a reason (rectification), in the same table
and with an audit entry about its decision and return. A proposed application has nothing to
reverse: it goes with its decision (409).

Served applications carry as_of and in_force, computed when served (ADR-15): in force while
confirmed, not reversed, and before the review date of the decision that applied it (UTC days).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import conflict, not_found
from apps.api.services.audit import record
from apps.api.services.decisions import decision_row_by_id
from contracts.sql import coupon_rule_applications, coupon_rules

RULE_PREFIX: Final = "coupon-rule-v"


def rule_view(row: Mapping[str, Any], active: bool) -> s.CouponRule:
    return s.CouponRule(
        rule_version=row["rule_version"],
        text=row["text"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        supersedes=row["supersedes_version"],
        active=active,
    )


def application_view(row: Mapping[str, Any], as_of: datetime) -> s.CouponRuleApplication:
    """An application as served at as_of: in force while confirmed and before its review date."""
    return s.CouponRuleApplication(
        application_id=row["application_id"],
        rule_version=row["rule_version"],
        cluster_id=row["cluster_id"],
        decision_id=row["decision_id"],
        affected_customer_ids=list(row["affected_customer_ids"]),
        state=row["state"],
        proposed_at=row["proposed_at"],
        confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"],
        review_at=row["review_at"],
        reversed_by=row["reversed_by"],
        reversed_at=row["reversed_at"],
        reversal_reason=row["reversal_reason"],
        as_of=as_of,
        in_force=row["state"] == "confirmed" and s.utc_day(as_of) < row["review_at"],
    )


async def _rules(conn: AsyncConnection) -> list[dict[str, Any]]:
    query = select(coupon_rules).order_by(coupon_rules.c.seq.desc())
    rows = [dict(r) for r in (await conn.execute(query)).mappings().all()]
    if not rows:
        raise LookupError("no general coupon rule: run python -m apps.api.reset")
    return rows


async def coupon_rules_response(conn: AsyncConnection, now: datetime) -> s.CouponRulesResponse:
    """GET /api/coupon-rules: the versions, newest first, and every application as of now."""
    history = [rule_view(r, active=i == 0) for i, r in enumerate(await _rules(conn))]
    query = select(coupon_rule_applications).order_by(
        coupon_rule_applications.c.proposed_at.desc(),
        coupon_rule_applications.c.application_id.desc(),
    )
    rows = (await conn.execute(query)).mappings().all()
    return s.CouponRulesResponse(
        current=history[0],
        history=history,
        applications=[application_view(dict(r), now) for r in rows],
        as_of=now,
    )


async def approve_rule(
    conn: AsyncConnection, body: s.CouponRuleCreate, *, at: datetime, request_id: str | None
) -> s.CouponRulesResponse:
    """POST /api/coupon-rules: the next version of the general rule, approved by the manager
    (the route checks the role). The next decisions apply it; earlier applications keep the
    version they applied."""
    current = (await _rules(conn))[0]
    seq = int(current["seq"]) + 1
    version = f"{RULE_PREFIX}{seq}"
    try:
        await conn.execute(
            insert(coupon_rules).values(
                rule_version=version,
                seq=seq,
                supersedes_version=current["rule_version"],
                text=body.text,
                approved_by=body.approved_by,
                approved_at=at,
            )
        )
    except IntegrityError:
        raise conflict(
            f"another rule version was approved from {current['rule_version']} at the same time"
        ) from None
    await record(
        conn,
        at=at,
        actor=body.approved_by,
        action="coupon_rule_approved",
        entity_type="coupon_rule",
        entity_id=version,
        before={"rule_version": current["rule_version"]},
        after={"rule_version": version},
        request_id=request_id,
    )
    return await coupon_rules_response(conn, at)


async def reverse_application(
    conn: AsyncConnection,
    application_id: str,
    body: s.CouponApplicationReverse,
    *,
    at: datetime,
    request_id: str | None,
) -> s.CouponRuleApplication:
    """POST /api/coupon-rules/applications/{application_id}/reverse: a person gives the linked
    accounts their coupons back (rectification on request, LEG-09). The decision stays as it
    was decided; only its block is reversed."""
    query = select(coupon_rule_applications).where(
        coupon_rule_applications.c.application_id == application_id
    )
    found = (await conn.execute(query)).mappings().first()
    if found is None:
        raise not_found(f"coupon rule application {application_id}")
    if found["state"] != "confirmed":
        raise conflict(
            f"{application_id} is {found['state']}: only a confirmed application is reversed "
            "(a proposed one is confirmed or not with its decision)"
        )
    changes = {
        "state": "reversed",
        "reversed_by": body.author,
        "reversed_at": at,
        "reversal_reason": body.reason,
    }
    result = await conn.execute(
        update(coupon_rule_applications)
        .where(
            (coupon_rule_applications.c.application_id == application_id)
            & (coupon_rule_applications.c.state == "confirmed")
        )
        .values(**changes)
    )
    if result.rowcount != 1:
        raise conflict(f"{application_id} changed meanwhile: reload it")
    decision = await decision_row_by_id(conn, found["decision_id"])
    if decision is None:
        raise LookupError(f"{found['decision_id']}: the decision of {application_id} is missing")
    await record(
        conn,
        at=at,
        actor=body.author,
        action="coupon_rule_application_reversed",
        entity_type="coupon_rule_application",
        entity_id=application_id,
        return_id=decision["return_id"],
        decision_id=found["decision_id"],
        reason=body.reason,
        before={"state": "confirmed"},
        after={"state": "reversed"},
        rules_version=decision["rules_version"],
        model_versions=decision["model_versions"],
        assumptions_version=decision["assumptions_version"],
        request_id=request_id,
    )
    return application_view({**found, **changes}, at)
