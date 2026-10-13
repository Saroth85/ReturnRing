"""Time helpers of the generator: a seasonal calendar for order times, weekdays and hours.

Instants are int64 microseconds since the epoch (UTC). The calendar weights are design hypotheses
for an Italian fashion e-shop (Black Friday, winter and summer sales, a quiet mid-August).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import numpy.typing as npt

from pipeline.generator.config import END_US, START_US, US_PER_DAY, US_PER_HOUR, US_PER_MINUTE

I64 = npt.NDArray[np.int64]
F64 = npt.NDArray[np.float64]

# (first day, day after the last, weight) over the period; other days weigh 1.
SEASONS: tuple[tuple[date, date, float], ...] = (
    (date(2025, 11, 24), date(2025, 12, 2), 2.2),  # Black Friday week
    (date(2025, 12, 2), date(2025, 12, 22), 1.35),  # Christmas shopping
    (date(2025, 12, 22), date(2026, 1, 3), 0.8),
    (date(2026, 1, 3), date(2026, 2, 1), 1.5),  # winter sales
    (date(2026, 7, 4), date(2026, 8, 1), 1.4),  # summer sales
    (date(2026, 8, 8), date(2026, 8, 24), 0.7),  # mid-August
)
WEEKDAY_WEIGHTS = (1.0, 0.95, 0.95, 1.0, 0.95, 0.95, 1.15)  # Monday .. Sunday
# Orders by hour of day (UTC, close to Italian time): lunch and evening peaks.
HOUR_WEIGHTS = (
    0.6, 0.3, 0.15, 0.1, 0.1, 0.15, 0.3, 0.6, 0.9, 1.0, 1.1, 1.3,
    1.6, 1.5, 1.2, 1.1, 1.1, 1.2, 1.4, 1.8, 2.2, 2.3, 1.9, 1.2,
)  # fmt: skip
_EPOCH_WEEKDAY = 3  # 1970-01-01 was a Thursday (Monday = 0)


def weekday(t: I64) -> I64:
    """Monday = 0 .. Sunday = 6 of each instant."""
    return np.asarray((np.floor_divide(t, US_PER_DAY) + _EPOCH_WEEKDAY) % 7, dtype=np.int64)


def midnight(t: I64) -> I64:
    return np.asarray(np.floor_divide(t, US_PER_DAY) * US_PER_DAY, dtype=np.int64)


def uniform_us(rng: np.random.Generator, low: I64 | int, high: I64 | int, n: int) -> I64:
    """Uniform instants in [low, high) (high > low expected; equal bounds give low)."""
    lo = np.broadcast_to(np.asarray(low, dtype=np.int64), (n,))
    hi = np.broadcast_to(np.asarray(high, dtype=np.int64), (n,))
    span = np.maximum(hi - lo, 0).astype(np.float64)
    return np.asarray(lo + np.floor(rng.random(n) * span).astype(np.int64), dtype=np.int64)


def minutes(rng: np.random.Generator, low: float, high: float, n: int) -> I64:
    """Durations uniform in [low, high) minutes."""
    return np.floor(rng.uniform(low, high, n) * US_PER_MINUTE).astype(np.int64)


def days(rng: np.random.Generator, low: float, high: float, n: int) -> I64:
    """Durations uniform in [low, high) days."""
    return np.floor(rng.uniform(low, high, n) * US_PER_DAY).astype(np.int64)


class Calendar:
    """Samples order instants in [low, high) with the seasonal, weekday and hour weights."""

    def __init__(self) -> None:
        n_days = (END_US - START_US) // US_PER_DAY
        first = START_US // US_PER_DAY
        weights = np.ones(n_days, dtype=np.float64)
        day_numbers = np.arange(first, first + n_days, dtype=np.int64)
        epoch = date(1970, 1, 1)
        for start, stop, weight in SEASONS:
            a = (start - epoch).days - first
            b = (stop - epoch).days - first
            weights[max(a, 0) : max(b, 0)] *= weight
        weights *= np.asarray(WEEKDAY_WEIGHTS)[(day_numbers + _EPOCH_WEEKDAY) % 7]
        self._grid = np.arange(n_days + 1, dtype=np.float64)
        self._cum = np.concatenate([[0.0], np.cumsum(weights)])
        hours = np.asarray(HOUR_WEIGHTS, dtype=np.float64)
        self._hour_cum = np.cumsum(hours) / hours.sum()

    def time_of_day(self, rng: np.random.Generator, n: int) -> I64:
        hour = np.searchsorted(self._hour_cum, rng.random(n), side="right")
        hour = np.minimum(hour, 23)
        return np.asarray(hour * US_PER_HOUR + uniform_us(rng, 0, US_PER_HOUR, n), dtype=np.int64)

    def sample(self, rng: np.random.Generator, low: I64, high: I64) -> I64:
        """One instant per (low, high) pair; the result is clipped to [low, high - 1 minute]."""
        n = len(low)
        a = (np.clip(low, START_US, END_US) - START_US) / US_PER_DAY
        b = (np.clip(high, START_US, END_US) - START_US) / US_PER_DAY
        ca, cb = np.interp(a, self._grid, self._cum), np.interp(b, self._grid, self._cum)
        u = ca + rng.random(n) * np.maximum(cb - ca, 0.0)
        day = np.floor(np.interp(u, self._cum, self._grid)).astype(np.int64)
        t = START_US + day * US_PER_DAY + self.time_of_day(rng, n)
        upper = np.maximum(high - US_PER_MINUTE, low)
        return np.asarray(np.clip(t, low, upper), dtype=np.int64)
