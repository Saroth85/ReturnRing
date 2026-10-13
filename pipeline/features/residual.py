"""Return residual of spec 8.2 (US-206, UNIT-02).

For a customer c with observed lines l (settled at the decision time, pipeline.features.lines),
outcome r_l (1: returned on the withdrawal path) and p_l from the product/cohort reference model
(never the customer's own history, rule 8):

    R_c = sum_l r_l - sum_l p_l        z_c = R_c / sqrt(sum_l p_l (1 - p_l))

z_c is missing (null), never infinite, when the variance is zero: a customer without observed
lines, or lines priced exactly 0 or 1 (the reference model keeps p in [0.01, 0.99], so only the
first happens in practice). The residual is an indicator, not a probability of abuse.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

RESIDUAL_COLUMNS = (
    "n_observations",
    "observed_returns",
    "expected_returns",
    "residual",
    "residual_variance",
    "residual_z",
)


@dataclass(frozen=True)
class Residual:
    n: int
    returned: int
    expected: float
    variance: float
    residual: float
    z: float | None


def residual(returned: Sequence[int | bool], p: Sequence[float]) -> Residual:
    """R_c and z_c of one customer from outcomes r_l and reference probabilities p_l."""
    if len(returned) != len(p):
        raise ValueError("one probability per line is needed")
    r = np.asarray(returned, dtype=np.float64)
    pp = np.asarray(p, dtype=np.float64)
    if not np.all(np.isfinite(pp)) or np.any((pp < 0) | (pp > 1)):
        raise ValueError("probabilities must lie in [0, 1]")
    if np.any((r != 0) & (r != 1)):
        raise ValueError("outcomes must be 0 or 1")
    expected = float(pp.sum())
    variance = float((pp * (1 - pp)).sum())
    value = float(r.sum()) - expected
    z = value / math.sqrt(variance) if variance > 0 else None
    return Residual(len(r), int(r.sum()), expected, variance, value, z)


def residuals(frame: pd.DataFrame, customers: Sequence[str] | None = None) -> pd.DataFrame:
    """Per customer, from rows (customer_id, returned, p): the RESIDUAL_COLUMNS. Customers listed
    in `customers` without rows get n_observations 0, residual 0 and a null z."""
    r = frame["returned"].astype(np.float64)
    p = frame["p"].astype(np.float64)
    if len(frame) and (not np.all(np.isfinite(p)) or ((p < 0) | (p > 1)).any()):
        raise ValueError("probabilities must lie in [0, 1]")
    parts = pd.DataFrame(
        {"customer_id": frame["customer_id"], "r": r, "p": p, "v": p * (1 - p), "one": 1}
    )
    grouped = parts.groupby("customer_id", sort=True)[["one", "r", "p", "v"]].sum()
    if customers is not None:
        grouped = grouped.reindex(sorted(set(customers)), fill_value=0)
    out = pd.DataFrame(index=grouped.index)
    out["n_observations"] = grouped["one"].astype(np.int64)
    out["observed_returns"] = grouped["r"].round().astype(np.int64)
    out["expected_returns"] = grouped["p"].astype(np.float64)
    out["residual"] = (grouped["r"] - grouped["p"]).astype(np.float64)
    out["residual_variance"] = grouped["v"].astype(np.float64)
    variance = out["residual_variance"]
    z = out["residual"] / np.sqrt(variance.where(variance > 0))
    out["residual_z"] = z.astype(np.float64)
    out.index.name = "customer_id"
    return out.reset_index()
