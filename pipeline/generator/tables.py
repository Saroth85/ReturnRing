"""Contract tables of the generated store (contracts/parquet.py, ADR-13).

Every table is sorted by its key so that equal input gives byte-identical files (DATA-04); ids are
numbered in time order like a real shop (orders and lines by placement, returns by request). Event
rows not available before the extraction instant (PERIOD_END) are dropped.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from contracts.parquet import SCHEMAS, TS
from pipeline.generator.catalogue import SIZE_LABELS, SIZE_RANGE, Catalogue, SizeKind
from pipeline.generator.config import END_US, ScenarioConfig
from pipeline.generator.orders import Coupons, Deliveries, OrderPlan
from pipeline.generator.population import Population
from pipeline.generator.returns import REASONS, ReturnRows
from pipeline.generator.timeline import minutes
from policy.engine import NAVIGLI_BASE_TERMS

I64 = npt.NDArray[np.int64]


def timestamps(values: I64 | Sequence[int]) -> pa.Array:
    return pa.array(np.asarray(values, dtype=np.int64), type=TS)


def euros(cents: I64 | Sequence[int]) -> pa.Array:
    """decimal128(12, 2) from integer cents, exactly (no float on the way)."""
    low = np.asarray(cents, dtype=np.int64)
    words = np.empty((len(low), 2), dtype=np.int64)
    words[:, 0] = low
    words[:, 1] = low >> 63  # sign extension of the 128-bit integer
    return pa.Array.from_buffers(
        pa.decimal128(12, 2), len(low), [None, pa.py_buffer(words.tobytes())]
    )


def _table(name: str, columns: dict[str, pa.Array | list[object]]) -> pa.Table:
    schema = SCHEMAS[name]
    arrays = [
        col if isinstance(col, pa.Array) else pa.array(col, type=schema.field(key).type)
        for key, col in columns.items()
    ]
    return pa.Table.from_arrays(arrays, schema=schema)


class Numbering:
    """Ids of orders, lines and returns, numbered in time order."""

    def __init__(
        self, plan: OrderPlan, pop: Population, rows: ReturnRows, cfg: ScenarioConfig
    ) -> None:
        p = cfg.id_prefix
        customer_key = np.argsort(np.argsort(np.asarray(pop.customer_id, dtype=object)))
        by_time = np.lexsort(
            (np.arange(plan.n_orders), customer_key[plan.customer], plan.placed_at)
        )
        self.order_rank = np.empty(plan.n_orders, dtype=np.int64)
        self.order_rank[by_time] = np.arange(plan.n_orders)
        self.order_id = [f"O-{p}{r + 1:07d}" for r in self.order_rank]
        self.terms_id = [f"TS-{p}{r + 1:07d}" for r in self.order_rank]
        line_by_time = np.lexsort((np.arange(plan.n_lines), self.order_rank[plan.line_order]))
        line_rank = np.empty(plan.n_lines, dtype=np.int64)
        line_rank[line_by_time] = np.arange(plan.n_lines)
        self.line_id = [f"L-{p}{r + 1:08d}" for r in line_rank]
        ret_by_time = np.lexsort((line_rank[rows.line], rows.requested_at))
        ret_rank = np.empty(len(rows.line), dtype=np.int64)
        ret_rank[ret_by_time] = np.arange(len(rows.line))
        self.return_id = [f"RET-{p}{r + 1:07d}" for r in ret_rank]


def _sorted(table: pa.Table, keys: list[tuple[str, str]]) -> pa.Table:
    return table.sort_by(keys)


def customers(pop: Population) -> pa.Table:
    t = _table(
        "store/customers",
        {
            "customer_id": pop.customer_id,
            "display_name": pop.display_name,
            "event_at": timestamps(pop.created_at),
            "available_at": timestamps(pop.created_at),
        },
    )
    return _sorted(t, [("customer_id", "ascending")])


def identifiers(
    pop: Population, rows: tuple[list[int], list[str], list[str], list[int]]
) -> pa.Table:
    customer, kind, value, first = rows
    t = _table(
        "store/identifiers",
        {
            "customer_id": [pop.customer_id[c] for c in customer],
            "kind": kind,
            "value_hash": value,
            "event_at": timestamps(first),
            "available_at": timestamps(first),
        },
    )
    return _sorted(
        t, [("customer_id", "ascending"), ("kind", "ascending"), ("value_hash", "ascending")]
    )


def variants(cat: Catalogue) -> pa.Table:
    t = _table(
        "store/variants",
        {
            "variant_id": cat.variant_id,
            "product_id": cat.product_id,
            "category": [cat.category(i) for i in range(cat.n)],
            "brand": cat.brand,
            "price_eur": euros(cat.price_cents),
            "size_range": [SIZE_RANGE[SizeKind(int(k))] for k in cat.size_kind],
            "event_at": timestamps(cat.listed_at),
            "available_at": timestamps(cat.listed_at),
        },
    )
    return _sorted(t, [("variant_id", "ascending")])


def terms_snapshots(plan: OrderPlan, ids: Numbering, rng: np.random.Generator) -> pa.Table:
    """The Navigli base terms shown at checkout, a few minutes before each order (ADR-05)."""
    n = plan.n_orders
    shown = plan.placed_at - minutes(rng, 1, 20, n)
    base = NAVIGLI_BASE_TERMS
    t = _table(
        "store/terms_snapshots",
        {
            "terms_snapshot_id": ids.terms_id,
            "order_id": ids.order_id,
            "source": ["base"] * n,
            "quadrant_at_purchase": [None] * n,
            "return_window_days": [base.return_window_days] * n,
            "free_return_shipping": [base.free_return_shipping] * n,
            "instant_refund": [base.instant_refund] * n,
            "free_size_exchange": [base.free_size_exchange] * n,
            "coupons_enabled": [base.coupons_enabled] * n,
            "event_at": timestamps(shown),
            "available_at": timestamps(shown),
        },
    )
    return _sorted(t, [("terms_snapshot_id", "ascending")])


def orders(
    plan: OrderPlan,
    pop: Population,
    ids: Numbering,
    coupons: Coupons,
    order_coupon: I64,
    subtotal: I64,
) -> pa.Table:
    discount = np.asarray(
        [coupons.discount_cents[c] if c >= 0 else 0 for c in order_coupon], dtype=np.int64
    )
    t = _table(
        "store/orders",
        {
            "order_id": ids.order_id,
            "customer_id": [pop.customer_id[c] for c in plan.customer],
            "terms_snapshot_id": ids.terms_id,
            "coupon_code": [coupons.code[c] if c >= 0 else None for c in order_coupon],
            "total_eur": euros(subtotal - discount),
            "event_at": timestamps(plan.placed_at),
            "available_at": timestamps(plan.placed_at),
        },
    )
    return _sorted(t, [("order_id", "ascending")])


def order_lines(plan: OrderPlan, cat: Catalogue, ids: Numbering) -> pa.Table:
    placed = plan.placed_at[plan.line_order]
    t = _table(
        "store/order_lines",
        {
            "line_id": ids.line_id,
            "order_id": [ids.order_id[o] for o in plan.line_order],
            "variant_id": [cat.variant_id[v] for v in plan.line_variant],
            "size": [SIZE_LABELS[s] if s >= 0 else None for s in plan.line_size],
            "price_eur": euros(cat.price_cents[plan.line_variant]),
            "event_at": timestamps(placed),
            "available_at": timestamps(placed),
        },
    )
    return _sorted(t, [("line_id", "ascending")])


def deliveries(deliveries: Deliveries, ids: Numbering) -> pa.Table:
    keep = np.flatnonzero((deliveries.delivered_at >= 0) & (deliveries.available_at < END_US))
    t = _table(
        "store/deliveries",
        {
            "order_id": [ids.order_id[o] for o in keep],
            "event_at": timestamps(deliveries.delivered_at[keep]),
            "available_at": timestamps(deliveries.available_at[keep]),
        },
    )
    return _sorted(t, [("order_id", "ascending")])


def returns(rows: ReturnRows, ids: Numbering) -> pa.Table:
    t = _table(
        "store/returns",
        {
            "return_id": ids.return_id,
            "line_id": [ids.line_id[i] for i in rows.line],
            "reason": [REASONS[r] for r in rows.reason],
            "days_since_delivery": pa.array(rows.days_since_delivery, type=pa.int32()),
            "event_at": timestamps(rows.requested_at),
            "available_at": timestamps(rows.available_at),
        },
    )
    return _sorted(t, [("return_id", "ascending")])


def return_events(rows: ReturnRows, ids: Numbering) -> pa.Table:
    t = _table(
        "store/return_events",
        {
            "return_id": [ids.return_id[r] for r in rows.state_return],
            "state": rows.state,
            "event_at": timestamps(rows.state_at),
            "available_at": timestamps(rows.state_available),
        },
    )
    return _sorted(
        t, [("return_id", "ascending"), ("event_at", "ascending"), ("state", "ascending")]
    )


def coupon_log(coupons: Coupons, pop: Population, plan: OrderPlan, ids: Numbering) -> pa.Table:
    """One row when a coupon is issued (order_id null), one when it is redeemed (ADR-13)."""
    code: list[str] = []
    customer: list[str] = []
    kind: list[str] = []
    discount: list[int] = []
    order_id: list[str | None] = []
    at: list[int] = []
    for c in range(len(coupons.code)):
        if coupons.issued_at[c] >= END_US:
            continue
        code.append(coupons.code[c])
        customer.append(pop.customer_id[coupons.customer[c]])
        kind.append(coupons.kind[c])
        discount.append(coupons.nominal_cents[c])
        order_id.append(None)
        at.append(coupons.issued_at[c])
        o = coupons.order[c]
        if o >= 0:
            code.append(coupons.code[c])
            customer.append(pop.customer_id[coupons.customer[c]])
            kind.append(coupons.kind[c])
            discount.append(coupons.discount_cents[c])
            order_id.append(ids.order_id[o])
            at.append(int(plan.placed_at[o]))
    t = _table(
        "store/coupons",
        {
            "coupon_code": code,
            "customer_id": customer,
            "kind": kind,
            "discount_eur": euros(discount),
            "order_id": order_id,
            "event_at": timestamps(at),
            "available_at": timestamps(at),
        },
    )
    return _sorted(t, [("coupon_code", "ascending"), ("event_at", "ascending")])


def labels(pop: Population, cfg: ScenarioConfig) -> pa.Table:
    t = _table(
        "truth/labels",
        {
            "customer_id": pop.customer_id,
            "behaviour": pop.behaviour,
            "ring_id": [pop.rings[r].ring_id if r >= 0 else None for r in pop.ring_of],
            "family_id": [pop.families[f].family_id if f >= 0 else None for f in pop.family_of],
            "scenario": [cfg.name] * pop.n,
        },
    )
    return _sorted(t, [("customer_id", "ascending")])


def truth_variants(cat: Catalogue) -> pa.Table:
    start = [int(s) if d else None for s, d in zip(cat.defect_start, cat.defective, strict=True)]
    end = [
        int(e) if d and e >= 0 else None for e, d in zip(cat.defect_end, cat.defective, strict=True)
    ]
    t = _table(
        "truth/variants",
        {
            "variant_id": cat.variant_id,
            "base_return_rate": pa.array(cat.base_rate, type=pa.float64()),
            "defective": pa.array(cat.defective, type=pa.bool_()),
            "defect_reason": cat.defect_reason,
            "defect_start": pa.array(start, type=TS),
            "defect_end": pa.array(end, type=TS),
        },
    )
    return _sorted(t, [("variant_id", "ascending")])
