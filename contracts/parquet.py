"""Parquet schemas for every file exchanged between modules (docs/spec.md section 7).

Every store/ table is an event table: it carries event_at and available_at, and features are
computed only from rows with available_at <= decision time (CLAUDE.md rule 7).
"""

from __future__ import annotations

import pyarrow as pa

TS = pa.timestamp("us", tz="UTC")
EUR = pa.decimal128(12, 2)

_EVENT = [pa.field("event_at", TS, nullable=False), pa.field("available_at", TS, nullable=False)]


def _event_table(*fields: pa.Field) -> pa.Schema:
    return pa.schema([*fields, *_EVENT])


SCHEMAS: dict[str, pa.Schema] = {
    "store/customers": _event_table(
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("display_name", pa.string()),
    ),
    "store/identifiers": _event_table(
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),  # card, email, device, address, phone, ip
        pa.field("value_hash", pa.string(), nullable=False),  # pseudonymous personal data
    ),
    "store/terms_snapshots": _event_table(
        pa.field("terms_snapshot_id", pa.string(), nullable=False),
        pa.field("return_window_days", pa.int32(), nullable=False),
        pa.field("free_return_shipping", pa.bool_(), nullable=False),
        pa.field("instant_refund", pa.bool_(), nullable=False),
        pa.field("free_size_exchange", pa.bool_(), nullable=False),
        pa.field("coupons_enabled", pa.bool_(), nullable=False),
    ),
    "store/orders": _event_table(
        pa.field("order_id", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("terms_snapshot_id", pa.string(), nullable=False),
        pa.field("coupon_code", pa.string()),
        pa.field("total_eur", EUR, nullable=False),
        pa.field("delivered_at", TS),
    ),
    "store/order_lines": _event_table(
        pa.field("line_id", pa.string(), nullable=False),
        pa.field("order_id", pa.string(), nullable=False),
        pa.field("variant_id", pa.string(), nullable=False),
        pa.field("size", pa.string()),
        pa.field("price_eur", EUR, nullable=False),
    ),
    "store/returns": _event_table(
        pa.field("return_id", pa.string(), nullable=False),
        pa.field("line_id", pa.string(), nullable=False),
        pa.field("reason", pa.string(), nullable=False),
        pa.field("days_since_delivery", pa.int32(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
    ),
    "store/coupons": _event_table(
        pa.field("coupon_code", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),  # welcome, referral, promo
        pa.field("discount_eur", EUR, nullable=False),
    ),
    "truth/labels": pa.schema(
        [
            pa.field("customer_id", pa.string(), nullable=False),
            pa.field("behaviour", pa.string(), nullable=False),
            pa.field("ring_id", pa.string()),
        ]
    ),
    "features/clusters": pa.schema(
        [
            pa.field("customer_id", pa.string(), nullable=False),
            pa.field("cluster_id", pa.string()),
            pa.field("cluster_size", pa.int32(), nullable=False),
            pa.field("shared_strong_ids", pa.int32(), nullable=False),
            pa.field("welcome_coupons", pa.int32(), nullable=False),
            pa.field("as_of", TS, nullable=False),
        ]
    ),
    "features/customer_features": pa.schema(
        [
            pa.field("customer_id", pa.string(), nullable=False),
            pa.field("return_rate", pa.float64(), nullable=False),
            pa.field("changed_mind_share", pa.float64(), nullable=False),
            pa.field("size_share", pa.float64(), nullable=False),
            pa.field("median_days_to_return", pa.float64()),
            pa.field("residual_z", pa.float64()),
            pa.field("n_observations", pa.int32(), nullable=False),
            pa.field("as_of", TS, nullable=False),
        ]
    ),
    "features/product_anomalies": pa.schema(
        [
            pa.field("variant_id", pa.string(), nullable=False),
            pa.field("reason", pa.string(), nullable=False),
            pa.field("week", pa.date32(), nullable=False),
            pa.field("observed", pa.int32(), nullable=False),
            pa.field("expected", pa.float64(), nullable=False),
            pa.field("z", pa.float64(), nullable=False),
            pa.field("n", pa.int32(), nullable=False),
            pa.field("status", pa.string(), nullable=False),
        ]
    ),
    "scores/scores": pa.schema(
        [
            pa.field("customer_id", pa.string(), nullable=False),
            pa.field("risk", pa.float64(), nullable=False),
            pa.field("evidence_quality", pa.string(), nullable=False),
            pa.field("value_eur", EUR, nullable=False),
            pa.field("quadrant", pa.string()),
            pa.field("shap_top3", pa.string()),  # JSON: contributions to the score, not causes
            pa.field("model_version", pa.string(), nullable=False),
            pa.field("as_of", TS, nullable=False),
        ]
    ),
}

# Never allowed in features or scores (CLAUDE.md rule 6).
FORBIDDEN_FEATURE_FIELDS = frozenset({"gender", "country", "display_name", "name", "value_hash"})
