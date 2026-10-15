// The API's clock in the browser (ADR-15 sections 15, 19, 20): a running demo clock moves on from
// the moment /readyz answered; a frozen or fixed clock never does.
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readExample } from '@/test/examples';
import { clockNow, frozenAtSnapshot, isRunning, useDemoClock, utcDay } from './clock';
import type { ClockView, ReadyzResponse } from './models';
import { createQueryClient } from './queries';
import { setScenario } from './scenario';

const live = readExample<ReadyzResponse>('readyz_response.live.json').clock;
const frozen = readExample<ReadyzResponse>('readyz_response.cached.json').clock;
const fetchedAt = Date.parse('2026-10-18T08:00:00Z');

afterEach(() => {
  act(() => setScenario('default'));
  vi.useRealTimers();
});

describe('clockNow', () => {
  it('advances a running demo clock by the time since /readyz answered', () => {
    expect(live.frozen).toBe(false);
    expect(isRunning(live)).toBe(true);
    expect(clockNow(live, fetchedAt, fetchedAt + 90_000).toISOString()).toBe('2026-09-30T19:01:30.000Z');
    // Never backwards: a wall time before the answer leaves the clock where it was read.
    expect(clockNow(live, fetchedAt, fetchedAt - 5_000).toISOString()).toBe('2026-09-30T19:00:00.000Z');
  });

  it('never advances a frozen clock (the public read-only demo) or a fixed one', () => {
    expect(frozen.frozen).toBe(true);
    expect(frozen.now).toBe(frozen.snapshot_as_of);
    expect(isRunning(frozen)).toBe(false);
    expect(clockNow(frozen, fetchedAt, fetchedAt + 3_600_000).toISOString()).toBe('2026-09-30T18:00:00.000Z');
    const fixed: ClockView = { ...live, kind: 'fixed' };
    expect(isRunning(fixed)).toBe(false);
    expect(clockNow(fixed, fetchedAt, fetchedAt + 3_600_000).toISOString()).toBe('2026-09-30T19:00:00.000Z');
  });

  it('a frozen clock stands at its snapshot, or at the latest act recorded after it (1.8.1)', () => {
    expect(frozenAtSnapshot(frozen)).toBe(true);
    // The same instant written differently is the same instant.
    expect(frozenAtSnapshot({ ...frozen, now: '2026-09-30T18:00:00.000Z' })).toBe(true);
    const afterActs: ClockView = { ...frozen, now: '2026-09-30T19:12:00Z' };
    expect(frozenAtSnapshot(afterActs)).toBe(false);
    expect(isRunning(afterActs)).toBe(false);
    expect(clockNow(afterActs, fetchedAt, fetchedAt + 3_600_000).toISOString()).toBe('2026-09-30T19:12:00.000Z');
    expect(frozenAtSnapshot(live)).toBe(false); // a running clock is not frozen
  });

  it('dates a period that ends now by the UTC day', () => {
    expect(utcDay(new Date('2026-09-30T23:59:59Z'))).toBe('2026-09-30');
  });
});

describe('useDemoClock', () => {
  function wrapper({ children }: { children: ReactNode }) {
    const client = createQueryClient();
    client.setDefaultOptions({ queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } });
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }

  it('is empty until /readyz answers, then serves its clock', async () => {
    const { result } = renderHook(() => useDemoClock(), { wrapper });
    expect(result.current.now).toBeNull();
    await waitFor(() => expect(result.current.clock).not.toBeNull());
    expect(result.current).toMatchObject({ kind: 'demo', frozen: false, running: true, snapshotAsOf: live.snapshot_as_of });
    expect(result.current.now?.getTime()).toBeGreaterThanOrEqual(Date.parse(live.now));
    expect(result.current.today).toBe('2026-09-30');
  });

  it('stands still on the frozen clock of the cached read-only demo', async () => {
    act(() => setScenario('cached'));
    const { result } = renderHook(() => useDemoClock(5), { wrapper });
    await waitFor(() => expect(result.current.clock).not.toBeNull());
    expect(result.current).toMatchObject({ frozen: true, running: false });
    const first = result.current.now?.toISOString();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(result.current.now?.toISOString()).toBe(first);
    expect(first).toBe('2026-09-30T18:00:00.000Z');
  });
});
