"""US-201 end to end: features, models A and B, baseline, calibration, metrics, files and report.

run() reads raw/asos_{events,customers,variants}.parquet through the contracts (provenance
checked), trains every model on the same splits (the ASOS train split; a seeded random 10% of it
for early stopping, calibration, thresholds and the choice of the baseline rule), evaluates on the
ASOS test split, and writes:

- <out>/models/propensity_A.json, propensity_B.json: XGBoost JSON models, trees up to the best
  iteration only, with the protocol and the provenance as booster attributes;
- <out>/models/propensity_{A,B}_calibrator.json and propensity_baseline.json;
- <out>/metrics/us-201-propensity.json: every number with origin, period, nature and protocol;
- the Markdown report (docs/results/us-201-propensity.md), rewritten from the metrics.

Models (all with the hyperparameters of train.PAPER_PARAMS):
- A: protocol A features, trained on every train event (paper protocol, benchmarks only);
- B: protocol B features, trained on the decision-time population (features.POPULATIONS);
- B_all_events: protocol B features trained on every event, the selection by the event's own
  return included. A diagnostic of that leak, never saved as a model and never used.
"""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl
import sklearn
import xgboost as xgb

from contracts.io import Provenance
from ml.propensity.baseline import CANDIDATES, SingleFeatureRule, candidate_values, fit_baseline
from ml.propensity.calibration import PROBABILITY_CLIP, IsotonicCalibrator
from ml.propensity.features import (
    POPULATIONS,
    PROTOCOL_LABELS,
    FeatureError,
    Protocol,
    Vocabulary,
    feature_matrix,
    join_events,
    population_masks,
    unknown_categories,
    validation_mask,
)
from ml.propensity.metrics import (
    ECE_BINS,
    ECE_MAX,
    BinaryMetrics,
    best_f1_threshold,
    binary_metrics,
    cross_entropy,
    expected_calibration_error,
)
from ml.propensity.report import render_markdown
from ml.propensity.train import (
    EARLY_STOPPING_ROUNDS,
    MAX_ROUNDS,
    PAPER_PARAMS,
    Device,
    TrainedBooster,
    predict,
    train_booster,
)
from pipeline.ingest.asos_convert import SPLIT_PERIODS
from pipeline.ingest.asos_profile import RawAsos, load_raw

STORY = "US-201"
METRICS_FILE = "metrics/us-201-propensity.json"
REPRODUCE = "python -m ml.propensity --raw data/raw --out artifacts"
PAPER_F1 = 0.774  # arXiv:2302.14096, XGBoost
PAPER_PROTOCOL_F1_MIN = 0.75  # spec US-201 / MOD-01, protocol A only
DIGITS = 6

Log = Callable[[str], None]


@dataclass(frozen=True)
class RunConfig:
    seed: int = 42
    device: Device = "cpu"
    max_rounds: int = MAX_ROUNDS
    validation_fraction: float = 0.1
    threads: int | None = None


@dataclass(frozen=True)
class ModelSpec:
    key: str
    protocol: Protocol
    trained_on: str  # a population of features.POPULATIONS
    title: str
    saved: bool


MODELS = (
    ModelSpec("A", "A", "all", "A, XGBoost", saved=True),
    ModelSpec("B", "B", "decision_time", "B, XGBoost", saved=True),
    ModelSpec(
        "B_all_events",
        "B",
        "all",
        "B trained on every event (selection leak, diagnostic, not used)",
        saved=False,
    ),
)


def _round(value: float) -> float:
    return round(float(value), DIGITS)


def _metrics_dict(m: BinaryMetrics) -> dict[str, Any]:
    return {k: (_round(v) if isinstance(v, float) else v) for k, v in m.as_dict().items()}


def _period(split: str) -> str:
    start, end = SPLIT_PERIODS[split]
    return f"{start.isoformat()}..{end.isoformat()}"


def _provenance_dict(p: Provenance) -> dict[str, str]:
    return {
        "origin": p.origin,
        "period_start": p.period_start.isoformat(),
        "period_end": p.period_end.isoformat(),
        "nature": p.nature,
    }


def _label(provenance: Provenance, protocol: str, split: str = "test") -> dict[str, str]:
    """The provenance of one number (rule 11): origin, period, nature, protocol."""
    return {
        "origin": provenance.origin,
        "nature": provenance.nature,
        "split": split,
        "period": _period(split),
        "protocol": protocol,
    }


def _evaluate(
    y: npt.NDArray[np.int8],
    p: npt.NDArray[np.float64],
    threshold: float,
    masks: dict[str, npt.NDArray[np.bool_]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, mask in masks.items():
        out[name] = (
            None if not mask.any() else _metrics_dict(binary_metrics(y[mask], p[mask], threshold))
        )
    return out


def _raw_calibration(
    y: npt.NDArray[np.int8], raw: npt.NDArray[np.float64], masks: dict[str, npt.NDArray[np.bool_]]
) -> dict[str, Any]:
    """Cross-entropy and ECE of the uncalibrated model output (clipped like the calibrator)."""
    p = np.clip(raw, PROBABILITY_CLIP, 1 - PROBABILITY_CLIP)
    return {
        name: None
        if not mask.any()
        else {
            "cross_entropy": _round(cross_entropy(y[mask], p[mask])),
            "ece": _round(expected_calibration_error(y[mask], p[mask])),
        }
        for name, mask in masks.items()
    }


def _top_gain(booster: xgb.Booster, k: int = 10) -> list[dict[str, Any]]:
    gains: dict[str, float] = {}
    for feature, gain in booster.get_score(importance_type="total_gain").items():
        if isinstance(gain, list):  # vector leaves only; not produced by binary:logistic
            raise TypeError(f"unexpected vector gain for {feature}")
        gains[feature] = float(gain)
    total = sum(gains.values()) or 1.0
    ranked = sorted(gains.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [{"feature": f, "share_of_total_gain": _round(g / total)} for f, g in ranked]


def _share(part: int, whole: int) -> float | None:
    return _round(part / whole) if whole else None


def _data_summary(split_frame: pl.DataFrame, vocabulary: Vocabulary) -> dict[str, Any]:
    n = split_frame.height
    y = pl.col("is_returned")
    own = split_frame.filter(pl.col("selected_by_own_return"))
    counts = split_frame.select(
        y.sum().alias("returned"),
        pl.col("has_customer_features").sum().alias("with_customer_row"),
        pl.col("has_variant_features").sum().alias("with_variant_row"),
        pl.col("fully_featured").sum().alias("fully_featured"),
        pl.col("customer_loo_clipped").sum().alias("customer_loo_clipped"),
        pl.col("variant_loo_clipped").sum().alias("variant_loo_clipped"),
        (pl.col("has_customer_features") & (pl.col("customer_returns_loo") == 0))
        .sum()
        .alias("own_return_only_customer"),
        (pl.col("has_variant_features") & (pl.col("variant_returns_loo") == 0))
        .sum()
        .alias("own_return_only_variant"),
    ).row(0, named=True)
    counts = {k: int(v or 0) for k, v in counts.items()}
    own_returned = int(own.select(y.sum()).item() or 0)
    clipped = pl.col("customer_loo_clipped") | pl.col("variant_loo_clipped")
    own_consistent, own_clipped = own.filter(~clipped), own.filter(clipped)

    def group(frame: pl.DataFrame) -> dict[str, Any]:
        returned = int(frame.select(y.sum()).item() or 0)
        return {"events": frame.height, "returned_share": _share(returned, frame.height)}

    return {
        "events": n,
        "returned_share": _share(counts["returned"], n),
        "with_customer_row": counts["with_customer_row"],
        "with_variant_row": counts["with_variant_row"],
        "fully_featured": counts["fully_featured"],
        "selected_by_own_return": {
            "events": own.height,
            "returned_share": _share(own_returned, own.height),
            "by_customer_row": counts["own_return_only_customer"],
            "by_variant_row": counts["own_return_only_variant"],
            # Rows whose counts hold the event, and rows whose counts had to be clipped.
            "consistent_counts": group(own_consistent),
            "clipped_counts": group(own_clipped),
        },
        "decision_time_events": n - own.height,
        "loo_clipped": {
            "customer": counts["customer_loo_clipped"],
            "variant": counts["variant_loo_clipped"],
        },
        "unknown_categories": unknown_categories(split_frame, vocabulary),
    }


def _environment(config: RunConfig) -> dict[str, Any]:
    cpu = platform.processor() or platform.machine()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    return {
        "device": config.device,
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "xgboost": xgb.__version__,
        "scikit_learn": sklearn.__version__,
        "numpy": np.__version__,
        "polars": pl.__version__,
    }


def _labels_array(frame: pl.DataFrame) -> npt.NDArray[np.int8]:
    return frame["is_returned"].to_numpy().astype(np.int8)


@dataclass(frozen=True)
class TrainingData:
    """The matrices one model trains on: fit rows, and validation rows for early stopping."""

    protocol: Protocol
    trained_on: str
    feature_names: list[str]
    x_fit: npt.NDArray[np.float32]
    y_fit: npt.NDArray[np.int8]
    x_valid: npt.NDArray[np.float32]
    y_valid: npt.NDArray[np.int8]


def _training_matrices(
    train: pl.DataFrame,
    protocol: Protocol,
    trained_on: str,
    vocabulary: Vocabulary,
    valid: npt.NDArray[np.bool_],
) -> TrainingData:
    y = _labels_array(train)
    population = population_masks(train)[trained_on]
    fit, val = population & ~valid, population & valid
    x, names = feature_matrix(train, protocol, vocabulary)
    return TrainingData(protocol, trained_on, names, x[fit], y[fit], x[val], y[val])


def _prepare(raw_dir: Path) -> tuple[RawAsos, pl.DataFrame, pl.DataFrame]:
    """The raw tables (contracts and provenance checked) and the joined train and test events."""
    raw_dir = Path(raw_dir)
    if raw_dir.name != "raw":
        raise FeatureError(f"--raw must be the raw/ folder of a data root, not {raw_dir}")
    raw = load_raw(raw_dir.parent)
    joined = join_events(raw.events, raw.customers, raw.variants)
    train = joined.filter(pl.col("split") == "train")
    test = joined.filter(pl.col("split") == "test")
    if train.height < 2 or test.height == 0:
        raise FeatureError("both ASOS splits are needed: train (2+ events) and test")
    return raw, train, test


def training_data(
    raw_dir: Path, protocol: Protocol = "A", config: RunConfig | None = None
) -> TrainingData:
    """The exact matrices `run` trains model `protocol` on (A: every train event; B: the
    decision-time population), for benchmark B1: time train_booster on them with device "cpu"
    and "cuda", same parameters and seed (ADR-10: B1 uses protocol A)."""
    config = config or RunConfig()
    raw, train, _ = _prepare(raw_dir)
    vocabulary = Vocabulary.from_variants(raw.variants.filter(pl.col("split") == "train"))
    valid = validation_mask(train.height, config.validation_fraction, config.seed)
    spec = next(s for s in MODELS if s.key == protocol)
    return _training_matrices(train, spec.protocol, spec.trained_on, vocabulary, valid)


def _fit_model(
    spec: ModelSpec,
    train: pl.DataFrame,
    test: pl.DataFrame,
    vocabulary: Vocabulary,
    valid: npt.NDArray[np.bool_],
    config: RunConfig,
    log: Log,
) -> tuple[TrainedBooster, IsotonicCalibrator, float, dict[str, Any]]:
    y_test = _labels_array(test)
    data = _training_matrices(train, spec.protocol, spec.trained_on, vocabulary, valid)
    names = data.feature_names
    log(f"{spec.key}: training on {data.y_fit.size} events, {len(names)} features")
    trained = train_booster(
        data.x_fit,
        data.y_fit,
        data.x_valid,
        data.y_valid,
        names,
        device=config.device,
        seed=config.seed,
        max_rounds=config.max_rounds,
        threads=config.threads,
    )
    raw_valid = predict(trained.booster, data.x_valid)
    y_valid = data.y_valid
    del data
    calibrator = IsotonicCalibrator.fit(raw_valid, y_valid)
    p_valid = calibrator.predict(raw_valid)
    threshold = best_f1_threshold(y_valid, p_valid)
    x_test, _ = feature_matrix(test, spec.protocol, vocabulary)
    raw_test = predict(trained.booster, x_test)
    del x_test
    p_test = calibrator.predict(raw_test)
    masks = population_masks(test)
    log(f"{spec.key}: {trained.best_iteration + 1} trees, {trained.train_seconds:.1f} s")
    result = {
        "title": spec.title,
        "protocol": spec.protocol,
        "protocol_label": PROTOCOL_LABELS[spec.protocol],
        "trained_on": spec.trained_on,
        "saved": spec.saved,
        "features": names,
        "training": trained.summary(),
        "threshold": _round(threshold),
        "validation": _metrics_dict(binary_metrics(y_valid, p_valid, threshold)),
        "test": _evaluate(y_test, p_test, threshold, masks),
        "test_uncalibrated": _raw_calibration(y_test, raw_test, masks),
        "top_features_by_gain": _top_gain(trained.booster),
    }
    return trained, calibrator, threshold, result


def _baseline(
    train: pl.DataFrame, test: pl.DataFrame, valid: npt.NDArray[np.bool_]
) -> tuple[SingleFeatureRule, dict[str, Any]]:
    population = population_masks(train)["decision_time"] & valid
    values_train = candidate_values(train)
    y_train, y_test = _labels_array(train), _labels_array(test)
    best, rules = fit_baseline(
        {k: v[population] for k, v in values_train.items()}, y_train[population]
    )
    values_test = candidate_values(test)
    masks = population_masks(test)
    candidates: dict[str, Any] = {}
    for rule in rules:
        p = rule.predict_proba(values_test[rule.feature])
        candidates[rule.feature] = {
            "description": CANDIDATES[rule.feature],
            "rule": rule.rule,
            "threshold": _round(rule.threshold),
            "validation_f1": _round(rule.validation_f1),
            "test": _evaluate(y_test, p, rule.threshold, masks),
        }
    result = {
        "title": f"Baseline rule ({best.feature})",
        "protocol": "B",
        "protocol_label": PROTOCOL_LABELS["B"],
        "trained_on": "decision_time",
        "feature": best.feature,
        "rule": best.rule,
        "threshold": _round(best.threshold),
        "test": candidates[best.feature]["test"],
        "candidates": candidates,
    }
    return best, result


def _go_no_go(models: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    b = models["B"]["test"]["decision_time"]
    base = baseline["test"]["decision_time"]
    candidates = [c["test"]["decision_time"] for c in baseline["candidates"].values()]
    a = models["A"]["test"]["all"]
    if b is None or base is None or None in candidates:
        raise FeatureError("no decision-time test event: the go/no-go cannot be measured")
    best_f1 = max(c["f1"] for c in candidates)
    best_ce = min(c["cross_entropy"] for c in candidates)
    checks = {
        "b_f1_beats_baseline": b["f1"] > base["f1"],
        "b_cross_entropy_beats_baseline": b["cross_entropy"] < base["cross_entropy"],
        "b_f1_beats_every_candidate": b["f1"] > best_f1,
        "b_cross_entropy_beats_every_candidate": b["cross_entropy"] < best_ce,
        "b_ece_within_mod06": b["ece"] <= ECE_MAX,
    }
    return {
        "best_candidate_test_f1": best_f1,
        "best_candidate_test_cross_entropy": best_ce,
        "population": "decision_time",
        "checks": checks,
        "go": all(checks.values()),
        "rule": "ADR-10: protocol B beats the best single-feature leave-one-out rule on F1 and "
        "cross-entropy, with calibrated probabilities (MOD-06: ECE <= 0.05)",
        "protocol_a": {
            "f1_at_half": a["f1_at_half"],
            "f1_tuned": a["f1"],
            "threshold_spec": PAPER_PROTOCOL_F1_MIN,
            "paper_f1": PAPER_F1,
            "meets_spec_at_half": a["f1_at_half"] >= PAPER_PROTOCOL_F1_MIN,
            "label": PROTOCOL_LABELS["A"],
        },
    }


def _model_attributes(spec: ModelSpec, provenance: Provenance, config: RunConfig) -> dict[str, str]:
    return {
        "returnring_story": STORY,
        "returnring_protocol": spec.protocol,
        "returnring_protocol_label": PROTOCOL_LABELS[spec.protocol],
        "returnring_trained_on": spec.trained_on,
        "returnring_provenance": json.dumps(_provenance_dict(provenance), sort_keys=True),
        "returnring_seed": str(config.seed),
        "returnring_calibrator": f"propensity_{spec.key}_calibrator.json",
    }


def _write_json(path: Path, doc: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path


def run(
    raw_dir: Path,
    out_dir: Path,
    doc_path: Path | None,
    config: RunConfig | None = None,
    log: Log = lambda _msg: None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Run US-201 on raw_dir (the raw/ folder of a data root) and write every output."""
    config = config or RunConfig()
    raw, train, test = _prepare(raw_dir)
    provenance = raw.provenance
    log(f"read {raw.events.height} events ({provenance.nature}, {provenance.origin})")
    vocabulary = Vocabulary.from_variants(raw.variants.filter(pl.col("split") == "train"))
    valid = validation_mask(train.height, config.validation_fraction, config.seed)

    models: dict[str, Any] = {}
    model_dir = out_dir / "models"
    for spec in MODELS:
        trained, calibrator, threshold, result = _fit_model(
            spec, train, test, vocabulary, valid, config, log
        )
        models[spec.key] = {**result, "labels": _label(provenance, result["protocol_label"])}
        if not spec.saved:
            continue
        model_dir.mkdir(parents=True, exist_ok=True)
        booster = trained.booster
        booster.set_attr(**_model_attributes(spec, provenance, config))
        booster.save_model(model_dir / f"propensity_{spec.key}.json")
        _write_json(
            model_dir / f"propensity_{spec.key}_calibrator.json",
            {
                **calibrator.to_dict(),
                "story": STORY,
                "model": f"propensity_{spec.key}.json",
                "protocol": spec.protocol,
                "protocol_label": PROTOCOL_LABELS[spec.protocol],
                "fitted_on": f"random {config.validation_fraction:.0%} validation of the ASOS "
                f"train split (seed {config.seed}), population {spec.trained_on}",
                "threshold_max_f1": _round(threshold),
                "provenance": _provenance_dict(provenance),
            },
        )

    log("baseline: single-feature leave-one-out rules")
    best, baseline = _baseline(train, test, valid)
    baseline["labels"] = _label(provenance, baseline["protocol_label"])
    _write_json(
        model_dir / "propensity_baseline.json",
        {
            **best.to_dict(),
            "story": STORY,
            "protocol_label": PROTOCOL_LABELS["B"],
            "provenance": _provenance_dict(provenance),
        },
    )
    doc: dict[str, Any] = {
        "story": STORY,
        "reproduce": REPRODUCE,
        "argv": argv or [],
        "provenance": _provenance_dict(provenance),
        "labels": {
            "test": _label(provenance, "see each model", "test"),
            "train": _label(provenance, "see each model", "train"),
        },
        "protocols": dict(PROTOCOL_LABELS),
        "populations": dict(POPULATIONS),
        "assumptions": {
            "seed": config.seed,
            "validation": f"seeded random {config.validation_fraction:.0%} of the train split: "
            "early stopping, isotonic calibration, F1 thresholds and the baseline rule",
            "xgboost_params": dict(PAPER_PARAMS),
            "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
            "max_rounds": config.max_rounds,
            "probability_clip": PROBABILITY_CLIP,
            "ece_bins": ECE_BINS,
            "ece_max_mod06": ECE_MAX,
            "threshold": "maximises F1 on the validation split; F1 at 0.5 also reported",
            "cross_entropy": "mean negative log-likelihood, natural log (nats)",
        },
        "environment": _environment(config),
        "data": {
            "train": _data_summary(train, vocabulary),
            "test": _data_summary(test, vocabulary),
            "validation_events": int(valid.sum()),
            "vocabulary": {
                "product_type": list(vocabulary.product_type),
                "brand": list(vocabulary.brand),
            },
        },
        "models": models,
        "baseline": baseline,
    }
    doc["go_no_go"] = _go_no_go(models, baseline)
    _write_json(out_dir / METRICS_FILE, doc)
    if doc_path is not None:
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(render_markdown(doc, out_dir))
    return doc
