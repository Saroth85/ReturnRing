"""features/clusters: clusters of linked accounts and their features (US-203, spec 8.3).

Clusters are the connected components of the links with weight >= 0.8 that no operator disputed;
a component of more than 20 accounts is split with Louvain (networkx, fixed seed), recursively,
into connected parts. A component Louvain cannot split (a clique, for example a card used by 30
accounts) stays whole: it is reported, never cut at random. An account with no such link is alone:
its cluster_id is null, its cluster_size 1, and its features are computed on itself with the same
definitions, so a model sees one scale.

A cluster says that accounts are linked, never that they are the same person (rule 11), and it is
not a judgement: the features below are what tell a family (distinct cards, old accounts, no
repeated coupons, normal returns) from a promotion ring. Cluster ids are opaque: "CL-" and the
first 12 hex digits of the sha256 of the sorted member ids, so the same members always give the
same id, and no ground-truth ring or family id can appear (rule 12).

Features, per cluster, from the store as known at the decision time:

- cluster_size: accounts;
- shared_strong_ids: card, e-mail and device values used by at least two of its accounts;
- strong_id_share: shared_strong_ids over every value used by at least two of its accounts
  (0 when none);
- n_distinct_cards: card values used by its accounts;
- creation_spread_days: days between the first and the last account creation;
- welcome_coupons: welcome coupons redeemed by its accounts; welcome_coupons_per_account;
- cluster_return_rate: delivered lines returned on the withdrawal path, over delivered lines not
  returned on the guarantee path (a defect is not behaviour, rule 4); null before any such line.

No price, amount or value enters (customer value never feeds risk, rule 6), and no forbidden
feature (CLUSTER_FEATURES is checked by tests with contracts.parquet.forbidden_features).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import networkx as nx
import polars as pl
import pyarrow as pa

from contracts.parquet import SCHEMAS
from pipeline.graph.snapshot import US_PER_DAY, StoreSnapshot
from pipeline.graph.weights import STRONG_KINDS, WEIGHT_TOLERANCE, GraphParams

CLUSTER_SCHEMA = SCHEMAS["features/clusters"]
CLUSTER_FEATURES = (
    "cluster_size",
    "shared_strong_ids",
    "welcome_coupons",
    "n_distinct_cards",
    "creation_spread_days",
    "welcome_coupons_per_account",
    "cluster_return_rate",
    "strong_id_share",
)
_TS = pl.Datetime("us", "UTC")


def cluster_id(members: Iterable[str]) -> str:
    """Opaque and stable: the same members give the same id, whatever their order."""
    digest = hashlib.sha256("\n".join(sorted(members)).encode()).hexdigest()
    return f"CL-{digest[:12]}"


def cluster_edges(links: pl.DataFrame, params: GraphParams) -> pl.DataFrame:
    """customer_a, customer_b, w: the links that join clusters (not disputed, strong enough)."""
    column = params.weight_column
    return (
        links.filter(~pl.col("disputed") & (pl.col(column) >= params.threshold - WEIGHT_TOLERANCE))
        .select("customer_a", "customer_b", pl.col(column).alias("w"))
        .sort("customer_a", "customer_b")
    )


def _graph(nodes: Iterable[str], edges: Iterable[tuple[str, str, float]]) -> nx.Graph:
    """Nodes and edges inserted in sorted order: networkx iterates in insertion order, so Louvain
    with a fixed seed gives the same communities on every run."""
    graph = nx.Graph()
    graph.add_nodes_from(sorted(nodes))
    graph.add_weighted_edges_from(sorted(edges), weight="w")
    return graph


def _split(graph: nx.Graph, params: GraphParams) -> list[frozenset[str]]:
    if graph.number_of_nodes() <= params.max_cluster_size:
        return [frozenset(graph.nodes)]
    fresh = _graph(
        graph.nodes, ((min(a, b), max(a, b), d["w"]) for a, b, d in graph.edges(data=True))
    )
    parts = nx.community.louvain_communities(fresh, weight="w", seed=params.seed)
    if len(parts) < 2:  # Louvain finds no better partition: keep the component whole
        return [frozenset(fresh.nodes)]
    out: list[frozenset[str]] = []
    for part in parts:
        for piece in nx.connected_components(fresh.subgraph(part)):
            out.extend(_split(fresh.subgraph(piece), params))
    return out


def communities(edges: pl.DataFrame, params: GraphParams) -> list[frozenset[str]]:
    """The clusters (two accounts or more) of the cluster edges, sorted by their first member."""
    graph = _graph(
        set(edges["customer_a"]) | set(edges["customer_b"]),
        edges.select("customer_a", "customer_b", "w").iter_rows(),
    )
    out: list[frozenset[str]] = []
    for component in nx.connected_components(graph):
        out.extend(_split(graph.subgraph(component), params))
    return sorted(out, key=min)


def membership(customers: Iterable[str], groups: list[frozenset[str]]) -> pl.DataFrame:
    """customer_id, cluster_id (null when alone), cluster_size, group (the key of its cluster:
    the cluster id, or its own id when alone)."""
    of: dict[str, tuple[str, int]] = {}
    for members in groups:
        cid = cluster_id(members)
        for m in members:
            of[m] = (cid, len(members))
    ids = sorted(set(customers))
    return pl.DataFrame(
        {
            "customer_id": ids,
            "cluster_id": [of[c][0] if c in of else None for c in ids],
            "cluster_size": [of[c][1] if c in of else 1 for c in ids],
        },
        schema={"customer_id": pl.String, "cluster_id": pl.String, "cluster_size": pl.Int32},
    ).with_columns(pl.coalesce("cluster_id", "customer_id").alias("group"))


def cluster_features(members: pl.DataFrame, snapshot: StoreSnapshot) -> pl.DataFrame:
    """The features/clusters rows of `members` (from membership), sorted by customer_id."""
    m = members.select("customer_id", "group")
    sizes = members.group_by("group").agg(pl.col("cluster_size").first())
    created = (
        snapshot.customers.join(m, on="customer_id")
        .group_by("group")
        .agg(
            (
                (pl.col("created_at").max() - pl.col("created_at").min()).dt.total_microseconds()
                / US_PER_DAY
            ).alias("creation_spread_days")
        )
    )
    ids = snapshot.identifiers.join(m, on="customer_id")
    cards = (
        ids.filter(pl.col("kind") == "card")
        .group_by("group")
        .agg(pl.col("value_hash").n_unique().alias("n_distinct_cards"))
    )
    shared = (
        ids.group_by("group", "kind", "value_hash")
        .agg(pl.col("customer_id").n_unique().alias("users"))
        .filter(pl.col("users") >= 2)
        .group_by("group")
        .agg(
            pl.len().alias("shared_ids"),
            pl.col("kind").is_in(STRONG_KINDS).sum().alias("shared_strong_ids"),
        )
    )
    welcome = (
        snapshot.welcome_redemptions.join(m, on="customer_id")
        .group_by("group")
        .agg(pl.len().alias("welcome_coupons"))
    )
    lines = (
        snapshot.lines.join(m, on="customer_id")
        .group_by("group")
        .agg(
            pl.col("withdrawal_return").sum().alias("returned"),
            (~pl.col("guarantee_return")).sum().alias("base"),
        )
    )
    counts = ("n_distinct_cards", "shared_ids", "shared_strong_ids", "welcome_coupons")
    per_group = (
        sizes.join(created, on="group", how="left")
        .join(cards, on="group", how="left")
        .join(shared, on="group", how="left")
        .join(welcome, on="group", how="left")
        .join(lines, on="group", how="left")
        .with_columns(
            *(pl.col(c).fill_null(0).cast(pl.Int32) for c in counts),
            pl.col("creation_spread_days").fill_null(0.0),
            pl.col("returned").fill_null(0).cast(pl.Float64),
            pl.col("base").fill_null(0).cast(pl.Float64),
        )
        .with_columns(
            (pl.col("welcome_coupons") / pl.col("cluster_size")).alias(
                "welcome_coupons_per_account"
            ),
            pl.when(pl.col("base") > 0)
            .then(pl.col("returned") / pl.col("base"))
            .otherwise(None)
            .alias("cluster_return_rate"),
            pl.when(pl.col("shared_ids") > 0)
            .then(pl.col("shared_strong_ids") / pl.col("shared_ids"))
            .otherwise(0.0)
            .alias("strong_id_share"),
        )
        .drop("cluster_size")
    )
    return (
        members.join(per_group, on="group")
        .with_columns(pl.lit(snapshot.as_of, dtype=_TS).alias("as_of"))
        .select(CLUSTER_SCHEMA.names)
        .sort("customer_id")
    )


def build_clusters(
    links: pl.DataFrame,
    snapshot: StoreSnapshot,
    params: GraphParams | None = None,
    customers: Iterable[str] | None = None,
) -> pl.DataFrame:
    """features/clusters at snapshot.as_of for `customers` (default: every account known then).

    With a subset, only the links between its accounts count: pass a whole component (closed
    under cluster links) to get the same rows as a full build."""
    params = params or GraphParams()
    who = sorted(set(snapshot.customers["customer_id"] if customers is None else customers))
    keep = pl.DataFrame({"customer_id": who}, schema={"customer_id": pl.String})
    edges = cluster_edges(links, params)
    edges = edges.join(
        keep.rename({"customer_id": "customer_a"}), on="customer_a", how="semi"
    ).join(keep.rename({"customer_id": "customer_b"}), on="customer_b", how="semi")
    return cluster_features(membership(who, communities(edges, params)), snapshot)


def clusters_table(clusters: pl.DataFrame) -> pa.Table:
    """The rows as a pyarrow table with the exact features/clusters contract schema."""
    return clusters.select(CLUSTER_SCHEMA.names).to_arrow().cast(CLUSTER_SCHEMA)
