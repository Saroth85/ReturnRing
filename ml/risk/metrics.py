"""Metrics of the risk models (spec 8.4, 8.6; MOD-04, MOD-06).

- average precision (AUPRC): sum over the distinct scores, from the highest, of
  precision x recall gained; equal scores form one step (sklearn average_precision_score);
- precision at k: the share of abusive customers among the k highest scores. Ties at the k-th
  score are shared out: the tied group contributes its positive rate times the slots left, the
  expected precision under a random order of the ties, so a coarse rule is not favoured or
  penalised by the row order;
- threshold for a precision target (spec 8.6): the lowest score t whose rule "score >= t" reaches
  the target precision on a sample (the validation split), or None when no t does;
- false-positive rate: non-abusers with score >= threshold over non-abusers, overall and on the
  high-value customers, the 30% of non-abusers with the highest customer value (spec 8.4).
  Value is used only to choose who is counted, never as a feature (rule 6);
- expected calibration error: ten equal-width bins (ml.propensity.metrics, MOD-06).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
from sklearn.metrics import average_precision_score

from ml.propensity.metrics import expected_calibration_error

F64 = npt.NDArray[np.float64]
B = npt.NDArray[np.bool_]
PRECISION_TARGET = 0.8  # spec 8.6
HIGH_VALUE_SHARE = 0.3  # spec 8.4: top 30% by value among non-abusers
TOP_K = (50, 100)


def _labels(y: npt.ArrayLike) -> npt.NDArray[np.int8]:
    labels = np.asarray(y)
    if labels.ndim != 1 or not np.isin(labels, (0, 1)).all():
        raise ValueError("labels must be a 1-D array of 0 and 1")
    return labels.astype(np.int8)


def _scores(p: npt.ArrayLike, n: int) -> F64:
    scores = np.asarray(p, dtype=np.float64)
    if scores.shape != (n,) or not np.isfinite(scores).all():
        raise ValueError(f"expected {n} finite scores, got shape {scores.shape}")
    return scores


def average_precision(y: npt.ArrayLike, p: npt.ArrayLike) -> float | None:
    """AUPRC as average precision; None without a positive label."""
    labels = _labels(y)
    scores = _scores(p, labels.size)
    if labels.sum() == 0:
        return None
    return float(average_precision_score(labels, scores))


def precision_at_k(y: npt.ArrayLike, p: npt.ArrayLike, k: int) -> float:
    """Expected precision of the k highest scores, ties at the boundary shared out (k <= n)."""
    labels = _labels(y)
    scores = _scores(p, labels.size)
    if not 0 < k <= labels.size:
        raise ValueError(f"k must be in 1..{labels.size}, got {k}")
    order = np.argsort(-scores, kind="stable")
    kth = scores[order[k - 1]]
    above = scores > kth
    tied = scores == kth
    slots = k - int(above.sum())
    tied_rate = float(labels[tied].mean())
    return float((labels[above].sum() + slots * tied_rate) / k)


def precision_recall_at(
    y: npt.ArrayLike, p: npt.ArrayLike, threshold: float
) -> tuple[float, float, int]:
    """Precision and recall of "score >= threshold", and how many rows it flags. Precision is 0
    when nothing is flagged."""
    labels = _labels(y)
    flagged = _scores(p, labels.size) >= threshold
    n_flagged = int(flagged.sum())
    tp = int((flagged & (labels == 1)).sum())
    positives = int(labels.sum())
    precision = tp / n_flagged if n_flagged else 0.0
    recall = tp / positives if positives else 0.0
    return precision, recall, n_flagged


def threshold_for_precision(
    y: npt.ArrayLike, p: npt.ArrayLike, target: float = PRECISION_TARGET
) -> float | None:
    """The lowest score t such that "score >= t" has precision >= target on this sample."""
    labels = _labels(y)
    scores = _scores(p, labels.size)
    if not 0 < target <= 1:
        raise ValueError(f"target precision must be in (0, 1], got {target}")
    if labels.size == 0:
        return None
    order = np.argsort(-scores, kind="stable")
    sorted_scores = scores[order]
    tp = np.cumsum(labels[order], dtype=np.int64)
    flagged = np.arange(1, labels.size + 1)
    last_of_value = np.r_[sorted_scores[1:] != sorted_scores[:-1], True]
    precision = tp / flagged
    ok = last_of_value & (precision >= target - 1e-12)
    if not ok.any():
        return None
    return float(sorted_scores[np.flatnonzero(ok)[-1]])


def high_value_mask(value: npt.ArrayLike, y: npt.ArrayLike, share: float = HIGH_VALUE_SHARE) -> B:
    """Non-abusers whose value is at or above the (1 - share) quantile of the non-abusers'
    values: the high-value customers of spec 8.4."""
    labels = _labels(y)
    values = _scores(value, labels.size)
    negatives = labels == 0
    if not negatives.any():
        return np.zeros(labels.size, dtype=np.bool_)
    cut = float(np.quantile(values[negatives], 1.0 - share))
    return np.asarray(negatives & (values >= cut), dtype=np.bool_)


def false_positive_rate(
    y: npt.ArrayLike, p: npt.ArrayLike, threshold: float, among: B | None = None
) -> tuple[int, int, float | None]:
    """(false positives, non-abusers, rate) of "score >= threshold", optionally among a subset."""
    labels = _labels(y)
    scores = _scores(p, labels.size)
    keep = np.ones(labels.size, dtype=np.bool_) if among is None else np.asarray(among, bool)
    negatives = keep & (labels == 0)
    fp = int((negatives & (scores >= threshold)).sum())
    n = int(negatives.sum())
    return fp, n, (fp / n if n else None)


@dataclass(frozen=True)
class RiskMetrics:
    """Metrics of one model on one population, with the threshold chosen on validation."""

    n: int
    positives: int
    auprc: float | None
    precision_at: dict[int, float | None]
    threshold: float | None  # None: the precision target is not reached on validation
    flagged: int
    precision: float | None
    recall: float | None
    fpr: float | None
    fpr_high_value: float | None
    high_value_negatives: int
    high_value_false_positives: int
    ece: float
    brier: float

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["precision_at"] = {str(k): v for k, v in self.precision_at.items()}
        return out


def evaluate(
    y: npt.ArrayLike,
    p: npt.ArrayLike,
    threshold: float | None,
    value: npt.ArrayLike,
    *,
    rank: npt.ArrayLike | None = None,
    high_value: B | None = None,
    top_k: tuple[int, ...] = TOP_K,
) -> RiskMetrics:
    """Every metric of spec 8.4 on one population. p is the calibrated risk: the threshold, the
    false-positive rates and the ECE use it. rank is the model's score before calibration, for
    AUPRC and precision at k: isotonic calibration is monotone but merges scores into ties, so
    ranking quality is measured before it (default: p). high_value: who counts as a high-value
    non-abuser (default: high_value_mask of `value`). Without a threshold nothing is flagged."""
    labels = _labels(y)
    scores = _scores(p, labels.size)
    if labels.size == 0:
        raise ValueError("no row to evaluate")
    ranking = scores if rank is None else _scores(rank, labels.size)
    if high_value is None:
        high = high_value_mask(value, labels)
    else:
        high = np.asarray(high_value, dtype=np.bool_) & (labels == 0)
        if high.shape != labels.shape:
            raise ValueError("high_value must have one entry per row")
    at_k = {k: precision_at_k(labels, ranking, k) if k <= labels.size else None for k in top_k}
    precision: float | None
    fpr: float | None
    fpr_high: float | None
    if threshold is None:
        flagged, precision, recall, fpr, fp_high = 0, None, 0.0, 0.0, 0
        n_high = int(high.sum())
        fpr_high = 0.0 if n_high else None
    else:
        precision_, recall, flagged = precision_recall_at(labels, scores, threshold)
        precision = precision_ if flagged else None
        _, _, fpr = false_positive_rate(labels, scores, threshold)
        fp_high, n_high, fpr_high = false_positive_rate(labels, scores, threshold, high)
    return RiskMetrics(
        n=int(labels.size),
        positives=int(labels.sum()),
        auprc=average_precision(labels, ranking),
        precision_at=at_k,
        threshold=threshold,
        flagged=flagged,
        precision=precision,
        recall=recall,
        fpr=fpr,
        fpr_high_value=fpr_high,
        high_value_negatives=n_high,
        high_value_false_positives=fp_high,
        ece=expected_calibration_error(labels, scores),
        brier=float(np.mean((scores - labels) ** 2)),
    )
