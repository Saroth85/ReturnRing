"""Inputs of the risk models, read as known at each decision time (rule 7; MOD-08).

- read_features: an as-of table of pipeline.features or pipeline.graph (all its snapshots);
- GraphSnapshots: features/clusters at several decision times from one read of the store, each
  built by pipeline.graph from the rows available at that instant only. The extraction-time file
  written by `python -m pipeline.graph` is used when it holds the instant; other instants (the
  train end, the nightly snapshots before each request) are built the same way, in memory.

Nothing here reads the ground truth: the batch scoring imports this module (rule 12). The labels
are read by ml.risk.labels, imported by the offline jobs only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from contracts.io import Provenance, read_table, require_derived
from contracts.parquet import TS
from ml.risk.features import CUSTOMER_FEATURES_ALL, customer_frame, request_frame
from pipeline.graph.build import build_graph
from pipeline.graph.clusters import cluster_features, clusters_table, membership
from pipeline.graph.snapshot import SNAPSHOT_TABLES, snapshot_from_tables


def utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"decision times must be timezone-aware, got {moment!r}")
    return moment.astimezone(UTC)


def read_features(root: Path | str, name: str) -> tuple[pd.DataFrame, Provenance]:
    """Every row of an as-of table (features/*), as a DataFrame with a UTC as_of."""
    table, provenance = read_table(name, root)
    return table.to_pandas(), provenance


def instants(frame: pd.DataFrame) -> list[datetime]:
    """The decision times (as_of) of an as-of table, ascending."""
    return sorted(utc(t.to_pydatetime()) for t in pd.unique(frame["as_of"]))


class GraphSnapshots:
    """features/clusters rows at decision times up to `until`, from one read of the store."""

    def __init__(
        self,
        store_root: Path | str,
        until: datetime,
        existing: pd.DataFrame | None = None,
        existing_provenance: Provenance | None = None,
    ) -> None:
        """existing: rows of a features/clusters file, with its provenance; a file computed
        from another store (an older seed) raises ContractError (review F22)."""
        self.until = utc(until)
        self.tables: dict[str, pa.Table] = {}
        self.provenance: dict[str, Provenance] = {}
        for name in SNAPSHOT_TABLES:
            self.tables[name], self.provenance[name] = read_table(name, store_root, as_of=until)
        if existing is not None:
            if existing_provenance is None:
                raise ValueError("existing features/clusters rows need their provenance")
            store = self.provenance["store/identifiers"]
            require_derived(existing_provenance, store, "features/clusters", "store/identifiers")
        self.existing = existing
        self.sources: dict[datetime, str] = {}
        self._cache: dict[datetime, pd.DataFrame] = {}

    def clusters_at(self, as_of: datetime) -> pd.DataFrame:
        """The features/clusters rows at as_of: from `existing` when it holds that instant,
        else built from the store rows available at as_of."""
        as_of = utc(as_of)
        if as_of in self._cache:
            return self._cache[as_of]
        if as_of > self.until:
            raise ValueError(f"{as_of.isoformat()} is after the store read ({self.until})")
        stamp = pd.Timestamp(as_of)
        if self.existing is not None and (self.existing["as_of"] == stamp).any():
            rows = self.existing.loc[self.existing["as_of"] == stamp].reset_index(drop=True)
            self.sources[as_of] = "features/clusters file"
        else:
            limit = pa.scalar(as_of, type=TS)
            tables = {
                name: t.filter(pc.less_equal(t.column("available_at"), limit))
                for name, t in self.tables.items()
            }
            snapshot = snapshot_from_tables(tables, as_of, self.provenance)
            rows = clusters_table(build_graph(snapshot).clusters).to_pandas()
            self.sources[as_of] = "built by pipeline.graph at this instant"
        self._cache[as_of] = rows
        return rows

    def singletons_at(self, as_of: datetime) -> pd.DataFrame:
        """features/clusters rows at as_of as if no account were linked: each account's cluster
        features computed on itself (the counterfactual of FAIR-03)."""
        as_of = utc(as_of)
        limit = pa.scalar(as_of, type=TS)
        tables = {
            name: t.filter(pc.less_equal(t.column("available_at"), limit))
            for name, t in self.tables.items()
        }
        snapshot = snapshot_from_tables(tables, as_of, self.provenance)
        alone = cluster_features(membership(snapshot.customers["customer_id"], []), snapshot)
        return clusters_table(alone).to_pandas()

    def clusters(self, moments: Iterable[datetime]) -> pd.DataFrame:
        """The rows of several decision times, stacked."""
        frames = [self.clusters_at(t) for t in sorted({utc(m) for m in moments})]
        return pd.concat(frames, ignore_index=True)


def latest_before(decided_at: pd.Series, times: Sequence[datetime]) -> pd.Series:
    """Per request, the latest decision time of `times` strictly before its decided_at (NaT when
    there is none): the nightly snapshot computed before the request arrived (MOD-08)."""
    grid_index = pd.DatetimeIndex(sorted(utc(t) for t in times)).as_unit("us")
    grid = grid_index.asi8
    at = pd.DatetimeIndex(decided_at).tz_convert("UTC").as_unit("us").asi8
    idx = np.searchsorted(grid, at, side="left") - 1
    values = [grid_index[i] if i >= 0 else pd.NaT for i in idx]
    return pd.Series(values, index=decided_at.index, dtype="datetime64[us, UTC]")


def empty_contexts() -> pd.DataFrame:
    frame = pd.DataFrame({"customer_id": pd.Series(dtype=object)})
    frame["as_of"] = pd.Series(dtype="datetime64[us, UTC]")
    frame["cluster_id"] = pd.Series(dtype=object)
    for name in CUSTOMER_FEATURES_ALL:
        frame[name] = pd.Series(dtype=np.float64)
    return frame


def request_design(
    requests: pd.DataFrame,
    features: pd.DataFrame,
    graph: GraphSnapshots,
    times: Sequence[datetime],
) -> pd.DataFrame:
    """ml.risk.features.request_frame for withdrawal-path requests, with the customer rows of
    the snapshots of `times` (features/customer_features as_of values) that precede them; the
    clusters of each snapshot come from `graph` at that instant."""
    needed = latest_before(requests["decided_at"], times).dropna().unique()
    contexts = [
        customer_frame(features, graph.clusters_at(t.to_pydatetime()), t.to_pydatetime())
        for t in sorted(needed)
    ]
    stacked = pd.concat(contexts, ignore_index=True) if contexts else empty_contexts()
    return request_frame(requests, stacked)
