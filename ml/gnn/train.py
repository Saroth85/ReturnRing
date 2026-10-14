"""Training loop of the edge classifier (US-202): Adam, mini-batches of edges, early stopping.

Each step recomputes the node representations on the whole graph (one GraphSAGE layer is cheap
next to the head), then scores a mini-batch of labelled edges; the loss is the binary
cross-entropy. After each epoch the validation loss decides: the best state is kept, and training
stops after `patience` epochs without a better validation loss (the paper's XGBoost rule, 5).
The batch order comes from a seeded generator, so a run on the CPU is repeatable.
"""

from __future__ import annotations

import copy
import math
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import torch
import torch.nn.functional as F
from torch import Tensor

from ml.gnn.model import EdgeClassifier
from ml.gnn.runtime import synchronize

Log = Callable[[str], None]


@dataclass(frozen=True)
class TrainConfig:
    lr: float = 0.01  # spec 8.1, the paper's Adam learning rate
    max_epochs: int = 60
    patience: int = 5
    batch_size: int = 131_072
    eval_batch_size: int = 1_048_576
    seed: int = 42

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EdgeSet:
    """Labelled edges on a device: endpoint indices, edge features, labels (float 0/1)."""

    left: Tensor
    right: Tensor
    x: Tensor
    y: Tensor

    def __len__(self) -> int:
        return int(self.left.shape[0])

    def take(self, index: Tensor) -> EdgeSet:
        return EdgeSet(self.left[index], self.right[index], self.x[index], self.y[index])


def edge_set(
    left: npt.NDArray[np.int64],
    right: npt.NDArray[np.int64],
    x: npt.NDArray[np.float32],
    y: npt.NDArray[np.int8] | None,
    device: torch.device,
) -> EdgeSet:
    labels = np.zeros(left.shape[0], dtype=np.float32) if y is None else y.astype(np.float32)
    return EdgeSet(
        torch.from_numpy(np.ascontiguousarray(left)).to(device),
        torch.from_numpy(np.ascontiguousarray(right)).to(device),
        torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)).to(device),
        torch.from_numpy(labels).to(device),
    )


@dataclass
class FitResult:
    best_epoch: int
    epochs_run: int
    stopped_early: bool
    train_seconds: float
    epoch_seconds: list[float] = field(default_factory=list)
    train_loss: list[float] = field(default_factory=list)
    valid_loss: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "best_epoch": self.best_epoch,
            "epochs_run": self.epochs_run,
            "stopped_early": self.stopped_early,
            "train_seconds": round(self.train_seconds, 3),
            "epoch_seconds_median": round(statistics.median(self.epoch_seconds), 4)
            if self.epoch_seconds
            else None,
            "epoch_seconds_first": round(self.epoch_seconds[0], 4) if self.epoch_seconds else None,
            "train_loss": [round(v, 6) for v in self.train_loss],
            "valid_loss": [round(v, 6) for v in self.valid_loss],
        }


def predict_logits(
    model: EdgeClassifier,
    graph: Any,
    x: dict[str, Tensor],
    edges: EdgeSet,
    batch_size: int,
) -> Tensor:
    """Logits of every edge, in order, with dropout off and no gradient."""
    model.eval()
    with torch.no_grad():
        h = model.nodes(graph, x)
        out = []
        for start in range(0, len(edges), batch_size):
            part = slice(start, start + batch_size)
            out.append(model.logits(h, edges.left[part], edges.right[part], edges.x[part]))
        result: Tensor = torch.cat(out) if out else torch.zeros(0, device=edges.y.device)
    return result


def mean_bce(logits: Tensor, y: Tensor) -> float:
    if logits.numel() == 0:
        return math.nan
    return float(F.binary_cross_entropy_with_logits(logits, y).item())


def fit(
    model: EdgeClassifier,
    graph: Any,
    x: dict[str, Tensor],
    train: EdgeSet,
    valid: EdgeSet,
    config: TrainConfig,
    device: torch.device,
    log: Log = lambda _m: None,
) -> FitResult:
    """Train with early stopping on the validation loss; the model ends at its best epoch."""
    if len(train) == 0 or len(valid) == 0:
        raise ValueError("training and validation edges are both needed")
    optimizer = torch.optim.Adam(model.parameters(), lr=config.lr)
    generator = torch.Generator(device="cpu").manual_seed(config.seed)
    best_loss, best_epoch, waited = math.inf, 0, 0
    best_state = copy.deepcopy(model.state_dict())
    result = FitResult(best_epoch=0, epochs_run=0, stopped_early=False, train_seconds=0.0)
    synchronize(device)
    start = time.perf_counter()
    for epoch in range(1, config.max_epochs + 1):
        synchronize(device)
        t0 = time.perf_counter()
        model.train()
        order = torch.randperm(len(train), generator=generator).to(device)
        total, seen = 0.0, 0
        for begin in range(0, len(train), config.batch_size):
            batch = train.take(order[begin : begin + config.batch_size])
            h = model.nodes(graph, x)
            logits = model.logits(h, batch.left, batch.right, batch.x)
            loss = F.binary_cross_entropy_with_logits(logits, batch.y)
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            optimizer.step()
            total += float(loss.detach().item()) * len(batch)
            seen += len(batch)
        valid_loss = mean_bce(
            predict_logits(model, graph, x, valid, config.eval_batch_size), valid.y
        )
        synchronize(device)
        result.epoch_seconds.append(time.perf_counter() - t0)
        result.train_loss.append(total / seen)
        result.valid_loss.append(valid_loss)
        result.epochs_run = epoch
        if not math.isfinite(valid_loss):
            raise FloatingPointError(f"non-finite validation loss at epoch {epoch}")
        if valid_loss < best_loss - 1e-6:
            best_loss, best_epoch, waited = valid_loss, epoch, 0
            best_state = copy.deepcopy(model.state_dict())
        else:
            waited += 1
        log(
            f"epoch {epoch}: train {total / seen:.5f}, valid {valid_loss:.5f}, "
            f"{result.epoch_seconds[-1]:.2f} s"
        )
        if waited >= config.patience:
            result.stopped_early = True
            break
    synchronize(device)
    result.train_seconds = time.perf_counter() - start
    result.best_epoch = best_epoch
    model.load_state_dict(best_state)
    return result
