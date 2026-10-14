"""Percentiles and summaries of timings (standard library only).

Percentiles use linear interpolation between closest ranks (numpy's default "linear" method), so
p50 of an even count is the mean of the two middle values; with fewer than 20 values a p95 is an
interpolation near the maximum, and the summary says how many values it is made of.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def percentile(values: Sequence[float], q: float) -> float:
    """The q-th percentile (0..100) of non-empty, finite values."""
    if not values:
        raise ValueError("no values")
    if not 0 <= q <= 100:
        raise ValueError(f"percentile {q} outside 0..100")
    ordered = sorted(float(v) for v in values)
    if any(not math.isfinite(v) for v in ordered):
        raise ValueError("values must be finite")
    position = (len(ordered) - 1) * q / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def median(values: Sequence[float]) -> float:
    return percentile(values, 50)


def summary(values: Sequence[float], digits: int = 3) -> dict[str, Any]:
    """n, min, p50, p95, max and mean, rounded to `digits` decimals."""
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), digits),
        "p50": round(percentile(values, 50), digits),
        "p95": round(percentile(values, 95), digits),
        "max": round(max(values), digits),
        "mean": round(sum(values) / len(values), digits),
    }
