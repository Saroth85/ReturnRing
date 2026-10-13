"""Identifier weights and link formulas of spec 8.3 (US-203).

A link between two accounts i and j that share the identifier kinds K_ij weighs

    w_ij = 1 - prod_{k in K_ij} (1 - w_k)

with w_k: card 1.0, normalised e-mail 0.9, device 0.8, address 0.6, phone 0.6, IP prefix 0.2.
Its decayed weight uses, for each kind, the time since the pair last came to share a value of that
kind (dt_k) and the number of accounts sharing that value (d_k >= 2):

    w_k* = w_k * exp(-dt_k / tau) / log2(d_k),   tau = 180 days (spec 8.3 and D-15: a hypothesis)

so a link between two accounts only (d_k = 2) keeps its weight, and a pickup point shared by 200
customers keeps 1/log2(200), about an eighth. Every value here is a design hypothesis of the spec.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from contracts.parquet import IDENTIFIER_KINDS

KIND_WEIGHTS: Final = MappingProxyType(
    {"card": 1.0, "email": 0.9, "device": 0.8, "address": 0.6, "phone": 0.6, "ip_prefix": 0.2}
)
TAU_DAYS: Final = 180.0
# Links with w_ij >= 0.8 form the candidate clusters; components over 20 accounts are split with
# Louvain (spec 8.3).
CLUSTER_THRESHOLD: Final = 0.8
MAX_CLUSTER_SIZE: Final = 20
LOUVAIN_SEED: Final = 42
# w_ij is compared with the threshold with this tolerance: 1 - (1 - 0.8) is not exactly 0.8.
WEIGHT_TOLERANCE: Final = 1e-9
# Kinds that make a cluster-strength link alone: card, e-mail, device.
STRONG_KINDS: Final = tuple(k for k in IDENTIFIER_KINDS if KIND_WEIGHTS[k] >= CLUSTER_THRESHOLD)

WeightColumn = Literal["weight", "weight_decayed"]


def prunable_kinds(threshold: float) -> tuple[str, ...]:
    """Kinds whose weight alone stays below `threshold`: one shared value of such a kind never
    makes a cluster link by itself, so a hub of that kind can be pruned without changing any
    cluster (links.build_links)."""
    return tuple(k for k in IDENTIFIER_KINDS if KIND_WEIGHTS[k] < threshold - WEIGHT_TOLERANCE)


def link_weight(kind_weights: Iterable[float]) -> float:
    """w_ij = 1 - prod (1 - w_k) over the shared kinds; 0.0 when nothing is shared."""
    remaining = 1.0
    for w in kind_weights:
        if not 0.0 <= w <= 1.0:
            raise ValueError(f"a kind weight lies in [0, 1], not {w!r}")
        remaining *= 1.0 - w
    return 1.0 - remaining


def decayed_kind_weight(
    kind: str, age_days: float, sharers: int, tau_days: float = TAU_DAYS
) -> float:
    """w_k* = w_k * exp(-age / tau) / log2(d_k) for one shared value.

    age_days: days from the instant the two accounts came to share the value to the decision
    time; never negative (a later use is not known at decision time, rule 7). sharers: d_k, the
    accounts using the value, at least 2."""
    weight = KIND_WEIGHTS[kind]
    if not age_days >= 0.0:  # also rejects NaN
        raise ValueError(f"age must be >= 0 days (no use after the decision time), not {age_days}")
    if sharers < 2:
        raise ValueError(f"a shared value has at least 2 sharers, not {sharers}")
    return weight * math.exp(-age_days / tau_days) / math.log2(sharers)


@dataclass(frozen=True)
class GraphParams:
    """Parameters of the account graph; the defaults are the spec 8.3 values.

    threshold: links with weight_column >= threshold join a cluster.
    max_cluster_size: connected components above it are split with Louvain (seed `seed`).
    hub_degree: a value of a prunable kind (prunable_kinds(threshold)) shared by more accounts
        than this is a hub: it creates no candidate pair by itself (links.build_links); None
        disables pruning. Default: the cluster size limit, since a value shared by more accounts
        than any admissible cluster describes a place or a network, not a group of accounts.
    tau_days: time constant of the decay.
    weight_column: "weight" (spec 8.3, the product) or "weight_decayed" (an ablation).
    """

    threshold: float = CLUSTER_THRESHOLD
    max_cluster_size: int = MAX_CLUSTER_SIZE
    hub_degree: int | None = MAX_CLUSTER_SIZE
    tau_days: float = TAU_DAYS
    seed: int = LOUVAIN_SEED
    weight_column: WeightColumn = "weight"

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError(f"threshold must lie in (0, 1], not {self.threshold}")
        if self.max_cluster_size < 2:
            raise ValueError("max_cluster_size must be at least 2")
        if self.hub_degree is not None and self.hub_degree < 2:
            raise ValueError("hub_degree must be at least 2, or None")
        if not self.tau_days > 0.0:
            raise ValueError("tau_days must be positive")
        if self.weight_column not in ("weight", "weight_decayed"):
            raise ValueError(
                f"weight_column must be weight or weight_decayed: {self.weight_column}"
            )

    def describe(self) -> str:
        """One line for provenance and reports."""
        hubs = "off" if self.hub_degree is None else f"d > {self.hub_degree}"
        return (
            f"{self.weight_column} >= {self.threshold:g}, Louvain above {self.max_cluster_size} "
            f"accounts (seed {self.seed}), tau {self.tau_days:g} days, hub pruning {hubs}"
        )
