"""features/product_anomalies at each decision time (US-208; ADR-13; rule 7, MOD-08).

load_sales reads the three store tables the detector needs with
contracts.io.read_table(..., as_of=...), so no row known after the decision time is ever read.
anomalies_at(snap, T) narrows the snapshot to T (StoreSnapshot.restrict, the same filter) and
returns the flagged cells as contract rows: one per (variant, reason, sales week) with n, observed,
expected and z of spec 8.9, status "open" and as_of = T. The status moves to task_opened and
resolved in the catalogue workflow (US-606), never here: a fresh computation cannot know that a
task was opened, and a decrease of returns alone does not resolve an anomaly.

build() writes every decision time into one file; the default decision times are the Mondays
00:00 UTC of the store period and the end of the period, one run a week as the detector's unit.
Running services may read this file; it holds no customer and no ground truth (rule 12).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
import pyarrow as pa

from contracts.io import Provenance, read_table, write_table
from contracts.parquet import SCHEMAS
from pipeline.anomalies.detect import (
    ANOMALY_THRESHOLDS,
    AnomalyThresholds,
    cohort_cells,
    flagged,
    sales_frame,
)
from pipeline.features.snapshot import StoreSnapshot, to_frame, utc

ANOMALIES_VERSION: Final = "anomalies-v1"
TABLE: Final = "features/product_anomalies"
STORE_TABLES: Final = ("store/variants", "store/order_lines", "store/returns")
OPEN: Final = "open"


def load_sales(root: Path | str, as_of: datetime) -> StoreSnapshot:
    """The store tables of the detector as known at `as_of` (rows with available_at <= as_of)."""
    as_of = utc(as_of)
    frames: dict[str, pd.DataFrame] = {}
    provenance: dict[str, Provenance] = {}
    for name in STORE_TABLES:
        table, prov = read_table(name, root, as_of=as_of)
        frames[name] = to_frame(name, table)
        provenance[name] = prov
    return StoreSnapshot(as_of, frames, provenance)


def cells_at(snap: StoreSnapshot, as_of: datetime, min_sales: int) -> pd.DataFrame:
    """Every cell with at least min_sales sales in its week, computed from the snapshot narrowed
    to as_of."""
    at = snap.restrict(as_of) if utc(as_of) != snap.as_of else snap
    return cohort_cells(sales_frame(at), at.as_of, min_sales=min_sales)


def anomaly_rows(
    cells: pd.DataFrame, as_of: datetime, thresholds: AnomalyThresholds
) -> pd.DataFrame:
    """The flagged cells as rows of features/product_anomalies at as_of."""
    rows = cells.loc[flagged(cells, thresholds)]
    return pd.DataFrame(
        {
            "variant_id": rows["variant_id"].astype(str),
            "reason": rows["reason"].astype(str),
            "week": rows["week"].dt.date,
            "observed": rows["observed"].astype(np.int32),
            "expected": rows["expected"].astype(np.float64),
            "z": rows["z"].astype(np.float64),
            "n": rows["n"].astype(np.int32),
            "status": OPEN,
            "as_of": pd.Timestamp(utc(as_of)),
        }
    ).reset_index(drop=True)


def anomalies_at(
    snap: StoreSnapshot, as_of: datetime, thresholds: AnomalyThresholds = ANOMALY_THRESHOLDS
) -> pd.DataFrame:
    """features/product_anomalies rows at as_of, from the snapshot narrowed to as_of."""
    return anomaly_rows(cells_at(snap, as_of, thresholds.min_sales), as_of, thresholds)


def weekly_decision_times(first: datetime, end: datetime) -> list[datetime]:
    """The Mondays 00:00 UTC in (first, end], then `end` itself if it is not a Monday."""
    first, end = utc(first), utc(end)
    day = datetime(first.year, first.month, first.day, tzinfo=UTC)
    monday = day + timedelta(days=(7 - day.weekday()) % 7)
    if monday <= first:
        monday += timedelta(days=7)
    out: list[datetime] = []
    while monday <= end:
        out.append(monday)
        monday += timedelta(days=7)
    if not out or out[-1] != end:
        out.append(end)
    return out


def store_decision_times(root: Path | str) -> list[datetime]:
    """Weekly decision times over the period of <root>/store/order_lines, to the instant after
    its last day."""
    _, prov = read_table("store/order_lines", root)
    midnight = datetime.min.time()
    first = datetime.combine(prov.period_start, midnight, tzinfo=UTC)
    end = datetime.combine(prov.period_end + timedelta(days=1), midnight, tzinfo=UTC)
    return weekly_decision_times(first, end)


def provenance(
    snap: StoreSnapshot, as_ofs: Sequence[datetime], thresholds: AnomalyThresholds
) -> Provenance:
    """Origin names the code, the thresholds and the store (rule 11); the period runs from the
    store's first day to the last decision time."""
    base = snap.provenance["store/order_lines"]
    origin = (
        f"ReturnRing {ANOMALIES_VERSION} (pipeline.anomalies; thresholds {thresholds.version}: "
        f"at least {thresholds.min_sales} sales in the week, z > {thresholds.z:g}"
        f"{', exact Poisson tail' if thresholds.exact_tail else ''}"
        f"{' shared by the reasons of a variant-week' if thresholds.family_wise else ''}), "
        f"computed from: {base.origin}"
    )
    last = (max(as_ofs) - timedelta(microseconds=1)).date()
    return Provenance(origin, base.period_start, max(last, base.period_start), base.nature)


def table(rows: pd.DataFrame) -> pa.Table:
    schema = SCHEMAS[TABLE]
    return pa.Table.from_pandas(rows[schema.names], schema=schema, preserve_index=False)


def build(
    store_root: Path | str,
    out_root: Path | str,
    as_ofs: Sequence[datetime],
    thresholds: AnomalyThresholds = ANOMALY_THRESHOLDS,
) -> dict[str, Any]:
    """Compute and write features/product_anomalies at every decision time; return a summary."""
    instants = sorted({utc(t) for t in as_ofs})
    if not instants:
        raise ValueError("at least one decision time is needed")
    snap = load_sales(store_root, instants[-1])
    parts = [anomalies_at(snap, t, thresholds) for t in instants]
    rows = pd.concat(parts, ignore_index=True)
    prov = provenance(snap, instants, thresholds)
    path = write_table(TABLE, table(rows), out_root, prov)
    return {
        "nature": prov.nature,
        "origin": prov.origin,
        "period": [prov.period_start.isoformat(), prov.period_end.isoformat()],
        "thresholds": thresholds.version,
        "file": os.fspath(path),
        "decision_times": len(instants),
        "rows": len(rows),
        "anomalies": {
            t.isoformat(): {"cells": len(p), "variants": int(p["variant_id"].nunique())}
            for t, p in zip(instants, parts, strict=True)
        },
    }
