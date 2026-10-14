"""The API's clock: every timestamp it writes comes from one Clock, so tests and the demo reset
(US-903) are deterministic (ADR-15 section 15, ADR-16 section 10).

- DemoClock (DEMO_CLOCK=on, the default): 'now' is the instant of the serving snapshot plus the
  time elapsed since the demo reset started the clock. The reset decides every open return at
  the snapshot instant; with this clock the acts that follow happen minutes after it, so refund
  deadlines, review dates and the KPI week stay coherent however many days after the reset the
  demo runs. Before any reset the clock starts at the snapshot instant when the service starts;
  without a snapshot it is the wall clock. A frozen DemoClock (the public read-only demo and
  the cached demo, Settings.frozen_clock; contract 1.6.0) stands at the snapshot instant: no
  refund deadline of the reset's proposals passes however long the demo stays up, and the acts
  of a cached demo with writes on are dated at that instant too.
- SystemClock (DEMO_CLOCK=off): the wall clock, UTC.
- FixedClock: a clock that stands still until moved (tests, the demo reset itself).

wall() is the wall clock of a clock, for what is measured in real time (amd-smi samples); a
fixed clock has no other time than its own.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

ClockKind = Literal["demo", "system", "fixed"]


def system_now() -> datetime:
    return datetime.now(UTC)


def _aware(at: datetime) -> datetime:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("the clock needs a timezone-aware instant")
    return at.astimezone(UTC)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    kind: ClockKind = "system"

    def now(self) -> datetime:
        return system_now()

    def wall(self) -> datetime:
        return system_now()


class FixedClock:
    """A clock that stands still until moved (tests, the demo reset)."""

    kind: ClockKind = "fixed"

    def __init__(self, at: datetime) -> None:
        self._at = _aware(at)

    def now(self) -> datetime:
        return self._at

    def wall(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = _aware(at)

    def advance(self, delta: timedelta) -> datetime:
        self._at += delta
        return self._at


class DemoClock:
    """The snapshot instant plus the wall time elapsed since the clock started (never before
    the snapshot instant); frozen, the snapshot instant itself. Unanchored, it is the wall
    clock."""

    kind: ClockKind = "demo"

    def __init__(
        self,
        anchor: datetime | None = None,
        started_at: datetime | None = None,
        wall: Callable[[], datetime] = system_now,
        *,
        frozen: bool = False,
    ) -> None:
        self._wall = wall
        self.frozen = frozen
        self._lock = threading.Lock()
        self._state: tuple[datetime, datetime] | None = None
        if anchor is not None:
            self.anchor(anchor, started_at or wall())

    def anchor(self, snapshot_as_of: datetime, started_at: datetime) -> None:
        """Start the clock at `snapshot_as_of`, as of the wall time `started_at`."""
        with self._lock:
            self._state = (_aware(snapshot_as_of), _aware(started_at))

    @property
    def anchored(self) -> tuple[datetime, datetime] | None:
        """(snapshot instant, wall time the clock started), or None for the wall clock."""
        return self._state

    def now(self) -> datetime:
        state = self._state
        if state is None:
            return self._wall()
        anchor, started = state
        if self.frozen:
            return anchor
        return anchor + max(self._wall() - started, timedelta(0))

    def wall(self) -> datetime:
        return self._wall()


def wall_now(clock: Clock) -> datetime:
    """The wall clock of `clock`: its own time for a fixed clock."""
    wall = getattr(clock, "wall", None)
    return wall() if callable(wall) else clock.now()


def kind_of(clock: Clock) -> ClockKind:
    if isinstance(clock, DemoClock):
        return "demo" if clock.anchored is not None else "system"
    found = getattr(clock, "kind", "system")
    if found == "fixed":
        return "fixed"
    return "demo" if found == "demo" else "system"
