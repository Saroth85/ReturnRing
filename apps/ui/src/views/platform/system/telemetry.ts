// Telemetry helpers (US-905, docs/ui-design.md 9.4): the natures of the numbers shown, and a
// sparkline on a fixed domain (deterministic visuals, no rescaling between polls).
import type { DisplayNature, Metric, TelemetryResponse } from '@/api/models';
import { displayNature } from '@/components/provenance';

/** Every figure of a telemetry answer (the current sample, the history, the LLM and API stats). */
export function telemetryMetrics(t: TelemetryResponse): Metric[] {
  const samples = [...(t.gpu ? [t.gpu] : []), ...t.gpu_history];
  const gpu = samples.flatMap((s) => [s.utilization, s.memory_used, s.memory_total, ...(s.power ? [s.power] : [])]);
  const llm = t.llm ? [t.llm.requests, t.llm.latency_p50, t.llm.latency_p95, t.llm.tokens_per_second] : [];
  return [...gpu, ...llm, ...(t.api_latency_p95 ? [t.api_latency_p95] : [])];
}

/** The distinct natures shown (the origin "Illustrative…" counts as illustrative, O-23). */
export function telemetryNatures(t: TelemetryResponse): DisplayNature[] {
  return [...new Set(telemetryMetrics(t).map((m) => displayNature(m.provenance)))];
}

export interface SparkDomain {
  width: number;
  height: number;
  min: number;
  max: number;
}

export interface Sparkline {
  /** SVG polyline points. */
  points: string;
  last: { x: number; y: number } | null;
}

const round = (n: number) => Math.round(n * 100) / 100;

export function sparkline(values: readonly number[], { width, height, min, max }: SparkDomain): Sparkline {
  if (values.length === 0) return { points: '', last: null };
  const span = max - min || 1;
  const coords = values.map((v, i) => {
    const clamped = Math.min(max, Math.max(min, v));
    const x = values.length === 1 ? width : (i * width) / (values.length - 1);
    const y = height - ((clamped - min) / span) * height;
    return { x: round(x), y: round(y) };
  });
  return { points: coords.map((c) => `${c.x},${c.y}`).join(' '), last: coords[coords.length - 1] ?? null };
}
