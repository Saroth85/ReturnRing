"""Batch scoring at a decision time T (US-204; spec 8.4 and 8.6; rules 1, 4, 6, 7, 11, 12).

scores/scores: one row per customer known at T, scored by the customer model (A3) on
features/customer_features and features/clusters computed at T, with its evidence quality and
value (features/evidence_quality and features/customer_value at T), the quadrant of
policy.engine.quadrant_for and the top 3 contributions (ml.risk.explain).

scores/request_scores: one row per return request still open at T (no refunded or rejected state
known at T), scored by the request model as known when the request arrived: request features at
its decided_at, customer features of the last snapshot before it (ml.risk.features). The evidence,
value and value threshold are those of that snapshot. A request on the guarantee path (defect or
non-conformity) is listed with its path and no score: it never enters the abuse scoring (rule 4).

Thresholds (spec 8.6): the risk threshold of each model, chosen on validation for precision >= 0.8
(stored with the model; when none reaches it, no one is high risk); the value threshold, the 70th
percentile of the customer value at the snapshot. The quadrant is null when the evidence is
insufficient: standard terms, never safe or suspicious (US-307). The engine decides; this module
only estimates (rule 1). Customer value is read for the quadrant, never as a risk feature (rule 6).

Nothing here reads the ground truth (rule 12).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from contracts.io import Provenance, read_table, write_table
from contracts.parquet import SCHEMAS
from ml.risk.data import (
    GraphSnapshots,
    instants,
    latest_before,
    read_features,
    request_design,
    utc,
)
from ml.risk.explain import check_factors, factors_json, top_factors
from ml.risk.features import customer_frame, matrix
from ml.risk.model import RiskModel
from pipeline.features.requests import request_features
from pipeline.features.snapshot import StoreSnapshot, load_snapshot
from policy.engine import Thresholds, quadrant_for
from policy.types import CaseScores, EvidenceQuality
from policy.types import Path as ReturnPath

CUSTOMER_MODEL: Final = "customer"
REQUEST_MODEL: Final = "request"
VALUE_PERCENTILE: Final = 70.0  # spec 8.6
NOT_SCORED: Final = "not scored: guarantee path (rule 4)"
CLOSED_STATES: Final = ("refunded", "rejected")
ORIGIN: Final = "ReturnRing risk scoring (ml.scoring, US-204)"


@dataclass(frozen=True)
class Models:
    customer: RiskModel
    request: RiskModel

    @classmethod
    def load(cls, directory: Path) -> Models:
        return cls(
            RiskModel.load(directory, CUSTOMER_MODEL), RiskModel.load(directory, REQUEST_MODEL)
        )


@dataclass
class Inputs:
    """The as-of tables of a data root, every snapshot, and the store for the graph."""

    root: Path
    features: pd.DataFrame
    features_provenance: Provenance
    value: pd.DataFrame
    evidence: pd.DataFrame
    graph: GraphSnapshots
    times: list[datetime]  # decision times of features/customer_features


def read_inputs(root: Path, until: datetime) -> Inputs:
    """Read features/customer_features, customer_value, evidence_quality and (if present)
    clusters under root, and the store known at `until` for the snapshots the graph builds."""
    until = utc(until)
    features, provenance = read_features(root, "features/customer_features")
    value, _ = read_features(root, "features/customer_value")
    evidence, _ = read_features(root, "features/evidence_quality")
    existing = None
    if (root / "features" / "clusters.parquet").exists():
        existing, _ = read_features(root, "features/clusters")
    times = [t for t in instants(features) if t <= until]
    return Inputs(
        root, features, provenance, value, evidence, GraphSnapshots(root, until, existing), times
    )


def _rows_at(frame: pd.DataFrame, as_of: datetime, name: str) -> pd.DataFrame:
    rows = frame.loc[frame["as_of"] == pd.Timestamp(utc(as_of))]
    if rows.empty:
        raise ValueError(f"{name}: no rows computed at {as_of.isoformat()}")
    return rows.set_index("customer_id")


def value_threshold(value: pd.DataFrame, as_of: datetime) -> Decimal:
    """The 70th percentile of value_eur at as_of, in cents (spec 8.6)."""
    rows = _rows_at(value, as_of, "features/customer_value")
    q = float(np.percentile(rows["value_eur"].astype(float).to_numpy(), VALUE_PERCENTILE))
    return Decimal(f"{q:.2f}")


def thresholds(model: RiskModel, value: pd.DataFrame, as_of: datetime) -> Thresholds:
    """Thresholds of spec 8.6. A model whose validation never reached the precision target has
    no threshold: 1.0 then flags no one (calibrated risks stay below 1)."""
    risk = model.threshold if model.threshold is not None else 1.0
    return Thresholds(risk=risk, value_eur=value_threshold(value, as_of))


def quadrant(risk: float, value_eur: Decimal, evidence: str, limits: Thresholds) -> str | None:
    """The quadrant of policy.engine.quadrant_for, or None with insufficient evidence."""
    quality = EvidenceQuality(evidence)
    if quality is EvidenceQuality.INSUFFICIENT:
        return None
    return quadrant_for(CaseScores(risk, value_eur, quality), limits).value


def _factors(model: RiskModel, frame: pd.DataFrame) -> list[str]:
    factors = top_factors(model.features, matrix(frame, model.features), model.contributions(frame))
    check = check_factors(factors)
    if len(frame) and not check.passed:
        raise RuntimeError(f"MOD-05: {check}")
    return [factors_json(f) for f in factors]


def score_customers(models: Models, inputs: Inputs, as_of: datetime) -> pd.DataFrame:
    """The scores/scores rows at as_of (one per customer known then)."""
    as_of = utc(as_of)
    frame = customer_frame(inputs.features, inputs.graph.clusters_at(as_of), as_of)
    value = _rows_at(inputs.value, as_of, "features/customer_value")["value_eur"]
    evidence = _rows_at(inputs.evidence, as_of, "features/evidence_quality")["evidence_quality"]
    ids = frame["customer_id"]
    unknown = sorted(set(ids) - set(value.index) | set(ids) - set(evidence.index))
    if unknown:
        raise ValueError(f"customers without value or evidence at {as_of}: {unknown[:3]}")
    risk = models.customer.predict(frame)
    limits = thresholds(models.customer, inputs.value, as_of)
    values = [Decimal(value[c]) for c in ids]
    qualities = [str(evidence[c]) for c in ids]
    out = pd.DataFrame(
        {
            "customer_id": ids,
            "risk": risk,
            "evidence_quality": qualities,
            "value_eur": values,
            "quadrant": [
                quadrant(float(r), v, q, limits)
                for r, v, q in zip(risk, values, qualities, strict=True)
            ],
            "shap_top3": _factors(models.customer, frame),
            "model_version": models.customer.version,
            "as_of": pd.Timestamp(as_of),
        }
    )
    return out.sort_values("customer_id", kind="stable").reset_index(drop=True)


def open_return_ids(root: Path, as_of: datetime) -> list[str]:
    """Return requests known at as_of with no refunded or rejected state known then."""
    returns, _ = read_table("store/returns", root, as_of=as_of)
    events, _ = read_table("store/return_events", root, as_of=as_of)
    closed = events.filter(pc.is_in(events.column("state"), value_set=pa.array(CLOSED_STATES)))
    done = set(closed.column("return_id").to_pylist())
    return sorted(r for r in returns.column("return_id").to_pylist() if r not in done)


def score_requests(
    models: Models,
    inputs: Inputs,
    snap: StoreSnapshot,
    return_ids: Sequence[str],
    as_of: datetime,
) -> pd.DataFrame:
    """The scores/request_scores rows of `return_ids`, each scored as at its decided_at."""
    as_of = utc(as_of)
    if snap.as_of != as_of:
        raise ValueError("the store snapshot must be read at the scoring time")
    columns = SCHEMAS["scores/request_scores"].names
    if not return_ids:
        return pd.DataFrame({c: [] for c in columns})
    req = request_features(snap, return_ids, include_guarantee=True)
    withdrawal = req.loc[req["path"] == ReturnPath.WITHDRAWAL.value].reset_index(drop=True)
    frame = request_design(withdrawal, inputs.features, inputs.graph, inputs.times)
    risk = models.request.predict(frame)
    factors = _factors(models.request, frame)
    rows: list[dict[str, Any]] = []
    limits: dict[pd.Timestamp, tuple[Thresholds, pd.Series, pd.Series]] = {}
    for i, rec in frame.iterrows():
        context = rec["context_as_of"]
        value: Decimal | None = None
        quality: str | None = None
        quad: str | None = None
        if not pd.isna(context):
            if context not in limits:
                at = context.to_pydatetime()
                limits[context] = (
                    thresholds(models.request, inputs.value, at),
                    _rows_at(inputs.value, at, "features/customer_value")["value_eur"],
                    _rows_at(inputs.evidence, at, "features/evidence_quality")["evidence_quality"],
                )
            limit, values, evidence = limits[context]
            value = Decimal(values[rec["customer_id"]])
            quality = str(evidence[rec["customer_id"]])
            quad = quadrant(float(risk[i]), value, quality, limit)
        rows.append(
            {
                "return_id": rec["return_id"],
                "customer_id": rec["customer_id"],
                "decided_at": rec["decided_at"],
                "path": ReturnPath.WITHDRAWAL.value,
                "context_as_of": context,
                "risk": float(risk[i]),
                "evidence_quality": quality,
                "value_eur": value,
                "quadrant": quad,
                "shap_top3": factors[i],
                "model_version": models.request.version,
            }
        )
    guarantee = req.loc[req["path"] == ReturnPath.GUARANTEE.value]
    for rec in guarantee.itertuples(index=False):
        rows.append(
            {
                "return_id": rec.return_id,
                "customer_id": rec.customer_id,
                "decided_at": rec.decided_at,
                "path": ReturnPath.GUARANTEE.value,
                "context_as_of": pd.NaT,
                "risk": None,
                "evidence_quality": None,
                "value_eur": None,
                "quadrant": None,
                "shap_top3": None,
                "model_version": NOT_SCORED,
            }
        )
    out = pd.DataFrame(rows)
    out["as_of"] = pd.Timestamp(as_of)
    out["risk"] = out["risk"].astype("Float64")
    return out[list(columns)].sort_values("return_id", kind="stable").reset_index(drop=True)


@dataclass
class ScoringResult:
    as_of: datetime
    scores: pd.DataFrame
    requests: pd.DataFrame
    provenance: Provenance
    summary: dict[str, Any]


def scoring_provenance(inputs: Inputs, models: Models, as_of: datetime) -> Provenance:
    """Origin: the method, the model versions and the features it read (rule 11); the period of
    the features up to the decision time; their nature (synthetic for Navigli Fashion)."""
    base = inputs.features_provenance
    origin = (
        f"{ORIGIN}: customer model {models.customer.version}, request model "
        f"{models.request.version}; risk thresholds at precision >= 0.8 on validation, value "
        f"threshold at the 70th percentile; as of {as_of.isoformat()}; computed from: "
        f"{base.origin}"
    )
    last = (as_of - timedelta(microseconds=1)).date()
    end = max(min(last, base.period_end), base.period_start)
    return Provenance(origin, base.period_start, end, base.nature)


def score(root: Path, models_dir: Path, as_of: datetime | None = None) -> ScoringResult:
    """Score every customer and every open return of the data root at as_of (default: the last
    decision time of features/customer_features)."""
    features, _ = read_features(root, "features/customer_features")
    moment = utc(as_of) if as_of is not None else instants(features)[-1]
    models = Models.load(models_dir)
    inputs = read_inputs(root, moment)
    return score_loaded(models, inputs, load_snapshot(root, moment), moment)


def score_loaded(
    models: Models, inputs: Inputs, snap: StoreSnapshot, as_of: datetime
) -> ScoringResult:
    """score() on inputs already read: the store snapshot must be read at as_of."""
    moment = utc(as_of)
    scores = score_customers(models, inputs, moment)
    open_ids = open_return_ids(inputs.root, moment)
    requests = score_requests(models, inputs, snap, open_ids, moment)
    prov = scoring_provenance(inputs, models, moment)
    scored = requests.loc[requests["path"] == ReturnPath.WITHDRAWAL.value]
    summary = {
        "as_of": moment.isoformat(),
        "nature": prov.nature,
        "origin": prov.origin,
        "period": [prov.period_start.isoformat(), prov.period_end.isoformat()],
        "customers": len(scores),
        "quadrants": {
            str(k): int(v) for k, v in scores["quadrant"].fillna("none").value_counts().items()
        },
        "insufficient_evidence": int((scores["evidence_quality"] == "insufficient").sum()),
        "open_returns": len(requests),
        "open_returns_scored": len(scored),
        "open_returns_guarantee_path": int((requests["path"] == ReturnPath.GUARANTEE.value).sum()),
        "open_returns_without_snapshot": int(scored["context_as_of"].isna().sum()),
        "models": {"customer": models.customer.version, "request": models.request.version},
        "thresholds": {
            "customer_risk": models.customer.threshold,
            "request_risk": models.request.threshold,
            "value_eur": str(value_threshold(inputs.value, moment)),
        },
        "clusters": {t.isoformat(): s for t, s in sorted(inputs.graph.sources.items())},
        "request_snapshots": sorted(
            {str(t) for t in latest_before(scored["decided_at"], inputs.times).dropna()}
        ),
    }
    return ScoringResult(moment, scores, requests, prov, summary)


def _table(name: str, frame: pd.DataFrame) -> pa.Table:
    schema = SCHEMAS[name]
    return pa.Table.from_pandas(frame[schema.names], schema=schema, preserve_index=False)


def write(result: ScoringResult, out_root: Path) -> list[Path]:
    """Write scores/scores and scores/request_scores under out_root (contracts.io)."""
    return [
        write_table(
            "scores/scores", _table("scores/scores", result.scores), out_root, result.provenance
        ),
        write_table(
            "scores/request_scores",
            _table("scores/request_scores", result.requests),
            out_root,
            result.provenance,
        ),
    ]


def summary_json(result: ScoringResult) -> str:
    return json.dumps(result.summary, indent=2, sort_keys=True)
