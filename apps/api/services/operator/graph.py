"""The account graph as people left it (US-310, US-203; ADR-16; rules 1, 7, 11 and 12).

Evidence that explains links (household members, a shared payment explained, a collective
address) and disputed links leave the cluster features (ADR-15 section 4, "Recompute").
GraphView is the serving snapshot seen with those links out:

- its clusters at a snapshot instant are pipeline.graph.build.dispute_link applied to the served
  graph of that instant, pair by pair. dispute_link recomputes the whole component of the pair,
  so the result equals a full rebuild of the graph with the pairs disputed (tested);
- the customer snapshot, the cluster members and therefore the case card follow those clusters;
- a request whose customer's cluster row changed is scored again with the request model of the
  snapshot and the batch's own code (ml.scoring.batch.score_requests, through
  Serving._score_live) on that graph: the score still comes from ml/ (rule 1), its features
  from the store as known at the request (rule 7). Any other request keeps its batch score.

The snapshot in memory never changes: a view shares its frames and keeps the graphs it computes
in a cache attached to the snapshot.

Which links are out for a decision version (version_exclusions): none for a version of the
intake, which reads the snapshot's graph (apps.api.services.cases.intake); for every later
version, the disputes and explanations made up to its creation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Final, Literal

import pandas as pd
import polars as pl
import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.serving import RequestScore, ReturnRecord, Serving, ServingError
from contracts.parquet import SCHEMAS
from contracts.sql import evidence_additions, link_disputes
from ml.risk.data import GraphSnapshots
from ml.scoring.batch import Inputs
from pipeline.graph.build import GraphResult, build_graph, dispute_link
from pipeline.graph.clusters import CLUSTER_FEATURES, clusters_table
from pipeline.graph.snapshot import StoreSnapshot, load_snapshot
from pipeline.graph.weights import GraphParams
from policy.types import Path as ReturnPath

GRAPH_PARAMS: Final = GraphParams()  # the parameters of the served graph (checked on use)
LINKS: Final = "features/links"
CLUSTERS: Final = "features/clusters"
_CACHE: Final = "_operator_graph_cache"  # attached to the snapshot: shared by its views
_CACHE_SIZE: Final = 256

Pair = tuple[str, str]
ExclusionKind = Literal["disputed", "explained"]


@dataclass(frozen=True)
class Exclusion:
    """A link out of the cluster features: disputed (link_disputes) or explained by evidence
    (evidence_additions of a link-explaining kind)."""

    link_id: str
    customer_a: str
    customer_b: str
    kind: ExclusionKind
    source_id: str  # the dispute or the evidence
    created_at: datetime

    @property
    def pair(self) -> Pair:
        return (self.customer_a, self.customer_b)


async def exclusions(conn: AsyncConnection, until: datetime | None = None) -> list[Exclusion]:
    """The links out of the cluster features, made at or before `until` (all by default), each
    once (its first dispute or explanation), in the order they were made."""
    found: list[Exclusion] = []
    for r in (await conn.execute(select(link_disputes))).mappings().all():
        found.append(
            Exclusion(
                r["link_id"], r["customer_a"], r["customer_b"], "disputed", r["dispute_id"],
                r["created_at"],
            )
        )  # fmt: skip
    for r in (await conn.execute(select(evidence_additions))).mappings().all():
        for link in r["target_link_ids"]:
            a, b = s.split_link_id(str(link))
            found.append(Exclusion(link, a, b, "explained", r["evidence_id"], r["created_at"]))
    if until is not None:
        found = [e for e in found if e.created_at <= until]
    found.sort(key=lambda e: (e.created_at, e.source_id, e.link_id))
    first: dict[str, Exclusion] = {}
    for e in found:
        first.setdefault(e.link_id, e)
    return list(first.values())


def version_exclusions(row: Mapping[str, Any], made: Iterable[Exclusion]) -> list[Exclusion]:
    """The exclusions a decision version was computed with: none for the intake (version 1 reads
    the snapshot's graph), those made up to its creation for a later version."""
    if row["revision_trigger"] == "initial":
        return []
    return [e for e in made if e.created_at <= row["created_at"]]


async def view_for(conn: AsyncConnection, serving: Serving, row: Mapping[str, Any]) -> Serving:
    """The snapshot as the decision version `row` saw it (the graph it was decided on)."""
    if row["revision_trigger"] == "initial":
        return GraphView.of(serving, ())
    return GraphView.of(serving, version_exclusions(row, await exclusions(conn)))


def _polars(frame: pd.DataFrame, name: str) -> pl.DataFrame:
    table = pa.Table.from_pandas(frame, schema=SCHEMAS[name], preserve_index=False)
    out = pl.from_arrow(table)
    assert isinstance(out, pl.DataFrame)
    return out


def _same(a: Any, b: Any) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    return bool(a == b)


class _ViewGraph(GraphSnapshots):
    """The GraphSnapshots of the request scoring with the clusters of a GraphView: the store
    tables read once by the snapshot's own graph, the clusters after the exclusions."""

    def __init__(self, base: GraphSnapshots, view: GraphView) -> None:
        # No super().__init__: it would read the store again; the tables are the snapshot's.
        self.until, self.tables, self.provenance = base.until, base.tables, base.provenance
        self.existing, self.sources, self._cache = base.existing, {}, {}
        self._view = view

    def clusters_at(self, as_of: datetime) -> pd.DataFrame:
        return self._view.clusters(as_of)


class GraphView(Serving):
    """The serving snapshot with the excluded links out of the cluster features (see the module
    docstring). Build it with GraphView.of; without exclusions it is the snapshot itself."""

    base: Serving
    excluded: frozenset[Pair]
    _rescored: dict[str, RequestScore]
    _view_inputs: Inputs | None

    @classmethod
    def of(cls, serving: Serving, excluded: Iterable[Exclusion | Pair]) -> Serving:
        base = serving.base if isinstance(serving, GraphView) else serving
        pairs = frozenset(
            tuple(sorted(x.pair if isinstance(x, Exclusion) else x)) for x in excluded
        )
        if not pairs:
            return base
        view = cls.__new__(cls)
        view.__dict__.update(base.__dict__)  # the frames and the derived frames, shared
        view.base = base
        view.excluded = frozenset((a, b) for a, b in pairs)
        view._rescored = {}
        view._view_inputs = None
        return view

    # ---- the graph after the exclusions -------------------------------------------------------

    def _store(self) -> dict[Any, Any]:
        cache: dict[Any, Any] = vars(self.base).setdefault(_CACHE, {})
        if len(cache) > _CACHE_SIZE:
            cache.clear()
        return cache

    def _cached(self, key: tuple[Any, ...], make: Any) -> Any:
        cache = self._store()
        if key not in cache:
            cache[key] = make()
        return cache[key]

    def _served(self, name: str, as_of: datetime) -> pd.DataFrame:
        frame = self.base.frames[name]
        return frame.loc[frame["as_of"] == pd.Timestamp(as_of)].reset_index(drop=True)

    def _snapshot(self, as_of: datetime) -> StoreSnapshot:
        """The store as known at `as_of` (pipeline.graph), read from the served store tables."""
        snapshot: StoreSnapshot = self._cached(
            ("snapshot", pd.Timestamp(as_of)), lambda: load_snapshot(self.base.root, as_of)
        )
        return snapshot

    def _graph(self, as_of: datetime) -> GraphResult:
        """The served graph at `as_of`, or the graph built from the store when the snapshot did
        not serve that instant (as GraphSnapshots does)."""

        def make() -> GraphResult:
            if GRAPH_PARAMS.describe() not in self.base.provenance[LINKS].origin:
                raise ServingError("the served account graph was built with other parameters")
            links = self._served(LINKS, as_of)
            if links.empty:
                return build_graph(self._snapshot(as_of), GRAPH_PARAMS)
            clusters = self._served(CLUSTERS, as_of)
            return GraphResult(
                as_of, GRAPH_PARAMS, _polars(links, LINKS), _polars(clusters, CLUSTERS)
            )

        result: GraphResult = self._cached(("graph", pd.Timestamp(as_of)), make)
        return result

    def clusters(self, as_of: datetime) -> pd.DataFrame:
        """features/clusters rows at `as_of` (the contract columns, as served) with the excluded
        links out of the clusters."""

        def make() -> pd.DataFrame:
            result = self._graph(as_of)
            links = result.links.filter(~pl.col("disputed"))
            present = set(zip(links["customer_a"], links["customer_b"], strict=True))
            pairs = sorted(p for p in self.excluded if p in present)
            if not pairs:
                served = self._served(CLUSTERS, as_of)
                if served.empty:
                    return clusters_table(result.clusters).to_pandas()
                return served
            snapshot = self._snapshot(as_of)
            for a, b in pairs:
                result = dispute_link(result, snapshot, a, b).result
            return clusters_table(result.clusters).to_pandas()

        frame: pd.DataFrame = self._cached(("clusters", self.excluded, pd.Timestamp(as_of)), make)
        return frame

    # ---- what the API reads through it --------------------------------------------------------

    def at(self, name: str, as_of: datetime) -> pd.DataFrame:
        if name != CLUSTERS:
            return super().at(name, as_of)
        frame: pd.DataFrame = self._cached(
            ("indexed", self.excluded, pd.Timestamp(as_of)),
            lambda: self.clusters(as_of).set_index("customer_id", drop=False),
        )
        return frame

    def cluster_members(self, cluster_id: str, as_of: datetime) -> list[str]:
        frame = self.clusters(as_of)
        return sorted(str(c) for c in frame.loc[frame["cluster_id"] == cluster_id, "customer_id"])

    def changed(self, customer_id: str, as_of: datetime) -> bool:
        """Whether the customer's features/clusters row at `as_of` differs from the snapshot's."""
        before, after = self.base.at(CLUSTERS, as_of), self.at(CLUSTERS, as_of)
        if (customer_id in before.index) != (customer_id in after.index):
            return True
        if customer_id not in before.index:
            return False
        a, b = before.loc[customer_id], after.loc[customer_id]
        return not all(_same(a[c], b[c]) for c in ("cluster_id", *CLUSTER_FEATURES))

    @property
    def _inputs(self) -> Inputs:
        """The scoring inputs of the snapshot with this view's clusters (Serving._score_live)."""
        if self._view_inputs is None:
            base = self.base._inputs
            self._view_inputs = replace(base, graph=_ViewGraph(base.graph, self))
        return self._view_inputs

    def request_score(self, record: ReturnRecord) -> RequestScore:
        """The batch's score, unless the customer's cluster at the snapshot the request read
        changed: then the request model scores it again on this graph (source live)."""
        instant = self.context_instant(record.known_at)
        if (
            record.path is not ReturnPath.WITHDRAWAL
            or instant is None
            or not self.changed(record.customer_id, instant)
        ):
            return self.base.request_score(record)
        if record.return_id not in self._rescored:
            self._rescored[record.return_id] = self._score_live(record)
        return self._rescored[record.return_id]
