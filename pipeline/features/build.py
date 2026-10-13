"""The as-of feature tables (ADR-13 amendment 2): features/customer_features,
features/customer_value and features/evidence_quality, one snapshot per decision time.

features_at(snapshot) computes the three frames at snapshot.as_of from that snapshot only. The
return residual uses the product/cohort reference model trained at the same instant, cross-fitted
by customer (ml.reference.model), or trained on another store at that instant (`source`: the main
store for the out-of-distribution store, which is never trained on). build() writes every
decision time into one file per table through contracts.io.write_table.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pandas as pd
import pyarrow as pa

from contracts.io import Provenance, read_table, write_table
from contracts.parquet import SCHEMAS
from ml.reference.model import MODEL_VERSION, Priced, TrainConfig, observed_line_probabilities
from pipeline.features.customer import behaviour_features
from pipeline.features.evidence import EVIDENCE_THRESHOLDS, EvidenceThresholds, evidence_quality
from pipeline.features.evidence import evidence_table as _evidence_table
from pipeline.features.lines import line_frame
from pipeline.features.residual import residuals
from pipeline.features.snapshot import StoreSnapshot, load_snapshot, utc
from pipeline.features.value import VALUE_ASSUMPTIONS, ValueAssumptions, customer_value
from pipeline.features.value import value_table as _value_table

FEATURES_VERSION: Final = "features-v1"
TABLES: Final = (
    "features/customer_features",
    "features/customer_value",
    "features/evidence_quality",
)


@dataclass(frozen=True)
class FeatureSet:
    as_of: datetime
    customer_features: pd.DataFrame
    value: pd.DataFrame
    evidence: pd.DataFrame
    priced: Priced  # the observed lines with their reference p_l


def features_at(
    snap: StoreSnapshot,
    *,
    config: TrainConfig | None = None,
    source: StoreSnapshot | None = None,
    clusters: pd.DataFrame | None = None,
    value_assumptions: ValueAssumptions = VALUE_ASSUMPTIONS,
    thresholds: EvidenceThresholds = EVIDENCE_THRESHOLDS,
) -> FeatureSet:
    """The three feature frames at snap.as_of, from `snap` only (and `source` for the reference
    model, at the same instant)."""
    if source is not None and source.as_of != snap.as_of:
        raise ValueError("the source store must be read at the same decision time")
    lines = line_frame(snap)
    behaviour = behaviour_features(snap, lines)
    source_lines = line_frame(source) if source is not None else None
    priced = observed_line_probabilities(lines, snap.as_of, "reference", config, source_lines)
    res = residuals(priced.lines, customers=behaviour["customer_id"].tolist())
    merged = behaviour.merge(res, on="customer_id", how="left", validate="one_to_one")
    if not (merged["n_observations_x"] == merged["n_observations_y"]).all():
        raise RuntimeError("the residual and the features disagree on the observed lines")
    merged = merged.drop(columns="n_observations_y").rename(
        columns={"n_observations_x": "n_observations"}
    )
    merged["reference_model_version"] = priced.version
    return FeatureSet(
        snap.as_of,
        merged,
        customer_value(snap, value_assumptions),
        evidence_quality(snap, lines, thresholds, clusters),
        priced,
    )


def _table(name: str, frame: pd.DataFrame, as_of: datetime) -> pa.Table:
    schema = SCHEMAS[name]
    data = frame.assign(as_of=pd.Timestamp(as_of))
    return pa.Table.from_pandas(data[schema.names], schema=schema, preserve_index=False)


def tables(sets: Sequence[FeatureSet]) -> dict[str, pa.Table]:
    """The rows of every decision time, per contract table."""
    parts: dict[str, list[pa.Table]] = {name: [] for name in TABLES}
    for fs in sets:
        parts[TABLES[0]].append(_table(TABLES[0], fs.customer_features, fs.as_of))
        parts[TABLES[1]].append(_value_table(fs.value, fs.as_of))
        parts[TABLES[2]].append(_evidence_table(fs.evidence, fs.as_of))
    return {name: pa.concat_tables(chunks) for name, chunks in parts.items()}


def provenance(store: StoreSnapshot, as_ofs: Sequence[datetime], source: str = "") -> Provenance:
    """Origin names the code versions and the store it was computed from (rule 11); the period
    runs from the store's first day to the last decision time."""
    base = store.provenance["store/orders"]
    origin = (
        f"ReturnRing {FEATURES_VERSION} (pipeline.features; reference model {MODEL_VERSION}; "
        f"value {VALUE_ASSUMPTIONS.version}; evidence {EVIDENCE_THRESHOLDS.version}"
        f"{'; reference trained on ' + source if source else ''}), computed from: {base.origin}"
    )
    last = (max(as_ofs) - timedelta(microseconds=1)).date()
    return Provenance(origin, base.period_start, max(last, base.period_start), base.nature)


def clusters_at(root: Path | str | None, as_of: datetime) -> pd.DataFrame | None:
    """features/clusters rows computed exactly at `as_of`, if that file exists under root."""
    if root is None or not (Path(root) / "features" / "clusters.parquet").exists():
        return None
    table, _ = read_table("features/clusters", root)
    frame = table.select(["customer_id", "cluster_id", "as_of"]).to_pandas()
    rows = frame.loc[frame["as_of"] == pd.Timestamp(utc(as_of))]
    return rows[["customer_id", "cluster_id"]] if len(rows) else None


def build(
    store_root: Path | str,
    out_root: Path | str,
    as_ofs: Sequence[datetime],
    *,
    source_root: Path | str | None = None,
    clusters_root: Path | str | None = None,
    config: TrainConfig | None = None,
) -> dict[str, Any]:
    """Compute and write the three tables at every decision time; return a summary."""
    instants = sorted({utc(t) for t in as_ofs})
    if not instants:
        raise ValueError("at least one decision time is needed")
    full = load_snapshot(store_root, instants[-1])
    source_full = load_snapshot(source_root, instants[-1]) if source_root is not None else None
    sets = []
    for t in instants:
        source = source_full.restrict(t) if source_full is not None else None
        clusters = clusters_at(clusters_root, t)
        sets.append(features_at(full.restrict(t), config=config, source=source, clusters=clusters))
    origin = "" if source_full is None else source_full.provenance["store/orders"].origin
    prov = provenance(full, instants, origin)
    written = {}
    for name, table in tables(sets).items():
        written[name] = os.fspath(write_table(name, table, out_root, prov))
    return {
        "nature": prov.nature,
        "origin": prov.origin,
        "period": [prov.period_start.isoformat(), prov.period_end.isoformat()],
        "as_of": [t.isoformat() for t in instants],
        "files": written,
        "rows": {
            t.isoformat(): {
                "customers": len(fs.customer_features),
                "observed_lines": len(fs.priced.lines),
                "reference_model": fs.priced.version,
                "insufficient_evidence": int(
                    (fs.evidence["evidence_quality"] == "insufficient").sum()
                ),
                "linked_accounts_known": bool(fs.evidence["n_observations_linked"].notna().any()),
            }
            for t, fs in zip(instants, sets, strict=True)
        },
    }
