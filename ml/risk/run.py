"""Offline training and evaluation of US-204 (spec 8.4, 8.6; MOD-04 to MOD-08, MOD-11, MOD-12,
FAIR-01 to FAIR-03). An offline job: it reads the ground truth (rule 12 as amended by ADR-13).

1. Split (ml.risk.split): groups of linked accounts (graph clusters at the evaluation time, rings
   and families of the ground truth) go to train, validation or test, stratified, seed 42.
2. Customer unit, time-ordered: the models learn from the customer rows computed at the train end
   (the first nine months) of the train groups; early stopping, isotonic calibration and the risk
   threshold use the validation groups at the same instant; the test groups are evaluated on their
   rows computed at the evaluation time (the end of the last three months). The ablation A0..A4
   runs on exactly these rows; A4 adds the GraphSAGE embeddings of features/customer_embeddings
   (`python -m ml.gnn embed`, US-202) at the same instants, and is reported as not run when that
   file is absent (RunConfig.embeddings: its data root, default the main data root; the OOD file
   under <root>/ood).
3. Request unit: the withdrawal-path requests that arrived before the train end train and validate
   the request model; the test groups' requests of the last three months evaluate it. Each request
   is scored as known when it arrived (ml.risk.features.request_frame).
4. Out-of-distribution store (US-105, MOD-12): every customer and every request of the last three
   months, scored by the models trained on the main store, reported on rows of their own. Nothing
   is fitted or chosen on it.
5. The product models (customer = A3, request) are saved, the batch scoring (ml.scoring) runs at
   the evaluation time, and FAIR-02/FAIR-03 are measured on its output for the held-out customers.
6. Every month-end snapshot (review F21): the customers are scored with the product model, and
   the golden F-ROSSI check and FAIR-03 are measured per instant. Instants before the train end
   are in-sample backtests (the models saw those labels), marked as such.

Every number is synthetic: measured on the generated Navigli Fashion store (rule 11). The OOD
rows are also split by abusive behaviour (review F4): the wardrober rules of the generator are
the same in both stores, only the rings change, so the pooled OOD row mixes in-distribution
wardrobers with shifted rings.

The metrics document goes to <out>/eval/risk/metrics.json: offline evaluation outputs live under
artifacts/eval/, apart from the models a service may load (rule 12, review F2). It holds counts
and metrics, never a customer listed with its ground truth.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from contracts.files import atomic_write
from contracts.io import Provenance
from ml.risk.data import instants, read_features, request_design, utc
from ml.risk.embeddings import FileEmbeddingSource, embedding_source
from ml.risk.explain import FactorCheck, check_factors, top_factors
from ml.risk.fairness import RESTRICTIVE, counterfactual_quadrants, evaluate_scores, fair03
from ml.risk.features import (
    ABLATION,
    EMBEDDING_PREFIX,
    REQUEST_MODEL_FEATURES,
    FeatureSet,
    customer_frame,
    matrix,
    with_embeddings,
)
from ml.risk.labels import ABUSIVE, has_labels, ood_truth_root, read_labels
from ml.risk.metrics import RiskMetrics, evaluate
from ml.risk.model import RiskModel, RuleModel, TrainConfig, fit_rules, fit_xgboost
from ml.risk.split import SplitConfig, shared_keys, split_customers, split_keys
from ml.scoring.batch import (
    CUSTOMER_MODEL,
    REQUEST_MODEL,
    Inputs,
    Models,
    ScoringResult,
    read_inputs,
    score_customers,
    score_loaded,
    write,
)
from pipeline.features.requests import request_features
from pipeline.features.snapshot import StoreSnapshot, load_snapshot

EVAL_MONTHS: Final = 3
MANIFEST: Final = "manifest.json"
FORMAT: Final = "returnring.risk.manifest.v1"
EVAL_DIR: Final = "eval"  # <out>/eval: offline evaluation outputs, never mounted by a service
Log = Callable[[str], None]
Model = RiskModel | RuleModel


def metrics_path(out: Path) -> Path:
    """Where the metrics document of a run under the artifacts root `out` is written."""
    return out / EVAL_DIR / "risk" / "metrics.json"


@dataclass(frozen=True)
class RunConfig:
    train_end: datetime | None = None  # default: EVAL_MONTHS before eval_end
    eval_end: datetime | None = None  # default: the last snapshot of customer_features
    split: SplitConfig = field(default_factory=SplitConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    # Root of features/customer_embeddings for A4 (default: the main data root); the OOD
    # embeddings are under <root>/ood, like the OOD store under <data>/ood.
    embeddings: Path | None = None


def months_before(moment: datetime, months: int) -> datetime:
    total = moment.year * 12 + (moment.month - 1) - months
    return moment.replace(year=total // 12, month=total % 12 + 1)


@dataclass
class Store:
    """One store (main or OOD) as the job sees it."""

    name: str
    root: Path
    inputs: Inputs
    labels: pd.DataFrame
    snap: StoreSnapshot
    value: pd.Series  # customer value at the evaluation time (for who counts as high-value)


def _value_at(inputs: Inputs, as_of: datetime) -> pd.Series:
    rows = inputs.value.loc[inputs.value["as_of"] == pd.Timestamp(as_of)]
    return rows.set_index("customer_id")["value_eur"].astype(float)


def load_store(name: str, root: Path, truth_root: Path, eval_end: datetime) -> Store:
    inputs = read_inputs(root, eval_end)
    return Store(
        name,
        root,
        inputs,
        read_labels(truth_root),
        load_snapshot(root, eval_end),
        _value_at(inputs, eval_end),
    )


def high_value_cut(store: Store, customers: pd.Index) -> float:
    """The 70th percentile of value among the non-abusive customers of `customers`."""
    negatives = customers[store.labels["abusive"].reindex(customers).to_numpy() == 0]
    return float(np.quantile(store.value.reindex(negatives).to_numpy(), 0.7))


def _evaluate(model: Model, frame: pd.DataFrame, store: Store, cut: float) -> RiskMetrics:
    y = store.labels["abusive"].reindex(frame["customer_id"]).to_numpy()
    value = store.value.reindex(frame["customer_id"]).to_numpy()
    if np.isnan(value).any() or pd.isna(y).any():
        raise ValueError(f"{store.name}: rows without a label or a value")
    high = (y == 0) & (value >= cut)
    return evaluate(
        y, model.predict(frame), model.threshold, value, rank=model.raw(frame), high_value=high
    )


def _labels_of(store: Store, frame: pd.DataFrame) -> npt.NDArray[np.int8]:
    return np.asarray(store.labels["abusive"].reindex(frame["customer_id"]).to_numpy(), np.int8)


def by_behaviour(model: Model, frame: pd.DataFrame, store: Store, cut: float) -> dict[str, Any]:
    """The metrics of each abusive behaviour against every non-abusive row of `frame` (review
    F4): one row per behaviour, so a pooled number cannot hide that one behaviour carries it."""
    behaviour = store.labels["behaviour"].reindex(frame["customer_id"]).to_numpy()
    abusive = store.labels["abusive"].reindex(frame["customer_id"]).to_numpy() == 1
    out: dict[str, Any] = {}
    for name in ABUSIVE:
        keep = (behaviour == name) | ~abusive
        part = frame.loc[keep].reset_index(drop=True)
        if (behaviour[keep] == name).any():
            out[name] = _evaluate(model, part, store, cut).as_dict()
    return out


def _with_behaviours(
    entry: dict[str, Any], key: str, model: Model, frame: pd.DataFrame, store: Store, cut: float
) -> None:
    entry[key] = _evaluate(model, frame, store, cut).as_dict()
    entry[f"{key}_by_behaviour"] = by_behaviour(model, frame, store, cut)


def _factor_check(model: RiskModel, frame: pd.DataFrame) -> FactorCheck:
    rows = top_factors(model.features, matrix(frame, model.features), model.contributions(frame))
    return check_factors(rows)


def gain_shares(model: RiskModel, top: int = 5) -> list[tuple[str, float]]:
    raw = model.booster.get_score(importance_type="total_gain")
    gains = {name: float(v) for name, v in raw.items() if isinstance(v, int | float)}
    total = sum(gains.values()) or 1.0
    ranked = sorted(gains.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return [(name, value / total) for name, value in ranked]


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True, default=str) + "\n"
    atomic_write(path, lambda out: out.write(text.encode("utf-8")))


def _provenance(p: Provenance) -> dict[str, str]:
    return {
        "origin": p.origin,
        "lineage": p.chain,  # the full chain of origins (ADR-13 amendment 5)
        "period": f"{p.period_start.isoformat()}..{p.period_end.isoformat()}",
        "nature": p.nature,
    }


def _context_lags(frame: pd.DataFrame) -> dict[str, Any]:
    known = frame["context_as_of"].notna()
    lag = (frame.loc[known, "decided_at"] - frame.loc[known, "context_as_of"]).dt.total_seconds()
    days = lag / 86_400.0
    strictly_before = bool((lag > 0).all())
    return {
        "requests": len(frame),
        "without_snapshot": int((~known).sum()),
        "median_days_since_snapshot": float(days.median()) if len(days) else None,
        "max_days_since_snapshot": float(days.max()) if len(days) else None,
        "snapshot_strictly_before_request": strictly_before,
    }


def run(
    data: Path,
    out: Path,
    scores_out: Path | None,
    config: RunConfig | None = None,
    log: Log = lambda _: None,
) -> dict[str, Any]:
    """Train, evaluate, save the models, score at the evaluation time and measure fairness.
    Returns the metrics document (also written to <out>/risk/metrics.json)."""
    cfg = config or RunConfig()
    features, _ = read_features(data, "features/customer_features")
    times = instants(features)
    eval_end = utc(cfg.eval_end) if cfg.eval_end else times[-1]
    train_end = utc(cfg.train_end) if cfg.train_end else months_before(eval_end, EVAL_MONTHS)
    for moment in (train_end, eval_end):
        if moment not in times:
            raise ValueError(f"no features/customer_features snapshot at {moment.isoformat()}")
    log(f"train end {train_end.isoformat()}, evaluation end {eval_end.isoformat()}")
    main = load_store("main", data, data, eval_end)
    ood_root, ood_truth = data / "ood", ood_truth_root(data)
    ood = (
        load_store("ood", ood_root, ood_truth, eval_end)
        if (ood_root / "features" / "customer_features.parquet").exists() and has_labels(ood_truth)
        else None
    )

    emb_root = cfg.embeddings or data
    emb_main = embedding_source(emb_root, main.inputs.graph.provenance["store/identifiers"])
    emb_ood = (
        embedding_source(emb_root / "ood", ood.inputs.graph.provenance["store/identifiers"])
        if ood is not None
        else None
    )
    if emb_main is not None:
        log(f"A4: GraphSAGE embeddings from {emb_main.describe()['file']}")

    # ---- split (MOD-07) ----
    clusters_eval = main.inputs.graph.clusters_at(eval_end)
    keys = split_keys(clusters_eval, main.labels)
    split = split_customers(
        clusters_eval["customer_id"], keys, main.labels["behaviour"], cfg.split
    ).set_index("customer_id")
    side = split["side"]
    mod07 = shared_keys(side, keys)
    log(f"split: {side.value_counts().to_dict()}")

    # ---- customer rows ----
    at_train = customer_frame(
        main.inputs.features, main.inputs.graph.clusters_at(train_end), train_end
    )
    at_eval = customer_frame(main.inputs.features, clusters_eval, eval_end)
    train = at_train.loc[at_train["customer_id"].map(side) == "train"].reset_index(drop=True)
    val = at_train.loc[at_train["customer_id"].map(side) == "validation"].reset_index(drop=True)
    test = at_eval.loc[at_eval["customer_id"].map(side) == "test"].reset_index(drop=True)
    y_train, y_val = _labels_of(main, train), _labels_of(main, val)
    cut_main = high_value_cut(main, pd.Index(test["customer_id"]))
    ood_frame = None
    cut_ood = 0.0
    if ood is not None:
        ood_frame = customer_frame(
            ood.inputs.features, ood.inputs.graph.clusters_at(eval_end), eval_end
        )
        cut_ood = high_value_cut(ood, pd.Index(ood_frame["customer_id"]))

    # ---- ablation A0..A4 (MOD-04) ----
    models: dict[str, Model] = {}
    ablation: list[dict[str, Any]] = []
    for fs in ABLATION:
        row: dict[str, Any] = {
            "set": fs.name,
            "learner": fs.learner,
            "purpose": fs.purpose,
            "features": list(fs.features),
        }
        if fs.needs_embeddings:
            if emb_main is None:
                row["status"] = (
                    "not run: no features/customer_embeddings (python -m ml.gnn embed, US-202)"
                )
                ablation.append(row)
                continue
            log(f"training {fs.name} with GraphSAGE embeddings")
            a4, a4_frames = _fit_a4(
                fs, emb_main, (train, val, test), (y_train, y_val), (train_end, eval_end), cfg
            )
            models[fs.name] = a4
            _a4_row(row, a4, a4_frames, emb_main)
            _with_behaviours(row, "test", a4, a4_frames[2], main, cut_main)
            if ood is not None and ood_frame is not None and emb_ood is not None:
                ood4, _ = with_embeddings(ood_frame, emb_ood, eval_end)
                row["embedding_coverage"]["ood"] = _coverage(ood4, a4.features)
                _with_behaviours(row, "ood", a4, ood4, ood, cut_ood)
            ablation.append(row)
            continue
        log(f"training {fs.name}")
        model: Model
        if fs.learner == "rules":
            model = fit_rules(fs.name, train, y_train, val, y_val, train_end, cfg.train)
            row["rules"] = [r.describe() for r in model.baseline.rules]
        else:
            model = fit_xgboost(
                fs.name, fs.features, train, y_train, val, y_val, train_end, cfg.train
            )
            row["rounds"] = model.best_iteration + 1
            row["gain_shares"] = gain_shares(model)
        models[fs.name] = model
        row["status"] = "run"
        row["version"] = model.version
        row["threshold"] = model.threshold
        _with_behaviours(row, "test", model, test, main, cut_main)
        if ood is not None and ood_frame is not None:
            _with_behaviours(row, "ood", model, ood_frame, ood, cut_ood)
        ablation.append(row)

    a3 = models["A3"]
    assert isinstance(a3, RiskModel)
    customer = replace(a3, name=CUSTOMER_MODEL)

    # ---- request unit ----
    log("request features")
    req = request_features(main.snap)
    design = request_design(req, main.inputs.features, main.inputs.graph, times)
    design_side = design["customer_id"].map(side)
    early = design["decided_at"] < pd.Timestamp(train_end)
    r_train = design.loc[(design_side == "train") & early].reset_index(drop=True)
    r_val = design.loc[(design_side == "validation") & early].reset_index(drop=True)
    r_test = design.loc[(design_side == "test") & ~early].reset_index(drop=True)
    log("training the request model")
    request = fit_xgboost(
        REQUEST_MODEL,
        REQUEST_MODEL_FEATURES,
        r_train,
        _labels_of(main, r_train),
        r_val,
        _labels_of(main, r_val),
        train_end,
        cfg.train,
    )
    request_rows: list[dict[str, Any]] = []
    candidates: list[tuple[str, Model]] = [
        ("request model (A3 features + request features)", request),
        ("customer model A3 on the last snapshot before the request", a3),
        ("A0 rules on the last snapshot before the request", models["A0"]),
    ]
    r_ood = None
    if ood is not None:
        ood_req = request_features(ood.snap)
        ood_design = request_design(
            ood_req, ood.inputs.features, ood.inputs.graph, ood.inputs.times
        )
        r_ood = ood_design.loc[ood_design["decided_at"] >= pd.Timestamp(train_end)].reset_index(
            drop=True
        )
    for label, model in candidates:
        entry: dict[str, Any] = {
            "model": label,
            "version": model.version,
            "threshold": model.threshold,
        }
        _with_behaviours(entry, "test", model, r_test, main, cut_main)
        if ood is not None and r_ood is not None:
            _with_behaviours(entry, "ood", model, r_ood, ood, cut_ood)
        request_rows.append(entry)

    # ---- MOD-05 ----
    mod05 = {
        "customers_test": asdict(_factor_check(customer, test)),
        "requests_test": asdict(_factor_check(request, r_test)),
    }
    if ood_frame is not None and r_ood is not None:
        mod05["customers_ood"] = asdict(_factor_check(customer, ood_frame))
        mod05["requests_ood"] = asdict(_factor_check(request, r_ood))

    # ---- save the models ----
    models_dir = out / "risk" / "models"
    saved: list[str] = []
    for model in [*models.values(), customer, request]:
        saved += [str(p) for p in model.save(models_dir)]
    manifest = {
        "format": FORMAT,
        "customer_model": CUSTOMER_MODEL,
        "request_model": REQUEST_MODEL,
        "train_end": train_end.isoformat(),
        "eval_end": eval_end.isoformat(),
        "split": asdict(cfg.split),
        "train": asdict(cfg.train),
        "data": _provenance(main.inputs.features_provenance),
    }
    _write_json(models_dir / MANIFEST, manifest)

    # ---- batch scoring and fairness ----
    log("batch scoring at the evaluation time")
    result = score_loaded(Models(customer, request), main.inputs, main.snap, eval_end)
    written = [str(p) for p in write(result, scores_out)] if scores_out is not None else []
    held = side == "test"
    f02, f03 = evaluate_scores(result, main.inputs, main.snap, customer, main.labels, held)
    log("golden cases and FAIR-03 at every month end")
    history = over_time(customer, main, [t for t in times if t <= eval_end], held, train_end)

    doc: dict[str, Any] = {
        "story": "US-204",
        "nature": "synthetic",
        "train_end": train_end.isoformat(),
        "eval_end": eval_end.isoformat(),
        "data": {
            "main": _provenance(main.inputs.features_provenance),
            "ood": None if ood is None else _provenance(ood.inputs.features_provenance),
            "clusters": {k.isoformat(): v for k, v in sorted(main.inputs.graph.sources.items())},
        },
        "split": {
            "config": asdict(cfg.split),
            "customers": {
                f"{s}/{t}": int(n)
                for (s, t), n in split.groupby(["side", "stratum"]).size().items()
            },
            # Counts of keys on two sides: the keys of rings and families are ground truth.
            "mod07_shared_keys": {kind: len(values) for kind, values in mod07.items()},
        },
        "customer_rows": {
            "train": {"rows": len(train), "positives": int(y_train.sum())},
            "validation": {"rows": len(val), "positives": int(y_val.sum())},
            "test": {"rows": len(test), "positives": int(_labels_of(main, test).sum())},
            "ood": None
            if ood_frame is None or ood is None
            else {"rows": len(ood_frame), "positives": int(_labels_of(ood, ood_frame).sum())},
            "high_value_cut_eur": {"test": cut_main, "ood": cut_ood if ood is not None else None},
        },
        "ablation": ablation,
        "embeddings": {
            "main": None if emb_main is None else emb_main.describe(),
            "ood": None if emb_ood is None else emb_ood.describe(),
        },
        "request_rows": {
            "train": {"rows": len(r_train), "positives": int(_labels_of(main, r_train).sum())},
            "validation": {"rows": len(r_val), "positives": int(_labels_of(main, r_val).sum())},
            "test": {"rows": len(r_test), "positives": int(_labels_of(main, r_test).sum())},
            "ood": None
            if r_ood is None or ood is None
            else {"rows": len(r_ood), "positives": int(_labels_of(ood, r_ood).sum())},
            "context_main": _context_lags(design),
            "context_ood": None if r_ood is None else _context_lags(r_ood),
        },
        "request_models": request_rows,
        "request_gain_shares": gain_shares(request),
        "customer_gain_shares": gain_shares(customer),
        "mod05": mod05,
        "fair02": f02.as_dict(),
        "fair03": [f.as_dict() for f in f03],
        "scoring": result.summary,
        "over_time": history,
        "golden": golden(result, customer, request, side, history),
        "artifacts": {"models": saved, "scores": written},
    }
    _write_json(metrics_path(out), doc)
    return doc


def _coverage(frame: pd.DataFrame, features: Sequence[str]) -> dict[str, int]:
    """Rows, and rows without an embedding (a customer the embedding file does not hold)."""
    columns = [f for f in features if f.startswith(EMBEDDING_PREFIX)]
    missing = frame[columns].isna().any(axis=1)
    return {"rows": len(frame), "without_embedding": int(missing.sum())}


def _fit_a4(
    fs: FeatureSet,
    source: FileEmbeddingSource,
    frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
    labels: tuple[npt.NDArray[np.int8], npt.NDArray[np.int8]],
    moments: tuple[datetime, datetime],
    cfg: RunConfig,
) -> tuple[RiskModel, tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]:
    """A4 on the rows of A0..A3: the train and validation rows (computed at the train end) take
    the embeddings at the train end, the test rows those at the evaluation time (rule 7)."""
    train, val, test = frames
    train_end, eval_end = moments
    train4, names = with_embeddings(train, source, train_end)
    val4, _ = with_embeddings(val, source, train_end)
    test4, _ = with_embeddings(test, source, eval_end)
    model = fit_xgboost(fs.name, names, train4, labels[0], val4, labels[1], train_end, cfg.train)
    return model, (train4, val4, test4)


def _a4_row(
    row: dict[str, Any],
    model: RiskModel,
    frames: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame],
    source: FileEmbeddingSource,
) -> None:
    """The A4 entry of the ablation: like A1..A3, plus the embeddings' provenance and coverage."""
    row["features"] = list(model.features)
    row["status"] = "run"
    row["version"] = model.version
    row["threshold"] = model.threshold
    row["rounds"] = model.best_iteration + 1
    row["gain_shares"] = gain_shares(model)
    row["embeddings"] = source.describe()
    row["embedding_coverage"] = {
        side: _coverage(frame, model.features)
        for side, frame in zip(("train", "validation", "test"), frames, strict=True)
    }


GOLDEN_IDS: Final = (
    "C-GIULIA",
    *(f"C-MARCO-{i}" for i in range(1, 8)),
    "C-ROSSI-1",
    "C-ROSSI-2",
    "C-ROSSI-3",
    "C-ROSSI-CARD-1",
    "C-ROSSI-CARD-2",
    "C-ROSSI-CARD-3",
    "C-ELENA",
)


def _text(value: object) -> str | None:
    """A string cell, or None for a missing one (pandas string columns hold NaN)."""
    return None if value is None or pd.isna(value) else str(value)


def over_time(
    customer: RiskModel,
    store: Store,
    times: Sequence[datetime],
    held: pd.Series,
    trained_at: datetime,
) -> list[dict[str, Any]]:
    """Per month-end snapshot (review F21): the quadrants the product's customer model gives
    then, the golden accounts' quadrants and FAIR-03 on all and on the held-out customers. An
    instant before the train end is an in-sample backtest: the model was fitted on labels of the
    train groups known later, so it is marked, never read as a held-out result."""
    inputs = store.inputs
    behaviour = store.labels["behaviour"]
    models = Models(customer, customer)  # score_customers reads the customer model only
    out: list[dict[str, Any]] = []
    for t in times:
        scores = score_customers(models, inputs, t)
        alone = counterfactual_quadrants(customer, inputs, scores, t)
        everyone = pd.Series(True, index=scores["customer_id"])
        checks = [
            fair03(scores, alone, behaviour, everyone, "all customers"),
            fair03(scores, alone, behaviour, held, "held-out test customers"),
        ]
        by_id = scores.set_index("customer_id")
        present = [c for c in GOLDEN_IDS if c in by_id.index]
        quadrants = {c: _text(by_id.at[c, "quadrant"]) for c in present}
        threshold = customer.threshold
        marco = [c for c in present if c.startswith("C-MARCO")]
        out.append(
            {
                "as_of": t.isoformat(),
                "in_sample": t < utc(trained_at),
                "customers": len(scores),
                "quadrants": {
                    str(k): int(v)
                    for k, v in scores["quadrant"].fillna("none").value_counts().items()
                },
                "golden_quadrants": quadrants,
                "rossi_restrictive": sorted(
                    c for c in present if c.startswith("C-ROSSI") and quadrants[c] in RESTRICTIVE
                ),
                "marco_accounts": len(marco),
                "marco_high_risk": sum(
                    threshold is not None and float(by_id.at[c, "risk"]) >= threshold for c in marco
                ),
                "fair03": [f.as_dict() for f in checks],
            }
        )
    return out


def golden(
    result: ScoringResult,
    customer: RiskModel,
    request: RiskModel,
    side: pd.Series,
    history: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """The golden cases in the batch scores (demo cases, never a measure of accuracy, US-906);
    the F-ROSSI check holds at every month end of `history` (over_time) as well."""
    scores = result.scores.set_index("customer_id")
    requests = result.requests
    rows: list[dict[str, Any]] = []
    for cid in GOLDEN_IDS:
        if cid not in scores.index:
            continue
        s = scores.loc[cid]
        factors = json.loads(s["shap_top3"]) if isinstance(s["shap_top3"], str) else []
        open_ = requests.loc[requests["customer_id"] == cid]
        rows.append(
            {
                "customer_id": cid,
                "side": _text(side.get(cid)),
                "risk": float(s["risk"]),
                "high_risk": customer.threshold is not None
                and float(s["risk"]) >= customer.threshold,
                "evidence_quality": _text(s["evidence_quality"]),
                "value_eur": str(s["value_eur"]),
                "quadrant": _text(s["quadrant"]),
                "factors": [f["feature"] for f in factors],
                "open_returns": [
                    {
                        "return_id": str(r.return_id),
                        "path": str(r.path),
                        "risk": None if pd.isna(r.risk) else float(r.risk),
                        "quadrant": _text(r.quadrant),
                        "context_as_of": None if pd.isna(r.context_as_of) else str(r.context_as_of),
                    }
                    for r in open_.itertuples(index=False)
                ],
            }
        )
    by = {r["customer_id"]: r for r in rows}
    marco = [by[c] for c in GOLDEN_IDS if c.startswith("C-MARCO") and c in by]
    rossi = [by[c] for c in GOLDEN_IDS if c.startswith("C-ROSSI") and c in by]
    elena = by.get("C-ELENA")
    checks = {
        "C-GIULIA in Q1": by.get("C-GIULIA", {}).get("quadrant") == "Q1",
        "R-MARCO accounts high risk": bool(marco) and all(r["high_risk"] for r in marco),
        "F-ROSSI and F-ROSSI-CARD never in Q3 or Q4, at every month end": bool(rossi)
        and all(r["quadrant"] not in RESTRICTIVE for r in rossi)
        and all(not h["rossi_restrictive"] for h in history),
        "C-ELENA's open return on the guarantee path, not scored": elena is not None
        and bool(elena["open_returns"])
        and all(o["path"] == "guarantee" and o["risk"] is None for o in elena["open_returns"]),
    }
    return {"customers": rows, "checks": checks, "request_threshold": request.threshold}


def ablation_table(doc: dict[str, Any], population: str) -> list[dict[str, Any]]:
    return [r.get(population, {}) for r in doc["ablation"]]


def summarize(doc: dict[str, Any]) -> Sequence[str]:
    out = []
    for row in doc["ablation"]:
        test = row.get("test")
        if test:
            out.append(f"{row['set']}: AUPRC {test['auprc']:.3f}")
    return out
