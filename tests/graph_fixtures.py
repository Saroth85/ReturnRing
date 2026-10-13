"""Hand-built stores for the account graph tests (US-203).

MiniStore describes a few accounts compactly; write() turns it into the contract tables read by
pipeline.graph.snapshot.load_snapshot and writes them with contracts.io.write_table, so every
fixture passes contract v2. Every value is a made-up test fixture (nature: synthetic).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from contracts.io import Provenance, write_table
from contracts.parquet import SCHEMAS

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TABLES = (
    "store/customers",
    "store/identifiers",
    "store/coupons",
    "store/orders",
    "store/order_lines",
    "store/deliveries",
    "store/returns",
)


def day(n: float) -> datetime:
    """T0 plus n days."""
    return T0 + timedelta(days=n)


@dataclass(frozen=True)
class Line:
    line_id: str
    price_cents: int = 5_000
    returned: str | None = None  # a ReturnReason value
    returned_at: datetime | None = None


@dataclass(frozen=True)
class Order:
    order_id: str
    customer: str
    placed: datetime
    delivered: datetime | None
    lines: tuple[Line, ...]
    coupon: str | None = None


@dataclass
class MiniStore:
    customers: dict[str, datetime] = field(default_factory=dict)  # id -> created
    # (customer, kind, value, first use); value is the hashed value as stored
    identifiers: list[tuple[str, str, str, datetime]] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    # (code, customer, kind, issued, redeemed by order or None, redeemed at)
    coupons: list[tuple[str, str, str, datetime, str | None, datetime | None]] = field(
        default_factory=list
    )

    def account(self, customer: str, created: datetime, **ids: str | list[str]) -> None:
        """An account with identifiers first used at creation: account("A", day(0), card="x")."""
        self.customers[customer] = created
        for kind, values in ids.items():
            for value in [values] if isinstance(values, str) else values:
                self.identifiers.append((customer, kind, value, created))

    def use(self, customer: str, kind: str, value: str, at: datetime) -> None:
        self.identifiers.append((customer, kind, value, at))

    def tables(self) -> dict[str, pa.Table]:
        def table(name: str, rows: list[dict[str, object]]) -> pa.Table:
            schema = SCHEMAS[name]
            columns = {f.name: [r[f.name] for r in rows] for f in schema}
            return pa.Table.from_pydict(columns, schema=schema)

        def event(at: datetime) -> dict[str, object]:
            return {"event_at": at, "available_at": at}

        out: dict[str, pa.Table] = {}
        out["store/customers"] = table(
            "store/customers",
            [
                {"customer_id": c, "display_name": None, **event(at)}
                for c, at in sorted(self.customers.items())
            ],
        )
        out["store/identifiers"] = table(
            "store/identifiers",
            [
                {"customer_id": c, "kind": k, "value_hash": v, **event(at)}
                for c, k, v, at in sorted(self.identifiers)
            ],
        )
        coupons: list[dict[str, object]] = []
        for code, customer, kind, issued, order_id, redeemed in self.coupons:
            coupons.append(
                {
                    "coupon_code": code,
                    "customer_id": customer,
                    "kind": kind,
                    "discount_eur": _eur(0),
                    "order_id": None,
                    **event(issued),
                }
            )
            if order_id is not None:
                assert redeemed is not None
                coupons.append(
                    {
                        "coupon_code": code,
                        "customer_id": customer,
                        "kind": kind,
                        "discount_eur": _eur(500),
                        "order_id": order_id,
                        **event(redeemed),
                    }
                )
        out["store/coupons"] = table("store/coupons", coupons)
        orders, lines, deliveries, returns = [], [], [], []
        for o in self.orders:
            total = sum(line.price_cents for line in o.lines)
            orders.append(
                {
                    "order_id": o.order_id,
                    "customer_id": o.customer,
                    "terms_snapshot_id": f"TS-{o.order_id}",
                    "coupon_code": o.coupon,
                    "total_eur": _eur(total),
                    **event(o.placed),
                }
            )
            if o.delivered is not None:
                deliveries.append({"order_id": o.order_id, **event(o.delivered)})
            for line in o.lines:
                lines.append(
                    {
                        "line_id": line.line_id,
                        "order_id": o.order_id,
                        "variant_id": "V-1",
                        "size": "M",
                        "price_eur": _eur(line.price_cents),
                        **event(o.placed),
                    }
                )
                if line.returned is not None:
                    assert o.delivered is not None and line.returned_at is not None
                    returns.append(
                        {
                            "return_id": f"R-{line.line_id}",
                            "line_id": line.line_id,
                            "reason": line.returned,
                            "days_since_delivery": (line.returned_at - o.delivered).days,
                            **event(line.returned_at),
                        }
                    )
        out["store/orders"] = table("store/orders", orders)
        out["store/order_lines"] = table("store/order_lines", lines)
        out["store/deliveries"] = table("store/deliveries", deliveries)
        out["store/returns"] = table("store/returns", returns)
        return out

    def write(self, root: Path, *, before: datetime | None = None) -> Path:
        """Write the store under root/store. With `before`, keep only the rows available at or
        before that instant, as if the store had been extracted then."""
        for name, table in self.tables().items():
            if before is not None:
                table = table.filter(pc.less_equal(table.column("available_at"), before))
            write_table(name, table, root, _provenance(table))
        return root


def _eur(cents: int) -> Decimal:
    return Decimal(cents).scaleb(-2)


def _provenance(table: pa.Table) -> Provenance:
    if table.num_rows:
        days = pc.cast(table.column("event_at"), pa.date32())
        first, last = pc.min(days).as_py(), pc.max(days).as_py()
    else:
        first = last = date(2026, 1, 1)
    return Provenance(
        origin="Account graph test fixture (hand-built)",
        period_start=min(first, date(2026, 1, 1)),
        period_end=max(last, date(2026, 1, 1)),
        nature="synthetic",
    )
