"""Export the serving snapshot of a data root (ADR-16; rules 7, 11 and 12).

What the API needs, and nothing else:

- store/*: every store table, rows available at the snapshot instant (as_of);
- features/customer_features, customer_value, evidence_quality, product_anomalies: the snapshots
  computed at or before as_of (the API reads the snapshot before each request, rule 7);
- features/links and features/clusters at as_of and at every customer snapshot a request score
  used (its context_as_of): the case card shows the cluster the engine was given. A snapshot the
  data root lacks is built by pipeline.graph from the store rows available at that instant;
- scores/scores and scores/request_scores at as_of (the batch scoring of ml.scoring);
- models/risk: the customer and request risk models and their manifest (ml.risk), which the API
  runs to score a request that arrives after the batch; not the ablation models A0..A3;
- models/checkout: the personalized line predictor (ml.reference), fitted here at as_of on the
  settled store lines (no label of the ground truth: the outcome of a line is in the store), which
  the checkout runs (ml.reference.checkout, US-401);
- reports/evaluation.json: the FAIR-02 aggregates of the offline evaluation of ml.risk
  (artifacts/eval/risk/metrics.json, ml.risk.run.metrics_path: false positives by dimension and
  segment, counts and rates only), which the fairness report and the KPIs serve (US-603,
  US-605); never metrics.json itself. An artifacts root without metrics.json gives a snapshot
  without the report, and both routes say so;
- manifest.json: as_of, the seed, the provenance and snapshots of each table, the model versions
  and the sha256 of every file.

The propensity models of ml.propensity are trained on ASOS for the benchmark of US-201; the API
runs none of them, so none is exported.

Ground truth never enters (rule 12): a source path with a "truth" part, a truth/ contract or a
column that only truth/ tables have is refused, and the tests check every file written.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from contracts.io import Provenance, read_table, write_table
from contracts.parquet import CONTRACT_VERSION, SCHEMAS, TS
from ml.reference.model import TrainConfig, fit, observed_frame
from ml.reference.persist import save_line_model
from ml.risk.run import metrics_path
from ml.scoring.batch import Models
from pipeline.anomalies.detect import ANOMALY_THRESHOLDS
from pipeline.features.lines import line_frame
from pipeline.features.snapshot import load_snapshot, utc
from pipeline.graph.build import build_graph, graph_provenance
from pipeline.graph.clusters import clusters_table
from pipeline.graph.links import links_table
from pipeline.graph.snapshot import load_snapshot as load_graph_snapshot
from pipeline.graph.weights import GraphParams
from pipeline.serving.evaluation import REPORT_FILE, write_report
from pipeline.serving.manifest import LLM_CACHE, write_manifest

STORE_TABLES: Final = tuple(name for name in SCHEMAS if name.startswith("store/"))
FEATURE_TABLES: Final = (
    "features/customer_features",
    "features/customer_value",
    "features/evidence_quality",
    "features/product_anomalies",
)
GRAPH_TABLES: Final = ("features/links", "features/clusters")
SCORE_TABLES: Final = ("scores/scores", "scores/request_scores")
SERVED_TABLES: Final = (*STORE_TABLES, *FEATURE_TABLES, *GRAPH_TABLES, *SCORE_TABLES)
RISK_MODELS: Final = ("customer", "request")
RISK_SUFFIXES: Final = (".json", ".calibrator.json", ".meta.json")
RISK_MANIFEST: Final = "manifest.json"
CHECKOUT_ROLE: Final = "personalized"
SUBDIRS: Final = ("store", "features", "scores", "models", "reports")
# Columns only the ground truth has (truth/labels, truth/variants): never in a served table.
TRUTH_COLUMNS: Final = frozenset(
    field.name for name, schema in SCHEMAS.items() if name.startswith("truth/") for field in schema
) - frozenset(field.name for name in SERVED_TABLES for field in SCHEMAS[name])

Log = Callable[[str], None]


class TruthError(ValueError):
    """The export was asked to read or write ground truth (rule 12)."""


def check_source(path: Path) -> Path:
    """A source path the export may read: never under a folder named truth (rule 12)."""
    resolved = path.resolve()
    if "truth" in resolved.parts:
        raise TruthError(f"{path}: the serving snapshot never reads the ground truth (rule 12)")
    return path


def check_columns(name: str, columns: Sequence[str]) -> None:
    if name.startswith("truth/"):
        raise TruthError(f"{name}: a truth table is never served (rule 12)")
    leaked = sorted(set(columns) & TRUTH_COLUMNS)
    if leaked:
        raise TruthError(f"{name}: truth columns {leaked} (rule 12)")


@dataclass(frozen=True)
class Export:
    out: Path
    as_of: datetime
    tables: dict[str, dict[str, Any]]
    versions: dict[str, str]
    report: str | None = None  # reports/evaluation.json when the offline evaluation was found


def _as_of_rows(table: pa.Table, as_of: datetime) -> pa.Table:
    limit = pa.scalar(as_of, type=TS)
    return table.filter(pc.less_equal(table.column("as_of"), limit))


def _instants(table: pa.Table) -> list[str]:
    values = pc.unique(table.column("as_of")).to_pylist()
    return sorted(utc(v).isoformat() for v in values)


def _provenance_json(p: Provenance) -> dict[str, str]:
    return {
        "origin": p.origin,
        "period_start": p.period_start.isoformat(),
        "period_end": p.period_end.isoformat(),
        "nature": p.nature,
    }


def _record(table: pa.Table, provenance: Provenance, as_of_table: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"rows": table.num_rows, "provenance": _provenance_json(provenance)}
    if as_of_table:
        entry["snapshots"] = _instants(table)
    return entry


def scores_as_of(data: Path) -> datetime:
    """The instant of the batch scoring: the one as_of of scores/scores."""
    table, _ = read_table("scores/scores", data)
    found = _instants(table)
    if len(found) != 1:
        raise ValueError(f"scores/scores holds {len(found)} scoring instants, expected one")
    return datetime.fromisoformat(found[0])


def _context_instants(requests: pa.Table) -> list[datetime]:
    contexts = pc.drop_null(requests.column("context_as_of"))
    return sorted({utc(t) for t in pc.unique(contexts).to_pylist()})


def _graph_at(data: Path, instants: Sequence[datetime], log: Log) -> dict[str, pa.Table]:
    """features/links and features/clusters at each instant: the data root's rows when it holds
    the instant, otherwise built by pipeline.graph from the store known then."""
    existing: dict[str, pa.Table] = {}
    for name in GRAPH_TABLES:
        path = data / f"{name}.parquet"
        existing[name] = read_table(name, data)[0] if path.exists() else SCHEMAS[name].empty_table()
    parts: dict[str, list[pa.Table]] = {name: [] for name in GRAPH_TABLES}
    for instant in instants:
        stamp = pa.scalar(instant, type=TS)
        held = {
            name: existing[name].filter(pc.equal(existing[name].column("as_of"), stamp))
            for name in GRAPH_TABLES
        }
        if all(t.num_rows for t in held.values()):
            for name in GRAPH_TABLES:
                parts[name].append(held[name])
            continue
        log(f"building the account graph at {instant.isoformat()} (pipeline.graph)")
        result = build_graph(load_graph_snapshot(data, instant))
        parts["features/links"].append(links_table(result.links))
        parts["features/clusters"].append(clusters_table(result.clusters))
    return {
        name: pa.concat_tables(tables).combine_chunks() if tables else SCHEMAS[name].empty_table()
        for name, tables in parts.items()
    }


def _copy_risk_models(artifacts: Path, out: Path) -> Models:
    source = check_source(artifacts / "risk" / "models")
    target = out / "models" / "risk"
    target.mkdir(parents=True, exist_ok=True)
    names = [f"{m}{suffix}" for m in RISK_MODELS for suffix in RISK_SUFFIXES] + [RISK_MANIFEST]
    for name in names:
        shutil.copyfile(source / name, target / name)
    return Models.load(target)  # the API loads them the same way: fail here, not at startup


def _fit_checkout_model(data: Path, out: Path, as_of: datetime, seed: int) -> str:
    snap = load_snapshot(data, as_of)
    frame = observed_frame(line_frame(snap))
    y = frame["returned"].to_numpy(dtype="float64")
    model = fit(
        frame, y, frame["customer_id"].to_numpy(), CHECKOUT_ROLE, as_of, TrainConfig(seed=seed)
    )
    save_line_model(model, out / "models" / "checkout")
    return model.version


def _first(frame: pd.DataFrame, column: str) -> str:
    values = sorted({str(v) for v in frame[column].dropna()})
    return values[-1] if values else "unknown"


def export(
    data: Path,
    artifacts: Path,
    out: Path,
    *,
    as_of: datetime | None = None,
    seed: int = 42,
    log: Log | None = None,
) -> Export:
    """Write the serving snapshot of `data` (and the models of `artifacts`) under `out`."""
    say = log or (lambda _message: None)
    data, artifacts = check_source(data), check_source(artifacts)
    if out.resolve() == data.resolve() or "truth" in out.resolve().parts:
        raise ValueError(f"{out}: the snapshot goes to a folder of its own, never into data/")
    moment = utc(as_of) if as_of is not None else scores_as_of(data)
    out.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:  # a fresh export; llm_cache/ stays (it is keyed by the llm_case)
        shutil.rmtree(out / sub, ignore_errors=True)
    (out / "manifest.json").unlink(missing_ok=True)
    records: dict[str, dict[str, Any]] = {}

    def put(name: str, table: pa.Table, provenance: Provenance, as_of_table: bool) -> None:
        check_columns(name, table.column_names)
        write_table(name, table, out, provenance)
        records[name] = _record(table, provenance, as_of_table)

    for name in STORE_TABLES:
        table, provenance = read_table(name, data, as_of=moment)
        put(name, table, provenance, as_of_table=False)
    say(f"store: {len(STORE_TABLES)} tables known at {moment.isoformat()}")

    for name in FEATURE_TABLES:
        table, provenance = read_table(name, data)
        put(name, _as_of_rows(table, moment), provenance, as_of_table=True)
    say("features: customer features, value, evidence and product anomalies")

    scores: dict[str, pa.Table] = {}
    for name in SCORE_TABLES:
        table, provenance = read_table(name, data)
        if _instants(table) != [moment.isoformat()]:
            raise ValueError(f"{name} is not scored at {moment.isoformat()}: rescore first")
        scores[name] = table
        put(name, table, provenance, as_of_table=True)
    say(f"scores at {moment.isoformat()}")

    instants = sorted({*_context_instants(scores["scores/request_scores"]), moment})
    graph = _graph_at(data, instants, say)
    graph_prov = graph_provenance(load_graph_snapshot(data, moment), GraphParams())
    for name in GRAPH_TABLES:
        put(name, graph[name], graph_prov, as_of_table=True)
    say(f"account graph at {', '.join(t.isoformat() for t in instants)}")

    models = _copy_risk_models(artifacts, out)
    checkout_version = _fit_checkout_model(data, out, moment, seed)
    say(f"models: {models.customer.version}, {models.request.version}, {checkout_version}")

    metrics = check_source(metrics_path(artifacts))
    report: str | None = None
    if metrics.exists():
        write_report(metrics, out, models.customer.version)
        report = REPORT_FILE
        say(f"offline evaluation: {REPORT_FILE} (FAIR-02 aggregates of {metrics})")
    else:
        say(f"no {metrics}: no evaluation report, so /api/fairness and /api/kpi answer 404")

    features = read_table("features/customer_features", out)[0].to_pandas()
    value = read_table("features/customer_value", out)[0].to_pandas()
    evidence = read_table("features/evidence_quality", out)[0].to_pandas()
    versions = {  # the model_versions of every decision (keys as in the contract examples)
        "risk": models.request.version,  # the request model: the risk the engine reads
        "risk_customer": models.customer.version,
        "checkout": checkout_version,
        "reference": _first(features, "reference_model_version"),
        "value": _first(value, "assumptions_version"),
        "evidence": _first(evidence, "thresholds_version"),
        "anomalies": ANOMALY_THRESHOLDS.version,
    }
    document: dict[str, Any] = {
        "as_of": moment.isoformat(),
        "seed": seed,
        "contract_version": CONTRACT_VERSION,
        "tables": records,
        "models": {
            "risk": {
                name: {
                    "version": getattr(models, name).version,
                    "threshold": getattr(models, name).threshold,
                    "features": list(getattr(models, name).features),
                }
                for name in RISK_MODELS
            },
            "checkout": {CHECKOUT_ROLE: {"version": checkout_version}},
        },
        "versions": versions,
        "reports": {"evaluation": report},
        "llm_cache": f"{LLM_CACHE}/ (python -m apps.api.batch_generate; not hashed here)",
    }
    write_manifest(out, document)
    say(f"manifest: {out / 'manifest.json'}")
    return Export(out, moment, records, versions, report)


def summary(result: Export) -> Mapping[str, Any]:
    return {
        "out": str(result.out),
        "as_of": result.as_of.isoformat(),
        "tables": {name: entry["rows"] for name, entry in sorted(result.tables.items())},
        "versions": result.versions,
        "report": result.report,
    }
