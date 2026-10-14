"""A4 of US-204: GraphSAGE customer embeddings by link prediction (spec 8.3; US-202, ADR-13 am. 6).

The encoder (two heterogeneous SAGE layers of 16 units, mean aggregator, dropout 0.2, Adam 0.01)
learns, without any label, to tell an (account, identifier value) use that exists from one that
does not: each epoch hides a seeded 20% of the uses, passes messages over the rest, and scores
the hidden uses against as many uses of a random value of the same kind (the dot product of the
two embeddings, binary cross-entropy). A fixed seeded 10% of the uses is held out of training and
of message passing, and early stopping watches its loss. Accounts that share values end up close,
which is the signal the cluster features of spec 8.3 hand-craft; whether it adds anything to them
is measured by the A4 row of the risk ablation, on the same splits (ml/risk), never assumed.

Time (rule 7): the encoder is fitted on the store known at fit_as_of (default: the risk job's train
end, three months before the last snapshot), and applied, frozen, to the store known at each
embedding instant, never one before fit_as_of (the encoder would hold later facts). The ground
truth is never read (rule 12): only store/ tables through pipeline.graph.snapshot.

Writes features/customer_embeddings (contracts, provenance derived from the store's
store/identifiers, so ml/risk checks it was computed from the store it joins), the encoder
(<artifacts>/gnn/models/customer_encoder.pt and .meta.json, the second store's embeddings reuse
it with --encoder) and the metrics with timings in
<artifacts>/eval/gnn/us-202-embeddings-<store>.json.
"""

from __future__ import annotations

import copy
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import torch
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score
from torch import Tensor

from contracts.files import atomic_write
from contracts.io import Provenance, read_table, write_table
from contracts.parquet import EMBEDDING_COLUMNS, EMBEDDING_DIM, SCHEMAS
from ml.gnn.identity_graph import (
    CUSTOMER,
    CUSTOMER_NODE_FEATURES,
    IDENTIFIER,
    IDENTIFIER_NODE_FEATURES,
    USED_BY,
    USES,
    IdentityGraph,
    identity_graph,
    months_before,
    standardizers,
)
from ml.gnn.model import Encoder, EncoderConfig, count_parameters, to_device_graph
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
from pipeline.graph.snapshot import StoreSnapshot, load_snapshot

TABLE: Final = "features/customer_embeddings"
ENCODER_NAME: Final = "customer_encoder"
ENCODER_FORMAT: Final = "returnring.gnn.customer_encoder.v1"
ENCODER_VERSION: Final = "graphsage-link-prediction-v1"
ORIGIN: Final = "ReturnRing GraphSAGE customer embeddings (ml.gnn, US-202, spec 8.3)"
EVAL_MONTHS: Final = 3  # the risk job's evaluation window (ml.risk.run.EVAL_MONTHS)
Log = Callable[[str], None]


@dataclass(frozen=True)
class EmbedConfig:
    seed: int = 42
    hidden: int = EMBEDDING_DIM
    layers: int = 2
    aggregator: str = "mean"
    dropout: float = 0.2
    lr: float = 0.01
    max_epochs: int = 300
    patience: int = 20
    target_share: float = 0.2  # uses hidden and scored each epoch
    validation_share: float = 0.1  # uses held out for early stopping
    deterministic: bool = True
    threads: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def encoder_config(config: EmbedConfig) -> EncoderConfig:
    return EncoderConfig(
        in_dims=(
            (CUSTOMER, len(CUSTOMER_NODE_FEATURES)),
            (IDENTIFIER, len(IDENTIFIER_NODE_FEATURES)),
        ),
        relations=((CUSTOMER, USES, IDENTIFIER), (IDENTIFIER, USED_BY, CUSTOMER)),
        hidden=config.hidden,
        out=EMBEDDING_DIM,
        layers=config.layers,
        aggregator="pool" if config.aggregator == "pool" else "mean",
        dropout=config.dropout,
        final_relu=False,  # embeddings are signed: dot products score the links
    )


def _inputs(
    graph: IdentityGraph, std: dict[str, Standardizer], device: torch.device
) -> dict[str, Tensor]:
    return {
        CUSTOMER: torch.from_numpy(std[CUSTOMER].transform(graph.x_customer)).to(device),
        IDENTIFIER: torch.from_numpy(std[IDENTIFIER].transform(graph.x_identifier)).to(device),
    }


def _same_kind_negatives(
    graph: IdentityGraph, edges: npt.NDArray[np.int64], rng: np.random.Generator
) -> npt.NDArray[np.int64]:
    """For each use, a random identifier node of the same kind (values are sorted by kind, so
    each kind is a contiguous block of nodes)."""
    kinds = graph.identifier_kind
    out = np.empty(edges.size, dtype=np.int64)
    edge_kind = kinds[graph.edge_identifier[edges]]
    for k in np.unique(edge_kind):
        block = np.flatnonzero(kinds == k)
        sel = np.flatnonzero(edge_kind == k)
        out[sel] = block[rng.integers(0, block.size, sel.size)]
    return out


def _scores(z: dict[str, Tensor], customers: Tensor, identifiers: Tensor) -> Tensor:
    return (
        z[CUSTOMER].index_select(0, customers) * z[IDENTIFIER].index_select(0, identifiers)
    ).sum(1)


def _link_loss(pos: Tensor, neg: Tensor) -> Tensor:
    logits = torch.cat([pos, neg])
    labels = torch.cat([torch.ones_like(pos), torch.zeros_like(neg)])
    loss: Tensor = F.binary_cross_entropy_with_logits(logits, labels)
    return loss


def fit_encoder(
    graph: IdentityGraph,
    config: EmbedConfig,
    backend: Backend,
    device: torch.device,
    log: Log = lambda _m: None,
) -> tuple[Encoder, dict[str, Standardizer], dict[str, Any]]:
    """Train the encoder by link prediction on `graph`; returns it at its best validation epoch."""
    if graph.n_edges < 10:
        raise ValueError("too few identifier uses to train a link-prediction encoder")
    seed_everything(config.seed, config.deterministic)
    rng = np.random.default_rng(config.seed)
    std = standardizers(graph)
    x = _inputs(graph, std, device)
    n = graph.n_edges
    order = rng.permutation(n)
    n_valid = max(1, round(n * config.validation_share))
    valid = np.zeros(n, dtype=bool)
    valid[order[:n_valid]] = True
    train_edges = np.flatnonzero(~valid)
    valid_edges = np.flatnonzero(valid)
    valid_neg = _same_kind_negatives(graph, valid_edges, rng)
    pair = (USES, USED_BY)
    g_valid = to_device_graph(graph.graph.without_edges(pair, ~valid), backend, device)

    def tensor(a: npt.NDArray[np.int64]) -> Tensor:
        return torch.from_numpy(np.ascontiguousarray(a)).to(device)

    vc = tensor(graph.edge_customer[valid_edges])
    vi = tensor(graph.edge_identifier[valid_edges])
    vn = tensor(valid_neg)
    encoder = Encoder(encoder_config(config), backend).to(device)
    optimizer = torch.optim.Adam(encoder.parameters(), lr=config.lr)
    best_loss, best_epoch, waited, best_auc = math.inf, 0, 0, math.nan
    best_state = copy.deepcopy(encoder.state_dict())
    history: list[dict[str, float]] = []
    epoch_seconds: list[float] = []
    synchronize(device)
    started = time.perf_counter()
    load_before = load_average()
    epoch = 0
    for epoch in range(1, config.max_epochs + 1):
        synchronize(device)
        t0 = time.perf_counter()
        encoder.train()
        shuffled = rng.permutation(train_edges)
        n_target = max(1, round(train_edges.size * config.target_share))
        targets = shuffled[:n_target]
        message = np.zeros(n, dtype=bool)
        message[shuffled[n_target:]] = True
        g = to_device_graph(graph.graph.without_edges(pair, message), backend, device)
        z = encoder(g, x)
        tc = tensor(graph.edge_customer[targets])
        ti = tensor(graph.edge_identifier[targets])
        tn = tensor(_same_kind_negatives(graph, targets, rng))
        loss = _link_loss(_scores(z, tc, ti), _scores(z, tc, tn))
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        encoder.eval()
        with torch.no_grad():
            zv = encoder(g_valid, x)
            pos, neg = _scores(zv, vc, vi), _scores(zv, vc, vn)
            valid_loss = float(_link_loss(pos, neg).item())
        synchronize(device)
        epoch_seconds.append(time.perf_counter() - t0)
        if not math.isfinite(valid_loss):
            raise FloatingPointError(f"non-finite validation loss at epoch {epoch}")
        history.append({"train_loss": float(loss.item()), "valid_loss": valid_loss})
        if valid_loss < best_loss - 1e-6:
            best_loss, best_epoch, waited = valid_loss, epoch, 0
            best_state = copy.deepcopy(encoder.state_dict())
            scores = torch.cat([pos, neg]).cpu().numpy()
            labels = np.r_[np.ones(pos.shape[0]), np.zeros(neg.shape[0])]
            best_auc = float(roc_auc_score(labels, scores))
        else:
            waited += 1
        if epoch == 1 or epoch % 25 == 0:
            log(f"epoch {epoch}: train {loss.item():.5f}, valid {valid_loss:.5f}")
        if waited >= config.patience:
            break
    synchronize(device)
    train_seconds = time.perf_counter() - started
    encoder.load_state_dict(best_state)
    summary = {
        "uses": n,
        "uses_validation": int(valid_edges.size),
        "uses_scored_per_epoch": max(1, round(train_edges.size * config.target_share)),
        "negatives": "one random value of the same identifier kind per scored use",
        "best_epoch": best_epoch,
        "epochs_run": epoch,
        "train_seconds": round(train_seconds, 3),
        "epoch_seconds_median": round(float(np.median(epoch_seconds)), 4),
        "best_validation_loss": round(best_loss, 6),
        "validation_auc_at_best": round(best_auc, 6),
        "auc_note": "how well the embeddings tell a held-out (account, value) use from a random "
        "value of the same kind: the self-supervised task, not abuse detection",
        "loss_first_last": [history[0], history[-1]],
        "parameters": count_parameters(encoder),
        "load_average_before": load_before,
        "load_average_after": load_average(),
        **gpu_memory(device),
    }
    return encoder, std, summary


def embed(
    graph: IdentityGraph,
    encoder: Encoder,
    std: dict[str, Standardizer],
    backend: Backend,
    device: torch.device,
) -> npt.NDArray[np.float64]:
    """The customer embeddings on the whole graph (every use), dropout off; node order."""
    encoder.eval()
    g = to_device_graph(graph.graph, backend, device)
    with torch.no_grad():
        z = encoder(g, _inputs(graph, std, device))[CUSTOMER]
    out: npt.NDArray[np.float64] = np.asarray(z.double().cpu().numpy(), dtype=np.float64)
    return out


def _write_json(path: Path, doc: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"
    return atomic_write(path, lambda out: out.write(text.encode("utf-8")))


def encoder_paths(prefix: Path) -> tuple[Path, Path]:
    """(weights, meta) of an encoder saved under the path prefix (no extension)."""
    return prefix.with_name(prefix.name + ".pt"), prefix.with_name(prefix.name + ".meta.json")


def save_encoder(prefix: Path, encoder: Encoder, meta: dict[str, Any]) -> list[str]:
    weights, meta_path = encoder_paths(prefix)
    weights.parent.mkdir(parents=True, exist_ok=True)
    torch.save({k: v.detach().cpu() for k, v in encoder.state_dict().items()}, weights)
    _write_json(meta_path, meta)
    return [str(weights), str(meta_path)]


def load_encoder(
    prefix: Path, backend: Backend, device: torch.device
) -> tuple[Encoder, dict[str, Standardizer], dict[str, Any]]:
    """A saved encoder, rebuilt for `backend` (the parameter names are the same in both)."""
    weights, meta_path = encoder_paths(prefix)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("format") != ENCODER_FORMAT:
        raise ValueError(f"{meta_path}: not a {ENCODER_FORMAT} encoder")
    cfg = meta["encoder"]
    config = EncoderConfig(
        in_dims=tuple((str(k), int(v)) for k, v in cfg["in_dims"].items()),
        relations=tuple((str(a), str(b), str(c)) for a, b, c in cfg["relations"]),
        hidden=int(cfg["hidden"]),
        out=int(cfg["out"]),
        layers=int(cfg["layers"]),
        aggregator="pool" if cfg["aggregator"] == "pool" else "mean",
        dropout=float(cfg["dropout"]),
        final_relu=bool(cfg["final_relu"]),
    )
    encoder = Encoder(config, backend).to(device)
    state = torch.load(weights, map_location=device, weights_only=True)
    encoder.load_state_dict(state)
    std = {k: Standardizer.from_dict(v) for k, v in meta["standardizers"].items()}
    return encoder, std, meta


def feature_instants(store: Path) -> list[datetime]:
    """The decision times of <store>/features/customer_features, ascending."""
    table, _ = read_table("features/customer_features", store)
    return sorted({t for t in table.column("as_of").to_pylist()})


def _table(rows: list[tuple[datetime, IdentityGraph, npt.NDArray[np.float64], str]]) -> pa.Table:
    columns: dict[str, list[Any]] = {"customer_id": []}
    for name in EMBEDDING_COLUMNS:
        columns[name] = []
    columns["encoder_version"] = []
    columns["as_of"] = []
    for as_of, graph, z, version in rows:
        columns["customer_id"] += list(graph.customer_ids)
        for j, name in enumerate(EMBEDDING_COLUMNS):
            columns[name] += z[:, j].tolist()
        columns["encoder_version"] += [version] * len(graph.customer_ids)
        columns["as_of"] += [as_of] * len(graph.customer_ids)
    return pa.table(columns, schema=SCHEMAS[TABLE])


def store_name(store: Path) -> str:
    return "ood" if Path(store).name == "ood" else "main"


def run_embed(
    store: Path,
    out: Path,
    artifacts: Path,
    encoder: Path | None,
    fit_as_of: datetime | None,
    as_ofs: Sequence[datetime] | None,
    config: EmbedConfig,
    backend: Backend,
    device: torch.device,
    cold_start: dict[str, float],
    log: Log = lambda _m: None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Fit (or load) the encoder, embed the customers at each instant, write the contract file."""
    if config.threads:
        torch.set_num_threads(config.threads)
    seed_everything(config.seed, config.deterministic)
    watch = Stopwatch(device)
    measured_at = utc_now()
    # The store's decision times are read only when an instant is not given.
    need = not as_ofs or (encoder is None and fit_as_of is None)
    instants = feature_instants(store) if need else []
    training: dict[str, Any] | None = None
    if encoder is None:
        fit = fit_as_of or months_before(instants[-1], EVAL_MONTHS)
        snapshot = watch.time("data_loading", lambda: load_snapshot(store, fit))
        graph = watch.time("data_loading", lambda: identity_graph(snapshot))
        log(f"fit at {fit.isoformat()}: {graph.summary()}")
        enc, std, training = watch.time(
            "training", lambda: fit_encoder(graph, config, backend, device, log)
        )
        version = f"{ENCODER_VERSION}@{fit:%Y-%m-%dT%H:%MZ}"
        prov = snapshot.provenance["store/identifiers"]
        meta: dict[str, Any] = {
            "format": ENCODER_FORMAT,
            "version": version,
            "fit_as_of": fit.isoformat(),
            "encoder": encoder_config(config).as_dict(),
            "config": config.as_dict(),
            "standardizers": {k: v.as_dict() for k, v in std.items()},
            "inputs": {
                CUSTOMER: list(CUSTOMER_NODE_FEATURES),
                IDENTIFIER: list(IDENTIFIER_NODE_FEATURES),
            },
            "trained_on": {"store": str(store), "origin": prov.origin, "lineage": prov.chain},
            "graph": graph.summary(),
            "training": training,
            "device": str(device),
            "backend": backend,
            "torch": torch.__version__,
        }
        saved = save_encoder(artifacts / "gnn" / "models" / ENCODER_NAME, enc, meta)
    else:
        enc, std, meta = load_encoder(encoder, backend, device)
        fit = datetime.fromisoformat(meta["fit_as_of"])
        version = str(meta["version"])
        saved = []
    targets = sorted(as_ofs) if as_ofs else [t for t in instants if t >= fit]
    early = [t for t in targets if t < fit]
    if early:
        raise ValueError(
            f"embedding instants before the encoder's training time {fit.isoformat()}: "
            f"{[t.isoformat() for t in early]} (rule 7: the encoder holds later facts)"
        )
    if not targets:
        raise ValueError("no embedding instant at or after the encoder's training time")
    rows = []
    graphs: list[dict[str, Any]] = []
    last: StoreSnapshot | None = None
    for t in targets:

        def read(t: datetime = t) -> tuple[StoreSnapshot, IdentityGraph]:
            snapshot = load_snapshot(store, t)
            return snapshot, identity_graph(snapshot)

        last, graph_t = watch.time("data_loading", read)

        def infer(graph_t: IdentityGraph = graph_t) -> npt.NDArray[np.float64]:
            return embed(graph_t, enc, std, backend, device)

        z = watch.time("inference", infer)
        rows.append((t, graph_t, z, version))
        graphs.append(graph_t.summary())
        log(f"embedded {len(graph_t.customer_ids)} customers at {t.isoformat()}")
    assert last is not None
    start, end = last.period()
    store_prov = last.provenance["store/identifiers"]
    trained_on = meta["trained_on"]["origin"] if encoder is not None else "this store"
    span = f"{targets[0]:%Y-%m-%d}..{targets[-1]:%Y-%m-%d}"
    provenance = store_prov.derive(
        f"{ORIGIN}: link prediction on the customer-identifier graph, no label; encoder "
        f"{version} ({device.type}, {backend}); as of {span}",
        start,
        end,
        detail=f"encoder fitted on {trained_on}; inputs {list(CUSTOMER_NODE_FEATURES)} and "
        f"{list(IDENTIFIER_NODE_FEATURES)}",
    )
    path = watch.time("writing", lambda: write_table(TABLE, _table(rows), out, provenance))
    doc = {
        "story": "US-202",
        "kind": "embeddings",
        "store": store_name(store),
        "argv": argv or [],
        "reproduce": "python -m ml.gnn embed --store data --out data --models artifacts"
        if encoder is None
        else "python -m ml.gnn embed --store data/ood --out data/ood "
        "--encoder artifacts/gnn/models/customer_encoder",
        "nature": provenance.nature,
        "encoder": {
            "version": version,
            "fit_as_of": fit.isoformat(),
            "trained_here": encoder is None,
            "config": meta["encoder"],
            "inputs": meta["inputs"],
            "trained_on": meta["trained_on"],
            "training": training if training is not None else meta.get("training"),
            "files": saved,
        },
        "graphs": graphs,
        "output": {
            "file": str(path),
            "rows": int(sum(len(g.customer_ids) for _, g, _, _ in rows)),
            "instants": [t.isoformat() for t in targets],
            "provenance": _provenance_dict(provenance),
        },
        "environment": environment(device, backend),
        "timings": {
            "cold_start": {k: round(v, 4) for k, v in cold_start.items()},
            **{f"{k}_seconds": v for k, v in watch.rounded().items()},
            "measured_at": measured_at,
            "finished_at": utc_now(),
            "nature": TIMING_NATURE,
        },
    }
    _write_json(artifacts / "eval" / "gnn" / f"us-202-embeddings-{store_name(store)}.json", doc)
    return doc


def _provenance_dict(p: Provenance) -> dict[str, str]:
    return {
        "origin": p.origin,
        "lineage": p.chain,
        "period": f"{p.period_start.isoformat()}..{p.period_end.isoformat()}",
        "nature": p.nature,
    }
