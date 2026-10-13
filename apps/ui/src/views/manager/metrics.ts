// Small helpers over contract Metrics for the manager screens.
import type { Metric, Provenance } from '@/api/models';

const sameProvenance = (a: Provenance, b: Provenance) =>
  a.origin === b.origin && a.period_start === b.period_start && a.period_end === b.period_end && a.nature === b.nature;

/**
 * The sum of figures that share unit and provenance (the scored customers of the four quadrants),
 * carrying that provenance; null when they differ, so a sum never borrows an origin it does not have.
 */
export function sumMetrics(metrics: readonly Metric[]): Metric | null {
  const first = metrics[0];
  if (!first) return null;
  if (!metrics.every((m) => m.unit === first.unit && sameProvenance(m.provenance, first.provenance))) return null;
  const cents = first.unit === 'eur';
  const total = metrics.reduce((sum, m) => sum + (cents ? Math.round(m.value * 100) : m.value), 0);
  return { value: cents ? total / 100 : total, unit: first.unit, provenance: first.provenance };
}
