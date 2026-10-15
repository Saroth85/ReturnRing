"""The return queue (GET /api/returns; US-301, US-607) and the decision history (GET
/api/decisions/{decision_id}; US-604). ADR-15 section 4, "Queue and case card".

A queue item shows the latest version of a case with the figures of its case card, from the
same sources (rule 11: one figure, one provenance): the risk given to the engine with the
provenance of its inputs, the customer value of the card (services.cases.value_view), the total
of the recommended alternative, the linked accounts of the cluster the engine was given (the
graph that version saw: services.operator.graph), the anomaly of the returned variant and the
draft status. review_due marks an approved or overridden restriction at or past its review date
(UTC days); refund_overdue a case past the day its refund is due that no message has stated
(rule 3): proposed (1.5.0, H1), or rejected or set to the standard terms after that day, an act
that sends no message (1.8.0); sort=refund_due_by brings such cases first. A guarantee case has
no refund deadline: refund_due_by null, never overdue, last in that sort (1.7.0; rule 4,
NFR-17). The filters mean exactly what QueueQuery.matches says; the queue sorts by expected net
benefit by default, ties by return id; pages start at 1 and total counts after the filters.

One query reads the latest version of every case; the rest is read from the snapshot in memory,
so the queue answers in well under the 2 seconds of US-301 (623 open returns at full scale).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services import decisions as store
from apps.api.services.cases import value_view
from apps.api.services.operator.graph import Exclusion, GraphView, exclusions, version_exclusions
from apps.api.services.operator.shown import line_price
from apps.api.services.provenance import from_file
from apps.api.serving import CustomerContext, Serving
from contracts.sql import decisions, return_requests
from ml.risk.data import latest_before
from policy.types import EvidenceQuality
from policy.types import Path as ReturnPath

SortKey = Callable[[s.QueueItem], tuple[Any, ...]]
SORTS: Final[dict[str, SortKey]] = {
    "-expected_net_benefit": lambda i: (-i.expected_net_benefit.value, i.return_id),
    "expected_net_benefit": lambda i: (i.expected_net_benefit.value, i.return_id),
    "-requested_at": lambda i: (-i.requested_at.timestamp(), i.return_id),
    "requested_at": lambda i: (i.requested_at.timestamp(), i.return_id),
    "-risk": lambda i: (i.risk is None, -(i.risk.value if i.risk else 0.0), i.return_id),
    "refund_due_by": lambda i: (i.refund_due_by or date.max, i.return_id),  # guarantee last
}
_LIVE_CONTEXTS: Final = "_operator_live_contexts"
# The decision columns a queue item and a dispute's affected cases read: the large JSON the queue
# does not show (terms, score factors, model versions) stays in the database (US-301: < 2 s).
_DECISION_COLUMNS: Final = (
    "decision_id",
    "return_id",
    "order_id",
    "customer_id",
    "version",
    "revision_trigger",
    "created_at",
    "state",
    "path",
    "evidence_quality",
    "quadrant",
    "requires_approval",
    "risk",
    "value_eur",
    "value_threshold_eur",
    "cluster_id",
    "inputs_provenance",
    "levers",
    "review_at",
    "refund_due_by",
    "approved_at",
    "alternatives",
    "generation_id",
)
_RETURN_COLUMNS: Final = (
    "line_id",
    "variant_id",
    "reason",
    "customer_text",
    "requested_at",
    "source",
)


async def latest_rows(conn: AsyncConnection) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The latest decision version of every queued return (the columns of _DECISION_COLUMNS),
    with its return_requests row."""
    last = (
        select(decisions.c.return_id, func.max(decisions.c.version).label("version"))
        .group_by(decisions.c.return_id)
        .subquery()
    )
    extra = [return_requests.c[c].label(f"request_{c}") for c in _RETURN_COLUMNS]
    query = (
        select(*(decisions.c[c] for c in _DECISION_COLUMNS), *extra)
        .join(
            last,
            (decisions.c.return_id == last.c.return_id) & (decisions.c.version == last.c.version),
        )
        .join(return_requests, return_requests.c.return_id == decisions.c.return_id)
        .order_by(decisions.c.return_id)
    )
    out = []
    for row in (await conn.execute(query)).mappings().all():
        values = dict(row)
        ret = {c: values.pop(f"request_{c}") for c in _RETURN_COLUMNS}
        ret.update(
            return_id=values["return_id"],
            order_id=values["order_id"],
            customer_id=values["customer_id"],
        )
        out.append((values, ret))
    return out


def flagged_variants(serving: Serving, as_of: datetime) -> set[str]:
    """The variants with an anomaly at the last anomaly snapshot computed by `as_of`: the rule
    of services.cases.variant_anomaly, for every variant at once."""
    frame = serving.frame("features/product_anomalies")
    known = frame.loc[frame["as_of"] <= pd.Timestamp(as_of)]
    if known.empty:
        return set()
    latest = known.loc[known["as_of"] == known["as_of"].max()]
    return {str(v) for v in latest["variant_id"]}


def _scored(row: Mapping[str, Any]) -> bool:
    return bool(
        row["path"] == ReturnPath.WITHDRAWAL.value
        and row["evidence_quality"] == EvidenceQuality.SUFFICIENT.value
    )


@dataclass(frozen=True)
class Snapshot:
    """What the queue reads of the customer snapshot before a request (MOD-08): the request
    instant, the snapshot instant and the customer's rows of that snapshot; a customer with no
    row is read whole by Serving.customer_context (computed at the request)."""

    known_at: datetime  # when the request became known: its decision time
    instant: datetime | None
    evidence: dict[str, Any] | None
    value: dict[str, Any] | None

    def context(self, serving: Serving, customer_id: str) -> CustomerContext:
        """The customer snapshot of the request, as Serving.customer_context reads it, for its
        value (services.cases.value_view reads the value rows and the instant)."""
        if self.instant is None or self.evidence is None or self.value is None:
            # computed from the store at the request (a new account): the same every time, so
            # kept with the snapshot, like the live request scores of Serving
            live: dict[tuple[str, datetime], CustomerContext] = vars(serving).setdefault(
                _LIVE_CONTEXTS, {}
            )
            key = (customer_id, self.known_at)
            if key not in live:
                live[key] = serving.customer_context(customer_id, self.known_at)
            return live[key]
        return CustomerContext(
            customer_id=customer_id,
            as_of=self.instant,
            live=False,
            evidence=self.evidence,
            value=self.value,
            value_threshold=serving.value_threshold(self.instant),
            features=None,
            cluster=None,
        )


def _rows(frame: pd.DataFrame, customers: Sequence[str]) -> dict[str, dict[str, Any]]:
    present = sorted(set(customers) & set(frame.index))
    rows: dict[str, dict[str, Any]] = frame.loc[present].to_dict("index") if present else {}
    return {
        str(c): {k: (None if _missing(v) else v) for k, v in row.items()} for c, row in rows.items()
    }


def _missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def snapshots(serving: Serving, rets: Sequence[Mapping[str, Any]]) -> dict[str, Snapshot]:
    """The snapshot before each request, read in bulk: the instant of the last customer
    snapshot strictly before the request (Serving.context_instant), and the evidence and value
    rows of its customer at that instant."""
    returns = serving.frame("store/returns")
    store_known = dict(zip(returns["return_id"], returns["available_at"], strict=True))
    known: dict[str, datetime] = {}
    for ret in rets:
        stamp = store_known.get(ret["return_id"])  # a store return; else the shop adapter's
        known[ret["return_id"]] = (
            pd.Timestamp(stamp).to_pydatetime() if stamp is not None else ret["requested_at"]
        )
    ids = list(known)
    if not ids:
        return {}
    found = latest_before(
        pd.Series([pd.Timestamp(known[i]) for i in ids]), serving.feature_instants
    )
    owner = {ret["return_id"]: str(ret["customer_id"]) for ret in rets}
    instants: dict[str, datetime | None] = {
        rid: None if pd.isna(t) else pd.Timestamp(t).to_pydatetime()
        for rid, t in zip(ids, found, strict=True)
    }
    by_instant: dict[datetime, list[str]] = {}
    for rid, instant in instants.items():
        if instant is not None:
            by_instant.setdefault(instant, []).append(owner[rid])
    rows: dict[datetime, tuple[dict[str, Any], dict[str, Any]]] = {}
    for instant, customers in by_instant.items():
        rows[instant] = (
            _rows(serving.at("features/evidence_quality", instant), customers),
            _rows(serving.at("features/customer_value", instant), customers),
        )
    out = {}
    for rid in ids:
        instant = instants[rid]
        evidence, value = rows[instant] if instant is not None else ({}, {})
        out[rid] = Snapshot(known[rid], instant, evidence.get(owner[rid]), value.get(owner[rid]))
    return out


def queue_item(
    serving: Serving,
    row: Mapping[str, Any],
    ret: Mapping[str, Any],
    snapshot: Snapshot,
    made: list[Exclusion],
    flagged: set[str],
    as_of: datetime,
) -> s.QueueItem:
    """The queue item of a case: its latest version `row`, its return `ret` and the customer
    snapshot before the request."""
    customer = str(row["customer_id"])
    context = snapshot.context(serving, customer)
    inputs = s.Provenance.model_validate(row["inputs_provenance"])
    threshold = (
        float(Decimal(str(row["value_threshold_eur"])))
        if row["value_eur"] is not None
        else float(context.value_threshold)
    )
    cluster = row["cluster_id"]
    linked = 0
    if cluster is not None and not context.live:
        graph = GraphView.of(serving, version_exclusions(row, made))
        linked = len(graph.cluster_members(cluster, context.as_of)) - 1
    recommended = next(a for a in row["alternatives"] if a["recommended"])
    levers = [s.Lever.model_validate(x) for x in row["levers"]]
    review_at = row["review_at"]
    due = (
        row["state"] in ("approved", "overridden")
        and review_at is not None
        and review_at <= s.utc_day(as_of)
    )
    return s.QueueItem(
        return_id=row["return_id"],
        order_id=row["order_id"],
        customer_id=customer,
        display_name=serving.display_names.get(customer),
        requested_at=ret["requested_at"],
        reason=ret["reason"],
        path=row["path"],
        evidence_quality=row["evidence_quality"],
        quadrant=row["quadrant"],
        decision_id=row["decision_id"],
        state=row["state"],
        requires_approval=row["requires_approval"],
        lever_ids=[x.lever_id() for x in levers],
        review_at=review_at,
        review_due=due,
        refund_due_by=row["refund_due_by"],
        refund_overdue=s.refund_overdue(
            row["state"],
            row["refund_due_by"],
            s.utc_day(as_of),
            late=not levers  # as s.late_without_message: no message stated the refund
            and s.acted_late(
                row["state"], row["revision_trigger"], row["refund_due_by"], row["approved_at"]
            ),
        ),
        risk=(
            s.Metric(value=row["risk"], unit="probability", provenance=inputs)
            if _scored(row)
            else None
        ),
        value=value_view(serving, context, threshold).value_eur,
        expected_net_benefit=s.Metric.model_validate(recommended["net_benefit"]["total"]),
        linked_account_count=max(linked, 0),
        variant_anomaly=ret["variant_id"] in flagged,
        draft_status="ready" if row["generation_id"] is not None else "pending",
    )


def warm(serving: Serving) -> None:
    """What the first queue would compute once and keep (US-301: the first load under 2 s
    too): the customer snapshot of every open store return computed at its request (a new
    account without a snapshot before it, Snapshot.context)."""
    rets = []
    for return_id in serving.open_return_ids():
        record = serving.store_return(return_id)
        if record is not None:
            rets.append(
                {
                    "return_id": return_id,
                    "customer_id": record.customer_id,
                    "requested_at": record.known_at,
                }
            )
    for ret, snapshot in zip(rets, snapshots(serving, rets).values(), strict=True):
        if snapshot.instant is None or snapshot.evidence is None or snapshot.value is None:
            snapshot.context(serving, str(ret["customer_id"]))


async def queue(
    conn: AsyncConnection, serving: Serving, query: s.QueueQuery, as_of: datetime
) -> s.QueueResponse:
    made = await exclusions(conn)
    flagged = flagged_variants(serving, as_of)
    latest = await latest_rows(conn)
    before = snapshots(serving, [ret for _, ret in latest])
    items = [
        queue_item(serving, row, ret, before[ret["return_id"]], made, flagged, as_of)
        for row, ret in latest
    ]
    selected = sorted((i for i in items if query.matches(i)), key=SORTS[query.sort])
    start = (query.page - 1) * query.page_size
    return s.QueueResponse(
        items=selected[start : start + query.page_size],
        total=len(selected),
        page=query.page,
        page_size=query.page_size,
        sort=query.sort,
        as_of=as_of,
        provenance=from_file(
            serving.provenance["features/clusters"],
            "linked accounts of the cluster each latest decision was given",
            as_of.date(),
        ),
    )


async def decision_record(
    conn: AsyncConnection, serving: Serving, decision_id: str, as_of: datetime
) -> s.DecisionRecord | None:
    """Any version of a case's decision, the latest or an earlier one, as stored and served at
    `as_of` (US-604): None when there is no such version."""
    row = await store.decision_row_by_id(conn, decision_id)
    if row is None:
        return None
    ret = await store.return_row(conn, row["return_id"])
    if ret is None:
        raise LookupError(f"{row['return_id']}: return request missing")
    price = line_price(serving, ret["line_id"])
    view = await store.view_of(conn, row, as_of, line_price=lambda _: price)
    return s.DecisionRecord(decision=view, alternatives=store.alternatives_of(row))
