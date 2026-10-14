// Benchmark logic (US-902, spec 12 "Benchmark AMD", docs/ui-design.md 9.4). The rules that make
// a benchmark defensible shape the charts: one small chart per phase and unit (cold start,
// training, scoring and generation kept apart, never a shared axis across units), CPU and MI300X
// paired per metric, and a speedup only when both sides are measured on the same metric, phase and
// protocol. The cost per decision includes idle time (the API's label says so).
import type { Benchmark, Metric, Unit } from '@/api/models';

export type BenchmarkResult = Benchmark['results'][number];
export type Phase = BenchmarkResult['phase'];

const PHASE_ORDER: readonly Phase[] = ['cold_start', 'training', 'scoring', 'generation', 'pipeline', 'cost'];
const DEVICE_ORDER = ['cpu', 'mi300x'] as const;

export interface MetricBars {
  metric: string;
  label: string;
  /** CPU first, then MI300X. */
  rows: BenchmarkResult[];
}

export interface ResultChart {
  key: string;
  phase: Phase;
  unit: Unit;
  /** The largest value of the chart: bars scale to it. */
  max: number;
  metrics: MetricBars[];
}

export function resultCharts(results: readonly BenchmarkResult[]): ResultChart[] {
  const charts = new Map<string, ResultChart>();
  for (const result of results) {
    const key = `${result.phase}:${result.value.unit}`;
    let chart = charts.get(key);
    if (!chart) {
      chart = { key, phase: result.phase, unit: result.value.unit, max: 0, metrics: [] };
      charts.set(key, chart);
    }
    chart.max = Math.max(chart.max, result.value.value);
    let bars = chart.metrics.find((m) => m.metric === result.metric);
    if (!bars) {
      bars = { metric: result.metric, label: result.label, rows: [] };
      chart.metrics.push(bars);
    }
    bars.rows.push(result);
  }
  for (const chart of charts.values()) {
    for (const bars of chart.metrics) bars.rows.sort((a, b) => DEVICE_ORDER.indexOf(a.device) - DEVICE_ORDER.indexOf(b.device));
  }
  return [...charts.values()].sort((a, b) => PHASE_ORDER.indexOf(a.phase) - PHASE_ORDER.indexOf(b.phase));
}

/** The bar length as a share of the chart's largest value, in 0..1. */
export function barFraction(value: number, max: number): number {
  if (!(max > 0) || !(value > 0)) return 0;
  return Math.min(1, value / max);
}

/** Lower is better for times, higher for throughput; other units are never turned into a speedup. */
const LOWER_IS_BETTER: readonly Unit[] = ['seconds', 'milliseconds'];
const HIGHER_IS_BETTER: readonly Unit[] = ['tokens_per_second'];

export interface Speedup {
  metric: string;
  label: string;
  /** How many times faster on the MI300X, computed from the two measured results. */
  factor: Metric;
}

/** Speedups of a measured benchmark: both devices, same metric, phase, unit and protocol. */
export function speedups(benchmark: Benchmark): Speedup[] {
  if (benchmark.status !== 'measured') return [];
  const out: Speedup[] = [];
  for (const chart of resultCharts(benchmark.results)) {
    for (const bars of chart.metrics) {
      const cpu = bars.rows.filter((r) => r.device === 'cpu');
      const gpu = bars.rows.filter((r) => r.device === 'mi300x');
      if (cpu.length !== 1 || gpu.length !== 1) continue;
      const [a, b] = [cpu[0] as BenchmarkResult, gpu[0] as BenchmarkResult];
      if (a.protocol !== b.protocol || a.value.value <= 0 || b.value.value <= 0) continue;
      const unit = a.value.unit;
      let factor: number;
      if (LOWER_IS_BETTER.includes(unit)) factor = a.value.value / b.value.value;
      else if (HIGHER_IS_BETTER.includes(unit)) factor = b.value.value / a.value.value;
      else continue;
      const pa = a.value.provenance;
      const pb = b.value.provenance;
      out.push({
        metric: bars.metric,
        label: bars.label,
        factor: {
          value: Math.round(factor * 100) / 100,
          unit: 'score',
          provenance: {
            origin: `Computed in this browser from the measured ${benchmark.id} results: ${bars.label}, CPU against MI300X`,
            period_start: pa.period_start < pb.period_start ? pa.period_start : pb.period_start,
            period_end: pa.period_end > pb.period_end ? pa.period_end : pb.period_end,
            nature: pa.nature === pb.nature ? pa.nature : 'simulated',
          },
        },
      });
    }
  }
  return out;
}

export interface CostPerDecision {
  benchmark: Benchmark;
  result: BenchmarkResult;
}

/** The cost per decision (spec 12: idle included), from a measured benchmark when one has it. */
export function costPerDecision(items: readonly Benchmark[]): CostPerDecision | null {
  const found: CostPerDecision[] = [];
  for (const benchmark of items) {
    const result = benchmark.results.find((r) => r.phase === 'cost');
    if (result) found.push({ benchmark, result });
  }
  return found.find((c) => c.benchmark.status === 'measured') ?? found[0] ?? null;
}
