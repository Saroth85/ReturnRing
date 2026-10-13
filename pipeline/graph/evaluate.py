"""Offline evaluation of the account graph: MOD-03, MOD-11 and MOD-12 (US-203, spec 8.3 and 12).

    python -m pipeline.graph.evaluate --data data [--as-of 2026-10-01T00:00:00Z]
        [--report docs/results/account-graph.md] [--json artifacts/graph/evaluation.json]

An offline evaluation job: it reads the generator's ground truth (truth/labels), which running
services never do (rule 12 as amended by ADR-13). The graph itself never sees it: it is built from
the store known at as_of exactly as in production (pipeline.graph.build), then compared with the
truth. The out-of-distribution store (US-105) is evaluated on rows of its own and never used to
choose anything (MOD-12). Every number is synthetic, measured on the generated store; the report
says so and names the store it measured. Rerunning the command on the same data rewrites the same
report: it holds no wall-clock value.

Definitions (accounts known at as_of; a "cluster" has two accounts or more):

- Ring pairs (MOD-03): a pair is predicted when its two accounts are in one cluster and at least
  one is a ring member; it is a true positive when both are in the same ring. Precision = true
  positives / predicted, recall = true positives / pairs of accounts of the same ring. Families
  are clustered on purpose (spec 8.3), so pairs of family accounts are not counted against
  precision; whether they mix with others is the MOD-11 check below.
- Ring clusters: member precision = members of the dominant ring / members, over the clusters
  holding a ring member; member recall = members in the best cluster of their ring / ring members.
  A ring is exact when one cluster holds exactly its members, whole when one cluster holds all of
  them (perhaps with others).
- Unrelated pairs (MOD-11): pairs in one cluster that are not of the same ring or the same family.
  A common address is an address value whose users belong to two groups or more (a group is a
  ring, a family, or one unrelated account): pickup points and collective addresses, which no
  truth table marks (docs/synthetic-store.md). MOD-11 for the graph: no unrelated pair is joined
  in a cluster, at a common address or anywhere, and F-ROSSI and F-ROSSI-CARD are clusters of
  exactly their accounts. Whether a cluster ends in Q3 or Q4 is checked with the risk model
  (US-204, FAIR-03).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from contracts.io import read_table
from pipeline.generator import golden
from pipeline.generator.core import scenario_roots
from pipeline.graph.build import GraphResult, default_as_of, parse_instant
from pipeline.graph.clusters import CLUSTER_FEATURES, build_clusters, cluster_edges
from pipeline.graph.links import build_links, hub_values
from pipeline.graph.snapshot import StoreSnapshot, load_snapshot
from pipeline.graph.weights import GraphParams

PRODUCT = "spec 8.3 (product)"
VARIANTS: tuple[tuple[str, GraphParams], ...] = (
    (PRODUCT, GraphParams()),
    ("clusters on weight_decayed", GraphParams(weight_column="weight_decayed")),
    ("threshold 0.6", GraphParams(threshold=0.6)),
    ("threshold 0.7", GraphParams(threshold=0.7)),
    ("threshold 0.9", GraphParams(threshold=0.9)),
)
GOLDEN_GROUPS = (
    golden.MARCO_RING,
    golden.ROSSI_FAMILY,
    golden.ROSSI_CARD_FAMILY,
    golden.OOD_HARD_FAMILY,
)
MOD03_PRECISION = 0.90
MOD03_RECALL = 0.80
TOP = 10


def _c2(n: pl.Expr) -> pl.Expr:
    return n * (n - 1) // 2


def _ratio(num: float, den: float) -> float | None:
    return num / den if den else None


def _sum(frame: pl.DataFrame, expr: pl.Expr) -> int:
    return int(frame.select(expr.sum()).item() or 0) if frame.height else 0


def _max(frame: pl.DataFrame, column: str) -> int:
    return int(frame.select(pl.col(column).max()).item() or 0) if frame.height else 0


def _stat(frame: pl.DataFrame, expr: pl.Expr) -> float | None:
    """A float statistic of a frame, None when it has no value (no row, or only nulls)."""
    value = frame.select(expr).item() if frame.height else None
    return None if value is None else float(value)


# ---- metrics on an assignment: customer_id, cluster_id, ring_id, family_id, behaviour ----------


def ring_metrics(assigned: pl.DataFrame) -> dict[str, Any]:
    """MOD-03: pairwise and cluster-level precision and recall on ring membership."""
    clustered = assigned.filter(pl.col("cluster_id").is_not_null())
    per_cluster = clustered.group_by("cluster_id").agg(
        pl.len().alias("n"), pl.col("ring_id").is_not_null().sum().alias("r")
    )
    per_cluster_ring = (
        clustered.filter(pl.col("ring_id").is_not_null())
        .group_by("cluster_id", "ring_id")
        .agg(pl.len().alias("k"))
    )
    rings = (
        assigned.filter(pl.col("ring_id").is_not_null())
        .group_by("ring_id")
        .agg(pl.len().alias("size"), pl.col("cluster_id").is_null().sum().alias("alone"))
    )
    predicted = _sum(per_cluster, _c2(pl.col("n")) - _c2(pl.col("n") - pl.col("r")))
    true_pos = _sum(per_cluster_ring, _c2(pl.col("k")))
    actual = _sum(rings, _c2(pl.col("size")))
    dominant = per_cluster_ring.group_by("cluster_id").agg(pl.col("k").max().alias("top"))
    ring_clusters = per_cluster.filter(pl.col("r") > 0).join(dominant, on="cluster_id")
    best = (
        per_cluster_ring.join(per_cluster, on="cluster_id")
        .join(rings, on="ring_id")
        .group_by("ring_id")
        .agg(
            pl.col("k").max().alias("best"),
            ((pl.col("k") == pl.col("size")) & (pl.col("n") == pl.col("size")))
            .any()
            .alias("exact"),
            (pl.col("k") == pl.col("size")).any().alias("whole"),
        )
    )
    per_ring = rings.join(best, on="ring_id", how="left").with_columns(
        pl.col("best").fill_null(0),
        pl.col("exact").fill_null(False),
        pl.col("whole").fill_null(False),
    )
    return {
        "rings": rings.height,
        "ring_members": _sum(rings, pl.col("size")),
        "pairs_predicted": predicted,
        "pairs_true_positive": true_pos,
        "pairs_actual": actual,
        "pairwise_precision": _ratio(true_pos, predicted),
        "pairwise_recall": _ratio(true_pos, actual),
        "member_precision": _ratio(
            _sum(ring_clusters, pl.col("top")), _sum(ring_clusters, pl.col("n"))
        ),
        "member_recall": _ratio(_sum(per_ring, pl.col("best")), _sum(per_ring, pl.col("size"))),
        "rings_exact": _sum(per_ring, pl.col("exact")),
        "rings_whole": _sum(per_ring, pl.col("whole")),
        "rings_without_cluster": _sum(per_ring, pl.col("best") == 0),
        "ring_members_unclustered": _sum(rings, pl.col("alone")),
    }


def family_metrics(assigned: pl.DataFrame) -> dict[str, Any]:
    """Families (shared benign) are clustered on purpose: exact, whole, or split."""
    clustered = assigned.filter(pl.col("cluster_id").is_not_null())
    sizes = clustered.group_by("cluster_id").agg(pl.len().alias("n"))
    families = (
        assigned.filter(pl.col("family_id").is_not_null())
        .group_by("family_id")
        .agg(pl.len().alias("size"), pl.col("cluster_id").is_null().sum().alias("alone"))
    )
    per = (
        clustered.filter(pl.col("family_id").is_not_null())
        .group_by("cluster_id", "family_id")
        .agg(pl.len().alias("k"))
        .join(sizes, on="cluster_id")
        .join(families, on="family_id")
        .group_by("family_id")
        .agg(
            ((pl.col("k") == pl.col("size")) & (pl.col("n") == pl.col("size")))
            .any()
            .alias("exact"),
            (pl.col("k") == pl.col("size")).any().alias("whole"),
        )
    )
    return {
        "families": families.height,
        "family_members": _sum(families, pl.col("size")),
        "families_exact": _sum(per, pl.col("exact")),
        "families_whole": _sum(per, pl.col("whole")),
        "family_members_unclustered": _sum(families, pl.col("alone")),
    }


def _with_group(assigned: pl.DataFrame) -> pl.DataFrame:
    return assigned.with_columns(pl.coalesce("ring_id", "family_id", "customer_id").alias("group"))


def mixing(assigned: pl.DataFrame) -> dict[str, Any]:
    """Pairs of unrelated accounts (not one ring, not one family) joined in a cluster."""
    clustered = _with_group(assigned).filter(pl.col("cluster_id").is_not_null())
    per_cluster = clustered.group_by("cluster_id").agg(
        pl.len().alias("n"), pl.col("group").n_unique().alias("groups")
    )
    per_group = clustered.group_by("cluster_id", "group").agg(pl.len().alias("k"))
    return {
        "clusters": per_cluster.height,
        "accounts_in_clusters": _sum(per_cluster, pl.col("n")),
        "largest_cluster": _max(per_cluster, "n"),
        "unrelated_pairs": _sum(per_cluster, _c2(pl.col("n"))) - _sum(per_group, _c2(pl.col("k"))),
        "mixed_clusters": _sum(per_cluster, pl.col("groups") > 1),
    }


def common_addresses(
    identifiers: pl.DataFrame, assigned: pl.DataFrame, hub_degree: int | None
) -> dict[str, Any]:
    """MOD-11: address values used by two groups or more (pickup points, collective addresses),
    and the unrelated pairs among their users that share a cluster. Values are never printed:
    they are pseudonymous personal data; addresses are ranked by users instead."""
    users = (
        identifiers.filter(pl.col("kind") == "address")
        .select("customer_id", "value_hash")
        .unique()
        .join(_with_group(assigned).select("customer_id", "cluster_id", "group"), on="customer_id")
    )
    per_value = users.group_by("value_hash").agg(
        pl.col("customer_id").n_unique().alias("sharers"),
        pl.col("group").n_unique().alias("groups"),
        pl.col("cluster_id").is_not_null().sum().alias("sharers_in_clusters"),
        pl.col("cluster_id").drop_nulls().n_unique().alias("clusters"),
    )
    common = per_value.filter((pl.col("sharers") >= 2) & (pl.col("groups") >= 2))
    inside = users.join(common.select("value_hash"), on="value_hash").filter(
        pl.col("cluster_id").is_not_null()
    )
    n = inside.group_by("value_hash", "cluster_id").agg(pl.len().alias("n"))
    k = inside.group_by("value_hash", "cluster_id", "group").agg(pl.len().alias("k"))
    unrelated = (
        n.with_columns(_c2(pl.col("n")).alias("pairs"))
        .group_by("value_hash")
        .agg(pl.col("pairs").sum())
        .join(
            k.with_columns(_c2(pl.col("k")).alias("same"))
            .group_by("value_hash")
            .agg(pl.col("same").sum()),
            on="value_hash",
            how="left",
        )
        .select("value_hash", (pl.col("pairs") - pl.col("same").fill_null(0)).alias("joined"))
    )
    common = (
        common.join(unrelated, on="value_hash", how="left")
        .with_columns(pl.col("joined").fill_null(0))
        .sort(["sharers", "groups", "value_hash"], descending=[True, True, False])
    )
    limit = hub_degree if hub_degree is not None else 0
    top = [
        {
            "rank": i + 1,
            "sharers": r["sharers"],
            "groups": r["groups"],
            "sharers_in_clusters": r["sharers_in_clusters"],
            "clusters": r["clusters"],
            "unrelated_pairs_joined": r["joined"],
        }
        for i, r in enumerate(common.head(TOP).iter_rows(named=True))
    ]
    return {
        "common_addresses": common.height,
        "largest_sharers": _max(common, "sharers"),
        "above_hub_degree": _sum(common, pl.col("sharers") > limit) if limit else 0,
        "users": _sum(common, pl.col("sharers")),
        "unrelated_pairs_joined": _sum(common, pl.col("joined")),
        "top": top,
    }


# ---- per-scenario report sections ---------------------------------------------------------------


def _assigned(clusters: pl.DataFrame, labels: pl.DataFrame) -> pl.DataFrame:
    return clusters.select("customer_id", "cluster_id").join(
        labels.select("customer_id", "behaviour", "ring_id", "family_id"),
        on="customer_id",
        how="inner",
    )


def _features(row: dict[str, Any]) -> dict[str, Any]:
    return {f: row[f] for f in CLUSTER_FEATURES}


def largest_clusters(clusters: pl.DataFrame, assigned: pl.DataFrame) -> list[dict[str, Any]]:
    """The largest clusters with their composition by ground-truth behaviour."""
    grouped = _with_group(assigned).filter(pl.col("cluster_id").is_not_null())
    sizes = (
        grouped.group_by("cluster_id")
        .agg(pl.len().alias("n"))
        .sort(["n", "cluster_id"], descending=[True, False])
        .head(TOP)
    )
    first = clusters.filter(pl.col("cluster_id").is_not_null()).unique("cluster_id", keep="first")
    out = []
    for cid, n in sizes.iter_rows():
        members = grouped.filter(pl.col("cluster_id") == cid)
        composition = dict(sorted(members.group_by("behaviour").len().iter_rows()))
        groups = members["group"].n_unique()
        mix = mixing(members)
        row = first.filter(pl.col("cluster_id") == cid).row(0, named=True)
        out.append(
            {
                "cluster_id": cid,
                "size": n,
                "composition": composition,
                "groups": groups,
                "unrelated_pairs": mix["unrelated_pairs"],
                "features": _features(row),
            }
        )
    return out


def feature_profile(clusters: pl.DataFrame, assigned: pl.DataFrame) -> list[dict[str, Any]]:
    """Median cluster features by cluster type (ring, family, mixed): what tells them apart."""
    kinds = (
        _with_group(assigned)
        .filter(pl.col("cluster_id").is_not_null())
        .group_by("cluster_id")
        .agg(
            pl.col("group").n_unique().alias("groups"),
            pl.col("ring_id").drop_nulls().n_unique().alias("rings"),
            pl.col("family_id").drop_nulls().n_unique().alias("families"),
            pl.col("ring_id").is_not_null().all().alias("all_ring"),
            pl.col("family_id").is_not_null().all().alias("all_family"),
        )
        .with_columns(
            pl.when((pl.col("groups") == 1) & pl.col("all_ring"))
            .then(pl.lit("ring"))
            .when((pl.col("groups") == 1) & pl.col("all_family"))
            .then(pl.lit("family"))
            .otherwise(pl.lit("mixed"))
            .alias("type")
        )
    )
    per_cluster = clusters.filter(pl.col("cluster_id").is_not_null()).unique(
        "cluster_id", keep="first"
    )
    table = per_cluster.join(kinds.select("cluster_id", "type"), on="cluster_id").with_columns(
        (pl.col("n_distinct_cards") / pl.col("cluster_size")).alias("cards_per_account")
    )
    columns = (
        "cluster_size",
        "creation_spread_days",
        "cards_per_account",
        "welcome_coupons_per_account",
        "cluster_return_rate",
        "strong_id_share",
    )
    out = []
    for kind in ("ring", "family", "mixed"):
        part = table.filter(pl.col("type") == kind)
        medians: dict[str, float | None] = {c: _stat(part, pl.col(c).median()) for c in columns}
        out.append({"type": kind, "clusters": part.height, "medians": medians})
    return out


def golden_cases(clusters: pl.DataFrame, assigned: pl.DataFrame) -> list[dict[str, Any]]:
    """The golden ring and families present in the store: is their cluster exactly them?"""
    out = []
    for group in GOLDEN_GROUPS:
        members = assigned.filter((pl.col("ring_id") == group) | (pl.col("family_id") == group))
        if members.height == 0:
            continue
        ids = sorted(members["cluster_id"].drop_nulls().unique().to_list())
        in_clusters = clusters.filter(pl.col("cluster_id").is_in(ids))
        outsiders = sorted(set(in_clusters["customer_id"]) - set(members["customer_id"]))
        features = _features(in_clusters.row(0, named=True)) if len(ids) == 1 else None
        out.append(
            {
                "group": group,
                "accounts": sorted(members["customer_id"].to_list()),
                "clusters": len(ids),
                "accounts_alone": int(members["cluster_id"].is_null().sum()),
                "outsiders": len(outsiders),
                "cluster_equals_group": len(ids) == 1
                and not outsiders
                and members["cluster_id"].null_count() == 0,
                "features": features,
            }
        )
    return out


WEIGHT_BANDS = (
    ("no link", None, None),
    ("below 0.6", 0.0, 0.6),
    ("0.6 to 0.8", 0.6, 0.8),
    ("0.8 or more", 0.8, None),
)


def ring_pair_weights(links: pl.DataFrame, assigned: pl.DataFrame) -> dict[str, int]:
    """Pairs of accounts of the same ring by the weight of their direct link: what the threshold
    sees. Pairs below 0.8 can still share a cluster through other members."""
    members = assigned.filter(pl.col("ring_id").is_not_null()).select("customer_id", "ring_id")
    pairs = (
        members.join(members, on="ring_id", suffix="_b")
        .filter(pl.col("customer_id") < pl.col("customer_id_b"))
        .select(
            pl.col("customer_id").alias("customer_a"), pl.col("customer_id_b").alias("customer_b")
        )
        .join(
            links.select("customer_a", "customer_b", "weight"),
            on=["customer_a", "customer_b"],
            how="left",
        )
    )
    tolerance = 1e-9
    out: dict[str, int] = {}
    for name, low, high in WEIGHT_BANDS:
        if low is None:
            mask = pl.col("weight").is_null()
        else:
            mask = pl.col("weight") >= low - tolerance
            if high is not None:
                mask = mask & (pl.col("weight") < high - tolerance)
        out[name] = pairs.filter(mask).height
    return out


def pruning_check(snapshot: StoreSnapshot, product: GraphResult) -> dict[str, Any]:
    """Hub pruning against the brute force (no pruning): same links on every pair kept, the
    pairs left out all below the threshold, the same clusters."""
    params = product.params
    full = build_links(snapshot.identifiers, snapshot.as_of, replace(params, hub_degree=None))
    keys = ["customer_a", "customer_b"]
    kept = full.join(product.links, on=keys, how="semi")
    dropped = full.join(product.links, on=keys, how="anti")
    hubs = hub_values(snapshot.identifiers, params)
    clusters_full = build_clusters(full, snapshot, params)
    return {
        "links_pruned": product.links.height,
        "links_brute_force": full.height,
        "pairs_left_out": dropped.height,
        "pairs_left_out_max_weight": _stat(dropped, pl.col("weight").max()),
        "pairs_left_out_below_threshold": bool((dropped["weight"] < params.threshold).all()),
        "kept_links_identical": kept.equals(product.links),
        "clusters_identical": clusters_full.equals(product.clusters),
        "hub_values": hubs.height,
        "hub_values_by_kind": dict(sorted(hubs.group_by("kind").len().iter_rows())),
        "hub_largest_sharers": _max(hubs, "sharers"),
    }


def evaluate_scenario(
    scenario: str, store_root: Path, truth_root: Path, as_of: datetime
) -> dict[str, Any]:
    snapshot = load_snapshot(store_root, as_of)
    labels_table, truth_provenance = read_table("truth/labels", truth_root)
    labels = pl.from_arrow(labels_table)
    assert isinstance(labels, pl.DataFrame)
    links_cache: dict[tuple[float, int | None], pl.DataFrame] = {}
    variants: list[dict[str, Any]] = []
    product: GraphResult | None = None
    for name, params in VARIANTS:
        key = (params.threshold, params.hub_degree)
        if key not in links_cache:
            links_cache[key] = build_links(snapshot.identifiers, snapshot.as_of, params)
        links = links_cache[key]
        result = GraphResult(as_of, params, links, build_clusters(links, snapshot, params))
        if product is None:
            product = result
        assigned = _assigned(result.clusters, labels)
        variants.append(
            {
                "variant": name,
                "params": params.describe(),
                "links": links.height,
                "cluster_links": cluster_edges(links, params).height,
                "ring": ring_metrics(assigned),
                "family": family_metrics(assigned),
                "mixing": mixing(assigned),
            }
        )
    assert product is not None
    assigned = _assigned(product.clusters, labels)
    store = snapshot.provenance["store/identifiers"]
    start, end = snapshot.period()
    return {
        "scenario": scenario,
        "store_root": str(store_root),
        "provenance": {
            "store_origin": store.origin,
            "store_nature": store.nature,
            "truth_origin": truth_provenance.origin,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
        },
        "accounts": snapshot.customers.height,
        "variants": variants,
        "golden": golden_cases(product.clusters, assigned),
        "largest_clusters": largest_clusters(product.clusters, assigned),
        "feature_profile": feature_profile(product.clusters, assigned),
        "common_addresses": common_addresses(
            snapshot.identifiers, assigned, product.params.hub_degree
        ),
        "ring_pair_weights": ring_pair_weights(product.links, assigned),
        "pruning": pruning_check(snapshot, product),
    }


def evaluate(data: Path, as_of: datetime | None = None) -> dict[str, Any]:
    """Every scenario found under data (main, then OOD), at as_of (default: the end of the main
    store's period)."""
    main_store, _ = scenario_roots(data, "main")
    as_of = as_of or default_as_of(main_store)
    scenarios = []
    missing = []
    for scenario in ("main", "ood"):
        store_root, truth_root = scenario_roots(data, scenario)
        if not (store_root / "store" / "identifiers.parquet").exists():
            missing.append(scenario)
            continue
        scenarios.append(evaluate_scenario(scenario, store_root, truth_root, as_of))
    return {
        "as_of": as_of.isoformat(),
        "nature": "synthetic",
        "targets": {"mod03_precision": MOD03_PRECISION, "mod03_recall": MOD03_RECALL},
        "scenarios": scenarios,
        "missing_scenarios": missing,
    }


# ---- report -------------------------------------------------------------------------------------

INTRO = """\
Generated by `python -m pipeline.graph.evaluate --data data`; method in docs/account-graph.md.
Do not edit by hand: rerun the command after regenerating the store and every number is
rewritten from the data."""

CAVEAT = """\
The generator plants the sharing patterns the graph looks for (docs/synthetic-store.md), so the
main scenario measures consistency with the generator, not efficacy on a real shop. The
out-of-distribution scenario (US-105) changes those rules and is never used to choose anything.
Linked accounts are accounts that share identifiers, never the same person (rule 11)."""


def _f(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _n(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return [*lines, ""]


def _verdict(ring: dict[str, Any]) -> str:
    p, r = ring["pairwise_precision"], ring["pairwise_recall"]
    if p is None or r is None:
        return "not measurable"
    return "pass" if p >= MOD03_PRECISION and r >= MOD03_RECALL else "fail"


def _features_row(ft: dict[str, Any] | None) -> list[str]:
    ft = ft or {}
    return [
        _f(ft.get("creation_spread_days"), 1),
        str(ft.get("n_distinct_cards", "n/a")),
        _f(ft.get("welcome_coupons_per_account"), 2),
        _f(ft.get("cluster_return_rate")),
        _f(ft.get("strong_id_share"), 2),
    ]


FEATURE_HEADERS = (
    "Creation spread (days)",
    "Distinct cards",
    "Welcome coupons per account",
    "Return rate",
    "Strong-identifier share",
)


def _header(report: dict[str, Any]) -> list[str]:
    rows: list[list[str]] = [
        ["Nature", "**Synthetic**: measured on the generated Navigli Fashion store"],
        ["Decision time (as_of)", f"{report['as_of']}: only rows available then (rule 7)"],
    ]
    for s in report["scenarios"]:
        p = s["provenance"]
        rows.append([f"Store, {s['scenario']}", p["store_origin"]])
        rows.append(
            [
                f"Period, {s['scenario']}",
                f"{p['period_start']} to {p['period_end']}: oldest account to the last day of "
                "data at as_of",
            ]
        )
    rows.append(["Ground truth", "truth/labels, read by this offline job only (rule 12, ADR-13)"])
    rows += [[f"Scenario {m}", "not generated: no rows"] for m in report["missing_scenarios"]]
    return [
        "# Account graph: measured results (US-203)",
        "",
        INTRO,
        "",
        *_table(["", ""], rows),
        CAVEAT,
        "",
    ]


def _mod03(scenarios: list[dict[str, Any]]) -> list[str]:
    out = [
        "## MOD-03: ring membership",
        "",
        f"Target (spec 12): pairwise precision >= {MOD03_PRECISION:.2f} and recall >= "
        f"{MOD03_RECALL:.2f} on the rings of the main scenario. The OOD row is reported whatever "
        "it is (MOD-12). Definitions in docs/account-graph.md.",
        "",
    ]
    rows = []
    for s in scenarios:
        r = s["variants"][0]["ring"]
        verdict = _verdict(r) if s["scenario"] == "main" else f"{_verdict(r)} (MOD-12: reported)"
        rows.append(
            [
                s["scenario"],
                f"{r['rings']} ({r['ring_members']})",
                _f(r["pairwise_precision"]),
                _f(r["pairwise_recall"]),
                f"{_n(r['pairs_predicted'])} / {_n(r['pairs_true_positive'])} / "
                f"{_n(r['pairs_actual'])}",
                _f(r["member_precision"]),
                _f(r["member_recall"]),
                r["rings_exact"],
                r["rings_whole"],
                r["ring_members_unclustered"],
                verdict,
            ]
        )
    out += _table(
        [
            "Scenario",
            "Rings (accounts)",
            "Pairwise precision",
            "Pairwise recall",
            "Pairs predicted / true / actual",
            "Member precision",
            "Member recall",
            "Rings exact",
            "Rings whole",
            "Ring accounts in no cluster",
            "MOD-03",
        ],
        rows,
    )
    out += [
        "Pairs of accounts of the same ring by the weight of their direct link (a pair below 0.8 "
        "can still share a cluster through other members):",
        "",
    ]
    out += _table(
        ["Scenario", *(name for name, _, _ in WEIGHT_BANDS)],
        [
            [s["scenario"], *(_n(s["ring_pair_weights"][n]) for n, _, _ in WEIGHT_BANDS)]
            for s in scenarios
        ],
    )
    return out


def _mod11(scenarios: list[dict[str, Any]]) -> list[str]:
    out = ["## MOD-11: common addresses and families", ""]
    rows = []
    for s in scenarios:
        m, c, f = s["variants"][0]["mixing"], s["common_addresses"], s["variants"][0]["family"]
        rows.append(
            [
                s["scenario"],
                m["unrelated_pairs"],
                m["mixed_clusters"],
                f"{c['common_addresses']} ({_n(c['users'])})",
                f"{c['largest_sharers']} users",
                c["above_hub_degree"],
                c["unrelated_pairs_joined"],
                f"{f['families_exact']} / {f['families_whole']} / {f['families']}",
            ]
        )
    out += _table(
        [
            "Scenario",
            "Unrelated pairs in one cluster",
            "Mixed clusters",
            "Common addresses (users)",
            "Largest common address",
            "Above the hub degree",
            "Unrelated pairs joined at them",
            "Families exact / whole / total",
        ],
        rows,
    )
    out += ["Golden groups: is their cluster exactly their accounts?", ""]
    out += _table(
        [
            "Scenario",
            "Group",
            "Accounts",
            "Clusters",
            "Accounts alone",
            "Outsiders",
            "Exactly the group",
            *FEATURE_HEADERS,
        ],
        [
            [
                s["scenario"],
                g["group"],
                len(g["accounts"]),
                g["clusters"],
                g["accounts_alone"],
                g["outsiders"],
                _yes(g["cluster_equals_group"]),
                *_features_row(g["features"]),
            ]
            for s in scenarios
            for g in s["golden"]
        ],
    )
    for s in scenarios:
        out += [
            f"Most used common addresses, {s['scenario']} (values not shown: pseudonymous data):",
            "",
        ]
        out += _table(
            [
                "Rank",
                "Users",
                "Groups among users",
                "Users in a cluster",
                "Their clusters",
                "Unrelated pairs joined",
            ],
            [
                [
                    t["rank"],
                    t["sharers"],
                    t["groups"],
                    t["sharers_in_clusters"],
                    t["clusters"],
                    t["unrelated_pairs_joined"],
                ]
                for t in s["common_addresses"]["top"]
            ],
        )
    return out


def _clusters(scenarios: list[dict[str, Any]]) -> list[str]:
    out = ["## Largest clusters and their composition", ""]
    for s in scenarios:
        out += [f"{s['scenario']} (composition by ground-truth behaviour):", ""]
        out += _table(
            ["Cluster", "Size", "Composition", "Groups", "Unrelated pairs", *FEATURE_HEADERS],
            [
                [
                    c["cluster_id"],
                    c["size"],
                    ", ".join(f"{k} {v}" for k, v in c["composition"].items()),
                    c["groups"],
                    c["unrelated_pairs"],
                    *_features_row(c["features"]),
                ]
                for c in s["largest_clusters"]
            ],
        )
    out += [
        "## Cluster features by cluster type",
        "",
        "Medians over the clusters of each type (type from the ground truth). The graph puts "
        "families in clusters on purpose; these features are what keep them apart from rings "
        "(spec 8.3).",
        "",
    ]
    out += _table(
        [
            "Scenario",
            "Type",
            "Clusters",
            "Size",
            "Creation spread (days)",
            "Cards per account",
            "Welcome coupons per account",
            "Return rate",
            "Strong-identifier share",
        ],
        [
            [
                s["scenario"],
                p["type"],
                p["clusters"],
                _f(p["medians"]["cluster_size"], 1),
                _f(p["medians"]["creation_spread_days"], 1),
                _f(p["medians"]["cards_per_account"], 2),
                _f(p["medians"]["welcome_coupons_per_account"], 2),
                _f(p["medians"]["cluster_return_rate"]),
                _f(p["medians"]["strong_id_share"], 2),
            ]
            for s in scenarios
            for p in s["feature_profile"]
        ],
    )
    return out


def _pruning(scenarios: list[dict[str, Any]]) -> list[str]:
    out = ["## Hub pruning against the brute force", ""]
    rows = []
    for s in scenarios:
        p = s["pruning"]
        kinds = ", ".join(f"{k} {v}" for k, v in p["hub_values_by_kind"].items()) or "none"
        rows.append(
            [
                s["scenario"],
                _n(p["links_pruned"]),
                _n(p["links_brute_force"]),
                _n(p["pairs_left_out"]),
                _f(p["pairs_left_out_max_weight"], 2),
                _yes(p["pairs_left_out_below_threshold"]),
                _yes(p["kept_links_identical"]),
                _yes(p["clusters_identical"]),
                f"{p['hub_values']} ({kinds})",
                f"{p['hub_largest_sharers']} users",
            ]
        )
    return out + _table(
        [
            "Scenario",
            "Links written",
            "Pairs of the brute force",
            "Pairs left out",
            "Largest weight left out",
            "All below the threshold",
            "Kept links identical",
            "Clusters identical",
            "Hub values (by kind)",
            "Largest hub",
        ],
        rows,
    )


def _variants(scenarios: list[dict[str, Any]]) -> list[str]:
    out = [
        "## Variants (sensitivity and ablation, reported as measured)",
        "",
        "The product uses the spec 8.3 values; these rows only show what other choices would do.",
        "",
    ]
    return out + _table(
        [
            "Scenario",
            "Variant",
            "Links",
            "Cluster links",
            "Clusters",
            "Largest",
            "Pairwise precision",
            "Pairwise recall",
            "Rings exact",
            "Unrelated pairs in one cluster",
            "Families exact",
        ],
        [
            [
                s["scenario"],
                v["variant"],
                _n(v["links"]),
                _n(v["cluster_links"]),
                _n(v["mixing"]["clusters"]),
                v["mixing"]["largest_cluster"],
                _f(v["ring"]["pairwise_precision"]),
                _f(v["ring"]["pairwise_recall"]),
                v["ring"]["rings_exact"],
                _n(v["mixing"]["unrelated_pairs"]),
                v["family"]["families_exact"],
            ]
            for s in scenarios
            for v in s["variants"]
        ],
    )


def render(report: dict[str, Any]) -> str:
    """The Markdown report, entirely from the evaluation JSON."""
    scenarios = report["scenarios"]
    lines = [
        *_header(report),
        *_mod03(scenarios),
        *_mod11(scenarios),
        *_clusters(scenarios),
        *_pruning(scenarios),
        *_variants(scenarios),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.graph.evaluate",
        description="Evaluate the account graph against the ground truth (offline, US-203).",
    )
    p.add_argument("--data", type=Path, default=Path("data"), help="root of store/ and truth/")
    p.add_argument("--as-of", type=parse_instant, help="decision time, ISO 8601 with a time zone")
    p.add_argument("--report", type=Path, default=Path("docs/results/account-graph.md"))
    p.add_argument("--json", type=Path, default=Path("artifacts/graph/evaluation.json"))
    args = p.parse_args(argv)
    report = evaluate(args.data, args.as_of)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render(report), encoding="utf-8")
    print(f"wrote {args.report} and {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
