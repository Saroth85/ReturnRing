"""The split shared by every risk model (spec 8.4; MOD-04, MOD-07).

Grouped: accounts that are linked stay on one side. A group is a connected set of customers
joined by any of these keys:

- the graph cluster at the evaluation time (features/clusters: observable linked accounts);
- the ground-truth ring and family (truth/labels, read by this offline job only), so a ring
  whose members the graph does not link is still never split.

Every group goes to exactly one side: train, validation or test. The groups are stratified by
their most specific behaviour (ring, wardrober, family, other: at least one member with it, in
that order), then shuffled with a fixed seed inside each stratum; the first test_share of each
stratum is test, validation_share of the rest is validation, the remainder train.

Time-ordered: the models learn from the rows known at the train end (the first nine months of
the store) and are evaluated on the rows of the last three months, with features filtered by
availability (rule 7). The split of the groups does not change with time, so no cluster, ring or
family is ever on both sides (MOD-07).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

import networkx as nx
import numpy as np
import pandas as pd

Side = Literal["train", "validation", "test"]
SIDES: Final[tuple[Side, ...]] = ("train", "validation", "test")
STRATA: Final = ("ring", "wardrober", "family", "other")
_STRATUM_OF: Final = {"promo_ring": "ring", "wardrober": "wardrober", "shared_benign": "family"}


@dataclass(frozen=True)
class SplitConfig:
    test_share: float = 0.3
    validation_share: float = 0.2  # of the groups that are not test
    seed: int = 42

    def __post_init__(self) -> None:
        for name in ("test_share", "validation_share"):
            if not 0.0 < getattr(self, name) < 1.0:
                raise ValueError(f"{name} must be in (0, 1)")


def groups(customers: Sequence[str], keys: Mapping[str, pd.Series]) -> pd.Series:
    """The group of every customer: customers sharing a non-null value of any key series (indexed
    by customer_id) are in one group. The group is named after its smallest customer id."""
    graph = nx.Graph()
    graph.add_nodes_from(customers)
    for kind, series in keys.items():
        values = series.dropna()
        for customer, value in zip(values.index, values.to_numpy(), strict=True):
            if customer in graph:
                graph.add_edge(customer, ("key", kind, str(value)))
    out: dict[str, str] = {}
    for component in nx.connected_components(graph):
        members = sorted(n for n in component if isinstance(n, str))
        for member in members:
            out[member] = members[0]
    return pd.Series(out, name="group").reindex(list(customers))


def strata(group: pd.Series, behaviour: pd.Series) -> pd.Series:
    """The stratum of every group (index: group name) from its members' behaviours."""
    frame = pd.DataFrame({"group": group, "behaviour": behaviour.reindex(group.index)})
    found = frame["behaviour"].map(_STRATUM_OF).fillna("other")
    rank = found.map({s: i for i, s in enumerate(STRATA)})
    best = rank.groupby(frame["group"]).min()
    return best.map(dict(enumerate(STRATA))).rename("stratum")


def assign(group: pd.Series, stratum: pd.Series, config: SplitConfig) -> pd.Series:
    """The side of every customer (index of `group`), drawn per stratum of groups."""
    rng = np.random.default_rng(config.seed)
    side_of: dict[str, Side] = {}
    for name in STRATA:
        names = sorted(stratum.index[stratum == name])
        if not names:
            continue
        order = rng.permutation(len(names))
        n_test = int(np.ceil(config.test_share * len(names)))
        n_val = int(np.round(config.validation_share * (len(names) - n_test)))
        for rank, i in enumerate(order):
            side: Side = (
                "test" if rank < n_test else "validation" if rank < n_test + n_val else "train"
            )
            side_of[names[i]] = side
    return group.map(side_of).rename("side")


def shared_keys(side: pd.Series, keys: Mapping[str, pd.Series]) -> dict[str, list[str]]:
    """MOD-07: per key kind, the values whose customers are on more than one side (empty)."""
    out: dict[str, list[str]] = {}
    for kind, series in keys.items():
        values = series.dropna()
        frame = pd.DataFrame({"value": values, "side": side.reindex(values.index)})
        sides = frame.groupby("value")["side"].nunique()
        out[kind] = sorted(str(v) for v in sides.index[sides > 1])
    return out


def split_keys(clusters: pd.DataFrame, labels: pd.DataFrame) -> dict[str, pd.Series]:
    """The keys that join customers into groups: the graph cluster (features/clusters rows at the
    evaluation time) and the ring and family of the labels (indexed by customer_id)."""
    return {
        "cluster": clusters.set_index("customer_id")["cluster_id"],
        "ring": labels["ring_id"],
        "family": labels["family_id"],
    }


def split_customers(
    customers: Iterable[str],
    keys: Mapping[str, pd.Series],
    behaviour: pd.Series,
    config: SplitConfig | None = None,
) -> pd.DataFrame:
    """customer_id, group, stratum and side for every customer; raises if MOD-07 fails."""
    config = config or SplitConfig()
    ids = sorted(set(customers))
    group = groups(ids, keys)
    stratum = strata(group, behaviour)
    side = assign(group, stratum, config)
    leaks = {kind: values for kind, values in shared_keys(side, keys).items() if values}
    if leaks:  # pragma: no cover - groups() makes this impossible; a guard on the invariant
        raise RuntimeError(f"MOD-07: keys on both sides of the split: {leaks}")
    return pd.DataFrame(
        {
            "customer_id": ids,
            "group": group.to_numpy(),
            "stratum": group.map(stratum).to_numpy(),
            "side": side.to_numpy(),
        }
    )
