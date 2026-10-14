"""Product anomalies and catalogue tasks (US-208, US-606, spec 8.9, flow F6, E2E-04).

An anomaly is a flagged row of features/product_anomalies at the last snapshot computed by the
time it is served: returns of a variant for one reason in one sales week above the category
baseline (z > 3 with at least 20 sales, and the exact Poisson tail of pipeline.anomalies). It is
a product matter: no customer is named, and nothing here changes a customer's risk, terms or
messages (Elena's return stays regular). Each item is the anomaly a case card shows for its
variant (services.cases.product_anomaly): the same figures, trend and returns.

A catalogue task opens from an anomaly (one task at a time per anomaly: a cancelled task frees
it), moves open -> in progress -> done or cancelled, and every move is in the audit log. Once
done, its follow-up compares the variant's return rate for the reason in the sales weeks before
the change (FOLLOW_WEEKS of pipeline.anomalies.catalogue, the week of the change left out) with
the full weeks after it whose sales the snapshot knows; a decrease alone does not prove the
effect of the change (the caveat travels with the figures).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Final

import pandas as pd
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import conflict, invalid_request, not_found
from apps.api.services.audit import record
from apps.api.services.cases import anomaly_detail
from apps.api.services.decisions import return_row
from apps.api.services.provenance import from_file
from apps.api.serving import Serving
from contracts.sql import catalog_tasks
from pipeline.anomalies.catalogue import FOLLOW_WEEKS, weekly_series
from pipeline.anomalies.detect import ANOMALY_THRESHOLDS, week_start
from policy.types import ReturnReason

TASK_PREFIX: Final = "CT-"
WEEK: Final = pd.Timedelta(days=7)
# The moves of a task: open -> in progress -> done or cancelled; done and cancelled are final.
MOVES: Final[dict[str, frozenset[str]]] = {
    "open": frozenset({"in_progress", "done", "cancelled"}),
    "in_progress": frozenset({"done", "cancelled"}),
}


def _ts(value: Any) -> datetime:
    moment: datetime = pd.Timestamp(value).to_pydatetime()
    return moment


def flagged(serving: Serving, at: datetime) -> pd.DataFrame:
    """The anomalies of the last snapshot of features/product_anomalies computed by `at`."""
    frame = serving.frame("features/product_anomalies")
    known = frame.loc[frame["as_of"] <= pd.Timestamp(at)]
    if known.empty:
        return known
    return known.loc[known["as_of"] == known["as_of"].max()]


def warm(serving: Serving) -> None:
    """Compute the trend and returns of every anomaly of the last snapshot once, at startup:
    services.cases.anomaly_detail keeps them with the snapshot, so the first list is fast."""
    for row in flagged(serving, serving.as_of).to_dict("records"):
        anomaly_detail(serving, row)


def anomaly_key(row: Mapping[str, Any]) -> str:
    week = pd.Timestamp(row["week"]).date()
    return s.anomaly_id(str(row["variant_id"]), ReturnReason(str(row["reason"])), week)


async def active_tasks(conn: AsyncConnection) -> dict[str, dict[str, Any]]:
    """anomaly_id -> its task: the latest one not cancelled."""
    query = select(catalog_tasks).order_by(catalog_tasks.c.created_at, catalog_tasks.c.task_id)
    found: dict[str, dict[str, Any]] = {}
    for row in (await conn.execute(query)).mappings().all():
        if row["status"] != "cancelled":
            found[row["anomaly_id"]] = dict(row)
    return found


def anomaly_view(
    serving: Serving, row: Mapping[str, Any], task: Mapping[str, Any] | None
) -> s.ProductAnomaly:
    """One anomaly with its catalogue task and the variant's weekly returns for the reason, as
    a case card shows it."""
    variant_id, reason = str(row["variant_id"]), str(row["reason"])
    week = pd.Timestamp(row["week"]).date()
    status: s.AnomalyStatus = "open"
    if task is not None:
        status = "resolved" if task["status"] == "done" else "task_opened"
    snapshot = _ts(row["as_of"])
    variant = serving.by_key("store/variants", variant_id) or {}
    prov = from_file(serving.provenance["features/product_anomalies"], None, s.utc_day(snapshot))
    trend, ids = anomaly_detail(serving, row)
    return s.ProductAnomaly(
        anomaly_id=anomaly_key(row),
        variant_id=variant_id,
        product_id=str(variant.get("product_id", "")),
        category=str(variant.get("category", "")),
        brand=variant.get("brand"),
        reason=ReturnReason(reason),
        week=week,
        observed=s.Metric(value=int(row["observed"]), unit="count", provenance=prov),
        expected=s.Metric(value=float(row["expected"]), unit="expected_count", provenance=prov),
        z=s.Metric(value=float(row["z"]), unit="z_score", provenance=prov),
        sales=s.Metric(value=int(row["n"]), unit="count", provenance=prov),
        status=status,
        catalog_task_id=None if task is None else task["task_id"],
        return_ids=ids,
        trend=trend,
    )


async def anomalies(
    conn: AsyncConnection, serving: Serving, query: s.AnomaliesQuery, now: datetime
) -> s.ProductAnomaliesResponse:
    """GET /api/products/anomalies: the flagged weeks, strongest first, with their tasks."""
    rows = flagged(serving, now)
    if query.variant_id is not None:
        rows = rows.loc[rows["variant_id"] == query.variant_id]
    rows = rows.sort_values(
        ["z", "week", "variant_id", "reason"], ascending=[False, False, True, True]
    )
    tasks = await active_tasks(conn)
    items = [anomaly_view(serving, r, tasks.get(anomaly_key(r))) for r in rows.to_dict("records")]
    if query.status:
        items = [i for i in items if i.status in query.status]
    frame = serving.frame("features/product_anomalies")
    known = frame.loc[frame["as_of"] <= pd.Timestamp(now)]
    end = s.utc_day(_ts(known["as_of"].max())) if not known.empty else s.utc_day(now)
    return s.ProductAnomaliesResponse(
        as_of=now,
        thresholds=s.AnomalyThresholds(
            z=ANOMALY_THRESHOLDS.z, min_sales=ANOMALY_THRESHOLDS.min_sales
        ),
        items=items,
        provenance=from_file(serving.provenance["features/product_anomalies"], None, end),
    )


def follow_up(
    serving: Serving, variant_id: str, reason: str, change_at: datetime
) -> s.FollowUp | None:
    """The variant's return rate for the reason before and after a change (US-606), from the
    sales the snapshot knows; None when the weeks before the change sold nothing."""
    known_at = pd.Timestamp(serving.as_of)
    change = pd.Timestamp(change_at)
    change_week = week_start(pd.Series([change]))[0]
    first_after = change_week if change == change_week else change_week + WEEK
    first = change_week - FOLLOW_WEEKS * WEEK
    last = min(first_after + (FOLLOW_WEEKS - 1) * WEEK, week_start(pd.Series([known_at]))[0])
    try:
        series = weekly_series(
            serving.sales,
            variant_id,
            reason,
            max(known_at, change).to_pydatetime(),
            first_week=first.to_pydatetime(),
            last_week=max(last, first).to_pydatetime(),
        )
    except ValueError:  # no sale of the variant known: nothing to follow
        return None
    weeks = series["week"]
    before = series.loc[(weeks >= first) & (weeks < change_week)]
    after = series.loc[(weeks >= first_after) & (weeks + WEEK <= known_at) & (series["n"] > 0)]
    if int(before["n"].sum()) == 0:
        return None
    prov = from_file(
        serving.provenance["store/order_lines"],
        f"weekly returns of {variant_id} for {reason}: {FOLLOW_WEEKS} sales weeks before the "
        f"change of {change:%Y-%m-%d} (its week left out) and the full weeks after it, sales "
        f"known at {known_at:%Y-%m-%d}",
        known_at.date(),
    )
    sold_after = int(after["n"].sum())
    return s.FollowUp(
        baseline_rate=s.Metric(
            value=round(int(before["observed"].sum()) / int(before["n"].sum()), 4),
            unit="ratio",
            provenance=prov,
        ),
        after_rate=(
            s.Metric(
                value=round(int(after["observed"].sum()) / sold_after, 4),
                unit="ratio",
                provenance=prov,
            )
            if sold_after
            else None
        ),
        weeks_after=len(after) if sold_after else 0,
    )


def task_view(serving: Serving, row: Mapping[str, Any]) -> s.CatalogTask:
    done = row["status"] == "done"
    return s.CatalogTask(
        task_id=row["task_id"],
        anomaly_id=row["anomaly_id"],
        variant_id=row["variant_id"],
        reason=ReturnReason(row["reason"]),
        kind=row["kind"],
        status=row["status"],
        note=row["note"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        done_at=row["done_at"],
        return_id=row["return_id"],
        follow_up=(
            follow_up(serving, row["variant_id"], row["reason"], row["done_at"]) if done else None
        ),
    )


async def tasks(conn: AsyncConnection, serving: Serving) -> s.CatalogTasksResponse:
    query = select(catalog_tasks).order_by(
        catalog_tasks.c.created_at.desc(), catalog_tasks.c.task_id.desc()
    )
    rows = (await conn.execute(query)).mappings().all()
    return s.CatalogTasksResponse(items=[task_view(serving, dict(r)) for r in rows])


def _anomaly_row(serving: Serving, anomaly_id: str, at: datetime) -> dict[str, Any]:
    parts = anomaly_id.split(s.LINK_SEPARATOR)
    if len(parts) == 3:
        rows = flagged(serving, at)
        for row in rows.to_dict("records"):
            if anomaly_key(row) == anomaly_id:
                return {str(k): v for k, v in row.items()}
    raise not_found(f"anomaly {anomaly_id} (a flagged week of the last anomaly snapshot)")


async def open_task(
    conn: AsyncConnection,
    serving: Serving,
    body: s.CatalogTaskCreate,
    *,
    at: datetime,
    request_id: str | None,
) -> s.CatalogTask:
    """POST /api/catalog-tasks: a task from a flagged anomaly, logged (catalog_task_opened).
    It touches no decision, lever or message (E2E-04)."""
    row = _anomaly_row(serving, body.anomaly_id, at)
    if body.return_id is not None:
        ret = await return_row(conn, body.return_id)
        if ret is None:
            raise not_found(f"return {body.return_id}")
        if ret["variant_id"] != row["variant_id"]:
            raise invalid_request(
                f"return {body.return_id} is not of the variant {row['variant_id']} of the anomaly"
            )
    held = (await active_tasks(conn)).get(body.anomaly_id)
    if held is not None:
        raise conflict(
            f"{held['task_id']} is already the task of {body.anomaly_id} ({held['status']})"
        )
    count = await conn.scalar(select(func.count()).select_from(catalog_tasks))
    task_id = f"{TASK_PREFIX}{int(count or 0) + 1:06d}"
    week: date = pd.Timestamp(row["week"]).date()
    values = {
        "task_id": task_id,
        "anomaly_id": body.anomaly_id,
        "variant_id": row["variant_id"],
        "reason": row["reason"],
        "week": week,
        "kind": body.kind,
        "status": "open",
        "note": body.note,
        "created_by": body.author,
        "created_at": at,
        "updated_at": at,
        "done_at": None,
        "return_id": body.return_id,
    }
    try:
        await conn.execute(insert(catalog_tasks).values(**values))
    except IntegrityError:
        raise conflict(f"another catalogue task took the id {task_id}: try again") from None
    await record(
        conn,
        at=at,
        actor=body.author,
        action="catalog_task_opened",
        entity_type="catalog_task",
        entity_id=task_id,
        return_id=body.return_id,
        after={"kind": body.kind, "anomaly_id": body.anomaly_id},
        request_id=request_id,
    )
    return task_view(serving, values)


async def update_task(
    conn: AsyncConnection,
    serving: Serving,
    task_id: str,
    body: s.CatalogTaskUpdate,
    *,
    at: datetime,
    request_id: str | None,
) -> s.CatalogTask:
    """PATCH /api/catalog-tasks/{task_id}: move a task (409 for a move it cannot make)."""
    query = select(catalog_tasks).where(catalog_tasks.c.task_id == task_id)
    found = (await conn.execute(query)).mappings().first()
    if found is None:
        raise not_found(f"catalogue task {task_id}")
    row = dict(found)
    old = str(row["status"])
    if body.status not in MOVES.get(old, frozenset()):
        raise conflict(f"{task_id} is {old}: it cannot move to {body.status}")
    changes = {
        "status": body.status,
        "note": body.note if body.note is not None else row["note"],
        "updated_at": at,
        "done_at": at if body.status == "done" else None,
    }
    result = await conn.execute(
        update(catalog_tasks)
        .where((catalog_tasks.c.task_id == task_id) & (catalog_tasks.c.status == old))
        .values(**changes)
    )
    if result.rowcount != 1:
        raise conflict(f"{task_id} changed meanwhile: reload it")
    await record(
        conn,
        at=at,
        actor=body.author,
        action="catalog_task_updated",
        entity_type="catalog_task",
        entity_id=task_id,
        return_id=row["return_id"],
        before={"status": old},
        after={"status": body.status},
        request_id=request_id,
    )
    return task_view(serving, {**row, **changes})
