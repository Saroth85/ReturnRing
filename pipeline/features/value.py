"""Customer value as net margin over 12 months (spec 8.5; US-205, UNIT-03).

    V_c = m * sum(price of kept lines) - L * #returns - sum(coupon discounts)

over the orders placed in the 12 months up to the decision time T (window_days), with what is known
at T: a line is returned when a return request for it is known at T, whatever the reason (the
logistics cost L is paid for a defect return too; spec 8.5 counts every return), and kept
otherwise, even if not yet delivered. Discounts are the coupon redemptions of those orders.

Reconciliation (UNIT-03): kept + returned - discounts equals the sum of the order totals, to the
cent, because an order total is its line prices minus its coupon discount. reconcile() lists any
customer where it does not hold.

m and L are the merchant hypotheses of spec section 4 (55% gross margin, 8 EUR per return),
versioned in VALUE_ASSUMPTIONS. Value orients benefits and service investments; it is never a sign
of abuse and never enters the risk score (rule 6): it lives in its own table, features/
customer_value, and no customer feature reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

import numpy as np
import pandas as pd
import pyarrow as pa

from contracts.parquet import SCHEMAS
from pipeline.features.snapshot import StoreSnapshot

CENT: Final = Decimal("0.01")


@dataclass(frozen=True)
class ValueAssumptions:
    """Explicit, versioned hypotheses of the value formula (rule 9)."""

    version: str
    gross_margin: Decimal  # m
    logistics_cost_per_return_eur: Decimal  # L
    window_days: int
    source: str

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.gross_margin <= Decimal(1):
            raise ValueError(f"gross margin must lie in [0, 1], got {self.gross_margin}")
        if self.logistics_cost_per_return_eur < 0:
            raise ValueError("the logistics cost per return cannot be negative")
        if self.window_days < 1:
            raise ValueError("the value window must be at least one day")


VALUE_ASSUMPTIONS: Final = ValueAssumptions(
    version="value-v1-2026-10-13",
    gross_margin=Decimal("0.55"),
    logistics_cost_per_return_eur=Decimal("8.00"),
    window_days=365,
    source="docs/spec.md section 4, Navigli Fashion hypotheses to calibrate with interviews (D-09)",
)

VALUE_COLUMNS: Final = (
    "value_eur",
    "kept_eur",
    "returned_eur",
    "discount_eur",
    "orders_total_eur",
)
ROW_COLUMNS: Final = (
    "customer_id",
    *VALUE_COLUMNS,
    "n_orders",
    "n_lines",
    "n_returns",
    "window_start",
    "assumptions_version",
)


def _eur(cents: int) -> Decimal:
    return (Decimal(int(cents)) / 100).quantize(CENT)


def customer_value(
    snap: StoreSnapshot, assumptions: ValueAssumptions = VALUE_ASSUMPTIONS
) -> pd.DataFrame:
    """One row per customer known at snap.as_of: V_c, its components and the window."""
    a = assumptions
    as_of = pd.Timestamp(snap.as_of)
    start = as_of - pd.Timedelta(days=a.window_days)
    orders = snap["store/orders"]
    orders = orders.loc[orders["event_at"] > start, ["order_id", "customer_id", "total_cents"]]
    lines = snap["store/order_lines"][["line_id", "order_id", "price_cents"]]
    lines = lines.merge(orders[["order_id", "customer_id"]], on="order_id", how="inner")
    returned = lines["line_id"].isin(snap["store/returns"]["line_id"])
    lines = lines.assign(
        kept=np.where(returned, 0, lines["price_cents"]),
        returned=np.where(returned, lines["price_cents"], 0),
        n_returns=returned.astype(np.int64),
        n_lines=1,
    )
    per_line = lines.groupby("customer_id")[["kept", "returned", "n_returns", "n_lines"]].sum()
    coupons = snap["store/coupons"]
    redeemed = coupons.loc[coupons["order_id"].isin(orders["order_id"])]
    discount = redeemed.groupby("customer_id")["discount_cents"].sum()
    per_order = orders.groupby("customer_id").agg(
        n_orders=("order_id", "size"), total=("total_cents", "sum")
    )
    customers = sorted(set(snap["store/customers"]["customer_id"]))
    table = pd.DataFrame(index=pd.Index(customers, name="customer_id"))
    table = table.join(per_line).join(per_order).join(discount.rename("discount"))
    table = table.fillna(0).astype(np.int64)

    margin, cost = a.gross_margin, a.logistics_cost_per_return_eur
    rows = []
    for customer, r in zip(table.index, table.itertuples(index=False), strict=True):
        kept = _eur(r.kept)
        discount_eur = _eur(r.discount)
        value = (margin * kept).quantize(CENT, rounding=ROUND_HALF_UP)
        value -= (cost * int(r.n_returns)).quantize(CENT)
        value -= discount_eur
        rows.append(
            {
                "customer_id": customer,
                "value_eur": value,
                "kept_eur": kept,
                "returned_eur": _eur(r.returned),
                "discount_eur": discount_eur,
                "orders_total_eur": _eur(r.total),
                "n_orders": int(r.n_orders),
                "n_lines": int(r.n_lines),
                "n_returns": int(r.n_returns),
                "window_start": start,
                "assumptions_version": a.version,
            }
        )
    return pd.DataFrame(rows, columns=list(ROW_COLUMNS))


def reconcile(values: pd.DataFrame) -> pd.DataFrame:
    """Customers whose kept + returned - discount differs from their order totals (UNIT-03)."""
    if values.empty:
        return values.assign(difference_eur=[])
    difference = [
        k + r - d - t
        for k, r, d, t in zip(
            values["kept_eur"],
            values["returned_eur"],
            values["discount_eur"],
            values["orders_total_eur"],
            strict=True,
        )
    ]
    out = values.assign(difference_eur=difference)
    return out.loc[[d != 0 for d in difference]].reset_index(drop=True)


def value_table(values: pd.DataFrame, as_of: datetime) -> pa.Table:
    """The rows of features/customer_value at `as_of`."""
    schema = SCHEMAS["features/customer_value"]
    data = values.assign(as_of=pd.Timestamp(as_of))
    return pa.Table.from_pandas(data[schema.names], schema=schema, preserve_index=False)
