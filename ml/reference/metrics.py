"""Calibration and discrimination metrics of a line predictor (MOD-06, spec 8.1)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from sklearn.metrics import roc_auc_score

from ml.reference.model import log_loss

ECE_BINS = 10


@dataclass(frozen=True)
class Calibration:
    n: int
    observed_rate: float
    mean_p: float
    log_loss: float
    brier: float
    ece: float  # expected calibration error, 10 equal-width bins, weighted by count
    auc: float | None  # None with a single class


def reliability(
    y: npt.ArrayLike, p: npt.ArrayLike, bins: int = ECE_BINS
) -> list[tuple[float, float, int, float, float]]:
    """Per equal-width bin of p: (low, high, count, mean p, observed rate); empty bins skipped."""
    yy = np.asarray(y, dtype=np.float64)
    pp = np.asarray(p, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.digitize(pp, edges[1:-1], right=False), 0, bins - 1)
    out = []
    for b in range(bins):
        mask = idx == b
        if mask.any():
            out.append(
                (
                    float(edges[b]),
                    float(edges[b + 1]),
                    int(mask.sum()),
                    float(pp[mask].mean()),
                    float(yy[mask].mean()),
                )
            )
    return out


def calibration(y: npt.ArrayLike, p: npt.ArrayLike, bins: int = ECE_BINS) -> Calibration:
    yy = np.asarray(y, dtype=np.float64)
    pp = np.asarray(p, dtype=np.float64)
    if len(yy) == 0:
        raise ValueError("no line to evaluate")
    table = reliability(yy, pp, bins)
    ece = sum(n * abs(mean_p - rate) for _, _, n, mean_p, rate in table) / len(yy)
    auc = float(roc_auc_score(yy, pp)) if yy.min() != yy.max() else None
    return Calibration(
        n=len(yy),
        observed_rate=float(yy.mean()),
        mean_p=float(pp.mean()),
        log_loss=log_loss(yy, pp),
        brier=float(np.mean((pp - yy) ** 2)),
        ece=float(ece),
        auc=auc,
    )
