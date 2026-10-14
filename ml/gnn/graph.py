"""Heterogeneous graphs as plain index arrays (no torch): node types, relations, node keys.

A relation is (source type, name, destination type) with one int64 source and destination index
per edge. Messages flow from source to destination, so an undirected bipartite graph is two
relations, one per direction (as dgl.heterograph needs). ml.gnn.model turns a HeteroGraph into a
DGL graph or into the tensors of the pure-PyTorch layers; both see the same edges.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

I64 = npt.NDArray[np.int64]


@dataclass(frozen=True)
class Relation:
    src_type: str
    name: str
    dst_type: str
    src: I64
    dst: I64

    @property
    def canonical(self) -> tuple[str, str, str]:
        return (self.src_type, self.name, self.dst_type)


@dataclass(frozen=True)
class HeteroGraph:
    num_nodes: dict[str, int]
    relations: tuple[Relation, ...]

    def __post_init__(self) -> None:
        names = [r.name for r in self.relations]
        if len(set(names)) != len(names):
            raise ValueError(f"relation names must be unique: {names}")
        for r in self.relations:
            for kind, index in ((r.src_type, r.src), (r.dst_type, r.dst)):
                if kind not in self.num_nodes:
                    raise ValueError(f"{r.name}: unknown node type {kind!r}")
                if index.shape != r.src.shape or index.ndim != 1:
                    raise ValueError(f"{r.name}: source and destination must be 1-D, same length")
                if index.size and (index.min() < 0 or index.max() >= self.num_nodes[kind]):
                    raise ValueError(f"{r.name}: index out of range for {kind!r}")

    @property
    def canonical_relations(self) -> list[tuple[str, str, str]]:
        return [r.canonical for r in self.relations]

    def num_edges(self) -> int:
        return int(sum(r.src.size for r in self.relations))

    def without_edges(
        self, name_pairs: tuple[str, str], keep: npt.NDArray[np.bool_]
    ) -> HeteroGraph:
        """The graph with only the `keep` edges of a pair of reverse relations (same edge order in
        both), every other relation unchanged: link prediction hides its target edges this way."""
        out = []
        for r in self.relations:
            if r.name in name_pairs:
                if keep.shape != r.src.shape:
                    raise ValueError(f"{r.name}: keep mask of the wrong length")
                out.append(Relation(r.src_type, r.name, r.dst_type, r.src[keep], r.dst[keep]))
            else:
                out.append(r)
        return HeteroGraph(dict(self.num_nodes), tuple(out))


def bipartite(
    left: str,
    right: str,
    forward: str,
    backward: str,
    left_index: I64,
    right_index: I64,
    n_left: int,
    n_right: int,
) -> HeteroGraph:
    """An undirected bipartite graph: one relation left -> right and its reverse, edge i of both
    joining left_index[i] and right_index[i]."""
    li = np.ascontiguousarray(left_index, dtype=np.int64)
    ri = np.ascontiguousarray(right_index, dtype=np.int64)
    return HeteroGraph(
        {left: int(n_left), right: int(n_right)},
        (Relation(left, forward, right, li, ri), Relation(right, backward, left, ri, li)),
    )


def index_keys(keys: npt.ArrayLike) -> tuple[npt.NDArray[np.generic], I64]:
    """(sorted unique keys, the index of each input key in them): node ids 0..n-1 from any keys."""
    unique, inverse = np.unique(np.asarray(keys), return_inverse=True)
    return unique, inverse.astype(np.int64).reshape(-1)


def degrees(index: I64, n: int) -> npt.NDArray[np.float64]:
    """Edges per node."""
    return np.bincount(index, minlength=n).astype(np.float64)
