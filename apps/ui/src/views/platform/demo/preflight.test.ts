import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api/errors';
import type { CaseCard, DemoCasesResponse, ReadyzResponse, SimulatorAssumptionsResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { buildBeats, currentBeat } from './beats';
import { componentReady, loadMetric, preflightChecks, preflightState, type PreflightInputs } from './preflight';

const live = readExample<ReadyzResponse>('readyz_response.live.json');
const cached = readExample<ReadyzResponse>('readyz_response.cached.json');
const cases = readExample<DemoCasesResponse>('demo_cases.json');
const marco = readExample<CaseCard>('case_card.marco.json');
const rossi = readExample<CaseCard>('case_card.rossi_card.json');
const assumptions = readExample<SimulatorAssumptionsResponse>('simulator_assumptions.v1.json');
const ok = <T,>(data: T) => ({ data, failed: false });
const fast = loadMetric(240, '2026-09-30', 'mock');
const noReset = new ApiError({ status: 404, code: 'not_found', message: 'no demo reset yet: run python -m apps.api.reset' });

const green: PreflightInputs = {
  expected: 'live',
  adapter: 'live',
  readyz: ok(live),
  cases: ok(cases),
  marco: ok(marco),
  rossi: ok(rossi),
  assumptions: ok(assumptions),
  load: ok(fast),
};

const stateOf = (inputs: PreflightInputs) => Object.fromEntries(preflightChecks(inputs).map((c) => [c.id, c.state]));
/** The live store's ids differ from the examples: the checks read them from the demo cases. */
const relabel = (data: DemoCasesResponse, key: string, decisionId: string): DemoCasesResponse => ({
  ...data,
  cases: data.cases.map((c) => (c.key === key ? { ...c, decision_id: decisionId } : c)),
});

describe('preflightChecks (docs/ui-design.md 9.5)', () => {
  it('is green on the contract examples for a live run', () => {
    const checks = preflightChecks(green);
    expect(checks.map((c) => c.id)).toEqual(['adapter', 'readiness', 'components', 'contract', 'cases', 'marco', 'rossi', 'assumptions', 'load']);
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

  it('is red before a demo reset, quoting the API, with the reset as the fix', () => {
    const checks = preflightChecks({ ...green, cases: { data: undefined, failed: true, error: noReset }, marco: { data: undefined, failed: false } });
    const byId = Object.fromEntries(checks.map((c) => [c.id, c]));
    expect(byId.cases).toMatchObject({ state: 'fail', found: 'no demo reset yet: run python -m apps.api.reset' });
    expect(byId.cases?.fix).toContain('python -m apps.api.reset');
    expect(byId.marco).toMatchObject({ state: 'fail', found: 'no demo cases' });
    expect(byId.rossi?.state).toBe('fail');
  });

  it('checks Marco and Rossi against the decisions the reset made, not fixed ids', () => {
    // A store whose Marco decision is another id: the example card no longer matches it.
    expect(stateOf({ ...green, cases: ok(relabel(cases, 'marco', 'DEC-0060430-1')) }).marco).toBe('fail');
    expect(stateOf({ ...green, cases: ok(relabel(cases, 'rossi_card', 'DEC-0060411-1')) }).rossi).toBe('fail');
    // The same ids on both sides pass, whatever they are.
    const liveMarco: CaseCard = { ...marco, decision: { ...marco.decision, decision_id: 'DEC-0060430-1' } };
    expect(stateOf({ ...green, cases: ok(relabel(cases, 'marco', 'DEC-0060430-1')), marco: ok(liveMarco) }).marco).toBe('pass');
  });

  it('names the fix when Marco was already approved', () => {
    const approved: CaseCard = { ...marco, decision: { ...marco.decision, state: 'approved' } };
    const check = preflightChecks({ ...green, marco: ok(approved) }).find((c) => c.id === 'marco');
    expect(check?.state).toBe('fail');
    expect(check?.fix).toContain('demo reset');
  });

  it('says which case the store does not hold', () => {
    const withoutMarco = { ...cases, cases: cases.cases.filter((c) => c.key !== 'marco') };
    expect(preflightChecks({ ...green, cases: ok(withoutMarco) }).find((c) => c.id === 'marco')).toMatchObject({
      state: 'fail',
      found: 'the store holds no Marco case',
    });
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

  it('accepts vLLM off on the mock language model and telemetry off on a CPU, nothing else off', () => {
    const cpu: ReadyzResponse = {
      ...live,
      llm_mode: 'mock',
      device: 'cpu',
      checks: live.checks.map((c) => (c.component === 'vllm' || c.component === 'telemetry' ? { ...c, status: 'disabled' as const } : c)),
    };
    expect(componentReady(cpu, 'vllm')).toBe(true);
    expect(componentReady(cpu, 'telemetry')).toBe(true);
    expect(stateOf({ ...green, readyz: ok(cpu) }).components).toBe('pass');
    const gpuOff: ReadyzResponse = { ...cpu, llm_mode: 'vllm', device: 'gpu' };
    expect(stateOf({ ...green, readyz: ok(gpuOff) }).components).toBe('fail');
    const broken: ReadyzResponse = { ...cpu, checks: cpu.checks.map((c) => (c.component === 'vllm' ? { ...c, status: 'unavailable' as const } : c)) };
    expect(componentReady(broken, 'vllm')).toBe(false);
  });

  it('is red on a slow load and on an API that does not answer, pending while waiting', () => {
    const slow = loadMetric(2400, '2026-09-30', 'live');
    expect(stateOf({ ...green, load: ok(slow) }).load).toBe('fail');
    const down = preflightChecks({ ...green, readyz: { data: undefined, failed: true } });
    expect(down.find((c) => c.id === 'readiness')).toMatchObject({ state: 'fail', found: 'no answer' });
    const waiting = preflightChecks({ ...green, load: { data: undefined, failed: false } });
    expect(preflightState(waiting)).toBe('pending');
  });

  it('measures the load as a real figure with its origin and the service day', () => {
    expect(fast).toEqual({
      value: 240,
      unit: 'milliseconds',
      provenance: {
        origin: 'Measured in this browser: the golden case cards and the matrix from the mock adapter on the contract examples',
        period_start: '2026-09-30',
        period_end: '2026-09-30',
        nature: 'real',
      },
    });
  });
});

describe('buildBeats (spec 16, ADR-15 sections 15 and 18)', () => {
  const beats = buildBeats(cases);
  const byId = Object.fromEntries(beats.map((b) => [b.id, b]));
  const id = (key: string) => cases.cases.find((c) => c.key === key)?.return_id ?? '';

  it('opens the six beats of the main path on the cases of the reset, then the Q&A beats', () => {
    expect(beats.map((b) => b.id)).toEqual([
      'same_indicator',
      'marco',
      'rossi',
      'elena',
      'net_benefit',
      'amd',
      'shop_ring',
      'shop_giulia',
      'dispute',
      'insufficient',
    ]);
    expect(beats.filter((b) => !b.offPath)).toHaveLength(6);
    expect(byId.same_indicator?.to).toBe(`/compare?a=${id('giulia')}&b=${id('marco')}`);
    expect(byId.marco?.to).toBe(`/returns/${id('marco')}`);
    expect(byId.rossi?.to).toBe(`/returns/${id('rossi_card')}`);
    expect(byId.elena?.to).toBe(`/returns/${id('elena')}`);
    expect(byId.insufficient?.to).toBe(`/returns/${id('insufficient')}`);
    expect(byId.net_benefit).toMatchObject({ to: '/simulator', persona: 'luca' });
  });

  it('plays the ring checkout after Marco is approved', () => {
    const ring = beats.findIndex((b) => b.id === 'shop_ring');
    expect(ring).toBeGreaterThan(beats.findIndex((b) => b.id === 'marco'));
    expect(byId.shop_ring).toMatchObject({ to: '/shop?preset=ring', after: 'marco', offPath: true });
  });

  it('links the dispute with its link selected, and says when the decision stays unchanged', () => {
    const dispute = cases.dispute!;
    expect(byId.dispute?.to).toBe(`/returns/${dispute.return_id}?link=${encodeURIComponent(dispute.link_id)}`);
    expect(byId.dispute?.say).toContain('decided again');
    const unchanged = buildBeats({ ...cases, dispute: { ...dispute, changes_decision: false } }).find((b) => b.id === 'dispute');
    expect(unchanged?.say).toContain('the decision stays as it was');
    expect(unchanged?.dispute?.changes_decision).toBe(false);
  });

  it('leaves out the beats whose case the store does not hold, and keeps two before a reset', () => {
    const noMarco = buildBeats({ ...cases, cases: cases.cases.filter((c) => c.key !== 'marco') }).map((b) => b.id);
    expect(noMarco).not.toContain('marco');
    expect(noMarco).not.toContain('same_indicator');
    expect(buildBeats({ ...cases, dispute: null }).map((b) => b.id)).not.toContain('dispute');
    expect(buildBeats(undefined).map((b) => b.id)).toEqual(['net_benefit', 'amd']);
    expect(buildBeats(null).map((b) => b.to)).toEqual(['/simulator', '/system']);
  });

  it('falls back to the Rossi family when the store holds no variant with a shared card', () => {
    const noCard = buildBeats({ ...cases, cases: cases.cases.filter((c) => c.key !== 'rossi_card') });
    expect(noCard.find((b) => b.id === 'rossi')?.to).toBe(`/returns/${id('rossi')}`);
  });
});

describe('currentBeat', () => {
  const beats = buildBeats(cases);
  const index = (beatId: string) => beats.findIndex((b) => b.id === beatId);
  const marcoPath = `/returns/${cases.cases.find((c) => c.key === 'marco')?.return_id}`;
  const compare = beats[index('same_indicator')]?.to.split('?')[1] ?? '';

  it('finds the beat on screen, a case beat also on its sub-pages', () => {
    expect(currentBeat(beats, '/compare', `?${compare}`)).toBe(index('same_indicator'));
    expect(currentBeat(beats, marcoPath, '')).toBe(index('marco'));
    expect(currentBeat(beats, `${marcoPath}/versions/DEC-1`, '')).toBe(index('marco'));
    expect(currentBeat(beats, '/system', '')).toBe(index('amd'));
    expect(currentBeat(beats, '/queue', '')).toBe(-1);
    expect(currentBeat(beats, '/compare', `?${compare}&present=1`)).toBe(index('same_indicator'));
    expect(currentBeat(beats, '/compare', '?a=RET-1&b=RET-2')).toBe(-1);
    expect(currentBeat(beats, `${marcoPath}0`, '')).toBe(-1);
  });

  it('treats ?preset=marco as the ring checkout and prefers the dispute beat on its own link', () => {
    expect(currentBeat(beats, '/shop', '?preset=marco')).toBe(index('shop_ring'));
    expect(currentBeat(beats, '/shop', '?preset=ring&present=1')).toBe(index('shop_ring'));
    expect(currentBeat(beats, '/shop', '?preset=giulia')).toBe(index('shop_giulia'));
    const dispute = cases.dispute!;
    // A dispute on Marco's own case: the link names the dispute beat, the bare case the Marco beat.
    const onMarco = buildBeats({ ...cases, dispute: { ...dispute, return_id: cases.cases.find((c) => c.key === 'marco')!.return_id } });
    const link = `?link=${encodeURIComponent(dispute.link_id)}`;
    expect(onMarco[currentBeat(onMarco, marcoPath, link)]?.id).toBe('dispute');
    expect(onMarco[currentBeat(onMarco, marcoPath, '')]?.id).toBe('marco');
  });
});
