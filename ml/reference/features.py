"""Line-level design matrices of the reference and personalized predictors (spec 8.1, 8.2).

A query row is an order line to price at an instant t. Its history counts come from
pipeline.features.lines.line_history: observed lines settled by t, per variant, product, category
and the whole store, always leaving the query's customer out, plus the customer's own other lines.

Reference features (product and cohort, rule 8): the variant and product withdrawal-return rates,
shrunk to the category rate, and how many lines back them; the category rate and code; the price;
the size range; the order context known at checkout (lines in the order, other sizes or colours of
the same product in it, which is how a size bracket shows). None of them reads the customer's own
history. The personalized predictor adds the customer's own return rate and line count.

Shrinkage: rate = (returns + k * prior) / (lines + k). k for variants and products is fitted on the
training lines (ml.reference.model.choose_k); K_CATEGORY and K_CUSTOMER are design hypotheses.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from contracts.parquet import forbidden_features, label_or_key_features
from pipeline.features.lines import line_history

REFERENCE_FEATURES: Final = (
    "variant_rate",
    "variant_log_n",
    "product_rate",
    "product_log_n",
    "category_rate",
    "category_code",
    "log_price",
    "size_range_code",
    "order_lines",
    "order_same_product",
)
CUSTOMER_HISTORY_FEATURES: Final = ("customer_rate", "customer_log_n")
PERSONALIZED_FEATURES: Final = (*REFERENCE_FEATURES, *CUSTOMER_HISTORY_FEATURES)


def check_feature_names(names: Sequence[str]) -> None:
    """FAIR-01 and rule 6 on a feature list: no forbidden feature, no ground-truth column and
    no row key (review F6). Raises ValueError."""
    bad = forbidden_features(names) + label_or_key_features(names)
    if bad:
        raise ValueError(f"forbidden features, labels or row keys (rule 6, FAIR-01): {bad}")


check_feature_names(PERSONALIZED_FEATURES)

K_CATEGORY: Final = 20.0  # pseudo-lines of the store rate inside a category rate (hypothesis)
K_CUSTOMER: Final = 2.0  # pseudo-lines of the store rate inside a customer's rate (hypothesis)
NO_SIZE: Final = "none"

QUERY_COLUMNS: Final = (
    "line_id",
    "customer_id",
    "variant_id",
    "product_id",
    "category",
    "size_range",
    "price_cents",
    "order_lines",
    "order_same_product",
)


@dataclass(frozen=True)
class Encoding:
    """What a design matrix needs besides the rows: the pseudo-count k of the variant and
    product rates, and the category and size-range vocabularies of the training lines."""

    k: float
    categories: tuple[str, ...]
    size_ranges: tuple[str, ...]

    @classmethod
    def from_lines(cls, lines: pd.DataFrame, k: float) -> Encoding:
        sizes = lines["size_range"].fillna(NO_SIZE)
        return cls(
            k=float(k),
            categories=tuple(sorted(set(lines["category"]))),
            size_ranges=tuple(sorted(set(sizes))),
        )


def line_queries(lines: pd.DataFrame, t: datetime | pd.Series) -> pd.DataFrame:
    """Query rows for `lines` (a pipeline.features.lines.line_frame slice) at instant(s) t."""
    out = lines[list(QUERY_COLUMNS)].copy()
    out["t"] = t if isinstance(t, pd.Series) else pd.Timestamp(t)
    return out.reset_index(drop=True)


def history_frame(events: pd.DataFrame, queries: pd.DataFrame) -> pd.DataFrame:
    """Query rows with their history counts (line_history)."""
    return pd.concat([queries, line_history(events, queries)], axis=1)


def _codes(values: pd.Series, vocabulary: Sequence[str]) -> npt.NDArray[np.float64]:
    index = {v: float(i) for i, v in enumerate(vocabulary)}
    return np.asarray(values.map(index).astype(float).to_numpy(), dtype=np.float64)


def design(frame: pd.DataFrame, enc: Encoding, features: Sequence[str]) -> pd.DataFrame:
    """The float matrix of `features` for history_frame rows. Unknown categories and size ranges
    are missing values (NaN), which XGBoost routes on its own."""
    unknown = sorted(set(features) - set(PERSONALIZED_FEATURES))
    if unknown:
        raise ValueError(f"unknown features {unknown}")
    f = frame
    store_rate = (f["all_r"] + 1.0) / (f["all_n"] + 2.0)
    category_rate = (f["category_r"] + K_CATEGORY * store_rate) / (f["category_n"] + K_CATEGORY)
    columns: dict[str, pd.Series | np.ndarray] = {
        "variant_rate": (f["variant_r"] + enc.k * category_rate) / (f["variant_n"] + enc.k),
        "variant_log_n": np.log1p(f["variant_n"]),
        "product_rate": (f["product_r"] + enc.k * category_rate) / (f["product_n"] + enc.k),
        "product_log_n": np.log1p(f["product_n"]),
        "category_rate": category_rate,
        "category_code": _codes(f["category"], enc.categories),
        "log_price": np.log1p(f["price_cents"] / 100.0),
        "size_range_code": _codes(f["size_range"].fillna(NO_SIZE), enc.size_ranges),
        "order_lines": f["order_lines"],
        "order_same_product": f["order_same_product"],
    }
    if any(name in CUSTOMER_HISTORY_FEATURES for name in features):
        columns["customer_rate"] = (f["customer_r"] + K_CUSTOMER * store_rate) / (
            f["customer_n"] + K_CUSTOMER
        )
        columns["customer_log_n"] = np.log1p(f["customer_n"])
    out = pd.DataFrame({name: columns[name] for name in features}, index=frame.index)
    return out.astype(np.float64)
