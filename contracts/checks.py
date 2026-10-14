"""Value checks on contract tables (DATA-01, CON-09): enums, ranges, keys and time order.

Small on purpose: each check is a frozen dataclass with violations(table) -> list[str], built on
pyarrow.compute. checks_for(name) adds the generic temporal rules to the per-table CHECKS:

- event tables: every timestamp of a row, event_at included, is <= that row's available_at;
- as-of tables: every timestamp and every date of a row is <= its as_of (a date is compared with
  the UTC day of as_of), and every key includes as_of, so one file may hold several snapshots.

Floats are finite everywhere: NaN and +/-inf are rejected, missing values are null.
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
from policy.types import EvidenceQuality, Path, Quadrant, ReturnReason

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
        before, after = table.column(self.before), table.column(self.after)
        if pa.types.is_date(before.type) and pa.types.is_timestamp(after.type):
            after = pc.cast(after, before.type)  # the UTC day of the instant
        bad = pc.fill_null(compare(before, after), False)
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
class NullUnless:
    """`column` is null on every row where `when` is not in `values` (it may be null there too)."""

    column: str
    when: str
    values: tuple[object, ...]

    def columns(self) -> tuple[str, ...]:
        return (self.column, self.when)

    def violations(self, table: pa.Table) -> list[str]:
        when = table.column(self.when)
        selected = pc.fill_null(pc.is_in(when, value_set=pa.array(self.values, when.type)), False)
        present = pc.is_valid(table.column(self.column))
        extra = pc.sum(pc.and_(pc.invert(selected), present)).as_py() or 0
        if extra:
            return [f"{self.column}: allowed only when {self.when} in {list(self.values)}"]
        return []


@dataclass(frozen=True)
class ListItems:
    """A list column: every non-null list has at least `min_length` elements, all distinct."""

    column: str
    min_length: int = 1

    def columns(self) -> tuple[str, ...]:
        return (self.column,)

    def violations(self, table: pa.Table) -> list[str]:
        col = table.column(self.column).combine_chunks()
        out: list[str] = []
        lengths = pc.drop_null(pc.list_value_length(col))
        short = pc.sum(pc.less(lengths, self.min_length)).as_py() or 0
        if short:
            out.append(f"{self.column}: {short} empty lists (fewer than {self.min_length} items)")
        pairs = pa.table({"row": pc.list_parent_indices(col), "item": pc.list_flatten(col)})
        repeated = pairs.num_rows - pairs.group_by(["row", "item"]).aggregate([]).num_rows
        if repeated:
            out.append(f"{self.column}: {repeated} items repeat within their list")
        return out


@dataclass(frozen=True)
class EventLog:
    """An issue-and-use log keyed by `key` (store/coupons, ADR-13): per key exactly one issue row
    (`use` null), at most one use row (`use` set), and the use not before the issue."""

    key: str
    use: str
    at: str = "event_at"

    def columns(self) -> tuple[str, ...]:
        return (self.key, self.use, self.at)

    def violations(self, table: pa.Table) -> list[str]:
        rows = table.select([self.key, self.use, self.at])
        is_use = pc.is_valid(rows.column(self.use))
        issues = rows.filter(pc.invert(is_use))
        uses = rows.filter(is_use)
        out: list[str] = []
        per_key = issues.group_by(self.key).aggregate([(self.at, "count"), (self.at, "min")])
        repeated = pc.sum(pc.greater(per_key.column(f"{self.at}_count"), 1)).as_py() or 0
        if repeated:
            out.append(f"{self.key}: {repeated} keys with more than one issue row")
        orphans = uses.join(per_key, self.key, join_type="left anti")
        if orphans.num_rows:
            out.append(
                f"{self.key}: {orphans.num_rows} {self.use} rows without an issue row: "
                f"{_sample(orphans.column(self.key))}"
            )
        used = uses.group_by(self.key).aggregate([(self.use, "count")])
        twice = pc.sum(pc.greater(used.column(f"{self.use}_count"), 1)).as_py() or 0
        if twice:
            out.append(f"{self.key}: {twice} keys with more than one redemption ({self.use} set)")
        joined = uses.join(per_key, self.key, join_type="inner")
        early = pc.sum(pc.less(joined.column(self.at), joined.column(f"{self.at}_min"))).as_py()
        if early:
            out.append(f"{self.key}: {early} redemptions before the issue row")
        return out


@dataclass(frozen=True)
class PresentTogether:
    """`column` and `other` are both null or both non-null on every row."""

    column: str
    other: str

    def columns(self) -> tuple[str, ...]:
        return (self.column, self.other)

    def violations(self, table: pa.Table) -> list[str]:
        a = pc.is_valid(table.column(self.column))
        b = pc.is_valid(table.column(self.other))
        n = pc.sum(pc.not_equal(a, b)).as_py() or 0
        return (
            [f"{self.column}: present without {self.other} or the reverse in {n} rows"] if n else []
        )


@dataclass(frozen=True)
class QuadrantRule:
    """Spec 8.6 on a score table (ADR-13 amendment 5, review F15): where the evidence is
    sufficient, the quadrant is policy.engine.quadrant_for of the risk, the value and the
    thresholds stored on the same row."""

    quadrant: str = "quadrant"
    evidence: str = "evidence_quality"
    risk: str = "risk"
    risk_threshold: str = "risk_threshold"
    value: str = "value_eur"
    value_threshold: str = "value_threshold_eur"

    def columns(self) -> tuple[str, ...]:
        return (
            self.quadrant,
            self.evidence,
            self.risk,
            self.risk_threshold,
            self.value,
            self.value_threshold,
        )

    def violations(self, table: pa.Table) -> list[str]:
        rows = table.select(list(self.columns())).to_pylist()
        wrong: list[str] = []
        for r in rows:
            if r[self.evidence] != EvidenceQuality.SUFFICIENT.value:
                continue
            needed = (r[self.risk], r[self.risk_threshold], r[self.value], r[self.value_threshold])
            if any(v is None for v in needed):
                wrong.append(f"{r[self.quadrant]} without its risk, value or thresholds")
                continue
            high_risk = r[self.risk] >= r[self.risk_threshold]
            high_value = r[self.value] >= r[self.value_threshold]
            expected = (
                (Quadrant.Q3 if high_risk else Quadrant.Q1)
                if high_value
                else (Quadrant.Q4 if high_risk else Quadrant.Q2)
            )
            if r[self.quadrant] != expected.value:
                wrong.append(f"{r[self.quadrant]} where the thresholds give {expected.value}")
        if wrong:
            return [
                f"{self.quadrant}: {len(wrong)} rows disagree with the stored risk and value "
                f"thresholds (spec 8.6), for example {sorted(set(wrong))[:_SHOWN]}"
            ]
        return []


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
        EventLog("coupon_code", "order_id"),
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
        NullUnless("defect_end", "defective", (True,)),  # null when defective: never fixed
        NotAfter("defect_start", "defect_end"),
    ),
    "features/links": (
        Unique(("customer_a", "customer_b", "as_of")),
        NotAfter("customer_a", "customer_b", strict=True),
        OneOf("kinds", IDENTIFIER_KINDS),
        ListItems("kinds", min_length=1),
        InRange("weight", *_SHARE),
        InRange("weight_decayed", *_SHARE),
        NotAfter("weight_decayed", "weight"),
        NotAfter("first_shared_at", "last_shared_at"),
        InRange("n_shared_events", 1),
        InRange("max_sharers", 2),
    ),
    "features/clusters": (
        Unique(("customer_id", "as_of")),
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
        Unique(("customer_id", "as_of")),
        *(InRange(c, 0) for c in ("n_orders", "n_lines", "n_delivered_lines", "n_returns")),
        InRange("n_observations", 0),
        NotAfter("n_delivered_lines", "n_lines"),
        NotAfter("n_observations", "n_delivered_lines"),
        NotAfter("n_orders", "n_lines"),
        InRange("return_rate", *_SHARE),
        InRange("return_rate_to_date", *_SHARE),
        InRange("size_share", *_SHARE),
        InRange("changed_mind_share", *_SHARE),
        InRange("not_as_expected_share", *_SHARE),
        InRange("other_reason_share", *_SHARE),
        InRange("median_days_to_return", 0),
        InRange("tenure_days", 0),
        InRange("n_welcome_coupons_used", 0),
        InRange("n_promo_coupons_used", 0),
        InRange("coupon_order_share", *_SHARE),
        InRange("weekend_order_share", *_SHARE),
        InRange("mean_line_price_eur", 0),
        InRange("expected_returns", 0),
        NotBlank("reference_model_version"),
    ),
    "features/customer_value": (
        Unique(("customer_id", "as_of")),
        *(InRange(c, 0) for c in ("kept_eur", "returned_eur", "discount_eur", "orders_total_eur")),
        InRange("n_orders", 0),
        InRange("n_lines", 0),
        InRange("n_returns", 0),
        NotAfter("n_returns", "n_lines"),
        NotAfter("n_orders", "n_lines"),
        NotBlank("assumptions_version"),
    ),
    "features/evidence_quality": (
        Unique(("customer_id", "as_of")),
        OneOf("evidence_quality", tuple(e.value for e in EvidenceQuality)),
        InRange("n_observations", 0),
        NotAfter("n_observations", "n_observations_linked"),
        InRange("identifier_completeness", *_SHARE),
        OneOf("missing_identifier_kinds", IDENTIFIER_KINDS),
        ListItems("missing_identifier_kinds", min_length=0),
        InRange("n_reasons", 0),
        InRange("reason_coherence", *_SHARE),
        ListItems("missing_evidence", min_length=0),
        NotBlank("thresholds_version"),
    ),
    "features/product_anomalies": (
        Unique(("variant_id", "reason", "week", "as_of")),
        OneOf("reason", _REASONS),
        OneOf("status", ANOMALY_STATUSES),
        InRange("observed", 0),
        InRange("expected", 0),
        InRange("n", 0),
    ),
    "scores/scores": (
        Unique(("customer_id", "as_of")),
        InRange("risk", *_SHARE),
        InRange("risk_threshold", *_SHARE),
        OneOf("evidence_quality", tuple(e.value for e in EvidenceQuality)),
        OneOf("quadrant", _QUADRANTS),
        NullUnless("quadrant", "evidence_quality", (EvidenceQuality.SUFFICIENT.value,)),
        QuadrantRule(),
        NotBlank("model_version"),
    ),
    "scores/request_scores": (
        Unique(("return_id", "as_of")),
        OneOf("path", tuple(p.value for p in Path)),
        PresentWhen("risk", "path", (Path.WITHDRAWAL.value,), only_then=True),
        PresentWhen("risk_threshold", "path", (Path.WITHDRAWAL.value,), only_then=True),
        PresentWhen("shap_top3", "path", (Path.WITHDRAWAL.value,), only_then=True),
        PresentWhen("model_version", "path", (Path.WITHDRAWAL.value,), only_then=True),
        PresentWhen("evidence_quality", "path", (Path.WITHDRAWAL.value,), only_then=True),
        InRange("risk", *_SHARE),
        InRange("risk_threshold", *_SHARE),
        OneOf("evidence_quality", tuple(e.value for e in EvidenceQuality)),
        OneOf("quadrant", _QUADRANTS),
        NullUnless("quadrant", "evidence_quality", (EvidenceQuality.SUFFICIENT.value,)),
        NullUnless("quadrant", "path", (Path.WITHDRAWAL.value,)),
        PresentTogether("value_eur", "value_threshold_eur"),
        PresentTogether("value_eur", "context_as_of"),
        QuadrantRule(),
        NotAfter("context_as_of", "decided_at", strict=True),
        NotBlank("model_version"),
    ),
}


def temporal_checks(name: str) -> tuple[NotAfter, ...]:
    """The generic time rules of a table: no timestamp or date after available_at (or as_of)."""
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
        if (pa.types.is_timestamp(field.type) or pa.types.is_date(field.type))
        and field.name != anchor
    )


def checks_for(name: str) -> tuple[Check, ...]:
    return (*temporal_checks(name), *CHECKS.get(name, ()))


def finite_floats(table: pa.Table) -> list[str]:
    """NaN and +/-inf are never values in a contract table: missing values are null."""
    out: list[str] = []
    for field in table.schema:
        if pa.types.is_floating(field.type):
            bad = pc.invert(pc.is_finite(table.column(field.name)))
            n = pc.sum(pc.fill_null(bad, False)).as_py() or 0
            if n:
                out.append(f"{field.name}: {n} NaN/inf values (use null for a missing value)")
    return out
