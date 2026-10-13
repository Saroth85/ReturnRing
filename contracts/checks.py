"""Value checks on contract tables (DATA-01, CON-09): enums, ranges, keys and time order.

Small on purpose: each check is a frozen dataclass with violations(table) -> list[str], built on
pyarrow.compute. checks_for(name) adds the generic temporal rules to the per-table CHECKS:

- event tables: every timestamp of a row, event_at included, is <= that row's available_at;
- as-of tables: every timestamp of a row is <= its as_of.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import pyarrow as pa
import pyarrow.compute as pc

from contracts.parquet import (
    ANOMALY_STATUSES,
    AS_OF_TABLES,
    ASOS_REASON_CODES,
    ASOS_SPLITS,
    BEHAVIOURS,
    COUPON_KINDS,
    EVENT_TABLES,
    IDENTIFIER_KINDS,
    RETURN_STATES,
    SCENARIOS,
    SCHEMAS,
)
from policy.legal_guard import LEGAL_WITHDRAWAL_DAYS
from policy.types import EvidenceQuality, Quadrant, ReturnReason

Number = int | float | Decimal
_SHOWN = 5  # offending values quoted in a message


class Check(Protocol):
    def columns(self) -> tuple[str, ...]: ...

    def violations(self, table: pa.Table) -> list[str]: ...


def _sample(values: pa.Array | pa.ChunkedArray) -> list[str]:
    return sorted({str(v) for v in values.to_pylist()})[:_SHOWN]


@dataclass(frozen=True)
class OneOf:
    """Non-null values (list elements for a list column) belong to `values`."""

    column: str
    values: tuple[object, ...]

    def columns(self) -> tuple[str, ...]:
        return (self.column,)

    def violations(self, table: pa.Table) -> list[str]:
        col = table.column(self.column)
        if pa.types.is_list(col.type):
            col = pc.list_flatten(col)
        col = pc.drop_null(col)
        allowed = pa.array(list(self.values), type=col.type)
        bad = pc.filter(col, pc.invert(pc.is_in(col, value_set=allowed)))
        if len(bad):
            return [f"{self.column}: values outside {list(self.values)}: {_sample(bad)}"]
        return []


@dataclass(frozen=True)
class InRange:
    """Non-null values lie in [min, max]; either bound may be None."""

    column: str
    min: Number | None = None
    max: Number | None = None

    def columns(self) -> tuple[str, ...]:
        return (self.column,)

    def violations(self, table: pa.Table) -> list[str]:
        col = pc.drop_null(table.column(self.column))
        tests = []
        if self.min is not None:
            tests.append(pc.less(col, pa.scalar(self.min, col.type)))
        if self.max is not None:
            tests.append(pc.greater(col, pa.scalar(self.max, col.type)))
        if not tests:
            return []
        bad = tests[0] if len(tests) == 1 else pc.or_(tests[0], tests[1])
        out = pc.filter(col, bad)
        if len(out):
            return [f"{self.column}: values outside [{self.min}, {self.max}]: {_sample(out)}"]
        return []


@dataclass(frozen=True)
class Unique:
    """No two rows share the same values of `keys` (null counts as a value)."""

    keys: tuple[str, ...]

    def columns(self) -> tuple[str, ...]:
        return self.keys

    def violations(self, table: pa.Table) -> list[str]:
        distinct = table.select(list(self.keys)).group_by(list(self.keys)).aggregate([])
        duplicates = table.num_rows - distinct.num_rows
        if duplicates:
            return [f"{', '.join(self.keys)}: not unique ({duplicates} duplicate rows)"]
        return []


@dataclass(frozen=True)
class NotAfter:
    """Row by row, before <= after (before < after when strict); rows with a null are skipped."""

    before: str
    after: str
    strict: bool = False

    def columns(self) -> tuple[str, ...]:
        return (self.before, self.after)

    def violations(self, table: pa.Table) -> list[str]:
        compare = pc.greater_equal if self.strict else pc.greater
        bad = pc.fill_null(compare(table.column(self.before), table.column(self.after)), False)
        n = pc.sum(bad).as_py() or 0
        if n:
            op = "<" if self.strict else "<="
            return [f"{self.before} {op} {self.after} violated in {n} rows"]
        return []


@dataclass(frozen=True)
class PresentWhen:
    """`column` is non-null on every row where `when` is in `values`; with only_then, it is
    null on every other row."""

    column: str
    when: str
    values: tuple[object, ...]
    only_then: bool = False

    def columns(self) -> tuple[str, ...]:
        return (self.column, self.when)

    def violations(self, table: pa.Table) -> list[str]:
        when = table.column(self.when)
        selected = pc.fill_null(pc.is_in(when, value_set=pa.array(self.values, when.type)), False)
        present = pc.is_valid(table.column(self.column))
        out: list[str] = []
        missing = pc.sum(pc.and_(selected, pc.invert(present))).as_py() or 0
        if missing:
            out.append(f"{self.column}: required when {self.when} in {list(self.values)}")
        if self.only_then:
            extra = pc.sum(pc.and_(pc.invert(selected), present)).as_py() or 0
            if extra:
                out.append(f"{self.column}: allowed only when {self.when} in {list(self.values)}")
        return out


@dataclass(frozen=True)
class NotBlank:
    """String values are not empty or whitespace."""

    column: str

    def columns(self) -> tuple[str, ...]:
        return (self.column,)

    def violations(self, table: pa.Table) -> list[str]:
        col = pc.drop_null(table.column(self.column))
        blank = pc.sum(pc.equal(pc.utf8_trim_whitespace(col), "")).as_py() or 0
        return [f"{self.column}: {blank} blank values"] if blank else []


_REASONS = tuple(r.value for r in ReturnReason)
_QUADRANTS = tuple(q.value for q in Quadrant)
_SHARE = (0.0, 1.0)


def _shares(prefix: str = "reason_share_") -> tuple[InRange, ...]:
    return tuple(InRange(f"{prefix}{code}", *_SHARE) for code in ASOS_REASON_CODES)


CHECKS: dict[str, tuple[Check, ...]] = {
    "raw/asos_events": (OneOf("split", ASOS_SPLITS),),
    "raw/asos_customers": (
        OneOf("split", ASOS_SPLITS),
        Unique(("split", "customer_hash")),
        InRange("sales", 0),
        InRange("returns", 0),
        InRange("return_rate", *_SHARE),
        *_shares(),
    ),
    "raw/asos_variants": (
        OneOf("split", ASOS_SPLITS),
        Unique(("split", "variant_hash")),
        InRange("avg_price_gbp", 0),
        InRange("avg_discount", 0),
        InRange("sales", 0),
        InRange("returns", 0),
        InRange("return_rate", *_SHARE),
        *_shares(),
    ),
    "store/customers": (Unique(("customer_id",)),),
    "store/identifiers": (
        OneOf("kind", IDENTIFIER_KINDS),
        Unique(("customer_id", "kind", "value_hash")),
    ),
    "store/variants": (Unique(("variant_id",)), InRange("price_eur", 0)),
    "store/terms_snapshots": (
        Unique(("terms_snapshot_id",)),
        Unique(("order_id",)),
        NotBlank("source"),
        OneOf("quadrant_at_purchase", _QUADRANTS),
        InRange("return_window_days", LEGAL_WITHDRAWAL_DAYS),
    ),
    "store/orders": (Unique(("order_id",)), InRange("total_eur", 0)),
    "store/order_lines": (Unique(("line_id",)), InRange("price_eur", 0)),
    "store/deliveries": (Unique(("order_id",)),),
    "store/returns": (
        Unique(("return_id",)),
        OneOf("reason", _REASONS),
        InRange("days_since_delivery", 0),
    ),
    "store/return_events": (
        OneOf("state", RETURN_STATES),
        Unique(("return_id", "state")),
    ),
    "store/coupons": (
        OneOf("kind", COUPON_KINDS),
        Unique(("coupon_code", "order_id")),
        InRange("discount_eur", 0),
    ),
    "truth/labels": (
        Unique(("customer_id",)),
        OneOf("behaviour", BEHAVIOURS),
        OneOf("scenario", SCENARIOS),
        PresentWhen("ring_id", "behaviour", ("promo_ring",), only_then=True),
        PresentWhen("family_id", "behaviour", ("shared_benign",)),
    ),
    "truth/variants": (
        Unique(("variant_id",)),
        InRange("base_return_rate", *_SHARE),
        OneOf("defect_reason", _REASONS),
        PresentWhen("defect_reason", "defective", (True,), only_then=True),
        PresentWhen("defect_start", "defective", (True,), only_then=True),
        NotAfter("defect_start", "defect_end"),
    ),
    "features/links": (
        Unique(("customer_a", "customer_b")),
        NotAfter("customer_a", "customer_b", strict=True),
        OneOf("kinds", IDENTIFIER_KINDS),
        InRange("weight", *_SHARE),
        InRange("weight_decayed", *_SHARE),
        NotAfter("weight_decayed", "weight"),
        NotAfter("first_shared_at", "last_shared_at"),
        InRange("n_shared_events", 1),
        InRange("max_sharers", 2),
    ),
    "features/clusters": (
        Unique(("customer_id",)),
        InRange("cluster_size", 1),
        InRange("shared_strong_ids", 0),
        InRange("welcome_coupons", 0),
        InRange("n_distinct_cards", 0),
        InRange("creation_spread_days", 0),
        InRange("welcome_coupons_per_account", 0),
        InRange("cluster_return_rate", *_SHARE),
        InRange("strong_id_share", *_SHARE),
    ),
    "features/customer_features": (
        Unique(("customer_id",)),
        InRange("return_rate", *_SHARE),
        InRange("changed_mind_share", *_SHARE),
        InRange("size_share", *_SHARE),
        InRange("median_days_to_return", 0),
        InRange("n_observations", 0),
    ),
    "features/product_anomalies": (
        Unique(("variant_id", "reason", "week")),
        OneOf("reason", _REASONS),
        OneOf("status", ANOMALY_STATUSES),
        InRange("observed", 0),
        InRange("expected", 0),
        InRange("n", 0),
    ),
    "scores/scores": (
        Unique(("customer_id",)),
        InRange("risk", *_SHARE),
        OneOf("evidence_quality", tuple(e.value for e in EvidenceQuality)),
        OneOf("quadrant", _QUADRANTS),
    ),
}


def temporal_checks(name: str) -> tuple[NotAfter, ...]:
    """The generic time rules of a table: no timestamp after available_at (or as_of)."""
    if name in EVENT_TABLES:
        anchor = "available_at"
    elif name in AS_OF_TABLES:
        anchor = "as_of"
    else:
        return ()
    schema = SCHEMAS[name]
    return tuple(
        NotAfter(field.name, anchor)
        for field in schema
        if pa.types.is_timestamp(field.type) and field.name != anchor
    )


def checks_for(name: str) -> tuple[Check, ...]:
    return (*temporal_checks(name), *CHECKS.get(name, ()))


def no_nan(table: pa.Table) -> list[str]:
    """NaN is never a value in a contract table: missing values are null."""
    out: list[str] = []
    for field in table.schema:
        if pa.types.is_floating(field.type):
            n = pc.sum(pc.fill_null(pc.is_nan(table.column(field.name)), False)).as_py() or 0
            if n:
                out.append(f"{field.name}: {n} NaN values (use null)")
    return out
