"""Build features/links and features/clusters at a decision time, and dispute a link (US-310).

    python -m pipeline.graph --store data [--out data] [--as-of 2026-10-01T00:00:00Z]
        [--disputed disputed.json]

reads <store>/store/*.parquet as known at as_of (default: the day after the store period ends,
00:00 UTC), writes <out>/features/links.parquet and <out>/features/clusters.parquet with
contracts.io.write_table, and prints a JSON summary. It never reads the ground truth (rule 12);
the offline evaluation is pipeline.graph.evaluate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import networkx as nx
import polars as pl

from contracts.io import Provenance, read_table, write_table
from pipeline.graph.clusters import build_clusters, cluster_edges, clusters_table
from pipeline.graph.links import build_links, hub_values, links_table
from pipeline.graph.snapshot import StoreSnapshot, check_as_of, load_snapshot
from pipeline.graph.weights import GraphParams

ORIGIN = "ReturnRing account graph (pipeline.graph, spec 8.3)"


@dataclass(frozen=True)
class GraphResult:
    """features/links and features/clusters rows at as_of, built with params."""

    as_of: datetime
    params: GraphParams
    links: pl.DataFrame
    clusters: pl.DataFrame


@dataclass(frozen=True)
class DisputeOutcome:
    """A dispute (US-310): the new result, the accounts whose cluster was recomputed, and their
    features/clusters rows before and after, for the before-and-after view and the audit log."""

    result: GraphResult
    affected: tuple[str, ...]
    before: pl.DataFrame
    after: pl.DataFrame


def build_graph(
    snapshot: StoreSnapshot,
    params: GraphParams | None = None,
    disputed: Iterable[tuple[str, str]] = (),
) -> GraphResult:
    """Links and clusters from the store known at snapshot.as_of. disputed: pairs an operator
    disputed, kept out of the clusters (a nightly rebuild passes the disputes of the audit log)."""
    params = params or GraphParams()
    links = build_links(snapshot.identifiers, snapshot.as_of, params, disputed)
    clusters = build_clusters(links, snapshot, params)
    return GraphResult(snapshot.as_of, params, links, clusters)


def dispute_link(
    result: GraphResult, snapshot: StoreSnapshot, customer_a: str, customer_b: str
) -> DisputeOutcome:
    """Mark the link between two accounts as disputed and recompute the cluster it was in.

    The accounts recomputed are the connected component of the cluster links that held the two
    accounts, so the result equals a full rebuild with the dispute (a Louvain split depends on the
    whole component). Disputing a disputed link changes nothing. KeyError if there is no link."""
    if customer_a == customer_b:
        raise ValueError("a link joins two different accounts")
    if snapshot.as_of != result.as_of:
        raise ValueError("the snapshot and the result have different as_of")
    a, b = sorted((customer_a, customer_b))
    pair = (pl.col("customer_a") == a) & (pl.col("customer_b") == b)
    if result.links.filter(pair).height != 1:
        raise KeyError(f"no link between {a} and {b} at {result.as_of.isoformat()}")
    graph = nx.Graph()
    graph.add_edges_from(
        cluster_edges(result.links, result.params).select("customer_a", "customer_b").iter_rows()
    )
    affected = {a, b}
    for customer in (a, b):
        if customer in graph:
            affected |= nx.node_connected_component(graph, customer)
    links = result.links.with_columns(
        pl.when(pair).then(True).otherwise(pl.col("disputed")).alias("disputed")
    )
    after = build_clusters(links, snapshot, result.params, customers=affected)
    inside = pl.col("customer_id").is_in(sorted(affected))
    before = result.clusters.filter(inside)
    clusters = pl.concat([result.clusters.filter(~inside), after]).sort("customer_id")
    return DisputeOutcome(
        GraphResult(result.as_of, result.params, links, clusters),
        tuple(sorted(affected)),
        before,
        after,
    )


def graph_provenance(snapshot: StoreSnapshot, params: GraphParams) -> Provenance:
    """Origin names the method, its parameters, the decision time and the store it read; the
    nature is the store's (synthetic for Navigli Fashion, rule 11)."""
    store = snapshot.provenance["store/identifiers"]
    start, end = snapshot.period()
    # A short origin (ADR-13 amendment 5); the lineage ends with the chain of the store it read.
    return store.derive(
        f"{ORIGIN}: {params.describe()}; as of {snapshot.as_of.isoformat()}; computed from the "
        "store of the lineage",
        start,
        end,
    )


def write_graph(result: GraphResult, out: Path | str, provenance: Provenance) -> list[Path]:
    """Write features/links and features/clusters under out (contracts.io, validated)."""
    return [
        write_table("features/links", links_table(result.links), out, provenance),
        write_table("features/clusters", clusters_table(result.clusters), out, provenance),
    ]


def default_as_of(store: Path | str) -> datetime:
    """The extraction instant of a store: 00:00 UTC on the day after its period ends."""
    _, provenance = read_table("store/identifiers", store)
    return datetime.combine(provenance.period_end + timedelta(days=1), time(0), tzinfo=UTC)


def summarize(
    result: GraphResult, snapshot: StoreSnapshot, provenance: Provenance
) -> dict[str, Any]:
    """Counts of what was built (nature and origin as in the provenance)."""
    hubs = hub_values(snapshot.identifiers, result.params)
    sizes = (
        result.clusters.filter(pl.col("cluster_id").is_not_null())
        .group_by("cluster_id")
        .agg(pl.len().alias("size"))["size"]
    )
    return {
        "as_of": result.as_of.isoformat(),
        "params": result.params.describe(),
        "nature": provenance.nature,
        "period": {
            "start": provenance.period_start.isoformat(),
            "end": provenance.period_end.isoformat(),
        },
        "accounts": snapshot.customers.height,
        "identifier_values_used": snapshot.identifiers.height,
        "links": result.links.height,
        "cluster_links": cluster_edges(result.links, result.params).height,
        "disputed_links": int(result.links["disputed"].sum()),
        "clusters": len(sizes),
        "accounts_in_clusters": int(sizes.sum()) if len(sizes) else 0,
        "largest_cluster": max(sizes.to_list(), default=0),
        "hubs": {
            "values": hubs.height,
            "by_kind": dict(sorted(hubs.group_by("kind").len().iter_rows())),
            "largest": max(hubs["sharers"].to_list(), default=0),
            "pairs_not_built": int(hubs["pairs"].sum()),
        },
    }


def _disputed(path: Path | None) -> list[tuple[str, str]]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(c, str) for c in p) for p in data
    ):
        raise ValueError(f"{path}: expected a JSON list of [customer_a, customer_b] pairs")
    return [(p[0], p[1]) for p in data]


def parse_instant(text: str) -> datetime:
    """An ISO 8601 instant with a time zone (a trailing Z means UTC), for --as-of."""
    try:
        return check_as_of(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a timezone-aware ISO instant: {text!r}") from exc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.graph",
        description="Build features/links and features/clusters (US-203, spec 8.3).",
    )
    p.add_argument("--store", type=Path, default=Path("data"), help="root holding store/")
    p.add_argument("--out", type=Path, help="root for features/ (default: --store)")
    p.add_argument("--as-of", type=parse_instant, help="decision time, ISO 8601 with a time zone")
    p.add_argument("--disputed", type=Path, help="JSON list of disputed [a, b] pairs (US-310)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    as_of = args.as_of or default_as_of(args.store)
    snapshot = load_snapshot(args.store, as_of)
    params = GraphParams()
    result = build_graph(snapshot, params, _disputed(args.disputed))
    provenance = graph_provenance(snapshot, params)
    paths = write_graph(result, args.out or args.store, provenance)
    summary = summarize(result, snapshot, provenance)
    summary["outputs"] = [str(p) for p in paths]
    summary["origin"] = provenance.origin
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
