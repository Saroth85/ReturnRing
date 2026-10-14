"""What the demo reset loads before any return (US-903): the stored assumption version
assumptions-v1 (the illustrative example of spec 8.10, ECO-01) and the general coupon rule
coupon-rule-v1 approved by the manager (US-402). Both tables are append-only: a change is a new
version, written by the manager's endpoints."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.audit import record
from contracts.sql import coupon_rules, simulator_assumptions
from policy.simulator import PROPOSAL_EXAMPLE
from policy.simulator import Assumptions as SimAssumptions

ASSUMPTIONS_V1: Final = PROPOSAL_EXAMPLE.version  # "assumptions-v1"
ASSUMPTIONS_NOTE: Final = "Seeded with the illustrative example of the proposals document (ECO-01)"
ASSUMPTIONS_BASELINE: Final = "Navigli base policy (spec 4) on the same period"
COUPON_RULE_V1: Final = "coupon-rule-v1"
COUPON_RULE_TEXT: Final = (
    "Welcome coupons and referrals are paused, on future orders, for every account of a cluster "
    "when a case of the cluster is in Q4 and an operator confirms it; review after 90 days."
)
COUPON_RULE_APPROVER: Final = "luca"  # the demo manager (spec 4)
SEED_NOTE: Final = "loaded by the demo reset (US-903)"


def _row_of(a: SimAssumptions) -> dict[str, Any]:
    values = asdict(a)
    values.pop("nature")
    values.pop("version")
    return values


async def load_seeds(conn: AsyncConnection, at: datetime, request_id: str | None = None) -> None:
    """Insert assumptions-v1 and coupon-rule-v1 (once: a second call finds them and stops)."""
    found = await conn.scalar(select(simulator_assumptions.c.version).limit(1))
    if found is not None:
        return
    await conn.execute(
        insert(simulator_assumptions).values(
            version=ASSUMPTIONS_V1,
            seq=1,
            supersedes_version=None,
            basis=PROPOSAL_EXAMPLE.nature,
            baseline=ASSUMPTIONS_BASELINE,
            note=ASSUMPTIONS_NOTE,
            created_by="system",
            created_at=at,
            **_row_of(PROPOSAL_EXAMPLE),
        )
    )
    await conn.execute(
        insert(coupon_rules).values(
            rule_version=COUPON_RULE_V1,
            seq=1,
            supersedes_version=None,
            text=COUPON_RULE_TEXT,
            approved_by=COUPON_RULE_APPROVER,
            approved_at=at,
        )
    )
    await record(
        conn,
        at=at,
        actor=COUPON_RULE_APPROVER,
        action="coupon_rule_approved",
        entity_type="coupon_rule",
        entity_id=COUPON_RULE_V1,
        after={"rule_version": COUPON_RULE_V1, "note": SEED_NOTE},
        request_id=request_id,
    )


async def current_assumptions(conn: AsyncConnection) -> s.Assumptions:
    """The stored assumption version in force: the last of the seq chain."""
    row = (
        (
            await conn.execute(
                select(simulator_assumptions).order_by(simulator_assumptions.c.seq.desc()).limit(1)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise LookupError("no stored assumption version: run python -m apps.api.reset")
    return assumptions_of(row)


def assumptions_of(row: Any) -> s.Assumptions:
    """The API view of a simulator_assumptions row (a stored version: based_on null)."""
    policy = SimAssumptions(
        period=row["period"],
        logistics_returns_avoided=int(row["logistics_returns_avoided"]),
        logistics_cost_per_return_eur=Decimal(row["logistics_cost_per_return_eur"]),
        undue_discounts_avoided=int(row["undue_discounts_avoided"]),
        avg_undue_discount_eur=Decimal(row["avg_undue_discount_eur"]),
        minutes_saved=Decimal(row["minutes_saved"]),
        hourly_cost_eur=Decimal(row["hourly_cost_eur"]),
        incentives_eur=Decimal(row["incentives_eur"]),
        system_cost_eur=Decimal(row["system_cost_eur"]),
        error_cost_eur=Decimal(row["error_cost_eur"]),
        abandonment_margin_lost_eur=Decimal(row["abandonment_margin_lost_eur"]),
        incremental_margin_eur=Decimal(row["incremental_margin_eur"]),
        nature=row["basis"],
        version=row["version"],
    )
    return s.Assumptions.from_policy(policy)


async def current_coupon_rule(conn: AsyncConnection) -> str:
    version = await conn.scalar(
        select(coupon_rules.c.rule_version).order_by(coupon_rules.c.seq.desc()).limit(1)
    )
    if version is None:
        raise LookupError("no general coupon rule: run python -m apps.api.reset")
    return str(version)
