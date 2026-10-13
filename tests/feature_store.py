"""A hand-built store for the feature tests: a few customers, orders and returns written through
contracts.io.write_table, so every fixture is itself a valid contract file.

Every value here is made up (origin: test fixture; nature: synthetic).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa

from contracts.io import Provenance, write_table
from contracts.parquet import SCHEMAS

STORE = (
    "store/customers",
    "store/identifiers",
    "store/variants",
    "store/terms_snapshots",
    "store/orders",
    "store/order_lines",
    "store/deliveries",
    "store/returns",
    "store/return_events",
    "store/coupons",
)
FIXTURE = Provenance(
    origin="feature test fixture (hand-built)",
    period_start=date(2019, 1, 1),
    period_end=date(2026, 12, 31),
    nature="synthetic",
)
T0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)  # a Monday


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


def eur(value: str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


@dataclass
class Line:
    line_id: str
    variant_id: str
    price: str
    size: str | None = "M"


@dataclass
class MiniStore:
    rows: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {name: [] for name in STORE}
    )

    def _add(self, name: str, row: dict[str, Any], event: datetime, known: datetime) -> None:
        self.rows[name].append({**row, "event_at": event, "available_at": known})

    def customer(self, customer_id: str, created: datetime) -> MiniStore:
        self._add(
            "store/customers",
            {"customer_id": customer_id, "display_name": f"Name {customer_id}"},
            created,
            created,
        )
        return self

    def identifier(
        self, customer_id: str, kind: str, first_use: datetime, value: str | None = None
    ) -> MiniStore:
        row = {"customer_id": customer_id, "kind": kind, "value_hash": value or kind + customer_id}
        self._add("store/identifiers", row, first_use, first_use)
        return self

    def variant(
        self,
        variant_id: str,
        product_id: str,
        category: str = "dresses",
        price: str = "50.00",
        size_range: str | None = "XS-XL",
        listed: datetime | None = None,
    ) -> MiniStore:
        moment = listed or at(-400)
        row = {
            "variant_id": variant_id,
            "product_id": product_id,
            "category": category,
            "brand": "Brand",
            "price_eur": eur(price),
            "size_range": size_range,
        }
        self._add("store/variants", row, moment, moment)
        return self

    def order(
        self,
        order_id: str,
        customer_id: str,
        placed: datetime,
        lines: Sequence[Line],
        delivered: datetime | None = None,
        delivery_known: datetime | None = None,
        coupon: tuple[str, str, str] | None = None,  # (code, kind, discount)
        window_days: int = 30,
        known: datetime | None = None,
    ) -> MiniStore:
        known = known or placed
        discount = eur(coupon[2]) if coupon else eur(0)
        total = sum((eur(line.price) for line in lines), eur(0)) - discount
        ts_id = f"TS-{order_id}"
        self._add(
            "store/terms_snapshots",
            {
                "terms_snapshot_id": ts_id,
                "order_id": order_id,
                "source": "base",
                "quadrant_at_purchase": None,
                "return_window_days": window_days,
                "free_return_shipping": True,
                "instant_refund": True,
                "free_size_exchange": True,
                "coupons_enabled": True,
            },
            placed - timedelta(minutes=5),
            placed - timedelta(minutes=5),
        )
        self._add(
            "store/orders",
            {
                "order_id": order_id,
                "customer_id": customer_id,
                "terms_snapshot_id": ts_id,
                "coupon_code": coupon[0] if coupon else None,
                "total_eur": total,
            },
            placed,
            known,
        )
        for line in lines:
            row = {
                "line_id": line.line_id,
                "order_id": order_id,
                "variant_id": line.variant_id,
                "size": line.size,
                "price_eur": eur(line.price),
            }
            self._add("store/order_lines", row, placed, known)
        if coupon:
            code, kind, _ = coupon
            self.coupon_issue(code, customer_id, kind, placed - timedelta(days=1))
            self._add(
                "store/coupons",
                {
                    "coupon_code": code,
                    "customer_id": customer_id,
                    "kind": kind,
                    "discount_eur": discount,
                    "order_id": order_id,
                },
                placed,
                known,
            )
        if delivered is not None:
            self._add(
                "store/deliveries",
                {"order_id": order_id},
                delivered,
                delivery_known or delivered + timedelta(hours=2),
            )
        return self

    def coupon_issue(
        self, code: str, customer_id: str, kind: str, issued: datetime, nominal: str = "0"
    ) -> MiniStore:
        row = {
            "coupon_code": code,
            "customer_id": customer_id,
            "kind": kind,
            "discount_eur": eur(nominal),
            "order_id": None,
        }
        self._add("store/coupons", row, issued, issued)
        return self

    def ret(
        self,
        return_id: str,
        line_id: str,
        reason: str,
        requested: datetime,
        days_since_delivery: int,
        known: datetime | None = None,
    ) -> MiniStore:
        row = {
            "return_id": return_id,
            "line_id": line_id,
            "reason": reason,
            "days_since_delivery": days_since_delivery,
        }
        self._add("store/returns", row, requested, known or requested + timedelta(minutes=2))
        return self

    def table(self, name: str) -> pa.Table:
        return pa.Table.from_pylist(self.rows[name], schema=SCHEMAS[name])

    def write(self, root: Path) -> Path:
        for name in STORE:
            write_table(name, self.table(name), root, FIXTURE)
        return root
