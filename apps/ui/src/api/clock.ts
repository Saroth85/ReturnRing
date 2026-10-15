// The API's clock in the browser (ADR-15 sections 15, 19 and 21): /readyz `clock` says what 'now'
// is for the service. A demo clock is the snapshot instant plus the time since the reset; the
// client advances it from the moment /readyz answered, so a label can show the service's 'now'
// between two polls. A frozen clock (the public read-only demo: at the snapshot instant, or at
// the latest act recorded after it) and a fixed one (tests) stand still: the client never
// advances them. Every period that ends 'now' on screen uses this time, never the browser's date.
import { useEffect, useState } from 'react';
import type { ClockKind, ClockView } from './models';
import { useReadyz } from './queries';

/** How often a running clock is advanced on screen: labels show minutes at most. */
export const CLOCK_TICK_MS = 15_000;

/** Whether the client advances this clock: a demo or system clock that is not frozen. */
export function isRunning(clock: Pick<ClockView, 'kind' | 'frozen'>): boolean {
  return !clock.frozen && clock.kind !== 'fixed';
}

/**
 * Whether a frozen clock stands at its snapshot instant (the read-only demo of a fresh reset)
 * rather than at the latest act the store recorded after it (a store acted on with writes on,
 * then served read-only: contract 1.8.1, ADR-15 section 21).
 */
export function frozenAtSnapshot(clock: Pick<ClockView, 'frozen' | 'now' | 'snapshot_as_of'>): boolean {
  return clock.frozen && clock.snapshot_as_of !== null && Date.parse(clock.now) === Date.parse(clock.snapshot_as_of);
}

/**
 * The service's 'now' at the wall time `wallNow`, from a clock read at `fetchedAt` (both in
 * milliseconds since the epoch). A clock that does not run stays at its `now`; a running one moves
 * on by the time elapsed since it was read, never backwards.
 */
export function clockNow(clock: Pick<ClockView, 'kind' | 'frozen' | 'now'>, fetchedAt: number, wallNow: number): Date {
  const at = Date.parse(clock.now);
  if (!isRunning(clock) || !Number.isFinite(fetchedAt) || fetchedAt <= 0) return new Date(at);
  return new Date(at + Math.max(0, wallNow - fetchedAt));
}

/** The UTC day of an instant, YYYY-MM-DD: the end of a period that ends 'now'. */
export const utcDay = (instant: Date): string => instant.toISOString().slice(0, 10);

export interface DemoClock {
  /** The /readyz clock as served, null until /readyz answers. */
  clock: ClockView | null;
  kind: ClockKind | null;
  frozen: boolean;
  /** Whether the client advances it (a running demo or system clock). */
  running: boolean;
  /** The service's 'now', null until /readyz answers. */
  now: Date | null;
  /** The UTC day of `now`, YYYY-MM-DD. */
  today: string | null;
  snapshotAsOf: string | null;
  note: string | null;
}

/** The service's clock, advanced every `tickMs` while it runs; still while frozen or fixed. */
export function useDemoClock(tickMs: number = CLOCK_TICK_MS): DemoClock {
  const readyz = useReadyz();
  const clock = readyz.data?.clock ?? null;
  const fetchedAt = readyz.dataUpdatedAt;
  const running = clock !== null && isRunning(clock);
  const [wallNow, setWallNow] = useState(fetchedAt);

  useEffect(() => {
    if (!running) return undefined;
    const timer = setInterval(() => setWallNow(Date.now()), tickMs);
    return () => clearInterval(timer);
  }, [running, tickMs]);

  const now = clock ? clockNow(clock, fetchedAt, Math.max(wallNow, fetchedAt)) : null;
  return {
    clock,
    kind: clock?.kind ?? null,
    frozen: clock?.frozen ?? false,
    running,
    now,
    today: now ? utcDay(now) : null,
    snapshotAsOf: clock?.snapshot_as_of ?? null,
    note: clock?.note ?? null,
  };
}
