"""The stored assumption versions of the economic simulator (US-602, spec 8.10; rules 9, 11).

GET serves every version, newest first, each with its net benefit (policy.simulator.net_benefit)
and the four scenarios of policy.simulator.sensitivity, labelled "<version>/<scenario>" and based
on the version. PUT creates the next version from the current one (409 when base_version is not
current), in the append-only simulator_assumptions table, and logs it (assumptions_updated, by
the manager). The demo reset loads assumptions-v1, the illustrative example of the proposals
document (ECO-01: 368, 48, 128 and -192 EUR).

There is no refund input: the refund owed to the consumer is never a saving (rule 9), and the
contract refuses any unknown field. Every figure is simulated, with a provenance that names the
version, its period and its author; its dates are the month that starts on the day the version
was created (a hypothesis about a month of the policy, not a measured period).
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import conflict, invalid_request
from apps.api.services.audit import record
from apps.api.services.provenance import provenance
from apps.api.services.seeds import assumptions_of
from contracts.sql import simulator_assumptions

VERSION_PREFIX: Final = "assumptions-v"
CENT: Final = Decimal("0.01")
MONEY_FIELDS: Final = (
    "logistics_cost_per_return_eur",
    "avg_undue_discount_eur",
    "hourly_cost_eur",
    "incentives_eur",
    "system_cost_eur",
    "error_cost_eur",
    "abandonment_margin_lost_eur",
    "incremental_margin_eur",
)


def one_month(start: date) -> date:
    """The last day of the month that starts on `start` (2026-10-01 -> 2026-10-31)."""
    year, month = start.year + start.month // 12, start.month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) - timedelta(days=1)


def version_provenance(row: Mapping[str, Any]) -> s.Provenance:
    start = s.utc_day(row["created_at"])
    origin = (
        f"economic simulator (policy.simulator) on the stored version {row['version']} "
        f"({row['period']}), set by {row['created_by']}: {row['note']}; hypotheses, not a "
        "measured result"
    )
    return provenance(origin, start, one_month(start), "simulated")


def scenario_sets(stored: s.Assumptions) -> dict[s.SensitivityId, s.Assumptions]:
    """The scenarios of policy.simulator.sensitivity as labelled assumption sets: the version
    itself, then "<version>/<scenario>" based on it, whose unit costs change on purpose."""
    policy = stored.as_policy()
    halved = policy.logistics_cost_per_return_eur / 2
    doubled = policy.abandonment_margin_lost_eur * 2
    variants: dict[s.SensitivityId, Any] = {
        "logistics_halved": replace(policy, logistics_cost_per_return_eur=halved),
        "abandonment_doubled": replace(policy, abandonment_margin_lost_eur=doubled),
        "both": replace(
            policy, logistics_cost_per_return_eur=halved, abandonment_margin_lost_eur=doubled
        ),
    }
    out: dict[s.SensitivityId, s.Assumptions] = {"base": stored}
    for key, variant in variants.items():
        labelled = replace(variant, version=f"{stored.version}/{key}")
        out[key] = s.Assumptions.from_policy(labelled, based_on=stored.version)
    return out


def assumption_version(row: Mapping[str, Any]) -> s.AssumptionVersion:
    """The API view of a simulator_assumptions row, with its net benefit and scenarios."""
    stored = assumptions_of(row)
    prov = version_provenance(row)
    return s.AssumptionVersion(
        version=row["version"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        note=row["note"],
        supersedes=row["supersedes_version"],
        baseline=row["baseline"],
        net_benefit=s.NetBenefitView.build(stored, prov),
        sensitivity=[
            s.SensitivityScenario(id=key, net_benefit=s.NetBenefitView.build(a, prov))
            for key, a in scenario_sets(stored).items()
        ],
    )


async def _rows(conn: AsyncConnection) -> list[dict[str, Any]]:
    query = select(simulator_assumptions).order_by(simulator_assumptions.c.seq.desc())
    return [dict(r) for r in (await conn.execute(query)).mappings().all()]


async def assumptions_response(conn: AsyncConnection) -> s.SimulatorAssumptionsResponse:
    """GET /api/simulator/assumptions: the current version and the history, newest first."""
    rows = await _rows(conn)
    if not rows:
        raise LookupError("no stored assumption version: run python -m apps.api.reset")
    history = [assumption_version(r) for r in rows]
    return s.SimulatorAssumptionsResponse(current=history[0], history=history)


def check_scenarios(inputs: s.AssumptionInputs) -> None:
    """The logistics_halved scenario halves the cost per return: a cost with an odd number of
    cents has a half no euro amount of the contract holds (2 decimals). Refused (422) rather
    than a scenario that is not policy.simulator's."""
    half = Decimal(str(inputs.logistics_cost_per_return_eur)) / 2
    if half != half.quantize(CENT):
        raise invalid_request(
            "logistics_cost_per_return_eur: the logistics_halved scenario needs half of it in "
            f"cents, and {half} EUR is not; use an even number of cents",
            [("scenario", "logistics_halved: half of the cost per return has 3 decimals")],
        )


def _values(inputs: s.AssumptionInputs) -> dict[str, Any]:
    values: dict[str, Any] = inputs.model_dump()
    for name in MONEY_FIELDS:
        values[name] = Decimal(str(values[name])).quantize(CENT)
    values["minutes_saved"] = Decimal(str(values["minutes_saved"])).quantize(CENT)
    return values


async def update_assumptions(
    conn: AsyncConnection,
    body: s.SimulatorAssumptionsUpdate,
    *,
    at: datetime,
    request_id: str | None,
) -> s.SimulatorAssumptionsResponse:
    """PUT /api/simulator/assumptions: the next version from the current one, logged."""
    rows = await _rows(conn)
    if not rows:
        raise LookupError("no stored assumption version: run python -m apps.api.reset")
    current = rows[0]
    if body.base_version != current["version"]:
        raise conflict(
            f"{body.base_version} is not the current assumption version: {current['version']} "
            "is; reload it and start from there"
        )
    check_scenarios(body.assumptions)
    seq = int(current["seq"]) + 1
    version = f"{VERSION_PREFIX}{seq}"
    try:
        await conn.execute(
            insert(simulator_assumptions).values(
                version=version,
                seq=seq,
                supersedes_version=current["version"],
                basis="simulated",
                baseline=current["baseline"],
                note=body.note,
                created_by=body.author,
                created_at=at,
                **_values(body.assumptions),
            )
        )
    except IntegrityError:
        raise conflict(
            f"another assumption version was created from {current['version']} at the same time"
        ) from None
    await record(
        conn,
        at=at,
        actor=body.author,
        action="assumptions_updated",
        entity_type="simulator_assumptions",
        entity_id=version,
        before={"version": current["version"]},
        after={"version": version},
        request_id=request_id,
    )
    return await assumptions_response(conn)
