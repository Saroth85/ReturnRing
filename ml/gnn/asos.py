"""US-202 on ASOS: GraphSAGE against XGBoost and the paper, protocols B and A (ADR-10; B2).

A benchmark of the ASOS return-prediction task, never evidence about rings (spec US-202): ASOS has
no identifiers, and abuse is measured on the synthetic store only (A4, ml.gnn.embeddings).

For each protocol (B first: the decision-time protocol; A: the paper's), on the same events,
splits and populations as US-201 (ml.propensity), in one run:

- GraphSAGE: one heterogeneous SAGE layer of 16 units (spec 8.1), aggregator mean by default
  (`--aggregator pool` is the paper's max-pool), dropout 0.2, Adam 0.01, then the 2-layer MLP edge
  head of the paper on the two node representations and the edge's own features
  (ml.gnn.asos_data says which input goes where and why no label travels along the graph);
- MLP: the same head on the input node features, no message passing (what the graph adds);
- XGBoost: ml.propensity's own model of the protocol (the US-201 code path, `_fit_model`), on the
  same rows, on the CPU.

Every model trains on the protocol's population (B: decision time; A: every event) minus the seeded
10% validation split of ml.propensity (early stopping, isotonic calibration, F1 threshold), and is
evaluated on the ASOS test split, on the four populations of US-201. `--sample f` keeps a seeded
share f of the customers (all their events, both splits) for a run that must fit a small machine;
every model of the run then uses the same sample, and the report says so.

Times are taken apart (spec 12): cold start (torch and DGL import, device context, first kernels),
data loading (read, join, graph and features, copy to the device), training and inference, each
on the device of the run (`--device cpu|gpu`), the GPU synchronised at the edges of each phase.

Writes <out>/eval/gnn/us-202-asos-<device>.json (every number with origin, period, nature and
protocol) and the GraphSAGE weights and calibrators under <out>/gnn/models/. Reads raw/asos_* only
(real data, rule 12 does not apply: no truth/ is read).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl
import torch
from torch import Tensor

from contracts.files import atomic_write
from contracts.io import Provenance
from ml.gnn.asos_data import (
    BOUGHT_BY,
    BUYS,
    CUSTOMER,
    VARIANT,
    FeatureSpec,
    SplitGraph,
    customer_sample,
    feature_spec,
    split_graph,
    standardizer,
)
from ml.gnn.b2 import metrics_file
from ml.gnn.model import (
    EdgeClassifier,
    Encoder,
    EncoderConfig,
    count_parameters,
    to_device_graph,
)
from ml.gnn.runtime import (
    TIMING_NATURE,
    Backend,
    Stopwatch,
    environment,
    gpu_memory,
    load_average,
    seed_everything,
    synchronize,
    utc_now,
)
from ml.gnn.scaling import Standardizer
from ml.gnn.train import EdgeSet, TrainConfig, edge_set, fit, predict_logits
from ml.propensity.calibration import PROBABILITY_CLIP, IsotonicCalibrator
from ml.propensity.features import (
    POPULATIONS,
    PROTOCOL_LABELS,
    FeatureError,
    Protocol,
    Vocabulary,
    validation_mask,
)
from ml.propensity.metrics import (
    ECE_BINS,
    best_f1_threshold,
    binary_metrics,
    cross_entropy,
    expected_calibration_error,
)
from ml.propensity.run import MODELS as XGB_MODELS
from ml.propensity.run import RunConfig as XgbConfig
from ml.propensity.run import _fit_model as fit_xgboost_model
from pipeline.ingest.asos_convert import SPLIT_PERIODS
from pipeline.ingest.asos_profile import load_raw

STORY = "US-202"
REPRODUCE = "python -m ml.gnn asos --raw data/raw --out artifacts"
DIGITS = 6
MOD02_F1_MIN = 0.78  # spec MOD-02: GraphSAGE, paper protocol only
# arXiv:2302.14096 v1, Table 1: full test data (1.5M events). The paper's GNN was trained on the
# events of variants with a return rate above 0.8 or below 0.2 (F1 0.783 when trained on all
# events, section 4) and uses country virtual nodes (forbidden here, rule 6). ECE is not reported.
PAPER: dict[str, Any] = {
    "source": "McGowan et al. 2023, arXiv:2302.14096 v1, Table 1 (full test set, 1.5M events)",
    "protocol_label": PROTOCOL_LABELS["A"],
    "models": {
        "GNN (1 GraphSAGE layer, 16, max-pool, dropout 0.2, MLP head)": {
            "precision": 0.816,
            "recall": 0.758,
            "f1": 0.792,
            "cross_entropy": 0.489,
        },
        "XGBoost": {"precision": 0.805, "recall": 0.745, "f1": 0.774, "cross_entropy": 0.561},
        "MLP (2 layers, 128)": {
            "precision": 0.870,
            "recall": 0.656,
            "f1": 0.748,
            "cross_entropy": 0.582,
        },
    },
    "gnn_f1_trained_on_all_events": 0.783,
    "ece": "not reported in the paper",
    "notes": [
        "the paper's GNN trains on the events of variants with a return rate above 0.8 or below "
        "0.2 only, and is evaluated on the full test set",
        "the paper's graph adds virtual nodes for countries (a forbidden feature here, rule 6), "
        "products, product types, brands and top return reasons",
        "the paper's node features are computed over the labelled window (ADR-10, protocol A)",
    ],
}

Log = Callable[[str], None]


@dataclass(frozen=True)
class AsosConfig:
    seed: int = 42
    device: str = "cpu"  # "cpu" or "gpu"
    backend: str = "auto"  # "auto", "dgl" or "torch"
    protocols: tuple[Protocol, ...] = ("B", "A")
    sample: float = 1.0  # share of customers kept (1.0: every event)
    aggregator: str = "mean"
    hidden: int = 16
    layers: int = 1
    head_hidden: int = 64
    dropout: float = 0.2
    validation_fraction: float = 0.1
    deterministic: bool = True
    threads: int | None = None
    xgboost: bool = True
    mlp: bool = True
    train: TrainConfig = field(default_factory=TrainConfig)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["protocols"] = list(self.protocols)
        return out

    def comparable(self) -> dict[str, Any]:
        """The settings two runs must share for their timings to be compared (spec 12)."""
        out = self.as_dict()
        for key in ("device", "threads"):
            out.pop(key)
        return out


def _round(value: float) -> float:
    return round(float(value), DIGITS)


def _metrics(
    y: npt.NDArray[np.int8],
    p: npt.NDArray[np.float64],
    threshold: float,
    masks: dict[str, npt.NDArray[np.bool_]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, mask in masks.items():
        if not mask.any():
            out[name] = None
            continue
        m = binary_metrics(y[mask], p[mask], threshold).as_dict()
        out[name] = {k: (_round(v) if isinstance(v, float) else v) for k, v in m.items()}
    return out


def _raw_calibration(
    y: npt.NDArray[np.int8], raw: npt.NDArray[np.float64], masks: dict[str, npt.NDArray[np.bool_]]
) -> dict[str, Any]:
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


def _period(split: str) -> str:
    start, end = SPLIT_PERIODS[split]
    return f"{start.isoformat()}..{end.isoformat()}"


def provenance_dict(p: Provenance) -> dict[str, str]:
    return {
        "origin": p.origin,
        "period_start": p.period_start.isoformat(),
        "period_end": p.period_end.isoformat(),
        "nature": p.nature,
    }


def _labels(p: Provenance, protocol: str, split: str = "test") -> dict[str, str]:
    return {
        "origin": p.origin,
        "nature": p.nature,
        "split": split,
        "period": _period(split),
        "protocol": protocol,
    }


@dataclass
class Prepared:
    """One protocol's data, standardised, on the device."""

    spec: FeatureSpec
    train: SplitGraph
    test: SplitGraph
    fit_rows: npt.NDArray[np.bool_]
    valid_rows: npt.NDArray[np.bool_]
    graph_train: Any
    graph_test: Any
    x_train: dict[str, Tensor]
    x_test: dict[str, Tensor]
    edges_fit: EdgeSet
    edges_valid: EdgeSet
    edges_test: EdgeSet
    standardizers: dict[str, Standardizer]


def _sample_masks(raw_events: pl.DataFrame, config: AsosConfig) -> dict[str, Any]:
    if config.sample >= 1.0:
        return {"train": None, "test": None}
    out: dict[str, Any] = {}
    for split in ("train", "test"):
        hashes = raw_events.filter(pl.col("split") == split)["customer_hash"].to_numpy()
        out[split] = customer_sample(hashes, config.sample, config.seed)
    return out


def prepare(
    raw: Any,
    protocol: Protocol,
    vocabulary: Vocabulary,
    config: AsosConfig,
    backend: Backend,
    device: torch.device,
    keep: dict[str, Any],
) -> Prepared:
    spec = feature_spec(protocol, vocabulary)
    train = split_graph(
        raw.events, raw.customers, raw.variants, "train", spec, vocabulary, keep["train"]
    )
    test = split_graph(
        raw.events, raw.customers, raw.variants, "test", spec, vocabulary, keep["test"]
    )
    population = "decision_time" if protocol == "B" else "all"
    valid = validation_mask(train.n_events, config.validation_fraction, config.seed)
    in_population = train.masks[population]
    fit_rows, valid_rows = in_population & ~valid, in_population & valid
    std = {
        CUSTOMER: standardizer(train.x_customer, spec.customer),
        VARIANT: standardizer(train.x_variant, spec.variant),
        "edge": standardizer(train.x_edge[fit_rows], spec.edge),
    }

    def nodes(g: SplitGraph) -> dict[str, Tensor]:
        return {
            CUSTOMER: torch.from_numpy(std[CUSTOMER].transform(g.x_customer)).to(device),
            VARIANT: torch.from_numpy(std[VARIANT].transform(g.x_variant)).to(device),
        }

    x_edge_train = std["edge"].transform(train.x_edge)
    x_edge_test = std["edge"].transform(test.x_edge)
    return Prepared(
        spec=spec,
        train=train,
        test=test,
        fit_rows=fit_rows,
        valid_rows=valid_rows,
        graph_train=to_device_graph(train.graph, backend, device),
        graph_test=to_device_graph(test.graph, backend, device),
        x_train=nodes(train),
        x_test=nodes(test),
        edges_fit=edge_set(
            train.customer[fit_rows],
            train.variant[fit_rows],
            x_edge_train[fit_rows],
            train.y[fit_rows],
            device,
        ),
        edges_valid=edge_set(
            train.customer[valid_rows],
            train.variant[valid_rows],
            x_edge_train[valid_rows],
            train.y[valid_rows],
            device,
        ),
        edges_test=edge_set(test.customer, test.variant, x_edge_test, test.y, device),
        standardizers=std,
    )


def build_model(
    kind: str, data: Prepared, config: AsosConfig, backend: Backend
) -> tuple[EdgeClassifier, EncoderConfig | None]:
    """GraphSAGE (encoder + head) or the MLP control (head on the input node features)."""
    dims = (len(data.spec.customer), len(data.spec.variant))
    edge_dim = len(data.spec.edge)
    if kind == "mlp":
        model = EdgeClassifier(None, dims, edge_dim, config.head_hidden, config.dropout)
        return model, None
    enc_config = EncoderConfig(
        in_dims=((CUSTOMER, dims[0]), (VARIANT, dims[1])),
        relations=((CUSTOMER, BUYS, VARIANT), (VARIANT, BOUGHT_BY, CUSTOMER)),
        hidden=config.hidden,
        out=config.hidden,
        layers=config.layers,
        aggregator="pool" if config.aggregator == "pool" else "mean",
        dropout=config.dropout,
        final_relu=True,
    )
    encoder = Encoder(enc_config, backend)
    model = EdgeClassifier(
        encoder, (config.hidden, config.hidden), edge_dim, config.head_hidden, config.dropout
    )
    return model, enc_config


def _sigmoid(logits: Tensor) -> npt.NDArray[np.float64]:
    return np.asarray(torch.sigmoid(logits.double()).cpu().numpy(), dtype=np.float64)


def run_neural(
    kind: str,
    data: Prepared,
    config: AsosConfig,
    backend: Backend,
    device: torch.device,
    log: Log,
) -> tuple[EdgeClassifier, IsotonicCalibrator, dict[str, Any]]:
    """Train one neural model, calibrate it on validation and evaluate it on the test split."""
    seed_everything(config.seed, config.deterministic)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    model, enc_config = build_model(kind, data, config, backend)
    model = model.to(device)
    log(f"{data.spec.protocol}/{kind}: {count_parameters(model)} parameters, {backend} backend")
    load_before = load_average()
    result = fit(
        model,
        data.graph_train,
        data.x_train,
        data.edges_fit,
        data.edges_valid,
        config.train,
        device,
        lambda m: log(f"{data.spec.protocol}/{kind} {m}"),
    )
    load_after = load_average()
    raw_valid = _sigmoid(
        predict_logits(
            model, data.graph_train, data.x_train, data.edges_valid, config.train.eval_batch_size
        )
    )
    y_valid = data.train.y[data.valid_rows]
    calibrator = IsotonicCalibrator.fit(raw_valid, y_valid)
    p_valid = calibrator.predict(raw_valid)
    threshold = best_f1_threshold(y_valid, p_valid)
    watch = Stopwatch(device)
    logits = watch.time(
        "inference",
        lambda: predict_logits(
            model, data.graph_test, data.x_test, data.edges_test, config.train.eval_batch_size
        ),
    )
    raw_test = _sigmoid(logits)
    p_test = calibrator.predict(raw_test)
    y_test = data.test.y
    title = "GraphSAGE" if kind == "graphsage" else "MLP, same head, no message passing"
    entry: dict[str, Any] = {
        "title": f"{data.spec.protocol}, {title}",
        "kind": kind,
        "protocol": data.spec.protocol,
        "protocol_label": PROTOCOL_LABELS[data.spec.protocol],
        "trained_on": "decision_time" if data.spec.protocol == "B" else "all",
        "parameters": count_parameters(model),
        "encoder": None if enc_config is None else enc_config.as_dict(),
        "head_hidden": config.head_hidden,
        "training": {
            **result.summary(),
            "rows_fit": len(data.edges_fit),
            "rows_valid": len(data.edges_valid),
            "load_average_before": load_before,
            "load_average_after": load_after,
            **gpu_memory(device),
        },
        "inference": {
            "seconds": round(watch.seconds["inference"], 4),
            "events": int(y_test.size),
            "events_per_second": round(y_test.size / max(watch.seconds["inference"], 1e-9), 1),
            "what": "node representations on the whole test graph, then the head on every test "
            "event (GPU synchronised); calibration on the CPU not included",
        },
        "threshold": _round(threshold),
        "validation": {
            k: (_round(v) if isinstance(v, float) else v)
            for k, v in binary_metrics(y_valid, p_valid, threshold).as_dict().items()
        },
        "test": _metrics(y_test, p_test, threshold, data.test.masks),
        "test_uncalibrated": _raw_calibration(y_test, raw_test, data.test.masks),
    }
    return model, calibrator, entry


def run_xgboost(data: Prepared, vocabulary: Vocabulary, config: AsosConfig, log: Log) -> Any:
    """ml.propensity's XGBoost of the protocol on the same rows (US-201 code path, CPU)."""
    spec = next(s for s in XGB_MODELS if s.key == data.spec.protocol)
    valid = validation_mask(data.train.n_events, config.validation_fraction, config.seed)
    xgb_config = XgbConfig(seed=config.seed, device="cpu", threads=config.threads)
    _, _, threshold, result = fit_xgboost_model(
        spec, data.train.frame, data.test.frame, vocabulary, valid, xgb_config, log
    )
    result["kind"] = "xgboost"
    result["title"] = f"{data.spec.protocol}, XGBoost (ml.propensity, same rows, CPU)"
    result["threshold"] = _round(threshold)
    return result


def _write_json(path: Path, doc: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return atomic_write(path, lambda out: out.write(text.encode("utf-8")))


def metrics_path(out: Path, device: torch.device) -> Path:
    return metrics_file(out, "gpu" if device.type == "cuda" else "cpu")


def _save(
    out: Path,
    device: torch.device,
    protocol: Protocol,
    model: EdgeClassifier,
    calibrator: IsotonicCalibrator,
    entry: dict[str, Any],
    provenance: Provenance,
) -> list[str]:
    tag = f"asos_{protocol}_graphsage_{'gpu' if device.type == 'cuda' else 'cpu'}"
    directory = out / "gnn" / "models"
    directory.mkdir(parents=True, exist_ok=True)
    weights = directory / f"{tag}.pt"
    state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
    torch.save(state, weights)
    meta = {
        "story": STORY,
        "protocol": protocol,
        "protocol_label": PROTOCOL_LABELS[protocol],
        "encoder": entry["encoder"],
        "head_hidden": entry["head_hidden"],
        "threshold": entry["threshold"],
        "calibrator": calibrator.to_dict(),
        "provenance": provenance_dict(provenance),
        "note": "a benchmark model of the ASOS task (US-202); never used by the product",
    }
    _write_json(directory / f"{tag}.meta.json", meta)
    return [str(weights), str(directory / f"{tag}.meta.json")]


def run(
    raw_dir: Path,
    out: Path,
    config: AsosConfig,
    backend: Backend,
    device: torch.device,
    cold_start: dict[str, float],
    log: Log = lambda _m: None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Run US-202 on raw_dir (the raw/ folder of a data root); returns and writes the metrics."""
    raw_dir = Path(raw_dir)
    if raw_dir.name != "raw":
        raise FeatureError(f"--raw must be the raw/ folder of a data root, not {raw_dir}")
    if config.threads:
        torch.set_num_threads(config.threads)
    seed_everything(config.seed, config.deterministic)
    watch = Stopwatch(device)
    measured_at = utc_now()
    started = time.perf_counter()
    raw = watch.time("read_raw", lambda: load_raw(raw_dir.parent))
    provenance = raw.provenance
    vocabulary = Vocabulary.from_variants(raw.variants.filter(pl.col("split") == "train"))
    keep = _sample_masks(raw.events, config)
    log(f"read {raw.events.height} events ({provenance.nature}); sample {config.sample}")

    protocols: dict[str, Any] = {}
    saved: list[str] = []
    for protocol in config.protocols:
        local = Stopwatch(device)

        def load(protocol: Protocol = protocol) -> Prepared:
            return prepare(raw, protocol, vocabulary, config, backend, device, keep)

        data = local.time("data_loading", load)
        log(f"{protocol}: {data.train.summary()}")
        models: dict[str, Any] = {}
        kinds = ["graphsage", *(["mlp"] if config.mlp else [])]
        for kind in kinds:
            model, calibrator, entry = run_neural(kind, data, config, backend, device, log)
            entry["labels"] = _labels(provenance, entry["protocol_label"])
            models[kind] = entry
            if kind == "graphsage":
                saved += _save(out, device, protocol, model, calibrator, entry, provenance)
            del model
        if config.xgboost:
            entry = run_xgboost(data, vocabulary, config, log)
            entry["labels"] = _labels(provenance, entry["protocol_label"])
            models["xgboost"] = entry
        protocols[protocol] = {
            "protocol_label": PROTOCOL_LABELS[protocol],
            "features": data.spec.as_dict(),
            "standardisation": {k: v.as_dict() for k, v in data.standardizers.items()},
            "data": {
                "train": data.train.summary(),
                "test": data.test.summary(),
                "rows_fit": int(data.fit_rows.sum()),
                "rows_valid": int(data.valid_rows.sum()),
            },
            "data_loading_seconds": round(local.seconds["data_loading"], 4),
            "models": models,
        }
        del data
        if device.type == "cuda":
            torch.cuda.empty_cache()
    synchronize(device)
    total = time.perf_counter() - started
    doc: dict[str, Any] = {
        "story": STORY,
        "kind": "asos",
        "reproduce": REPRODUCE,
        "argv": argv or [],
        "provenance": provenance_dict(provenance),
        "labels": {s: _labels(provenance, "see each model", s) for s in ("train", "test")},
        "protocols_labels": dict(PROTOCOL_LABELS),
        "populations": dict(POPULATIONS),
        "config": config.as_dict(),
        "comparable": config.comparable(),
        "environment": environment(device, backend),
        "timings": {
            "cold_start": {k: round(v, 4) for k, v in cold_start.items()},
            "cold_start_seconds": round(sum(cold_start.values()), 4),
            "read_raw_seconds": round(watch.seconds["read_raw"], 4),
            "job_seconds_after_cold_start": round(total, 3),
            "measured_at": measured_at,
            "finished_at": utc_now(),
            "nature": TIMING_NATURE,
        },
        "assumptions": {
            "validation": f"seeded random {config.validation_fraction:.0%} of the train split "
            "(ml.propensity.validation_mask): early stopping, isotonic calibration, F1 threshold",
            "threshold": "maximises F1 on the validation split; F1 at 0.5 also reported",
            "cross_entropy": "mean negative log-likelihood, natural log (nats)",
            "ece_bins": ECE_BINS,
            "probability_clip": PROBABILITY_CLIP,
            "sample": "every event"
            if config.sample >= 1.0
            else f"seeded {config.sample:.0%} of the customers (splitmix64 of the customer hash, "
            f"seed {config.seed}), all their events, both splits; node tables unchanged",
        },
        "protocols": protocols,
        "paper": PAPER,
        "mod02": _mod02(protocols),
        "artifacts": saved,
        "scope": "benchmark of the ASOS return-prediction task (B2), never evidence about rings "
        "(spec US-202)",
    }
    _write_json(metrics_path(out, device), doc)
    return doc


def _mod02(protocols: dict[str, Any]) -> dict[str, Any]:
    """MOD-02: GraphSAGE F1 >= 0.78 under the paper protocol (A), full test set, at 0.5 and at the
    validation threshold; protocol B is reported, with no threshold (ADR-10)."""
    a = protocols.get("A", {}).get("models", {}).get("graphsage")
    if a is None:
        return {"measured": False, "reason": "protocol A not run"}
    full = a["test"]["all"]
    return {
        "measured": True,
        "threshold_spec": MOD02_F1_MIN,
        "f1_at_half": full["f1_at_half"],
        "f1_tuned": full["f1"],
        "pass_at_half": full["f1_at_half"] >= MOD02_F1_MIN,
        "pass_tuned": full["f1"] >= MOD02_F1_MIN,
        "label": PROTOCOL_LABELS["A"],
    }
