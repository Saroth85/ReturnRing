"""Metrics of a probabilistic binary classifier (US-201, MOD-01, MOD-06).

- precision, recall and F1 of the rule "returned when p >= threshold"; precision is 0 when nothing
  is predicted returned, F1 is 0 when nothing is right (no division by zero);
- cross-entropy: mean negative log-likelihood in nats, natural logarithm; the probabilities must
  lie strictly inside (0, 1), which the calibrator guarantees by clipping;
- expected calibration error (ECE): ten equal-width bins of the predicted probability over [0, 1]
  (the last bin holds 1.0), the mean of |observed rate - mean prediction| weighted by bin size;
- best_f1_threshold: the predicted value that maximises F1 on a sample; it is chosen on the
  validation split, never on the test split.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

ECE_BINS = 10
ECE_MAX = 0.05  # MOD-06
FloatArray = npt.NDArray[np.floating[Any]]
IntArray = npt.NDArray[np.integer[Any]] | npt.NDArray[np.bool_]


@dataclass(frozen=True)
class BinaryMetrics:
    """The metrics of one model on one population (rounded only when shown)."""

    n: int
    positives: int
    threshold: float
    precision: float
    recall: float
    f1: float
    f1_at_half: float
    cross_entropy: float
    ece: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _labels(y: IntArray) -> npt.NDArray[np.int8]:
    labels = np.asarray(y).astype(np.int8)
    if labels.ndim != 1 or not np.isin(labels, (0, 1)).all():
        raise ValueError("labels must be a 1-D array of 0 and 1")
    return labels


def _probabilities(p: FloatArray, n: int) -> npt.NDArray[np.float64]:
    probs = np.asarray(p, dtype=np.float64)
    if probs.shape != (n,):
        raise ValueError(f"expected {n} probabilities, got shape {probs.shape}")
    if not np.isfinite(probs).all() or (probs < 0).any() or (probs > 1).any():
        raise ValueError("probabilities must be finite and in [0, 1]")
    return probs


def precision_recall_f1(y: IntArray, p: FloatArray, threshold: float) -> tuple[float, float, float]:
    labels = _labels(y)
    predicted = _probabilities(p, labels.size) >= threshold
    tp = int(np.sum(predicted & (labels == 1)))
    fp = int(np.sum(predicted & (labels == 0)))
    fn = int(np.sum(~predicted & (labels == 1)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
    return precision, recall, f1


def cross_entropy(y: IntArray, p: FloatArray) -> float:
    labels = _labels(y)
    probs = _probabilities(p, labels.size)
    if labels.size == 0:
        raise ValueError("cross-entropy of an empty sample")
    if (probs <= 0).any() or (probs >= 1).any():
        raise ValueError("cross-entropy needs probabilities strictly inside (0, 1)")
    ll = np.where(labels == 1, np.log(probs), np.log1p(-probs))
    return float(-ll.mean())


def expected_calibration_error(y: IntArray, p: FloatArray, bins: int = ECE_BINS) -> float:
    labels = _labels(y)
    probs = _probabilities(p, labels.size)
    if labels.size == 0:
        raise ValueError("ECE of an empty sample")
    index = np.minimum((probs * bins).astype(np.int64), bins - 1)
    count = np.bincount(index, minlength=bins)
    observed = np.bincount(index, weights=labels, minlength=bins)
    predicted = np.bincount(index, weights=probs, minlength=bins)
    used = count > 0
    gap = np.abs(observed[used] - predicted[used])  # = count * |rate - mean prediction|
    return float(gap.sum() / labels.size)


def best_f1_threshold(y: IntArray, p: FloatArray) -> float:
    """The predicted value t maximising F1 of "p >= t" (ties between values: the highest t)."""
    labels = _labels(y)
    probs = _probabilities(p, labels.size)
    if labels.size == 0 or labels.sum() == 0:
        raise ValueError("a threshold needs at least one positive label")
    order = np.argsort(-probs, kind="stable")
    sorted_p = probs[order]
    tp = np.cumsum(labels[order], dtype=np.int64)
    predicted = np.arange(1, labels.size + 1)
    f1 = 2 * tp / (predicted + labels.sum())
    last_of_value = np.r_[sorted_p[1:] != sorted_p[:-1], True]
    candidates = np.where(last_of_value, f1, -1.0)
    return float(sorted_p[int(np.argmax(candidates))])


def binary_metrics(y: IntArray, p: FloatArray, threshold: float) -> BinaryMetrics:
    labels = _labels(y)
    probs = _probabilities(p, labels.size)
    precision, recall, f1 = precision_recall_f1(labels, probs, threshold)
    return BinaryMetrics(
        n=int(labels.size),
        positives=int(labels.sum()),
        threshold=float(threshold),
        precision=precision,
        recall=recall,
        f1=f1,
        f1_at_half=precision_recall_f1(labels, probs, 0.5)[2],
        cross_entropy=cross_entropy(labels, probs),
        ece=expected_calibration_error(labels, probs),
    )
