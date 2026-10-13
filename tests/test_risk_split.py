"""The grouped split shared by every risk model (US-204; MOD-04, MOD-07) on hand-built groups.

Every value here is a made-up test fixture (nature: synthetic)."""

from __future__ import annotations

import pandas as pd
import pytest

from ml.risk.split import SplitConfig, groups, shared_keys, split_customers, strata


def keys() -> dict[str, pd.Series]:
    # Cluster CL-1 links A and B; ring R-1 links B and C (C has no cluster link); family F-1
    # links D and E; F is alone.
    return {
        "cluster": pd.Series({"A": "CL-1", "B": "CL-1", "C": None, "D": "CL-2", "E": "CL-2"}),
        "ring": pd.Series({"A": None, "B": "R-1", "C": "R-1", "D": None}),
        "family": pd.Series({"D": "F-1", "E": "F-1"}),
    }


def test_groups_join_any_shared_key() -> None:
    g = groups(["A", "B", "C", "D", "E", "F"], keys())
    assert g.to_dict() == {"A": "A", "B": "A", "C": "A", "D": "D", "E": "D", "F": "F"}


def test_strata_take_the_most_specific_behaviour() -> None:
    g = groups(["A", "B", "C", "D", "E", "F"], keys())
    behaviour = pd.Series(
        {
            "A": "normal",
            "B": "promo_ring",
            "C": "wardrober",
            "D": "shared_benign",
            "E": "shared_benign",
            "F": "size",
        }
    )
    assert strata(g, behaviour).to_dict() == {"A": "ring", "D": "family", "F": "other"}


def population(n: int) -> tuple[list[str], dict[str, pd.Series], pd.Series]:
    """n pairs of linked customers, one ring of five accounts, and n single customers."""
    ids, cluster, ring, behaviour = [], {}, {}, {}
    for i in range(n):
        a, b, c = f"P{i:03d}a", f"P{i:03d}b", f"S{i:03d}"
        ids += [a, b, c]
        cluster[a] = cluster[b] = f"CL-{i:03d}"
        behaviour[a] = behaviour[b] = "shared_benign" if i % 3 == 0 else "normal"
        behaviour[c] = "wardrober" if i % 10 == 0 else "normal"
    for j in range(5):
        cid = f"R{j}"
        ids.append(cid)
        ring[cid] = "R-1"
        behaviour[cid] = "promo_ring"
    keys = {
        "cluster": pd.Series(cluster),
        "ring": pd.Series(ring),
        "family": pd.Series(dtype=object),
    }
    return ids, keys, pd.Series(behaviour)


def test_no_key_is_on_two_sides_and_shares_hold_per_stratum() -> None:
    """MOD-07: a cluster, ring or family never has members in two sides."""
    ids, k, behaviour = population(60)
    split = split_customers(ids, k, behaviour, SplitConfig(test_share=0.3, validation_share=0.2))
    side = split.set_index("customer_id")["side"]
    assert all(v == [] for v in shared_keys(side, k).values())
    ring = split.loc[split["customer_id"].str.startswith("R"), "side"]
    assert ring.nunique() == 1  # the whole ring on one side
    per_group = split.drop_duplicates("group")
    others = per_group.loc[per_group["stratum"] == "other", "side"].value_counts()
    # "other": 40 pairs without a family member and 54 single customers, 94 groups.
    assert others["test"] == 29  # ceil(0.3 * 94)
    assert others["validation"] == 13  # round(0.2 * (94 - 29))
    assert set(split["side"]) == {"train", "validation", "test"}


def test_the_split_is_deterministic_and_depends_on_the_seed() -> None:
    ids, k, behaviour = population(60)
    first = split_customers(ids, k, behaviour, SplitConfig(seed=42))
    again = split_customers(list(reversed(ids)), k, behaviour, SplitConfig(seed=42))
    pd.testing.assert_frame_equal(first, again)
    other = split_customers(ids, k, behaviour, SplitConfig(seed=7))
    assert not first["side"].equals(other["side"])


def test_shared_keys_finds_a_leak() -> None:
    side = pd.Series({"A": "train", "B": "test"})
    assert shared_keys(side, {"cluster": pd.Series({"A": "CL-1", "B": "CL-1"})}) == {
        "cluster": ["CL-1"]
    }


@pytest.mark.parametrize("share", [0.0, 1.0, -0.1])
def test_shares_must_be_proper_fractions(share: float) -> None:
    with pytest.raises(ValueError):
        SplitConfig(test_share=share)
