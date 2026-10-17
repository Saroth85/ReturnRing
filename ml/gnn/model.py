"""GraphSAGE layers with two backends that are the same model (US-202, spec 8.1).

- backend "dgl": dgl.nn.SAGEConv per relation inside dgl.nn.HeteroGraphConv (sum over relations),
  DGL when importable (optional; no recorded run used it).
- backend "torch": TorchSAGEConv and TorchHeteroConv below, plain PyTorch, so the CPU tests and a
  machine without DGL run the same model.

TorchSAGEConv follows dgl.nn.SAGEConv 2.4 line by line for the mean and pool aggregators on a
(source, destination) feature pair: the same submodules (feat_drop, fc_pool, fc_neigh without bias,
fc_self with the bias), created and initialised in the same order (Xavier uniform with the ReLU
gain), the same linear-before-aggregation rule for mean (when the source features are wider than the
output), and zeros for a node without neighbours. TorchHeteroConv keeps its convolutions in
`mods`, like HeteroGraphConv. So, at the same seed, both backends start from the same weights, and a
state_dict of one loads into the other (tests/test_gnn_model.py checks the outputs match when DGL is
installed).

Aggregation: mean is a sparse product with the row-normalised adjacency (CSR, its transpose
precomputed for the gradient), so no atomic add and no dependence on thread scheduling; pool is the
max over neighbours of relu(fc_pool(h)) with scatter_reduce "amax". Both run on CPU and on ROCm.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from ml.gnn.graph import HeteroGraph
from ml.gnn.runtime import Backend

Aggregator = Literal["mean", "pool"]
AGGREGATORS: tuple[Aggregator, ...] = ("mean", "pool")


class _MeanSpMM(torch.autograd.Function):
    """mean_neigh = A @ h with A the row-normalised adjacency (destination x source, CSR); the
    gradient is A^T @ g with A^T precomputed, also CSR. Row-parallel sparse products: no atomic
    add, so the result does not depend on thread scheduling."""

    @staticmethod
    def forward(ctx: Any, a: Tensor, a_t: Tensor, h: Tensor) -> Tensor:
        ctx.a_t = a_t
        out: Tensor = torch.sparse.mm(a, h)
        return out

    @staticmethod
    def backward(ctx: Any, grad: Tensor) -> tuple[None, None, Tensor]:
        return None, None, torch.sparse.mm(ctx.a_t, grad.contiguous())


def _csr(rows: Tensor, cols: Tensor, values: Tensor, shape: tuple[int, int]) -> Tensor:
    order = torch.argsort(rows, stable=True)
    crow = torch.zeros(shape[0] + 1, dtype=torch.int64, device=rows.device)
    crow[1:] = torch.cumsum(torch.bincount(rows, minlength=shape[0]), 0)
    return torch.sparse_csr_tensor(crow, cols[order], values[order], size=shape)


@dataclass(frozen=True)
class TorchRelation:
    """One relation on a device: source and destination indices, and the row-normalised
    adjacency (destination x source) with its transpose, both CSR, for the mean."""

    src_type: str
    name: str
    dst_type: str
    src: Tensor
    dst: Tensor
    num_dst: int
    adjacency: Tensor  # CSR, (num_dst, num_src), 1 / in-degree on each edge
    adjacency_t: Tensor  # CSR, (num_src, num_dst), the transpose

    def mean(self, h: Tensor) -> Tensor:
        """Mean of the source rows over each destination's in-edges; 0 without in-edges."""
        out: Tensor = _MeanSpMM.apply(self.adjacency, self.adjacency_t, h)
        return out

    def max(self, h: Tensor) -> Tensor:
        """Element-wise max over each destination's in-edges; 0 without in-edges."""
        out = h.new_zeros((self.num_dst, h.shape[1]))
        index = self.dst.unsqueeze(1).expand(-1, h.shape[1])
        result: Tensor = out.scatter_reduce(
            0, index, h.index_select(0, self.src), "amax", include_self=False
        )
        return result


@dataclass(frozen=True)
class TorchGraph:
    num_nodes: dict[str, int]
    relations: tuple[TorchRelation, ...]


def torch_relation(
    src_type: str, name: str, dst_type: str, src: Tensor, dst: Tensor, n_src: int, n_dst: int
) -> TorchRelation:
    degree = torch.bincount(dst, minlength=n_dst).to(torch.float32).clamp_(min=1.0)
    values = degree.index_select(0, dst).reciprocal()
    return TorchRelation(
        src_type,
        name,
        dst_type,
        src,
        dst,
        n_dst,
        _csr(dst, src, values, (n_dst, n_src)),
        _csr(src, dst, values, (n_src, n_dst)),
    )


def to_device_graph(graph: HeteroGraph, backend: Backend, device: torch.device) -> Any:
    """The graph for a backend: a dgl.DGLHeteroGraph or a TorchGraph, on `device`."""
    if backend == "dgl":
        os.environ.setdefault("DGLBACKEND", "pytorch")
        import dgl

        data = {
            r.canonical: (torch.from_numpy(r.src), torch.from_numpy(r.dst)) for r in graph.relations
        }
        return dgl.heterograph(data, num_nodes_dict=dict(graph.num_nodes)).to(device)
    relations = []
    for r in graph.relations:
        relations.append(
            torch_relation(
                r.src_type,
                r.name,
                r.dst_type,
                torch.from_numpy(r.src).to(device),
                torch.from_numpy(r.dst).to(device),
                graph.num_nodes[r.src_type],
                graph.num_nodes[r.dst_type],
            )
        )
    return TorchGraph(dict(graph.num_nodes), tuple(relations))


class TorchSAGEConv(nn.Module):
    """dgl.nn.SAGEConv (mean or pool) on a (source, destination) pair, in plain PyTorch."""

    def __init__(
        self,
        in_feats: tuple[int, int],
        out_feats: int,
        aggregator_type: Aggregator,
        feat_drop: float = 0.0,
        bias: bool = True,
    ) -> None:
        super().__init__()
        if aggregator_type not in AGGREGATORS:
            raise ValueError(f"aggregator must be one of {AGGREGATORS}, not {aggregator_type!r}")
        self._in_src_feats, self._in_dst_feats = in_feats
        self._out_feats = out_feats
        self._aggre_type = aggregator_type
        self.feat_drop = nn.Dropout(feat_drop)
        if aggregator_type == "pool":
            self.fc_pool = nn.Linear(self._in_src_feats, self._in_src_feats)
        self.fc_neigh = nn.Linear(self._in_src_feats, out_feats, bias=False)
        self.fc_self = nn.Linear(self._in_dst_feats, out_feats, bias=bias)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        gain = nn.init.calculate_gain("relu")
        if self._aggre_type == "pool":
            nn.init.xavier_uniform_(self.fc_pool.weight, gain=gain)
        nn.init.xavier_uniform_(self.fc_self.weight, gain=gain)
        nn.init.xavier_uniform_(self.fc_neigh.weight, gain=gain)

    def forward(self, relation: TorchRelation, feat: tuple[Tensor, Tensor]) -> Tensor:
        feat_src = self.feat_drop(feat[0])
        feat_dst = self.feat_drop(feat[1])
        if self._aggre_type == "mean":
            lin_before_mp = self._in_src_feats > self._out_feats
            h = self.fc_neigh(feat_src) if lin_before_mp else feat_src
            h_neigh = relation.mean(h)
            if not lin_before_mp:
                h_neigh = self.fc_neigh(h_neigh)
        else:
            h_neigh = self.fc_neigh(relation.max(F.relu(self.fc_pool(feat_src))))
        out: Tensor = self.fc_self(feat_dst) + h_neigh
        return out


class TorchHeteroConv(nn.Module):
    """dgl.nn.HeteroGraphConv with aggregate="sum": one convolution per relation in `mods`."""

    def __init__(self, mods: Mapping[str, nn.Module]) -> None:
        super().__init__()
        self.mods = nn.ModuleDict(dict(mods))

    def forward(self, graph: TorchGraph, inputs: Mapping[str, Tensor]) -> dict[str, Tensor]:
        outputs: dict[str, list[Tensor]] = {}
        for r in graph.relations:
            if r.src_type not in inputs or r.dst_type not in inputs:
                continue
            out = self.mods[r.name](r, (inputs[r.src_type], inputs[r.dst_type]))
            outputs.setdefault(r.dst_type, []).append(out)
        return {k: torch.stack(v, dim=0).sum(dim=0) for k, v in outputs.items()}


def _conv(
    backend: Backend,
    in_feats: tuple[int, int],
    out_feats: int,
    aggregator: Aggregator,
    dropout: float,
) -> nn.Module:
    if backend == "dgl":
        import dgl.nn as dglnn

        conv: nn.Module = dglnn.SAGEConv(in_feats, out_feats, aggregator, feat_drop=dropout)
        return conv
    return TorchSAGEConv(in_feats, out_feats, aggregator, feat_drop=dropout)


def _hetero(backend: Backend, mods: dict[str, nn.Module]) -> nn.Module:
    if backend == "dgl":
        import dgl.nn as dglnn

        layer: nn.Module = dglnn.HeteroGraphConv(mods, aggregate="sum")
        return layer
    return TorchHeteroConv(mods)


@dataclass(frozen=True)
class EncoderConfig:
    """GraphSAGE encoder: `layers` heterogeneous SAGE layers, `hidden` units, the last `out`."""

    in_dims: tuple[tuple[str, int], ...]  # (node type, input features), in a fixed order
    relations: tuple[tuple[str, str, str], ...]  # canonical (src, name, dst)
    hidden: int = 16
    out: int = 16
    layers: int = 1
    aggregator: Aggregator = "mean"
    dropout: float = 0.2
    final_relu: bool = True  # classification heads: relu after the last layer, as in the spike

    def as_dict(self) -> dict[str, Any]:
        return {
            "in_dims": dict(self.in_dims),
            "relations": [list(r) for r in self.relations],
            "hidden": self.hidden,
            "out": self.out,
            "layers": self.layers,
            "aggregator": self.aggregator,
            "dropout": self.dropout,
            "final_relu": self.final_relu,
        }


class Encoder(nn.Module):
    """Stacked heterogeneous SAGE layers; relu between layers (and after the last if asked)."""

    def __init__(self, config: EncoderConfig, backend: Backend) -> None:
        super().__init__()
        if config.layers < 1:
            raise ValueError("an encoder needs at least one layer")
        self.config = config
        self.backend = backend
        dims = dict(config.in_dims)
        self.layers = nn.ModuleList()
        for i in range(config.layers):
            out = config.out if i == config.layers - 1 else config.hidden
            mods = {
                name: _conv(backend, (dims[src], dims[dst]), out, config.aggregator, config.dropout)
                for src, name, dst in config.relations
            }
            self.layers.append(_hetero(backend, mods))
            dims = {t: out for t in dims}

    def forward(self, graph: Any, x: Mapping[str, Tensor]) -> dict[str, Tensor]:
        h: dict[str, Tensor] = dict(x)
        last = len(self.layers) - 1
        for i, layer in enumerate(self.layers):
            h = layer(graph, h)
            if i < last or self.config.final_relu:
                h = {k: F.relu(v) for k, v in h.items()}
        return h


class EdgeClassifier(nn.Module):
    """Logit of an edge (customer, variant): a 2-layer MLP head, as in the paper, on the two node
    representations and the edge's own features. Without an encoder, the head reads the input
    node features directly: the same model with no message passing (the "no graph" control)."""

    def __init__(
        self,
        encoder: Encoder | None,
        node_dims: tuple[int, int],
        edge_dim: int,
        hidden: int = 64,
        dropout: float = 0.2,
        types: tuple[str, str] = ("customer", "variant"),
    ) -> None:
        super().__init__()
        self.encoder = encoder
        self.types = types
        width = node_dims[0] + node_dims[1] + edge_dim
        self.head = nn.Sequential(
            nn.Linear(width, hidden), nn.ReLU(), nn.Dropout(dropout), nn.Linear(hidden, 1)
        )

    def nodes(self, graph: Any, x: Mapping[str, Tensor]) -> dict[str, Tensor]:
        """Node representations: the encoder's output, or the input features without one."""
        return dict(x) if self.encoder is None else self.encoder(graph, x)

    def logits(
        self, h: Mapping[str, Tensor], left: Tensor, right: Tensor, edge_x: Tensor
    ) -> Tensor:
        a, b = self.types
        z = torch.cat([h[a].index_select(0, left), h[b].index_select(0, right), edge_x], dim=1)
        out: Tensor = self.head(z).squeeze(1)
        return out


def count_parameters(module: nn.Module) -> int:
    return int(sum(p.numel() for p in module.parameters() if p.requires_grad))
