"""features/links: pairs of accounts that share identifier values, with their evidence (US-203).

The graph is bipartite, accounts on one side and identifier values (card, e-mail, device,
address, phone, IP prefix, as salted hashes) on the other, projected on pairs of accounts
(spec 8.3). A shared event is one value used by both accounts (ADR-13): its instant is the later
of the two first uses, when the pair came to share it, and d_k is the number of accounts using
the value at the decision time. For each pair:

- kinds: the shared kinds, strongest first, each once;
- weight: w_ij = 1 - prod (1 - w_k) over the kinds;
- weight_decayed: the same product over w_k*, where w_k* is the largest
  w_k * exp(-dt / tau) / log2(d) among the pair's values of kind k (dt: days from the instant the
  pair came to share the value to the decision time); never above weight;
- first_shared_at, last_shared_at: earliest and latest of those instants;
- n_shared_events: shared values; max_sharers: the largest d among them; disputed: flagged by an
  operator (US-310).

Hub pruning. A pickup point used by 200 customers would make 19,900 pairs, a mobile carrier prefix
a few hundred. A value is a hub when more than params.hub_degree accounts use it and its kind
alone stays below the cluster threshold (address, phone and IP prefix at 0.8). A hub creates no
pair by itself: candidate pairs are those sharing a value that is not a hub, or two hub values.
Every value a candidate pair shares, hubs included, then enters its link with its real d_k,
counted before pruning, so the down-weighted contribution of a pickup point to a link is exact.
The pairs left out share one hub value and nothing else; their weight is that kind's weight,
below the threshold, so no cluster changes. A strong kind (card, e-mail, device) is never pruned:
one shared value already makes a cluster link.

Inputs are the identifiers known at the decision time (rule 7): a use after it is refused.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import polars as pl
import pyarrow as pa

from contracts.parquet import IDENTIFIER_KINDS, SCHEMAS
from pipeline.graph.snapshot import US_PER_DAY, check_as_of
from pipeline.graph.weights import KIND_WEIGHTS, GraphParams, prunable_kinds

LINK_SCHEMA = SCHEMAS["features/links"]
LINK_FEATURES = (
    "weight",
    "weight_decayed",
    "n_shared_events",
    "max_sharers",
)  # numeric evidence a model may use; kinds, dates and keys are evidence for people
IDENTIFIER_COLUMNS = ("customer_id", "kind", "value_hash", "used_at")
_TS = pl.Datetime("us", "UTC")
_RANK = {k: i for i, k in enumerate(IDENTIFIER_KINDS)}


def _identifiers(identifiers: pl.DataFrame, as_of: datetime) -> pl.DataFrame:
    missing = [c for c in IDENTIFIER_COLUMNS if c not in identifiers.columns]
    if missing:
        raise ValueError(f"identifiers lack columns {missing}")
    ids = identifiers.select(
        pl.col("customer_id").cast(pl.String),
        pl.col("kind").cast(pl.String),
        pl.col("value_hash").cast(pl.String),
        pl.col("used_at").cast(_TS),
    )
    unknown = ids.filter(~pl.col("kind").is_in(IDENTIFIER_KINDS))["kind"].unique().to_list()
    if unknown:
        raise ValueError(f"unknown identifier kinds {sorted(unknown)}")
    late = ids.filter(pl.col("used_at") > as_of).height
    if late:
        raise ValueError(f"{late} identifier uses after as_of {as_of.isoformat()} (rule 7)")
    return ids.group_by("customer_id", "kind", "value_hash").agg(pl.col("used_at").min())


def shared_values(identifiers: pl.DataFrame) -> pl.DataFrame:
    """customer_id, kind, value_hash, used_at, sharers: the uses of values used by at least two
    accounts; sharers is d_k."""
    degree = identifiers.group_by("kind", "value_hash").agg(
        pl.col("customer_id").n_unique().alias("sharers")
    )
    return identifiers.join(degree, on=["kind", "value_hash"]).filter(pl.col("sharers") >= 2)


def _is_hub(params: GraphParams) -> pl.Expr:
    if params.hub_degree is None:
        return pl.lit(False)
    return (pl.col("sharers") > params.hub_degree) & pl.col("kind").is_in(
        prunable_kinds(params.threshold)
    )


def hub_values(identifiers: pl.DataFrame, params: GraphParams) -> pl.DataFrame:
    """kind, value_hash, sharers, pairs: the values pruned as hubs, and the pairs each one alone
    would have made (sharers choose 2), most shared first."""
    ids = identifiers.select(IDENTIFIER_COLUMNS).unique(["customer_id", "kind", "value_hash"])
    return (
        shared_values(ids)
        .filter(_is_hub(params))
        .group_by("kind", "value_hash")
        .agg(pl.col("sharers").first())
        .with_columns((pl.col("sharers") * (pl.col("sharers") - 1) // 2).alias("pairs"))
        .sort(["sharers", "kind", "value_hash"], descending=[True, False, False])
    )


def _pairs_on(frame: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    return (
        frame.join(frame, on=keys, suffix="_b")
        .filter(pl.col("customer_id") < pl.col("customer_id_b"))
        .select(
            pl.col("customer_id").alias("customer_a"), pl.col("customer_id_b").alias("customer_b")
        )
    )


def candidate_pairs(shared: pl.DataFrame, params: GraphParams) -> pl.DataFrame:
    """customer_a < customer_b: pairs sharing a value that is not a hub, or two hub values."""
    flagged = shared.with_columns(_is_hub(params).alias("hub"))
    plain = _pairs_on(
        flagged.filter(~pl.col("hub")).select("customer_id", "kind", "value_hash"),
        ["kind", "value_hash"],
    )
    hubs = flagged.filter(pl.col("hub")).select(
        "customer_id", pl.concat_str("kind", pl.lit(":"), "value_hash").alias("key")
    )
    combos = (
        hubs.join(hubs, on="customer_id", suffix="_2")
        .filter(pl.col("key") < pl.col("key_2"))
        .select("customer_id", "key", "key_2")
    )
    return pl.concat([plain, _pairs_on(combos, ["key", "key_2"])]).unique()


def _disputed_frame(disputed: Iterable[tuple[str, str]]) -> pl.DataFrame:
    pairs = sorted({(min(a, b), max(a, b)) for a, b in disputed if a != b})
    return pl.DataFrame(
        {
            "customer_a": [a for a, _ in pairs],
            "customer_b": [b for _, b in pairs],
            "disputed": [True] * len(pairs),
        },
        schema={"customer_a": pl.String, "customer_b": pl.String, "disputed": pl.Boolean},
    )


def build_links(
    identifiers: pl.DataFrame,
    as_of: datetime,
    params: GraphParams | None = None,
    disputed: Iterable[tuple[str, str]] = (),
) -> pl.DataFrame:
    """The features/links rows at as_of, sorted by (customer_a, customer_b).

    identifiers: customer_id, kind, value_hash, used_at (first use), none after as_of.
    disputed: pairs an operator disputed (US-310), in either order; their links are flagged."""
    params = params or GraphParams()
    as_of = check_as_of(as_of)
    shared = shared_values(_identifiers(identifiers, as_of))
    pairs = candidate_pairs(shared, params)
    side_a = shared.select(
        pl.col("customer_id").alias("customer_a"),
        "kind",
        "value_hash",
        "sharers",
        pl.col("used_at").alias("used_a"),
    )
    side_b = shared.select(
        pl.col("customer_id").alias("customer_b"),
        "kind",
        "value_hash",
        pl.col("used_at").alias("used_b"),
    )
    age_days = (pl.lit(as_of, dtype=_TS) - pl.col("shared_at")).dt.total_microseconds() / US_PER_DAY
    events = (
        pairs.join(side_a, on="customer_a")
        .join(side_b, on=["customer_b", "kind", "value_hash"])
        .with_columns(
            pl.max_horizontal("used_a", "used_b").alias("shared_at"),
            pl.col("kind").replace_strict(KIND_WEIGHTS, return_dtype=pl.Float64).alias("w"),
            pl.col("kind").replace_strict(_RANK, return_dtype=pl.Int32).alias("rank"),
        )
        .with_columns(
            (
                pl.col("w")
                * (-age_days / params.tau_days).exp()
                / pl.col("sharers").cast(pl.Float64).log(base=2)
            ).alias("w_star")
        )
        # A fixed row order makes every product below, so every weight, reproducible.
        .sort("customer_a", "customer_b", "rank", "value_hash")
    )
    per_kind = events.group_by("customer_a", "customer_b", "rank", maintain_order=True).agg(
        pl.col("kind").first(),
        pl.col("w").first(),
        pl.col("w_star").max(),
        pl.col("shared_at").min().alias("first"),
        pl.col("shared_at").max().alias("last"),
        pl.len().alias("n"),
        pl.col("sharers").max().alias("d"),
    )
    links = (
        per_kind.group_by("customer_a", "customer_b", maintain_order=True)
        .agg(
            pl.col("kind").alias("kinds"),
            (1.0 - (1.0 - pl.col("w")).product()).alias("weight"),
            (1.0 - (1.0 - pl.col("w_star")).product()).alias("weight_decayed"),
            pl.col("first").min().alias("first_shared_at"),
            pl.col("last").max().alias("last_shared_at"),
            pl.col("n").sum().cast(pl.Int32).alias("n_shared_events"),
            pl.col("d").max().cast(pl.Int32).alias("max_sharers"),
        )
        .with_columns(pl.min_horizontal("weight_decayed", "weight").alias("weight_decayed"))
        .join(_disputed_frame(disputed), on=["customer_a", "customer_b"], how="left")
        .with_columns(
            pl.col("disputed").fill_null(False),
            pl.lit(as_of, dtype=_TS).alias("as_of"),
        )
    )
    return links.select(LINK_SCHEMA.names).sort("customer_a", "customer_b")


def links_table(links: pl.DataFrame) -> pa.Table:
    """The rows as a pyarrow table with the exact features/links contract schema."""
    return links.select(LINK_SCHEMA.names).to_arrow().cast(LINK_SCHEMA)
