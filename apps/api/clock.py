"""The API's clock: every timestamp it writes comes from one Clock, so tests and the demo reset
(US-903) are deterministic. The service runs on the system clock (UTC)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock that stands still until moved (tests, the demo reset)."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("the clock needs a timezone-aware instant")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("the clock needs a timezone-aware instant")
        self._at = at.astimezone(UTC)

    def advance(self, delta: timedelta) -> datetime:
        self._at += delta
        return self._at
