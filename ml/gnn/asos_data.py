"""The ASOS purchase graph and its GraphSAGE features under the two protocols of ADR-10 (US-202).

No torch here. One graph per ASOS split: customer and variant nodes, one undirected edge per event
(a purchase), as in the paper (arXiv:2302.14096, Figure 1). The events, the joins, the populations,
the leave-one-out counts, the one-hot vocabulary and the validation split are those of
ml.propensity (US-201), so GraphSAGE and XGBoost see the same data.

Where each input goes:

- Message passing carries node features only, and no node feature holds a label under protocol B.
  A node's return count holds the label of every one of its edges, so it can never travel along
  the graph: with one layer, a customer's own count would come back to it through any neighbour.
  Protocol B node features: the node's leave-one-out sales (sales - 1, the same for every event of
  the node, a decision-time quantity), its degree in the split's graph, whether the node has a
  row, and for variants the static product attributes (price, discount, type and brand one-hot).
- The edge classifier takes the two node embeddings and, under protocol B, the event's own
  features: the 30 protocol B features of ml.propensity (leave-one-out counts of the customer and
  the variant without the event, product attributes, flags), so the returns of the node's other
  events enter per event, never along the graph.
- Protocol A (the paper's protocol, benchmarks only) puts the paper's node features on the nodes,
  return rates and reason shares included, and nothing on the edge, as in the paper.

The forbidden features (gender, country, age: rule 6, ADR-11) never reach a node: join_events drops
them, and every feature list is checked by ml.propensity.features.check_feature_names.

Features are standardised (log1p first for counts, degrees and prices) with means and deviations
fitted on the training graph; a missing value (no node row) becomes 0 after standardisation, the
mean, next to its has_*_features flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl

from ml.gnn.graph import I64, HeteroGraph, bipartite, degrees, index_keys
from ml.gnn.scaling import Standardizer
from ml.propensity.features import (
    CUSTOMER_AGGREGATES,
    PRODUCT_NUMERIC,
    VARIANT_AGGREGATES,
    FeatureError,
    Protocol,
    Vocabulary,
    check_feature_names,
    feature_matrix,
    feature_names,
    join_events,
    population_masks,
)

F32 = npt.NDArray[np.float32]
CUSTOMER, VARIANT = "customer", "variant"
BUYS, BOUGHT_BY = "buys", "bought_by"
DEGREE = {CUSTOMER: "customer_degree", VARIANT: "variant_degree"}
# Counts, degrees and prices are skewed: log1p before standardisation.
_LOG_SUFFIXES = ("_sales", "_returns", "_sales_loo", "_returns_loo", "_degree")
_LOG_NAMES = ("avg_price_gbp",)


@dataclass(frozen=True)
class FeatureSpec:
    """The ordered feature lists of one protocol: customer nodes, variant nodes, edges."""

    protocol: Protocol
    customer: tuple[str, ...]
    variant: tuple[str, ...]
    edge: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "customer_node": list(self.customer),
            "variant_node": list(self.variant),
            "edge": list(self.edge),
        }


def feature_spec(protocol: Protocol, vocabulary: Vocabulary) -> FeatureSpec:
    """The node and edge features of a protocol (see the module docstring), checked fail-closed:
    no forbidden feature, no label or key, and under protocol B no rate, share or aggregate."""
    one_hot = tuple(name for name, _, _ in vocabulary.columns())
    if protocol == "B":
        customer: tuple[str, ...] = (
            "customer_sales_loo",
            "has_customer_features",
            DEGREE[CUSTOMER],
        )
        variant = (
            "variant_sales_loo",
            *PRODUCT_NUMERIC,
            *one_hot,
            "has_variant_features",
            DEGREE[VARIANT],
        )
        edge = tuple(feature_names("B", vocabulary))
    elif protocol == "A":
        customer = (
            *CUSTOMER_AGGREGATES,
            "customer_premier",
            "has_customer_features",
            DEGREE[CUSTOMER],
        )
        variant = (
            *VARIANT_AGGREGATES,
            *PRODUCT_NUMERIC,
            *one_hot,
            "has_variant_features",
            DEGREE[VARIANT],
        )
        edge = ()
    else:
        raise FeatureError(f"unknown protocol {protocol!r}")
    for group in (customer, variant, edge):
        check_feature_names(protocol, group)
    if protocol == "B" and any(n.endswith("_returns_loo") for n in (*customer, *variant)):
        raise FeatureError("protocol B: a return count never travels along the graph")
    return FeatureSpec(protocol, customer, variant, edge)


def is_log_feature(name: str) -> bool:
    """Counts, degrees and prices: log1p before standardisation."""
    return name.endswith(_LOG_SUFFIXES) or name in _LOG_NAMES


def standardizer(x: F32, names: tuple[str, ...]) -> Standardizer:
    """The standardiser of ASOS features fitted on the rows of x."""
    return Standardizer.fit(x, names, is_log_feature)


@dataclass(frozen=True)
class SplitGraph:
    """One ASOS split as a graph: the joined events (ml.propensity.join_events, event order), the
    node index of each event, the raw node and edge feature matrices, labels and populations."""

    split: str
    frame: pl.DataFrame
    graph: HeteroGraph
    customer: I64  # node index of each event's customer
    variant: I64  # node index of each event's variant
    x_customer: F32
    x_variant: F32
    x_edge: F32
    y: npt.NDArray[np.int8]
    masks: dict[str, npt.NDArray[np.bool_]]

    @property
    def n_events(self) -> int:
        return int(self.y.size)

    def summary(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "events": self.n_events,
            "returned_share": round(float(self.y.mean()), 6) if self.n_events else None,
            "customer_nodes": self.graph.num_nodes[CUSTOMER],
            "variant_nodes": self.graph.num_nodes[VARIANT],
            "edges_per_direction": self.n_events,
            "population_events": {k: int(v.sum()) for k, v in self.masks.items()},
        }


def _node_matrix(
    first_rows: pl.DataFrame,
    names: tuple[str, ...],
    vocabulary: Vocabulary,
    degree_name: str,
    degree: npt.NDArray[np.float64],
) -> F32:
    """Node features from the joined row of each node's first event (node columns are the same
    on every event of a node within a split); the degree comes from the graph."""
    one_hot = {name: (column, value) for name, column, value in vocabulary.columns()}
    exprs: list[pl.Expr] = []
    for name in names:
        if name == degree_name:
            continue
        expr: pl.Expr
        if name in one_hot:
            column, value = one_hot[name]
            expr = pl.when(pl.col("has_variant_features")).then(pl.col(column) == value)
        else:
            expr = pl.col(name)
        exprs.append(expr.cast(pl.Float64).alias(name))
    table = first_rows.select(exprs).to_numpy().astype(np.float64)
    out = np.empty((first_rows.height, len(names)), dtype=np.float64)
    j_table = 0
    for j, name in enumerate(names):
        if name == degree_name:
            out[:, j] = degree
        else:
            out[:, j] = table[:, j_table]
            j_table += 1
    return np.ascontiguousarray(out, dtype=np.float32)


def _first_rows(index: I64, n: int) -> I64:
    """The position of the first event of each node 0..n-1."""
    nodes, first = np.unique(index, return_index=True)
    if nodes.size != n or (nodes != np.arange(n)).any():
        raise ValueError("node indices must be 0..n-1, each with an event")
    return first.astype(np.int64)


def split_graph(
    events: pl.DataFrame,
    customers: pl.DataFrame,
    variants: pl.DataFrame,
    split: str,
    spec: FeatureSpec,
    vocabulary: Vocabulary,
    keep: npt.NDArray[np.bool_] | None = None,
) -> SplitGraph:
    """The graph of one split from the raw tables (raw/asos_*), events in their file order; keep
    selects events (a customer sample) before anything is built."""
    split_events = events.filter(pl.col("split") == split)
    if keep is not None:
        if keep.shape != (split_events.height,):
            raise ValueError("keep must have one entry per event of the split")
        split_events = split_events.filter(pl.Series(keep))
    if split_events.height == 0:
        raise FeatureError(f"no {split} event")
    frame = join_events(split_events, customers, variants)
    _, c_idx = index_keys(split_events["customer_hash"].to_numpy())
    _, v_idx = index_keys(split_events["variant_hash"].to_numpy())
    n_c, n_v = int(c_idx.max()) + 1, int(v_idx.max()) + 1
    graph = bipartite(CUSTOMER, VARIANT, BUYS, BOUGHT_BY, c_idx, v_idx, n_c, n_v)
    first_c = frame[_first_rows(c_idx, n_c)]
    first_v = frame[_first_rows(v_idx, n_v)]
    x_customer = _node_matrix(
        first_c, spec.customer, vocabulary, DEGREE[CUSTOMER], degrees(c_idx, n_c)
    )
    x_variant = _node_matrix(
        first_v, spec.variant, vocabulary, DEGREE[VARIANT], degrees(v_idx, n_v)
    )
    if spec.edge:
        x_edge, names = feature_matrix(frame, spec.protocol, vocabulary)
        if tuple(names) != spec.edge:
            raise FeatureError("edge features differ from the protocol's feature list")
    else:
        x_edge = np.zeros((frame.height, 0), dtype=np.float32)
    y = frame["is_returned"].to_numpy().astype(np.int8)
    return SplitGraph(
        split=split,
        frame=frame,
        graph=graph,
        customer=c_idx,
        variant=v_idx,
        x_customer=x_customer,
        x_variant=x_variant,
        x_edge=x_edge,
        y=y,
        masks=population_masks(frame),
    )


def customer_sample(
    customer_hash: npt.ArrayLike, fraction: float, seed: int
) -> npt.NDArray[np.bool_]:
    """A seeded sample of customers, decided per customer hash (splitmix64), so a customer keeps
    all its events and the same customer is kept in both splits."""
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"sample fraction must be in (0, 1], not {fraction}")
    if fraction == 1.0:
        return np.ones(np.asarray(customer_hash).shape, dtype=bool)
    mask64 = np.uint64(0xFFFFFFFFFFFFFFFF)
    with np.errstate(over="ignore"):
        x = np.asarray(customer_hash).astype(np.int64).view(np.uint64)
        x = x ^ (np.uint64(seed) * np.uint64(0x9E3779B97F4A7C15) & mask64)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
    out: npt.NDArray[np.bool_] = (x % np.uint64(1_000_000)) < np.uint64(round(fraction * 1e6))
    return out
