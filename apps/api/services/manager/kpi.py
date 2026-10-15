"""GET /api/kpi: the KPIs of a week (US-603) from the decisions table, the stored assumptions and
the offline evaluation report.

The week's decisions are the decision versions proposed in it (created between Monday 00:00 and
the next Monday, UTC), with their outcome as served: approved, overridden, rejected or pending (a
proposal no person acted on yet). A proposal superseded before anyone acted on it (a recompute,
F7) is replaced by its successor and not counted; a review version (lift or renewal, US-607)
counts as the act it is.

- decision time: from the proposal to the person's act (approved_at, the time of the act's
  audit entry), for the proposals people acted on; 0 when none was;
- abstentions: withdrawal returns with insufficient evidence (US-307), over the week's decisions;
- override reasons: the categories of the overrides and rejections;
- net_benefit_simulated: policy.simulator on the summed quantities of the engine's proposal of
  every decision whose proposal applies (approved, or proposed without a need for approval) and
  is still in force: a version a lift or a recompute superseded applies no more (1.5.0, H5),
  labelled kpi/<week> and priced with the unit costs of the stored version in force (US-602).
  Overrides, rejections and proposals awaiting approval apply none of the proposal's levers. The
  refund owed to the consumer is never a quantity (rule 9);
- net_benefit_realized: null. It needs the outcomes of the decisions, and the serving snapshot
  ends at its instant, before any decision's effect could be observed; it is never filled with
  simulated figures;
- false_positives: the cohorts of the offline evaluation report (rule 12, services.evaluation);
- open_catalog_tasks: the catalogue tasks opened by the end of the week and still open or in
  progress.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import invalid_request
from apps.api.services.manager.evaluation import EvaluationReport, cohort_rates
from apps.api.services.provenance import provenance
from apps.api.services.seeds import current_assumptions
from contracts.sql import catalog_tasks, decisions
from contracts.vocab import OverrideCategory

WEEK: Final = timedelta(days=7)
ACTED: Final = ("approved", "overridden", "rejected")
COUNTS: Final = ("logistics_returns_avoided", "undue_discounts_avoided")
QUANTITIES: Final = (
    *COUNTS,
    "minutes_saved",
    "incentives_eur",
    "system_cost_eur",
    "error_cost_eur",
    "abandonment_margin_lost_eur",
    "incremental_margin_eur",
)


def default_week(now: datetime, latest: datetime | None = None) -> date:
    """The Monday of the week (UTC) of the latest decision version, `latest`, no later than
    `now` (1.5.0, H31: the demo's decisions are made in the week of the snapshot, the acts on
    them just after); without a decision, the last complete week before `now`."""
    today = s.utc_day(now)
    if latest is not None and s.utc_day(latest) <= today:
        day = s.utc_day(latest)
        return day - timedelta(days=day.weekday())
    return today - timedelta(days=today.weekday()) - WEEK


async def latest_decision_at(conn: AsyncConnection) -> datetime | None:
    """When the latest decision version was created or acted on."""
    created = await conn.scalar(select(func.max(decisions.c.created_at)))
    acted = await conn.scalar(select(func.max(decisions.c.approved_at)))
    found = [x for x in (created, acted) if x is not None]
    return max(found) if found else None


def _start(week: date) -> datetime:
    return datetime.combine(week, time(0), tzinfo=UTC)


async def week_rows(conn: AsyncConnection, week: date) -> list[dict[str, Any]]:
    """The decision versions proposed in the week, without the proposals superseded before a
    person acted on them; each row says whether a later version superseded it (superseded)."""
    start = _start(week)
    query = (
        select(decisions)
        .where((decisions.c.created_at >= start) & (decisions.c.created_at < start + WEEK))
        .order_by(decisions.c.created_at, decisions.c.decision_id)
    )
    rows = [dict(r) for r in (await conn.execute(query)).mappings().all()]
    superseded = set(
        (
            await conn.execute(
                select(decisions.c.supersedes_decision_id).where(
                    decisions.c.supersedes_decision_id.is_not(None)
                )
            )
        ).scalars()
    )
    return [
        {**r, "superseded": r["decision_id"] in superseded}
        for r in rows
        if not (r["state"] == "proposed" and r["decision_id"] in superseded)
    ]


def proposal_applies(row: Mapping[str, Any]) -> bool:
    """The engine's proposal applies: approved, or proposed without a need for approval, and
    not superseded by a lift or a recompute (H5)."""
    standing = row["state"] == "approved" or (
        row["state"] == "proposed" and not row["requires_approval"]
    )
    return standing and not row.get("superseded", False)


def week_quantities(rows: Sequence[Mapping[str, Any]]) -> dict[str, int | float]:
    """The summed quantities of the engine's proposal of the decisions whose proposal applies."""
    sums = dict.fromkeys(QUANTITIES, Decimal(0))
    for row in rows:
        if not proposal_applies(row):
            continue
        proposal = next(a for a in row["alternatives"] if a["recommended"])
        values = proposal["net_benefit"]["assumptions"]
        for name in QUANTITIES:
            sums[name] += Decimal(str(values[name]))
    return {name: int(v) if name in COUNTS else float(v) for name, v in sums.items()}


def _metric(value: float | int, unit: s.Unit, prov: s.Provenance) -> s.Metric:
    return s.Metric(value=value, unit=unit, provenance=prov)


def _percentile(values: Sequence[float], q: float) -> float:
    return round(float(np.percentile(values, q)), 1) if values else 0.0


async def kpi(
    conn: AsyncConnection,
    report: EvaluationReport,
    week: date,
    now: datetime,
) -> s.KpiResponse:
    if week > s.utc_day(now):
        refused = f"the week of {week} has not started yet"
        raise invalid_request(refused, [("week_not_started", f"query.week_start: {refused}")])
    end = week + timedelta(days=6)
    rows = await week_rows(conn, week)
    served = f"{now:%Y-%m-%d %H:%M} UTC"
    decided = provenance(
        f"decisions table of the API: the {len(rows)} decision versions proposed from {week} to "
        f"{end}, outcomes as of {served}; Navigli Fashion synthetic store",
        week,
        end,
        "synthetic",
    )
    states = Counter(str(r["state"]) for r in rows)
    acted = [
        (r["approved_at"] - r["created_at"]).total_seconds()
        for r in rows
        if r["state"] in ACTED and r["revision_trigger"] != "review" and r["approved_at"]
    ]
    timing = provenance(
        f"decision time from the proposal to the person's act (audit log), {len(acted)} "
        f"decisions of the week acted on as of {served}"
        + ("" if acted else ": none yet, shown as 0"),
        week,
        end,
        "synthetic",
    )
    abstentions = sum(
        r["path"] == "withdrawal" and r["evidence_quality"] == "insufficient" for r in rows
    )
    reasons: Counter[OverrideCategory] = Counter(
        r["override_category"]
        for r in rows
        if r["state"] in ("overridden", "rejected") and r["override_category"] is not None
    )
    stored = await current_assumptions(conn)
    applied = sum(proposal_applies(r) for r in rows)
    label = f"kpi/{week.isoformat()}"
    simulated = s.NetBenefitView.build(
        stored.derive(
            label,
            f"week {week} to {end}: the proposals in force of the {applied} decisions proposed "
            "in the week, over the 12 months after each decision",
            **week_quantities(rows),
        ),
        provenance(
            f"economic simulator (policy.simulator) on the proposals that apply among the "
            f"decisions of the week, unit costs of {stored.version}; quantities of the "
            "alternatives of each decision (hypotheses)",
            week,
            end,
            "simulated",
        ),
    )
    tasks = await conn.scalar(
        select(func.count())
        .select_from(catalog_tasks)
        .where(
            catalog_tasks.c.status.in_(("open", "in_progress"))
            & (catalog_tasks.c.created_at < _start(week) + WEEK)
        )
    )
    tasks_prov = provenance(
        f"catalogue tasks opened by {end} and still open or in progress as of {served}",
        week,
        end,
        "synthetic",
    )
    return s.KpiResponse(
        period_start=week,
        period_end=end,
        net_benefit_simulated=simulated,
        net_benefit_realized=None,
        evaluation_report_id=report.report_id,
        false_positives=cohort_rates(report),
        decision_time_p50=_metric(_percentile(acted, 50), "seconds", timing),
        decision_time_p95=_metric(_percentile(acted, 95), "seconds", timing),
        abstentions_insufficient_evidence=_metric(abstentions, "count", decided),
        abstention_rate=_metric(
            round(abstentions / len(rows), 4) if rows else 0.0, "ratio", decided
        ),
        decisions=s.DecisionCounts(
            total=_metric(len(rows), "count", decided),
            approved=_metric(states["approved"], "count", decided),
            overridden=_metric(states["overridden"], "count", decided),
            rejected=_metric(states["rejected"], "count", decided),
            pending=_metric(states["proposed"], "count", decided),
        ),
        override_reasons=[
            s.OverrideReasonCount(category=category, count=_metric(n, "count", decided))
            for category, n in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        open_catalog_tasks=_metric(int(tasks or 0), "count", tasks_prov),
    )
