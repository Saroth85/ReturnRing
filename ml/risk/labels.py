"""Labels of the abuse-risk model, from the synthetic ground truth (spec 8.4).

Offline training and evaluation jobs only (rule 12 as amended by ADR-13): the batch scoring and
every service never import this module.

wardrober and promo_ring are 1; normal, size and shared_benign are 0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pandas as pd

from contracts.io import read_table

ABUSIVE: Final = ("wardrober", "promo_ring")
NOT_ABUSIVE: Final = ("normal", "size", "shared_benign")


def ood_truth_root(data: Path) -> Path:
    """Where the out-of-distribution ground truth lives under a data root (ADR-13 amendment 1):
    <data>/truth/ood, holding truth/labels.parquet."""
    return data / "truth" / "ood"


def has_labels(truth_root: Path) -> bool:
    return (truth_root / "truth" / "labels.parquet").exists()


def read_labels(truth_root: Path | str) -> pd.DataFrame:
    """truth/labels under truth_root, indexed by customer_id, with the 0/1 column `abusive`."""
    table, _ = read_table("truth/labels", truth_root)
    labels = table.to_pandas()
    unknown = set(labels["behaviour"]) - set(ABUSIVE) - set(NOT_ABUSIVE)
    if unknown:
        raise ValueError(f"behaviours without a label: {sorted(unknown)}")
    labels["abusive"] = labels["behaviour"].isin(ABUSIVE).astype("int8")
    return labels.set_index("customer_id", drop=False)
