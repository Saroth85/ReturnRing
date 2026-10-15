// System page logic (US-901, US-902, US-905; docs/ui-design.md 9.4), on the contract examples.
import { describe, expect, it } from 'vitest';
import type { Benchmark, BenchmarksResponse, ReadyzResponse, TelemetryResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { barFraction, costPerDecision, resultCharts, speedups } from './benchmarks';
import { headerAgreement, readinessTone, serviceLine } from './readiness';
import { sparkline, telemetryNatures } from './telemetry';

const benchmarks = readExample<BenchmarksResponse>('benchmarks.json');
const byId = (id: string) => benchmarks.items.find((b) => b.id === id) as Benchmark;
const live = readExample<TelemetryResponse>('telemetry.live.json');
const cached = readExample<TelemetryResponse>('telemetry.cached.json');

/** B1 as if measured: real results with a measurement time. */
function measured(b: Benchmark): Benchmark {
  return {
    ...b,
    status: 'measured',
    measured_at: '2026-10-15T20:00:00Z',
    results: b.results.map((r) => ({
      ...r,
      value: { ...r.value, provenance: { ...r.value.provenance, origin: 'B1 run on the AMD Developer Cloud droplet', nature: 'real' } },
    })),
  };
}

describe('resultCharts (one small chart per phase and unit, US-902)', () => {
  it('pairs CPU and MI300X per metric for B1', () => {
    const charts = resultCharts(byId('B1').results);
    expect(charts.map((c) => [c.phase, c.unit, c.max])).toEqual([['training', 'seconds', 412]]);
    expect(charts[0]?.metrics.map((m) => [m.metric, m.rows.map((r) => r.device)])).toEqual([['train_time', ['cpu', 'mi300x']]]);
  });

  it('keeps generation latency, throughput and cost apart for B3 (no shared axis across units)', () => {
    const charts = resultCharts(byId('B3').results);
    expect(charts.map((c) => `${c.phase}/${c.unit}`)).toEqual(['generation/milliseconds', 'generation/tokens_per_second', 'cost/eur']);
    expect(charts[0]?.metrics.map((m) => m.metric)).toEqual(['latency_p50', 'latency_p95']);
    expect(charts[0]?.max).toBe(9100);
  });

  it('orders phases cold start, training, scoring, generation, pipeline, cost', () => {
    const base = byId('B1').results[0]!;
    const results = (['cost', 'generation', 'scoring', 'cold_start'] as const).map((phase) => ({ ...base, phase }));
    expect(resultCharts(results).map((c) => c.phase)).toEqual(['cold_start', 'scoring', 'generation', 'cost']);
  });

  it('has no chart for a pending benchmark', () => {
    expect(resultCharts(byId('B2').results)).toEqual([]);
  });
});

describe('barFraction', () => {
  it('scales to the largest value of the chart, never outside 0..1', () => {
    expect(barFraction(38.5, 412)).toBeCloseTo(0.0934, 3);
    expect(barFraction(412, 412)).toBe(1);
    expect(barFraction(5, 0)).toBe(0);
    expect(barFraction(-1, 10)).toBe(0);
  });
});

describe('speedups', () => {
  it('never appear on illustrative or pending benchmarks', () => {
    expect(speedups(byId('B1'))).toEqual([]);
    expect(speedups(byId('B2'))).toEqual([]);
    expect(speedups(byId('B3'))).toEqual([]);
  });

  it('appear when both devices are measured on the same metric, phase and protocol', () => {
    const [s] = speedups(measured(byId('B1')));
    expect(s?.metric).toBe('train_time');
    expect(s?.factor.value).toBeCloseTo(10.7, 1);
    expect(s?.factor.provenance.nature).toBe('real');
    expect(s?.factor.provenance.origin).toBe('Computed in this browser from the measured B1 results: Training time, CPU against MI300X');
  });

  it('refuse to compare different protocols', () => {
    const b = measured(byId('B1'));
    const results = b.results.map((r) => (r.device === 'cpu' ? { ...r, protocol: 'paper' as const } : r));
    expect(speedups({ ...b, results })).toEqual([]);
  });
});

describe('costPerDecision (US-902: idle time included)', () => {
  it('finds the cost result of B3 with its benchmark', () => {
    const cost = costPerDecision(benchmarks.items);
    expect(cost?.benchmark.id).toBe('B3');
    expect(cost?.result.value).toMatchObject({ value: 0.01, unit: 'eur' });
  });

  it('prefers a measured cost to an illustrative one', () => {
    const b3 = byId('B3');
    const measuredCost: Benchmark = { ...b3, id: 'B4', status: 'measured', results: b3.results.filter((r) => r.phase === 'cost') };
    expect(costPerDecision([b3, measuredCost])?.benchmark.id).toBe('B4');
  });

  it('is null when no benchmark has a cost', () => {
    expect(costPerDecision([byId('B1'), byId('B2')])).toBeNull();
  });
});

describe('telemetry', () => {
  it('sees the illustrative origin of the contract example (O-23)', () => {
    expect(telemetryNatures(live)).toEqual(['illustrative']);
    expect(telemetryNatures(cached)).toEqual([]);
  });

  it('draws a sparkline on a fixed domain', () => {
    const line = sparkline([0, 50, 100], { width: 100, height: 20, min: 0, max: 100 });
    expect(line.points).toBe('0,20 50,10 100,0');
    expect(line.last).toEqual({ x: 100, y: 0 });
    expect(sparkline([], { width: 100, height: 20, min: 0, max: 100 })).toEqual({ points: '', last: null });
    expect(sparkline([40], { width: 100, height: 20, min: 0, max: 100 }).points).toBe('100,12');
  });

  it('clamps values outside the domain', () => {
    expect(sparkline([-5, 120], { width: 10, height: 10, min: 0, max: 100 }).points).toBe('0,10 10,0');
  });
});

describe('readiness', () => {
  const ready = readExample<ReadyzResponse>('readyz_response.live.json');

  it('tones the checks: ok, disabled, unavailable', () => {
    expect(ready.checks.map((c) => readinessTone(c.status))).toEqual(['ok', 'ok', 'ok', 'ok', 'off', 'ok']);
    expect(readinessTone('unavailable')).toBe('fail');
  });

  it('compares the response header mode with /readyz', () => {
    expect(headerAgreement('live', 'live')).toBe('agrees');
    expect(headerAgreement('live', 'cached')).toBe('differs');
    expect(headerAgreement('cached', null)).toBe('unknown');
  });
});

describe('serviceLine: what answers now, from /readyz (ADR-15 sections 16, 17)', () => {
  const live = readExample<ReadyzResponse>('readyz_response.live.json');
  const cached = readExample<ReadyzResponse>('readyz_response.cached.json');

  it('a CPU run with the mock model never reads as the MI300X', () => {
    const line = serviceLine({ ...live, device: 'cpu', llm_mode: 'mock', label: null }, 'live');
    expect(line).toBe('This service: live on the CPU, language model: mock: hand-written stand-ins');
    expect(line).not.toContain('MI300X');
  });

  it('a cached demo names where its outputs were made only through readyz.label', () => {
    expect(serviceLine(cached, 'live')).toBe(`This service: cached demo, GPU off · ${cached.label}`);
    const unlabelled = serviceLine({ ...cached, label: null }, 'live');
    expect(unlabelled).toContain('no label');
    expect(unlabelled).not.toContain('MI300X');
  });

  it('live on the GPU with vLLM, and contract examples on the mock adapter', () => {
    expect(serviceLine(live, 'live')).toBe('This service: live on the GPU, language model: Gemma 4 on vLLM');
    expect(serviceLine(live, 'mock')).toBe('This UI: contract examples, no service behind it');
    expect(serviceLine(undefined, 'live')).toBeNull();
  });
});
