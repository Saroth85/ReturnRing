"""Per-purchase features on ASOS GraphReturns under the two protocols of ADR-10 (US-201).

One row per event (a purchase of a variant by a customer, labelled returned or kept). The customer
and variant rows are joined by split with left joins: a missing row gives explicit nulls (NaN in
the matrices) and has_customer_features / has_variant_features = 0. Product attributes are taken
from the variant table of the event's own split, since a few variants shared by the two splits
differ in their static attributes. Hashes and the descriptive-only columns (gender, country, year
of birth: rule 6, ADR-11) are dropped right after the join and never reach a matrix.

Protocol A (paper): the node features of the paper, minus the forbidden ones. Every aggregate is
computed over a window that contains the labelled purchase, so it holds the label (rule 7).

Protocol B (decision time): leave-one-out counts, the customer's and the variant's sales and
returns excluding the event itself (sales - 1, returns - is_returned), plus the static product
attributes; no rate and no reason share. A leave-one-out count larger than its sales count
(aggregates that do not hold the event) is clipped and counted.

Selection by the event's own return. Every customer row and every variant row of the dataset has at
least one return: the dataset keeps only nodes with a return. A row whose only return is the event
itself (leave-one-out returns 0) exists because the event was returned, so for these events the
leave-one-out count reveals the label (measured on ASOS: every one whose row's counts hold the
event is returned; the exceptions are rows with clipped, inconsistent counts). The decision-time
population leaves them out, for training, calibration and evaluation of protocol B; every model is
still reported on the full test set too.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
import polars as pl

from contracts.parquet import ASOS_REASON_CODES, forbidden_features

Protocol = Literal["A", "B"]
PROTOCOLS: tuple[Protocol, ...] = ("A", "B")
PROTOCOL_LABELS: dict[Protocol, str] = {
    "A": (
        "Paper protocol: features computed over the labelled window, not available at decision time"
    ),
    "B": "Decision-time protocol",
}

SHARES = tuple(f"reason_share_{code}" for code in ASOS_REASON_CODES)
_AGGREGATES = ("sales", "returns", "return_rate", *SHARES)
CUSTOMER_AGGREGATES = tuple(f"customer_{c}" for c in _AGGREGATES)
VARIANT_AGGREGATES = tuple(f"variant_{c}" for c in _AGGREGATES)
PRODUCT_NUMERIC = ("avg_price_gbp", "avg_discount")
PRODUCT_CATEGORIES = ("product_type", "brand")
FLAGS = ("has_customer_features", "has_variant_features")
LOO_COUNTS = (
    "customer_sales_loo",
    "customer_returns_loo",
    "variant_sales_loo",
    "variant_returns_loo",
)
# Words a protocol B feature may not hold: the node rates and reason shares carry the label.
_B_FORBIDDEN_WORDS = ("rate", "share", "premier")

POPULATIONS: dict[str, str] = {
    "all": "every event of the split",
    "fully_featured": "events with a customer row and a variant row",
    "decision_time": (
        "events except those whose customer or variant row exists only because of their own "
        "return (leave-one-out returns 0)"
    ),
    "decision_time_fully_featured": "decision-time events with a customer row and a variant row",
}


class FeatureError(ValueError):
    """Inputs or feature lists that break a rule of the propensity features."""


# ---- Join ---------------------------------------------------------------------------------------


def _unique(frame: pl.DataFrame, keys: list[str], what: str) -> None:
    if frame.select(keys).is_duplicated().any():
        raise FeatureError(f"{what} rows are not unique on {keys}: the join would repeat events")


def join_events(
    events: pl.DataFrame, customers: pl.DataFrame, variants: pl.DataFrame
) -> pl.DataFrame:
    """One row per event, in event order: label, flags, node features, leave-one-out counts and the
    population masks. No hash and no descriptive-only column survives the join."""
    _unique(customers, ["split", "customer_hash"], "customer")
    _unique(variants, ["split", "variant_hash"], "variant")
    cust = customers.select(
        "split",
        "customer_hash",
        pl.lit(True).alias("has_customer_features"),
        *(pl.col(c).alias(f"customer_{c}") for c in _AGGREGATES),
        pl.col("premier").alias("customer_premier"),
    )
    var = variants.select(
        "split",
        "variant_hash",
        pl.lit(True).alias("has_variant_features"),
        *(pl.col(c).alias(f"variant_{c}") for c in _AGGREGATES),
        *PRODUCT_CATEGORIES,
        *PRODUCT_NUMERIC,
    )
    joined = (
        events.select("split", "customer_hash", "variant_hash", "is_returned")
        .join(cust, on=["split", "customer_hash"], how="left", maintain_order="left")
        .join(var, on=["split", "variant_hash"], how="left", maintain_order="left")
        .drop("customer_hash", "variant_hash")
        .with_columns(pl.col(*FLAGS).fill_null(False))
    )
    label = pl.col("is_returned").cast(pl.Int64)
    loo: list[pl.Expr] = []
    for node in ("customer", "variant"):
        sales, returns = pl.col(f"{node}_sales"), pl.col(f"{node}_returns")
        # max/min_horizontal skip nulls (max(null, 0) is 0): gate on the row being present, so a
        # missing row keeps null counts.
        present = pl.col(f"has_{node}_features")
        sales_loo = pl.when(present).then(pl.max_horizontal(sales - 1, pl.lit(0)))
        returns_loo = pl.when(present).then(
            pl.min_horizontal(pl.max_horizontal(returns - label, pl.lit(0)), sales_loo)
        )
        loo += [
            sales_loo.alias(f"{node}_sales_loo"),
            returns_loo.alias(f"{node}_returns_loo"),
            ((sales - 1 != sales_loo) | (returns - label != returns_loo))
            .fill_null(False)
            .alias(f"{node}_loo_clipped"),
        ]
    joined = joined.with_columns(loo)
    own = (pl.col("has_customer_features") & (pl.col("customer_returns_loo") == 0)) | (
        pl.col("has_variant_features") & (pl.col("variant_returns_loo") == 0)
    )
    fully = pl.col("has_customer_features") & pl.col("has_variant_features")
    return joined.with_columns(
        fully.alias("fully_featured"), own.fill_null(False).alias("selected_by_own_return")
    )


def population_masks(frame: pl.DataFrame) -> dict[str, npt.NDArray[np.bool_]]:
    """The boolean mask of every population of POPULATIONS over the rows of `frame`."""
    fully = frame["fully_featured"].to_numpy().astype(bool)
    decision = ~frame["selected_by_own_return"].to_numpy().astype(bool)
    masks = {
        "all": np.ones(frame.height, dtype=bool),
        "fully_featured": fully,
        "decision_time": decision,
        "decision_time_fully_featured": fully & decision,
    }
    assert set(masks) == set(POPULATIONS)
    return masks


# ---- Feature lists ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Vocabulary:
    """Categories one-hot encoded, learnt from the train split's variant rows (sorted)."""

    product_type: tuple[str, ...]
    brand: tuple[str, ...]

    @classmethod
    def from_variants(cls, variants: pl.DataFrame) -> Vocabulary:
        def values(column: str) -> tuple[str, ...]:
            found = variants.get_column(column).drop_nulls().unique().to_list()
            return tuple(sorted(str(v) for v in found))

        return cls(values("product_type"), values("brand"))

    def columns(self) -> list[tuple[str, str, str]]:
        """(feature name, source column, category) of every one-hot column, in order."""
        out: list[tuple[str, str, str]] = []
        for column in PRODUCT_CATEGORIES:
            names: set[str] = set()
            for value in getattr(self, column):
                name = f"{column}_{_slug(value)}"
                if name in names:
                    raise FeatureError(f"two {column} values give the feature name {name}")
                names.add(name)
                out.append((name, column, value))
        return out


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "blank"


def feature_names(protocol: Protocol, vocabulary: Vocabulary) -> list[str]:
    """The ordered feature list of a protocol; checked by check_feature_names."""
    one_hot = [name for name, _, _ in vocabulary.columns()]
    if protocol == "A":
        names = [
            *CUSTOMER_AGGREGATES,
            "customer_premier",
            *VARIANT_AGGREGATES,
            *PRODUCT_NUMERIC,
            *one_hot,
            *FLAGS,
        ]
    elif protocol == "B":
        names = [*LOO_COUNTS, *PRODUCT_NUMERIC, *one_hot, *FLAGS]
    else:
        raise FeatureError(f"unknown protocol {protocol!r}")
    check_feature_names(protocol, names)
    return names


def check_feature_names(protocol: Protocol, names: Sequence[str]) -> None:
    """Fail closed: no forbidden feature in any model (rule 6, FAIR-01), and under protocol B no
    rate, reason share or other node aggregate that holds the event's own label (ADR-10)."""
    forbidden = forbidden_features(names)
    if forbidden:
        raise FeatureError(f"forbidden features in protocol {protocol}: {forbidden}")
    if protocol == "B":
        leaky = [n for n in names if any(w in n.split("_") for w in _B_FORBIDDEN_WORDS)] + [
            n for n in names if n in CUSTOMER_AGGREGATES or n in VARIANT_AGGREGATES
        ]
        if leaky:
            raise FeatureError(f"protocol B may not use rates or aggregates: {sorted(set(leaky))}")
    if len(set(names)) != len(names):
        raise FeatureError("duplicate feature names")


def feature_matrix(
    frame: pl.DataFrame, protocol: Protocol, vocabulary: Vocabulary
) -> tuple[npt.NDArray[np.float32], list[str]]:
    """The float32 matrix of `frame` under `protocol`; null and unknown values are NaN, and the
    one-hot columns of an event without a variant row are NaN too."""
    names = feature_names(protocol, vocabulary)
    one_hot = {name: (column, value) for name, column, value in vocabulary.columns()}
    exprs: list[pl.Expr] = []
    for name in names:
        expr: pl.Expr
        if name in one_hot:
            column, value = one_hot[name]
            expr = pl.when(pl.col("has_variant_features")).then(pl.col(column) == value)
        else:
            expr = pl.col(name)
        exprs.append(expr.cast(pl.Float32).alias(name))
    matrix = frame.select(exprs).to_numpy().astype(np.float32, copy=False)
    return np.ascontiguousarray(matrix), names


def unknown_categories(frame: pl.DataFrame, vocabulary: Vocabulary) -> dict[str, int]:
    """Events whose variant row has a category the vocabulary does not hold (one-hot all zero)."""
    out: dict[str, int] = {}
    for column in PRODUCT_CATEGORIES:
        known = list(getattr(vocabulary, column))
        unknown = pl.col(column).is_not_null() & ~pl.col(column).is_in(known)
        out[column] = int(frame.select(unknown.sum()).item())
    return out


def validation_mask(n: int, fraction: float, seed: int) -> npt.NDArray[np.bool_]:
    """A seeded random `fraction` of n events (at least one, never all): early stopping,
    calibration, thresholds and the choice of the baseline rule use it; models fit on the rest."""
    if n < 2:
        raise FeatureError("at least two training events are needed for a validation split")
    if not 0.0 < fraction < 1.0:
        raise FeatureError(f"validation fraction must be in (0, 1), not {fraction}")
    size = min(max(1, round(n * fraction)), n - 1)
    mask = np.zeros(n, dtype=bool)
    mask[np.random.default_rng(seed).permutation(n)[:size]] = True
    return mask
