import { describe, expect, it } from 'vitest';
import type { CaseCard, ReadyzResponse, SimulatorAssumptionsResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { currentBeat } from './beats';
import { loadMetric, preflightChecks, preflightState, type PreflightInputs } from './preflight';

const live = readExample<ReadyzResponse>('readyz_response.live.json');
const cached = readExample<ReadyzResponse>('readyz_response.cached.json');
const marco = readExample<CaseCard>('case_card.marco.json');
const rossi = readExample<CaseCard>('case_card.rossi_card.json');
const assumptions = readExample<SimulatorAssumptionsResponse>('simulator_assumptions.v1.json');
const ok = <T,>(data: T) => ({ data, failed: false });
const fast = loadMetric(240, '2026-10-13T20:00:00Z', 'mock');

const green: PreflightInputs = {
  expected: 'live',
  adapter: 'live',
  readyz: ok(live),
  marco: ok(marco),
  rossi: ok(rossi),
  assumptions: ok(assumptions),
  load: ok(fast),
};

const stateOf = (inputs: PreflightInputs) => Object.fromEntries(preflightChecks(inputs).map((c) => [c.id, c.state]));

describe('preflightChecks (docs/ui-design.md 9.5)', () => {
  it('is green on the contract examples for a live run', () => {
    const checks = preflightChecks(green);
    expect(checks.map((c) => c.id)).toEqual(['adapter', 'readiness', 'components', 'contract', 'marco', 'rossi', 'assumptions', 'load']);
    expect(checks.every((c) => c.state === 'pass' && c.fix === null)).toBe(true);
    expect(preflightState(checks)).toBe('pass');
  });

  it('is never green on the mock adapter: the answers are contract examples (UI12, UI29)', () => {
    const checks = preflightChecks({ ...green, adapter: 'mock' });
    expect(checks[0]).toMatchObject({ id: 'adapter', state: 'fail' });
    expect(checks[0]?.fix).toContain('npm run dev:live');
    expect(preflightState(checks)).toBe('fail');
  });

  it('fails when the API serves another contract version (UI1)', () => {
    const check = preflightChecks({ ...green, readyz: ok({ ...live, contract_version: '1.4.0' }) }).find((c) => c.id === 'contract');
    expect(check?.state).toBe('fail');
    expect(check?.found).toContain('1.4.0');
    expect(check?.fix).toContain('gen:types');
  });

  it('names the fix when Marco was already approved', () => {
    const approved: CaseCard = { ...marco, decision: { ...marco.decision, state: 'approved' } };
    const check = preflightChecks({ ...green, marco: ok(approved) }).find((c) => c.id === 'marco');
    expect(check?.state).toBe('fail');
    expect(check?.fix).toContain('demo reset');
  });

  it('fails when Rossi was recomputed or the assumptions moved on', () => {
    const recomputed: CaseCard = { ...rossi, decision: { ...rossi.decision, decision_id: 'DEC-000104-2' } };
    const v2: SimulatorAssumptionsResponse = { ...assumptions, current: { ...assumptions.current, version: 'assumptions-v2' } };
    expect(stateOf({ ...green, rossi: ok(recomputed), assumptions: ok(v2) })).toMatchObject({ rossi: 'fail', assumptions: 'fail' });
  });

  it('checks the mode it was told to expect, with the components of that mode', () => {
    expect(stateOf({ ...green, readyz: ok(cached) })).toMatchObject({ readiness: 'fail' });
    const checks = preflightChecks({ ...green, expected: 'cached', readyz: ok(cached) });
    expect(checks.find((c) => c.id === 'readiness')?.state).toBe('pass');
    expect(checks.find((c) => c.id === 'components')?.found).toContain('demo_cache');
  });

  it('is red on a slow load and on an API that does not answer, pending while waiting', () => {
    const slow = loadMetric(2400, '2026-10-13T20:00:00Z', 'live');
    expect(stateOf({ ...green, load: ok(slow) }).load).toBe('fail');
    const down = preflightChecks({ ...green, readyz: { data: undefined, failed: true } });
    expect(down.find((c) => c.id === 'readiness')).toMatchObject({ state: 'fail', found: 'no answer' });
    const waiting = preflightChecks({ ...green, load: { data: undefined, failed: false } });
    expect(preflightState(waiting)).toBe('pending');
  });

  it('measures the load as a real figure with its origin and day', () => {
    expect(fast).toEqual({
      value: 240,
      unit: 'milliseconds',
      provenance: {
        origin: 'Measured in this browser: the five golden case cards and the matrix from the mock adapter on the contract examples',
        period_start: '2026-10-13',
        period_end: '2026-10-13',
        nature: 'real',
      },
    });
  });
});

describe('currentBeat', () => {
  it('finds the beat on screen, a case beat also on its sub-pages', () => {
    expect(currentBeat('/compare', '?a=RET-000101&b=RET-000102')).toBe(0);
    expect(currentBeat('/returns/RET-000102', '')).toBe(1);
    expect(currentBeat('/returns/RET-000102/versions/DEC-000102-1', '')).toBe(1);
    expect(currentBeat('/system', '')).toBe(5);
    expect(currentBeat('/queue', '')).toBe(-1);
    expect(currentBeat('/compare', '?a=RET-000101&b=RET-000102&present=1')).toBe(0);
    expect(currentBeat('/compare', '?a=RET-000106&b=RET-000102')).toBe(-1);
    expect(currentBeat('/returns/RET-0001020', '')).toBe(-1);
  });
});
