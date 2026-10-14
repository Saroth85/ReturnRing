"""Sliding-window rate limits (spec 11, "Sicurezza"): the agent per person, and on the public
read-only demo the writes it keeps (the agent and the checkout evaluation) per client.

A key whose calls have all left the window is forgotten (swept at most once per window), so the
memory of a limiter is bounded by the clients of one window, never by every client ever seen."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Final

AGENT_RATE_LIMIT: Final = 10  # questions per person per window (a design hypothesis)
AGENT_RATE_WINDOW_S: Final = 60.0
PUBLIC_RATE_LIMIT: Final = 30  # public writes per client per window (a design hypothesis)
PUBLIC_RATE_WINDOW_S: Final = 60.0


def retry_seconds(wait: float) -> int:
    """The whole seconds of a Retry-After header for a wait: rounded up, at least 1."""
    return max(1, math.ceil(wait))


class RateLimiter:
    """At most `limit` calls per key in any `window_s` seconds (a sliding window)."""

    def __init__(
        self,
        limit: int = AGENT_RATE_LIMIT,
        window_s: float = AGENT_RATE_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._calls: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._next_sweep = clock() + window_s

    def __len__(self) -> int:
        """The keys the limiter remembers."""
        return len(self._calls)

    def retry_after(self, key: str) -> float | None:
        """None when the call is allowed (and counted), else the seconds until it would be."""
        now = self._clock()
        with self._lock:
            if now >= self._next_sweep:
                self._sweep(now)
            calls = self._calls.setdefault(key, deque())
            while calls and calls[0] <= now - self.window_s:
                calls.popleft()
            if len(calls) >= self.limit:
                return max(0.0, calls[0] + self.window_s - now)
            calls.append(now)
            return None

    def _sweep(self, now: float) -> None:
        """Delete every key whose calls are all out of the window (empty once pruned)."""
        idle = [
            k for k, calls in self._calls.items() if not calls or calls[-1] <= now - self.window_s
        ]
        for key in idle:
            del self._calls[key]
        self._next_sweep = now + self.window_s
