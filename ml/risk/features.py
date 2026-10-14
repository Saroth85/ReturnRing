"""Feature sets and design matrices of the abuse-risk model (spec 8.4; rules 6 and 7).

Ablation of spec 8.4 (MOD-04), one feature set per step, all on the same split:

- A0: return rate, return window (days from delivery to the request) and value of the items
  (mean line price), as rules (ml.risk.rules);
- A1: A0 + the individual behaviour features of features/customer_features, XGBoost;
- A2: A1 + the cluster features of features/clusters (account graph, spec 8.3), XGBoost;
- A3: A2 + the return residual (expected returns, R_c, z_c of spec 8.2), XGBoost;
- A4: A3 + GraphSAGE embeddings (features/customer_embeddings, written by `python -m ml.gnn
  embed`, US-202): run when that file exists, through an EmbeddingSource (ml.risk.embeddings).

"Value" in A0 is the price of the items a customer buys, the returnguard-style high-value rule. The
customer value V_c (features/customer_value) never feeds risk (rule 6): no feature set holds one
of its columns, and nothing here reads that table.

Two scoring units (spec 8.4):

- the customer, at a decision time T: customer_frame joins features/customer_features and
  features/clusters computed at exactly T, from the store known at T (rule 7);
- the return request, at the instant it became known (its decided_at): request_frame adds days
  since delivery, reason, price and the item's base return rate at that instant
  (pipeline.features.requests, customer left out) to the customer row of the latest snapshot
  computed strictly before decided_at: the nightly batch before the request arrived. A snapshot at
  decided_at or later would already hold the request (MOD-08). A request with no earlier snapshot
  of its customer has missing customer features.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Protocol

import numpy as np
import numpy.typing as npt
import pandas as pd

from contracts.parquet import EMBEDDING_PREFIX as CONTRACT_EMBEDDING_PREFIX
from contracts.parquet import SCHEMAS, forbidden_features, label_or_key_features
from pipeline.graph.clusters import CLUSTER_FEATURES as GRAPH_CLUSTER_FEATURES
from policy.types import Path, ReturnReason

F64 = npt.NDArray[np.float64]

A0_FEATURES: Final = ("return_rate_to_date", "median_days_to_return", "mean_line_price_eur")
INDIVIDUAL_FEATURES: Final = (
    "n_orders",
    "n_lines",
    "n_delivered_lines",
    "n_observations",
    "n_returns",
    "return_rate",
    "size_share",
    "changed_mind_share",
    "not_as_expected_share",
    "other_reason_share",
    "tenure_days",
    "n_welcome_coupons_used",
    "n_promo_coupons_used",
    "coupon_order_share",
    "weekend_order_share",
)
CLUSTER_FEATURES: Final = tuple(GRAPH_CLUSTER_FEATURES)
RESIDUAL_FEATURES: Final = ("expected_returns", "residual", "residual_z")
CUSTOMER_FEATURES: Final = (*A0_FEATURES, *INDIVIDUAL_FEATURES, *CLUSTER_FEATURES)
CUSTOMER_FEATURES_ALL: Final = (*CUSTOMER_FEATURES, *RESIDUAL_FEATURES)

# Request-level features (spec 8.4): source column of pipeline.features.requests -> feature.
REQUEST_COLUMNS: Final = {
    "days_since_delivery": "days_since_delivery",
    "price_eur": "item_price_eur",
    "variant_rate": "variant_return_rate",
    "variant_n": "variant_lines",
    "category_rate": "category_return_rate",
}
REASON_FEATURES: Final = {
    "reason_size": ReturnReason.SIZE.value,
    "reason_changed_mind": ReturnReason.CHANGED_MIND.value,
    "reason_not_as_expected": ReturnReason.NOT_AS_EXPECTED.value,
    "reason_other": ReturnReason.OTHER.value,
}
REQUEST_FEATURES: Final = (*REQUEST_COLUMNS.values(), *REASON_FEATURES)
EMBEDDING_PREFIX: Final = "graphsage_"
assert EMBEDDING_PREFIX == CONTRACT_EMBEDDING_PREFIX  # features/customer_embeddings columns

Learner = Literal["rules", "xgboost"]


@dataclass(frozen=True)
class FeatureSet:
    """One step of the ablation: its features, its learner and what it measures (spec 8.4)."""

    name: str
    features: tuple[str, ...]
    learner: Learner
    purpose: str
    needs_embeddings: bool = False


A0 = FeatureSet("A0", A0_FEATURES, "rules", "rules only: the returnguard-like baseline")
A1 = FeatureSet("A1", (*A0_FEATURES, *INDIVIDUAL_FEATURES), "xgboost", "what the model adds")
A2 = FeatureSet("A2", (*A1.features, *CLUSTER_FEATURES), "xgboost", "what the account graph adds")
A3 = FeatureSet("A3", (*A2.features, *RESIDUAL_FEATURES), "xgboost", "what the residual adds")
A4 = FeatureSet(
    "A4",
    A3.features,
    "xgboost",
    "what GraphSAGE embeddings add (phase 6, GPU)",
    needs_embeddings=True,
)
ABLATION: Final = (A0, A1, A2, A3, A4)
PRODUCT: Final = A3  # the customer model of the product
REQUEST_MODEL_FEATURES: Final = (*A3.features, *REQUEST_FEATURES)

# Columns of the customer value table (rule 6: never a risk feature). The counts it shares with
# features/customer_features (n_orders, n_lines, n_returns) are behaviour, not value.
VALUE_COLUMNS: Final = frozenset(
    {"value_eur", "kept_eur", "returned_eur", "discount_eur", "orders_total_eur"}
)
assert set(SCHEMAS["features/customer_value"].names) >= VALUE_COLUMNS


def all_feature_names() -> tuple[str, ...]:
    """Every feature any risk model may use, in a stable order."""
    names: list[str] = []
    for name in (*CUSTOMER_FEATURES_ALL, *REQUEST_FEATURES):
        if name not in names:
            names.append(name)
    return tuple(names)


def check_feature_names(names: Sequence[str]) -> None:
    """FAIR-01 and rule 6 on a feature list: no forbidden feature, no ground-truth column or row
    key (review F6), no customer value, no duplicate. Raises ValueError."""
    bad = forbidden_features(names)
    if bad:
        raise ValueError(f"forbidden features (rule 6, FAIR-01): {bad}")
    keys = label_or_key_features(names)
    if keys:
        raise ValueError(f"labels and row keys are never features (rule 6, FAIR-01): {keys}")
    value = sorted(set(names) & VALUE_COLUMNS)
    if value:
        raise ValueError(f"customer value never feeds risk (rule 6): {value}")
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate features in {list(names)}")


for _set in ABLATION:
    check_feature_names(_set.features)
check_feature_names(REQUEST_MODEL_FEATURES)


def _at(frame: pd.DataFrame, as_of: datetime, what: str) -> pd.DataFrame:
    stamp = pd.Timestamp(as_of)
    rows = frame.loc[frame["as_of"] == stamp]
    if rows.empty:
        raise ValueError(f"{what}: no rows computed at {stamp.isoformat()}")
    return rows


def customer_frame(features: pd.DataFrame, clusters: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """One row per customer known at as_of: customer_id, as_of, cluster_id and
    CUSTOMER_FEATURES_ALL as float64, from the rows of features/customer_features and
    features/clusters computed at exactly as_of (never a later snapshot: rule 7)."""
    f = _at(features, as_of, "features/customer_features")
    c = _at(clusters, as_of, "features/clusters")
    left = f[["customer_id", *A0_FEATURES, *INDIVIDUAL_FEATURES, *RESIDUAL_FEATURES]]
    right = c[["customer_id", "cluster_id", *CLUSTER_FEATURES]]
    merged = left.merge(right, on="customer_id", how="left", validate="one_to_one")
    missing = merged.loc[merged["cluster_size"].isna(), "customer_id"]
    if len(missing):
        raise ValueError(
            f"{len(missing)} customers without a features/clusters row at {as_of.isoformat()}, "
            f"for example {sorted(missing)[:3]}"
        )
    out = merged[["customer_id", "cluster_id"]].copy()
    out.insert(1, "as_of", pd.Timestamp(as_of))
    for name in CUSTOMER_FEATURES_ALL:
        out[name] = pd.to_numeric(merged[name], errors="raise").astype(np.float64)
    return out.sort_values("customer_id", kind="stable").reset_index(drop=True)


def _utc_micros(values: pd.Series) -> pd.Series:
    return values.dt.tz_convert("UTC").dt.as_unit("us")


def request_frame(requests: pd.DataFrame, contexts: pd.DataFrame) -> pd.DataFrame:
    """One row per withdrawal-path request of `requests` (pipeline.features.requests rows):
    return_id, customer_id, decided_at, context_as_of, cluster_id and REQUEST_MODEL_FEATURES.

    contexts: customer_frame rows at one or more as_of. A request takes its customer's row of the
    latest as_of strictly before decided_at; with none, its customer features are NaN and
    context_as_of is NaT. Rows computed at or after decided_at are never used (MOD-08)."""
    if (requests["path"] != Path.WITHDRAWAL.value).any():
        raise ValueError("only withdrawal-path requests are scored (rule 4)")
    left = requests[["return_id", "customer_id", "decided_at", "reason", *REQUEST_COLUMNS]].copy()
    left["customer_id"] = left["customer_id"].astype(object)
    left["decided_at"] = _utc_micros(left["decided_at"])
    left = left.sort_values(["decided_at", "return_id"], kind="stable").reset_index(drop=True)
    right = contexts[["customer_id", "as_of", "cluster_id", *CUSTOMER_FEATURES_ALL]].copy()
    right["customer_id"] = right["customer_id"].astype(object)
    right["as_of"] = _utc_micros(right["as_of"])
    right = right.rename(columns={"as_of": "context_as_of"})
    right = right.sort_values(["context_as_of", "customer_id"], kind="stable")
    merged = pd.merge_asof(
        left,
        right,
        left_on="decided_at",
        right_on="context_as_of",
        by="customer_id",
        direction="backward",
        allow_exact_matches=False,
    )
    out = merged[["return_id", "customer_id", "decided_at", "context_as_of", "cluster_id"]].copy()
    for name in CUSTOMER_FEATURES_ALL:
        out[name] = merged[name].astype(np.float64)
    for source, name in REQUEST_COLUMNS.items():
        out[name] = pd.to_numeric(merged[source], errors="raise").astype(np.float64)
    for name, reason in REASON_FEATURES.items():
        out[name] = (merged["reason"] == reason).astype(np.float64)
    return out.sort_values("return_id", kind="stable").reset_index(drop=True)


def matrix(frame: pd.DataFrame, features: Sequence[str]) -> F64:
    """The design matrix of `features` (float64, NaN for a missing value)."""
    missing = [name for name in features if name not in frame.columns]
    if missing:
        raise KeyError(f"features not in the frame: {missing}")
    return np.asarray(frame[list(features)].to_numpy(dtype=np.float64), dtype=np.float64)


class EmbeddingSource(Protocol):
    """A4 (US-202): GraphSAGE embeddings of the customers, trained by link prediction on the
    customer-identifier graph known at as_of (no labels, so no leak from the ground truth).
    Columns: customer_id and EMBEDDING_PREFIX + index. ml.risk.embeddings.FileEmbeddingSource
    reads them from features/customer_embeddings."""

    def embeddings_at(self, as_of: datetime) -> pd.DataFrame: ...


def with_embeddings(
    frame: pd.DataFrame, source: EmbeddingSource, as_of: datetime
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """The customer frame with the embedding columns of `source` at as_of, and the A4 features."""
    emb = source.embeddings_at(as_of)
    columns = tuple(c for c in emb.columns if c.startswith(EMBEDDING_PREFIX))
    if not columns:
        raise ValueError(f"no {EMBEDDING_PREFIX}* column in the embeddings")
    check_feature_names((*A3.features, *columns))
    out = frame.merge(emb[["customer_id", *columns]], on="customer_id", how="left")
    return out, (*A3.features, *columns)
