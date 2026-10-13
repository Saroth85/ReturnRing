"""Parquet schemas for every file exchanged between modules (docs/spec.md section 7, ADR-13).

Contract version 2. Three kinds of table:

- event tables (store/*): every row carries event_at and available_at, and a row holds only facts
  known at its available_at (CON-01). A fact learnt later (a delivery, a return state) is a row of
  its own. Features are computed only from rows with available_at <= decision time (rule 7);
  contracts.io.read_table(..., as_of=...) applies that filter.
- as-of tables (features/*, scores/*): computed at as_of; no timestamp in a row is after as_of.
- static tables (raw/asos_*, truth/*): no decision time. raw/ is real ASOS data (ADR-10); truth/ is
  the generator's ground truth, read only by offline training and evaluation jobs (rule 12).

Value checks (enums, ranges, keys, time order) live in contracts/checks.py; contracts/io.py writes
and reads the files with provenance metadata.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import pyarrow as pa

CONTRACT_VERSION = "2.0.0"

TS = pa.timestamp("us", tz="UTC")
EUR = pa.decimal128(12, 2)

# ---- Vocabularies ------------------------------------------------------------------------------

IDENTIFIER_KINDS = ("card", "email", "device", "address", "phone", "ip_prefix")
RETURN_STATES = ("notified", "shipped_or_proof", "received", "refunded", "rejected")  # NFR-17
COUPON_KINDS = ("welcome", "referral", "promo")
BEHAVIOURS = ("normal", "size", "wardrober", "promo_ring", "shared_benign")
SCENARIOS = ("main", "ood")  # ood: the out-of-distribution scenario, never trained on (US-105)
ANOMALY_STATUSES = ("open", "task_opened", "resolved")
ASOS_SPLITS = ("train", "test")
# The 13 masked ASOS reason codes in source order. The source repeats the label "D" (5th and 6th
# code); by position the first is D and the second D2 (ADR-13).
ASOS_REASON_CODES = ("A", "B", "C", "D", "E", "D2", "F", "G", "H", "I", "J", "K", "L")

# ---- Schemas -----------------------------------------------------------------------------------

_EVENT = [pa.field("event_at", TS, nullable=False), pa.field("available_at", TS, nullable=False)]
_AS_OF = pa.field("as_of", TS, nullable=False)


def _event_table(*fields: pa.Field) -> pa.Schema:
    return pa.schema([*fields, *_EVENT])


def _as_of_table(*fields: pa.Field) -> pa.Schema:
    return pa.schema([*fields, _AS_OF])


def _reason_shares() -> list[pa.Field]:
    return [pa.field(f"reason_share_{code}", pa.float64()) for code in ASOS_REASON_CODES]


SCHEMAS: dict[str, pa.Schema] = {
    # ---- raw: ASOS GraphReturns, real data, no timestamps (ADR-10) ----
    "raw/asos_events": pa.schema(
        [
            pa.field("split", pa.string(), nullable=False),
            pa.field("variant_hash", pa.int64(), nullable=False),
            pa.field("customer_hash", pa.int64(), nullable=False),
            pa.field("is_returned", pa.bool_(), nullable=False),
        ]
    ),
    "raw/asos_customers": pa.schema(
        [
            pa.field("split", pa.string(), nullable=False),
            pa.field("customer_hash", pa.int64(), nullable=False),
            pa.field("sales", pa.int64(), nullable=False),
            pa.field("returns", pa.int64(), nullable=False),
            pa.field("return_rate", pa.float64(), nullable=False),
            *_reason_shares(),
            pa.field("premier", pa.bool_(), nullable=False),
            # Descriptive statistics only, never a feature (rule 6, ADR-11): DESCRIPTIVE_ONLY.
            pa.field("is_male", pa.bool_()),
            pa.field("shipping_country", pa.string()),
            pa.field("year_of_birth", pa.int32()),
        ]
    ),
    "raw/asos_variants": pa.schema(
        [
            pa.field("split", pa.string(), nullable=False),
            pa.field("variant_hash", pa.int64(), nullable=False),
            pa.field("product_hash", pa.int64(), nullable=False),
            pa.field("supplier_hash", pa.int64()),
            pa.field("product_type", pa.string()),
            pa.field("brand", pa.string()),
            pa.field("avg_price_gbp", pa.float64()),
            pa.field("avg_discount", pa.float64()),
            pa.field("sales", pa.int64(), nullable=False),
            pa.field("returns", pa.int64(), nullable=False),
            pa.field("return_rate", pa.float64(), nullable=False),
            *_reason_shares(),
        ]
    ),
    # ---- store: the synthetic shop, event tables ----
    "store/customers": _event_table(  # event_at: account created
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("display_name", pa.string()),  # UI only, a forbidden feature
    ),
    "store/identifiers": _event_table(  # event_at: first use of the identifier by the customer
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),  # IDENTIFIER_KINDS
        pa.field("value_hash", pa.string(), nullable=False),  # pseudonymous personal data
    ),
    "store/variants": _event_table(  # event_at: listed; observable catalogue attributes only
        pa.field("variant_id", pa.string(), nullable=False),
        pa.field("product_id", pa.string(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("brand", pa.string()),
        pa.field("price_eur", EUR, nullable=False),
        pa.field("size_range", pa.string()),  # for example "XS-XL"
    ),
    "store/terms_snapshots": _event_table(  # event_at: terms shown at checkout (ADR-05)
        pa.field("terms_snapshot_id", pa.string(), nullable=False),
        pa.field("order_id", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),  # "base" or the id of a decision
        pa.field("quadrant_at_purchase", pa.string()),  # Q1..Q4, null when none applied
        pa.field("return_window_days", pa.int32(), nullable=False),
        pa.field("free_return_shipping", pa.bool_(), nullable=False),
        pa.field("instant_refund", pa.bool_(), nullable=False),
        pa.field("free_size_exchange", pa.bool_(), nullable=False),
        pa.field("coupons_enabled", pa.bool_(), nullable=False),
    ),
    "store/orders": _event_table(  # event_at: order placed; delivery is in store/deliveries
        pa.field("order_id", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("terms_snapshot_id", pa.string(), nullable=False),
        pa.field("coupon_code", pa.string()),
        pa.field("total_eur", EUR, nullable=False),
    ),
    "store/order_lines": _event_table(
        pa.field("line_id", pa.string(), nullable=False),
        pa.field("order_id", pa.string(), nullable=False),
        pa.field("variant_id", pa.string(), nullable=False),
        pa.field("size", pa.string()),
        pa.field("price_eur", EUR, nullable=False),
    ),
    "store/deliveries": _event_table(  # event_at: delivered
        pa.field("order_id", pa.string(), nullable=False),
    ),
    "store/returns": _event_table(  # event_at: return requested; states in store/return_events
        pa.field("return_id", pa.string(), nullable=False),
        pa.field("line_id", pa.string(), nullable=False),
        pa.field("reason", pa.string(), nullable=False),  # policy.types.ReturnReason
        pa.field("days_since_delivery", pa.int32(), nullable=False),  # known at request time
    ),
    "store/return_events": _event_table(  # one row per state reached (NFR-17, spec 8.6)
        pa.field("return_id", pa.string(), nullable=False),
        pa.field("state", pa.string(), nullable=False),  # RETURN_STATES
    ),
    "store/coupons": _event_table(
        # An event log: one row when a coupon is issued (order_id null) and one when it is
        # redeemed (order_id set, event_at = redemption), so no row holds a later fact.
        pa.field("coupon_code", pa.string(), nullable=False),
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),  # COUPON_KINDS
        pa.field("discount_eur", EUR, nullable=False),
        pa.field("order_id", pa.string()),
    ),
    # ---- truth: ground truth of the generator (rule 12: never read by running services) ----
    "truth/labels": pa.schema(
        [
            pa.field("customer_id", pa.string(), nullable=False),
            pa.field("behaviour", pa.string(), nullable=False),  # BEHAVIOURS
            pa.field("ring_id", pa.string()),  # set exactly for promo_ring
            pa.field("family_id", pa.string()),  # set for every shared_benign account
            pa.field("scenario", pa.string(), nullable=False),  # SCENARIOS
        ]
    ),
    "truth/variants": pa.schema(
        [
            pa.field("variant_id", pa.string(), nullable=False),
            pa.field("base_return_rate", pa.float64(), nullable=False),  # latent, from ASOS
            pa.field("defective", pa.bool_(), nullable=False),
            pa.field("defect_reason", pa.string()),  # policy.types.ReturnReason
            pa.field("defect_start", TS),
            pa.field("defect_end", TS),  # null: the defect is never fixed in the period
        ]
    ),
    # ---- features ----
    "features/links": _as_of_table(  # US-203, US-302, US-310; spec 8.3
        pa.field("customer_a", pa.string(), nullable=False),  # customer_a < customer_b
        pa.field("customer_b", pa.string(), nullable=False),
        pa.field("kinds", pa.list_(pa.field("element", pa.string(), nullable=False))),
        pa.field("weight", pa.float64(), nullable=False),  # w_ij of 8.3, no decay
        pa.field("weight_decayed", pa.float64(), nullable=False),  # with time and d_k decay
        pa.field("first_shared_at", TS, nullable=False),
        pa.field("last_shared_at", TS, nullable=False),
        pa.field("n_shared_events", pa.int32(), nullable=False),
        pa.field("max_sharers", pa.int32(), nullable=False),  # largest d_k among the kinds
        pa.field("disputed", pa.bool_(), nullable=False),  # disputed by an operator (US-310)
    ),
    "features/clusters": _as_of_table(
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("cluster_id", pa.string()),
        pa.field("cluster_size", pa.int32(), nullable=False),
        pa.field("shared_strong_ids", pa.int32(), nullable=False),
        pa.field("welcome_coupons", pa.int32(), nullable=False),
        pa.field("n_distinct_cards", pa.int32(), nullable=False),
        pa.field("creation_spread_days", pa.float64(), nullable=False),
        pa.field("welcome_coupons_per_account", pa.float64(), nullable=False),
        pa.field("cluster_return_rate", pa.float64()),  # null: no line delivered yet
        pa.field("strong_id_share", pa.float64(), nullable=False),
    ),
    "features/customer_features": _as_of_table(
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("return_rate", pa.float64(), nullable=False),
        pa.field("changed_mind_share", pa.float64(), nullable=False),
        pa.field("size_share", pa.float64(), nullable=False),
        pa.field("median_days_to_return", pa.float64()),
        pa.field("residual_z", pa.float64()),
        pa.field("n_observations", pa.int32(), nullable=False),
    ),
    "features/product_anomalies": _as_of_table(
        pa.field("variant_id", pa.string(), nullable=False),
        pa.field("reason", pa.string(), nullable=False),  # policy.types.ReturnReason
        pa.field("week", pa.date32(), nullable=False),  # first day of the week
        pa.field("observed", pa.int32(), nullable=False),
        pa.field("expected", pa.float64(), nullable=False),
        pa.field("z", pa.float64(), nullable=False),
        pa.field("n", pa.int32(), nullable=False),
        pa.field("status", pa.string(), nullable=False),  # ANOMALY_STATUSES
    ),
    # ---- scores ----
    "scores/scores": _as_of_table(
        pa.field("customer_id", pa.string(), nullable=False),
        pa.field("risk", pa.float64(), nullable=False),
        pa.field("evidence_quality", pa.string(), nullable=False),
        pa.field("value_eur", EUR, nullable=False),
        pa.field("quadrant", pa.string()),
        pa.field("shap_top3", pa.string()),  # JSON: contributions to the score, not causes
        pa.field("model_version", pa.string(), nullable=False),
    ),
}

EVENT_TABLES = frozenset(name for name in SCHEMAS if name.startswith("store/"))
AS_OF_TABLES = frozenset(name for name in SCHEMAS if name.startswith(("features/", "scores/")))

# ---- Forbidden features (rule 6, FAIR-01, ADR-11, ASOS-7) ---------------------------------------

# ASOS columns kept in raw/asos_customers for descriptive statistics only.
DESCRIPTIVE_ONLY = frozenset({"is_male", "shipping_country", "year_of_birth"})

# Canonical names, kept for compatibility; is_forbidden_feature is the check to use.
FORBIDDEN_FEATURE_FIELDS = frozenset(
    {
        "gender",
        "country",
        "display_name",
        "name",
        "value_hash",
        "age",
        "is_male",
        "isMale",
        "shipping_country",
        "shippingCountry",
        "year_of_birth",
        "yearOfBirth",
    }
)

# A name is split into lower-case words ("yearOfBirth" -> year, of, birth; "Country_A" ->
# country, a; "hash(customerId)" -> hash, customer, id). A forbidden word anywhere forbids it.
_FORBIDDEN_WORDS = frozenset(
    {
        "gender",
        "sex",
        "male",
        "female",
        "ismale",
        "country",
        "age",
        "dob",
        "birth",
        "yearofbirth",
        "name",
        "names",
        "displayname",
        "firstname",
        "lastname",
        "surname",
        "hash",
    }
)
# Also forbidden inside a word, for names written without separators ("shippingcountry").
_FORBIDDEN_FRAGMENTS = ("gender", "country", "ismale", "yearofbirth", "birth", "hash")
# Raw identifiers (pseudonymous personal data) by their whole name, separators removed.
_RAW_IDENTIFIERS = frozenset(
    {
        "email",
        "emailaddress",
        "phone",
        "phonenumber",
        "ip",
        "ipaddress",
        "ipprefix",
        "address",
        "deviceid",
        "cardnumber",
        "cardfingerprint",
        "iban",
    }
)


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", spaced)
    return [w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w]


def is_forbidden_feature(name: str) -> bool:
    """True if a column with this name may never feed a score or a model (case-insensitive):
    gender, country, age, names and raw identifiers, in any spelling found in our data."""
    words = _words(name)
    joined = "".join(words)
    return (
        any(w in _FORBIDDEN_WORDS for w in words)
        or any(fragment in joined for fragment in _FORBIDDEN_FRAGMENTS)
        or joined in _RAW_IDENTIFIERS
    )


def forbidden_features(names: Iterable[str]) -> list[str]:
    """The forbidden names among `names`, in order: run it on every model's feature list."""
    return [name for name in names if is_forbidden_feature(name)]
