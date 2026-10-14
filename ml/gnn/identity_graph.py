"""The customer-identifier graph of a store snapshot and its node features (A4; no torch).

Built from pipeline.graph.snapshot.StoreSnapshot only: what the store knew at the decision time
(rule 7), never the ground truth (rule 12). Two node types:

- customer: every account known at as_of, in customer_id order;
- identifier: every identifier value (kind and salted hash) used by at least one of them. The
  hashes are node keys in memory only: no hash, value or kind of a single value is ever written.

One undirected edge per (account, value) use, two relations (uses, used_by). The node features
describe the graph and the account's own decision-time events, with no price, amount or value
(customer value never feeds risk, rule 6) and no forbidden feature (checked on import):

- customer: values used per identifier kind, tenure in days, delivered lines, the share of its
  delivered lines returned on the withdrawal path (guarantee returns left out, rule 4) and its
  flag, welcome coupons redeemed;
- identifier: the kind (one-hot by position in IDENTIFIER_KINDS) and its spec 8.3 weight, and the
  number of accounts using the value (d_k of spec 8.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import polars as pl

from contracts.parquet import IDENTIFIER_KINDS, forbidden_features, label_or_key_features
from ml.gnn.graph import I64, HeteroGraph, bipartite, degrees
from ml.gnn.scaling import F32, Standardizer
from pipeline.graph.snapshot import US_PER_DAY, StoreSnapshot
from pipeline.graph.weights import KIND_WEIGHTS

CUSTOMER: Final = "customer"
IDENTIFIER: Final = "identifier"
USES: Final = "uses"
USED_BY: Final = "used_by"
CUSTOMER_NODE_FEATURES: Final = (
    *(f"n_{kind}_values" for kind in IDENTIFIER_KINDS),
    "tenure_days",
    "n_delivered_lines",
    "withdrawal_return_share",
    "has_delivered_lines",
    "n_welcome_coupons_used",
)
IDENTIFIER_NODE_FEATURES: Final = (
    *(f"kind_index_{i}" for i in range(len(IDENTIFIER_KINDS))),
    "kind_weight",
    "n_sharers",
)
_LOG: Final = frozenset(
    {
        *(f"n_{kind}_values" for kind in IDENTIFIER_KINDS),
        "tenure_days",
        "n_delivered_lines",
        "n_welcome_coupons_used",
        "n_sharers",
    }
)
for _names in (CUSTOMER_NODE_FEATURES, IDENTIFIER_NODE_FEATURES):
    _bad = forbidden_features(_names) + label_or_key_features(_names)
    if _bad:  # pragma: no cover - a guard on the constants
        raise RuntimeError(f"forbidden encoder inputs (rule 6): {_bad}")


def is_log_feature(name: str) -> bool:
    return name in _LOG


@dataclass(frozen=True)
class IdentityGraph:
    as_of: datetime
    customer_ids: tuple[str, ...]  # node order
    graph: HeteroGraph
    x_customer: F32
    x_identifier: F32
    identifier_kind: I64  # kind position of each identifier node
    edge_customer: I64  # one entry per (account, value) use
    edge_identifier: I64

    @property
    def n_edges(self) -> int:
        return int(self.edge_customer.size)

    def summary(self) -> dict[str, Any]:
        sharers = degrees(self.edge_identifier, self.graph.num_nodes[IDENTIFIER])
        return {
            "as_of": self.as_of.isoformat(),
            "customers": self.graph.num_nodes[CUSTOMER],
            "identifier_values": self.graph.num_nodes[IDENTIFIER],
            "uses": self.n_edges,
            "values_shared_by_two_or_more": int((sharers >= 2).sum()),
            "largest_sharers": int(sharers.max()) if sharers.size else 0,
            "customers_without_identifier": int(
                (degrees(self.edge_customer, self.graph.num_nodes[CUSTOMER]) == 0).sum()
            ),
        }


def _counts(
    frame: pl.DataFrame, key: str, n: int, index: dict[str, int]
) -> npt.NDArray[np.float64]:
    out = np.zeros(n, dtype=np.float64)
    if frame.height:
        rows = frame.group_by(key).len()
        idx = np.array([index[c] for c in rows[key].to_list()], dtype=np.int64)
        out[idx] = rows["len"].to_numpy().astype(np.float64)
    return out


def identity_graph(snapshot: StoreSnapshot) -> IdentityGraph:
    """The graph and raw node features of the store known at snapshot.as_of."""
    customers = snapshot.customers.sort("customer_id")
    ids = tuple(str(c) for c in customers["customer_id"].to_list())
    n_c = len(ids)
    index = {c: i for i, c in enumerate(ids)}
    uses = (
        snapshot.identifiers.select("customer_id", "kind", "value_hash")
        .unique()
        .sort("customer_id", "kind", "value_hash")
    )
    unknown = set(uses["customer_id"].to_list()) - set(index)
    if unknown:
        raise ValueError(f"{len(unknown)} identifier uses of accounts not in the snapshot")
    kinds = {k: i for i, k in enumerate(IDENTIFIER_KINDS)}
    values = uses.select("kind", "value_hash").unique().sort("kind", "value_hash")
    values = values.with_row_index("node")
    edges = uses.join(values, on=["kind", "value_hash"], how="left", maintain_order="left")
    edge_c = np.array([index[c] for c in edges["customer_id"].to_list()], dtype=np.int64)
    edge_i = edges["node"].to_numpy().astype(np.int64)
    n_i = values.height
    graph = bipartite(CUSTOMER, IDENTIFIER, USES, USED_BY, edge_c, edge_i, n_c, n_i)

    # customer features
    x_c = np.zeros((n_c, len(CUSTOMER_NODE_FEATURES)), dtype=np.float64)
    for j, kind in enumerate(IDENTIFIER_KINDS):
        x_c[:, j] = _counts(uses.filter(pl.col("kind") == kind), "customer_id", n_c, index)
    created = customers["created_at"].dt.epoch("us").to_numpy().astype(np.float64)
    as_of_us = snapshot.as_of.timestamp() * 1e6
    j = len(IDENTIFIER_KINDS)
    x_c[:, j] = np.maximum(as_of_us - created, 0.0) / US_PER_DAY
    lines = snapshot.lines.filter(~pl.col("guarantee_return"))
    delivered = _counts(snapshot.lines, "customer_id", n_c, index)
    judged = _counts(lines, "customer_id", n_c, index)
    returned = _counts(lines.filter(pl.col("withdrawal_return")), "customer_id", n_c, index)
    x_c[:, j + 1] = delivered
    with np.errstate(invalid="ignore", divide="ignore"):
        x_c[:, j + 2] = np.where(judged > 0, returned / judged, np.nan)
    x_c[:, j + 3] = (judged > 0).astype(np.float64)
    x_c[:, j + 4] = _counts(snapshot.welcome_redemptions, "customer_id", n_c, index)

    # identifier features
    kind_of = np.array([kinds[k] for k in values["kind"].to_list()], dtype=np.int64)
    x_i = np.zeros((n_i, len(IDENTIFIER_NODE_FEATURES)), dtype=np.float64)
    if n_i:
        x_i[np.arange(n_i), kind_of] = 1.0
        weights = np.array([KIND_WEIGHTS[k] for k in IDENTIFIER_KINDS])
        x_i[:, len(IDENTIFIER_KINDS)] = weights[kind_of]
        x_i[:, len(IDENTIFIER_KINDS) + 1] = degrees(edge_i, n_i)
    return IdentityGraph(
        as_of=snapshot.as_of,
        customer_ids=ids,
        graph=graph,
        x_customer=x_c.astype(np.float32),
        x_identifier=x_i.astype(np.float32),
        identifier_kind=kind_of,
        edge_customer=edge_c,
        edge_identifier=edge_i,
    )


def standardizers(graph: IdentityGraph) -> dict[str, Standardizer]:
    """Fitted on the graph the encoder trains on; stored with the encoder."""
    return {
        CUSTOMER: Standardizer.fit(graph.x_customer, CUSTOMER_NODE_FEATURES, is_log_feature),
        IDENTIFIER: Standardizer.fit(graph.x_identifier, IDENTIFIER_NODE_FEATURES, is_log_feature),
    }


def months_before(moment: datetime, months: int) -> datetime:
    """The same calendar arithmetic as ml.risk.run.months_before (the risk job's train end)."""
    total = moment.year * 12 + (moment.month - 1) - months
    return moment.replace(year=total // 12, month=total % 12 + 1)
