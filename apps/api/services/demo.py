"""The demo reset's own record: the golden cases with their live ids and the demo clock
(ADR-15 section 15, ADR-16 section 10; US-903; H9, H11).

The reset decides the open returns of the serving snapshot; their ids are the store's
(RET-0060386...), not the ids of the contract examples (RET-000101...). build_cases() finds,
from the golden customer ids of the store (pipeline.generator.golden), the cases the UI's demo
flows open:

- giulia, elena: the open return of the customer;
- marco: an open return of a Marco ring account, a Q4 restriction awaiting approval when there
  is one;
- rossi, rossi_card: an open return of the family, one with linked accounts when there is one;
- insufficient: the first withdrawal case with insufficient evidence (US-307);
- dispute: a link whose dispute changes the cluster of a queued case (US-310), looked for in
  the Rossi families first, then in the clusters of the other cases (the Marco ring and the
  other golden cases keep their story);
- carts: Giulia's sizes of her order, and the returned item of the ring account with an
  unredeemed coupon of the account when it has one.

record_reset() stores them with the instant of the snapshot and the wall time the demo clock
starts (the end of the reset) in the one row of demo_reset; GET /api/demo/cases serves them and
the API's DemoClock reads its anchor there.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final

import pandas as pd
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.operator.graph import GraphView
from apps.api.services.provenance import from_file
from apps.api.serving import ReturnRecord, Serving
from contracts.sql import decisions, demo_reset, return_requests
from pipeline.generator.golden import (
    ELENA,
    GIULIA,
    MARCO_ACCOUNTS,
    ROSSI_ACCOUNTS,
    ROSSI_CARD_ACCOUNTS,
)

GOLDEN: Final[dict[s.DemoCaseKey, tuple[str, ...]]] = {
    "giulia": (GIULIA,),
    "marco": MARCO_ACCOUNTS,
    "rossi": ROSSI_ACCOUNTS,
    "rossi_card": ROSSI_CARD_ACCOUNTS,
    "elena": (ELENA,),
}
GOLDEN_CUSTOMERS: Final = frozenset(c for group in GOLDEN.values() for c in group)
MAX_DISPUTE_CASES: Final = 40  # clustered cases tried for the dispute, after the Rossi families
MAX_CART_LINES: Final = 20  # CheckoutEvaluateRequest.lines


async def _cases(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Version 1 of every queued case of the store, with its return, in return id order."""
    query = (
        select(
            decisions.c.return_id,
            decisions.c.decision_id,
            decisions.c.customer_id,
            decisions.c.cluster_id,
            decisions.c.path,
            decisions.c.evidence_quality,
            decisions.c.quadrant,
            decisions.c.requires_approval,
        )
        .join(return_requests, return_requests.c.return_id == decisions.c.return_id)
        .where((decisions.c.version == 1) & (return_requests.c.source == "store"))
        .order_by(decisions.c.return_id)
    )
    return [dict(r) for r in (await conn.execute(query)).mappings().all()]


def _pick(key: s.DemoCaseKey, rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    mine = [r for r in rows if r["customer_id"] in GOLDEN[key]]
    if key == "marco":
        mine = [r for r in mine if r["quadrant"] == "Q4" and r["requires_approval"]] or mine
    if key in ("rossi", "rossi_card"):
        mine = [r for r in mine if r["cluster_id"] is not None] or mine
    return mine[0] if mine else None


def _case(key: s.DemoCaseKey, row: Mapping[str, Any]) -> s.DemoCase:
    return s.DemoCase(
        key=key,
        return_id=row["return_id"],
        customer_id=row["customer_id"],
        decision_id=row["decision_id"],
        cluster_id=row["cluster_id"],
    )


def _cluster_size(graph: Serving, record: ReturnRecord) -> tuple[str | None, int]:
    context = graph.customer_context(record.customer_id, record.known_at)
    if context.cluster_id is None:
        return None, 1
    return context.cluster_id, len(graph.cluster_members(context.cluster_id, context.as_of))


def find_dispute(serving: Serving, rows: Sequence[Mapping[str, Any]]) -> s.DemoDispute | None:
    """The first link, in the order of the module docstring, whose dispute makes the cluster of
    the case customer smaller (pipeline.graph.build.dispute_link, through GraphView)."""
    clustered = [r for r in rows if r["cluster_id"] is not None and r["path"] == "withdrawal"]
    rossi = [r for r in clustered if r["customer_id"] in ROSSI_CARD_ACCOUNTS]
    rossi += [r for r in clustered if r["customer_id"] in ROSSI_ACCOUNTS]
    others = [r for r in clustered if r["customer_id"] not in GOLDEN_CUSTOMERS]
    for row in [*rossi, *others[:MAX_DISPUTE_CASES]]:
        record = serving.store_return(str(row["return_id"]))
        if record is None:
            continue
        context = serving.customer_context(record.customer_id, record.known_at)
        if context.live or context.cluster_id is None:
            continue
        members = serving.cluster_members(context.cluster_id, context.as_of)
        links = serving.cluster_links(members, context.as_of)
        pairs = sorted(
            (str(a), str(b))
            for a, b, disputed in zip(
                links["customer_a"], links["customer_b"], links["disputed"], strict=True
            )
            if not disputed
        )
        for a, b in pairs:
            _, after = _cluster_size(GraphView.of(serving, [(a, b)]), record)
            if after < len(members):
                return s.DemoDispute(
                    link_id=s.link_id(a, b),
                    return_id=str(row["return_id"]),
                    decision_id=str(row["decision_id"]),
                    cluster_id=context.cluster_id,
                    cluster_size_before=len(members),
                    cluster_size_after=after,
                )
    return None


def _lines(serving: Serving, order_id: str) -> pd.DataFrame:
    lines = serving.frame("store/order_lines")
    return lines.loc[lines["order_id"] == order_id].sort_values("line_id", kind="stable")


def _cart_lines(rows: Iterable[tuple[Any, Any]]) -> list[s.CartLine]:
    out: list[s.CartLine] = []
    for variant, size in rows:
        if size is None or pd.isna(size) or not str(size):
            continue
        line = s.CartLine(variant_id=str(variant), size=str(size), quantity=1)
        if line not in out:
            out.append(line)
    return out[:MAX_CART_LINES]


def _coupon(serving: Serving, customer_id: str) -> str | None:
    """An unredeemed coupon the store issued to the customer by the snapshot, if any."""
    coupons = serving.frame("store/coupons")
    free = coupons.loc[
        (coupons["customer_id"] == customer_id)
        & coupons["order_id"].isna()
        & (coupons["available_at"] <= pd.Timestamp(serving.as_of))
    ]
    codes = sorted(str(c) for c in free["coupon_code"])
    return codes[0] if codes else None


def carts(serving: Serving, cases: Mapping[str, s.DemoCase]) -> list[s.DemoCart]:
    out: list[s.DemoCart] = []
    giulia = cases.get("giulia")
    if giulia is not None:
        record = serving.store_return(giulia.return_id)
        if record is not None:
            lines = _lines(serving, record.order_id)
            items = _cart_lines(zip(lines["variant_id"], lines["size"], strict=True))
            if items:
                request = s.CheckoutEvaluateRequest(
                    customer_id=giulia.customer_id, lines=items, coupon_code=None
                )
                out.append(s.DemoCart(key="giulia", request=request))
    ring = cases.get("marco")
    if ring is not None:
        record = serving.store_return(ring.return_id)
        line: Mapping[str, Any] | None = (
            serving.by_key("store/order_lines", record.line_id) if record else None
        )
        items = _cart_lines([(line["variant_id"], line["size"])]) if line else []
        if items:
            request = s.CheckoutEvaluateRequest(
                customer_id=ring.customer_id,
                lines=items,
                coupon_code=_coupon(serving, ring.customer_id),
            )
            out.append(s.DemoCart(key="ring", request=request))
    return out


async def build_cases(
    conn: AsyncConnection, serving: Serving
) -> tuple[list[s.DemoCase], s.DemoDispute | None, list[s.DemoCart]]:
    """The golden cases of the reset just made (see the module docstring)."""
    rows = await _cases(conn)
    found: dict[str, s.DemoCase] = {}
    for key in s.DEMO_CASE_KEYS:
        row: Mapping[str, Any] | None
        if key == "insufficient":
            row = next(
                (
                    r
                    for r in rows
                    if r["path"] == "withdrawal"
                    and r["evidence_quality"] == "insufficient"
                    and r["customer_id"] not in GOLDEN_CUSTOMERS
                ),
                None,
            )
        else:
            row = _pick(key, rows)
        if row is not None:
            found[key] = _case(key, row)
    return list(found.values()), find_dispute(serving, rows), carts(serving, found)


def provenance(serving: Serving) -> s.Provenance:
    day = serving.as_of.date()
    return from_file(
        serving.provenance["features/links"],
        "golden cases of the demo reset: the open returns of the golden customers of the "
        f"synthetic store and the account graph at {day}",
        day,
    )


async def record_reset(conn: AsyncConnection, serving: Serving, started_at: datetime) -> None:
    """The one row of demo_reset: the golden cases and the start of the demo clock."""
    cases, dispute, demo_carts = await build_cases(conn, serving)
    body = {
        "snapshot_as_of": serving.as_of,
        "as_of": serving.as_of,
        "cases": cases,
        "dispute": dispute,
        "carts": demo_carts,
        "provenance": provenance(serving),
    }
    stored = s.DemoCasesResponse.model_validate(body).model_dump(mode="json", exclude={"as_of"})
    await conn.execute(delete(demo_reset))
    await conn.execute(
        insert(demo_reset).values(
            reset_id=1, snapshot_as_of=serving.as_of, started_at=started_at, cases=stored
        )
    )


async def reset_row(conn: AsyncConnection) -> Mapping[str, Any] | None:
    row = (await conn.execute(select(demo_reset))).mappings().first()
    return None if row is None else dict(row)


def cases_response(row: Mapping[str, Any], now: datetime) -> s.DemoCasesResponse:
    """GET /api/demo/cases: the stored cases, served at `now`."""
    return s.DemoCasesResponse.model_validate({**row["cases"], "as_of": now})
