"""Sliding-window rate limits (spec 11, "Sicurezza"): the agent per person, and on the public
read-only demo the writes it keeps (the agent and the checkout evaluation) per client."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Final

AGENT_RATE_LIMIT: Final = 10  # questions per person per window (a design hypothesis)
AGENT_RATE_WINDOW_S: Final = 60.0
PUBLIC_RATE_LIMIT: Final = 30  # public writes per client per window (a design hypothesis)
PUBLIC_RATE_WINDOW_S: Final = 60.0


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

    def retry_after(self, key: str) -> float | None:
        """None when the call is allowed (and counted), else the seconds until it would be."""
        now = self._clock()
        with self._lock:
            calls = self._calls.setdefault(key, deque())
            while calls and calls[0] <= now - self.window_s:
                calls.popleft()
            if len(calls) >= self.limit:
                return max(0.0, calls[0] + self.window_s - now)
            calls.append(now)
            return None
