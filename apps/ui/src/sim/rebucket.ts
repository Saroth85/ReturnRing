// Quadrant counts for given thresholds (docs/ui-design.md 8.2 threshold sandbox, ADR-04): the
// rule of policy.engine.quadrant_for on the matrix points, in the browser. A preview only: the
// thresholds here never change the engine, and they do not feed the net benefit (open question O-6).
import type { PortfolioMatrix, QuadrantId } from '@/api/models';

export interface SandboxThresholds {
  risk: number;
  valueEur: number;
}

/** The fields of a MatrixPoint the re-bucketing reads. */
export interface MatrixPointLike {
  risk: number | null;
  value_eur: number;
  returns_12m: number;
}

export interface QuadrantBucket {
  customers: number;
  /** Total customer value, summed in cents so it matches the server's Decimal sum. */
  valueEur: number;
  returns12m: number;
}

export interface Buckets {
  quadrants: Record<QuadrantId, QuadrantBucket>;
  /** Not enough evidence: no risk score, never placed in a quadrant (US-307). */
  insufficient: number;
}

export const QUADRANT_ORDER: readonly QuadrantId[] = ['Q1', 'Q2', 'Q3', 'Q4'];

/** policy.engine.quadrant_for: at or above a threshold is "high". */
export function quadrantFor(risk: number, valueEur: number, t: SandboxThresholds): QuadrantId {
  const highRisk = risk >= t.risk;
  const highValue = valueEur >= t.valueEur;
  if (highValue) return highRisk ? 'Q3' : 'Q1';
  return highRisk ? 'Q4' : 'Q2';
}

/** The engine thresholds of a matrix response. */
export function matrixThresholds(matrix: Pick<PortfolioMatrix, 'thresholds'>): SandboxThresholds {
  return { risk: matrix.thresholds.risk.value, valueEur: matrix.thresholds.value_eur.value };
}

export function rebucket(points: readonly MatrixPointLike[], t: SandboxThresholds): Buckets {
  const cents: Record<QuadrantId, number> = { Q1: 0, Q2: 0, Q3: 0, Q4: 0 };
  const quadrants: Record<QuadrantId, QuadrantBucket> = {
    Q1: { customers: 0, valueEur: 0, returns12m: 0 },
    Q2: { customers: 0, valueEur: 0, returns12m: 0 },
    Q3: { customers: 0, valueEur: 0, returns12m: 0 },
    Q4: { customers: 0, valueEur: 0, returns12m: 0 },
  };
  let insufficient = 0;
  for (const p of points) {
    if (p.risk === null) {
      insufficient += 1;
      continue;
    }
    const q = quadrantFor(p.risk, p.value_eur, t);
    const bucket = quadrants[q];
    bucket.customers += 1;
    bucket.returns12m += p.returns_12m;
    cents[q] += Math.round(p.value_eur * 100);
  }
  for (const q of QUADRANT_ORDER) quadrants[q].valueEur = cents[q] / 100;
  return { quadrants, insufficient };
}

export interface BucketChange {
  before: number;
  after: number;
  change: number;
}

/** Customers per quadrant, before -> after. */
export function bucketDelta(before: Buckets, after: Buckets): Record<QuadrantId, BucketChange> {
  const out = {} as Record<QuadrantId, BucketChange>;
  for (const q of QUADRANT_ORDER) {
    const b = before.quadrants[q].customers;
    const a = after.quadrants[q].customers;
    out[q] = { before: b, after: a, change: a - b };
  }
  return out;
}
