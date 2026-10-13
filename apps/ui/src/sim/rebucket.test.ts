// The threshold sandbox (docs/ui-design.md 8.2, ECO-01-UI): re-bucketing the matrix points in the
// browser reproduces the quadrant totals of the contract (computed with policy.engine.quadrant_for).
import { describe, expect, it } from 'vitest';
import type { PortfolioMatrix } from '@/api/models';
import { readExample } from '@/test/examples';
import { bucketDelta, matrixThresholds, quadrantFor, rebucket, type MatrixPointLike } from './rebucket';

const matrix = readExample<PortfolioMatrix>('portfolio_matrix.json');

describe('quadrantFor (policy.engine.quadrant_for)', () => {
  const t = { risk: 0.6, valueEur: 185 };
  it.each([
    [0.12, 211.85, 'Q1'],
    [0.09, 68.05, 'Q2'],
    [0.7, 300, 'Q3'],
    [0.87, 42.1, 'Q4'],
    [0.6, 185, 'Q3'],
    [0.59, 185, 'Q1'],
    [0.6, 184.99, 'Q4'],
    [0.2, -19.78, 'Q2'],
  ] as const)('risk %s, value %s -> %s', (risk, value, quadrant) => {
    expect(quadrantFor(risk, value, t)).toBe(quadrant);
  });
});

describe('rebucket', () => {
  it('reproduces the quadrant totals and the not-placed count of the example', () => {
    const buckets = rebucket(matrix.points, matrixThresholds(matrix));
    for (const q of matrix.quadrants) {
      const b = buckets.quadrants[q.quadrant];
      expect(b.customers, q.quadrant).toBe(q.customers.value);
      expect(b.valueEur, q.quadrant).toBe(q.value_eur.value);
      expect(b.returns12m, q.quadrant).toBe(q.returns_12m.value);
    }
    expect(buckets.insufficient).toBe(matrix.insufficient_evidence.value);
  });

  it('agrees with the quadrant of every point at the engine thresholds', () => {
    const t = matrixThresholds(matrix);
    for (const p of matrix.points) {
      expect(p.risk === null ? null : quadrantFor(p.risk, p.value_eur, t), p.customer_id).toBe(p.quadrant);
    }
  });

  it('never places a point without a risk score (US-307)', () => {
    const points: MatrixPointLike[] = [
      { risk: null, value_eur: 500, returns_12m: 3 },
      { risk: 0.9, value_eur: 10, returns_12m: 1 },
    ];
    const buckets = rebucket(points, { risk: 0, valueEur: 0 });
    expect(buckets.insufficient).toBe(1);
    expect(buckets.quadrants.Q3.customers).toBe(1);
    expect(Object.values(buckets.quadrants).reduce((n, b) => n + b.customers, 0)).toBe(1);
  });

  it('moves customers when the thresholds move, and reports before -> after', () => {
    const engine = rebucket(matrix.points, matrixThresholds(matrix));
    const lower = rebucket(matrix.points, { ...matrixThresholds(matrix), risk: 0.5 });
    const delta = bucketDelta(engine, lower);
    expect(delta.Q4.after).toBeGreaterThanOrEqual(delta.Q4.before);
    expect(delta.Q4.change).toBe(delta.Q4.after - delta.Q4.before);
    const sum = (k: 'before' | 'after') => Object.values(delta).reduce((n, d) => n + d[k], 0);
    expect(sum('after')).toBe(sum('before'));
  });

  it('re-buckets twenty thousand points quickly (sandbox budget: one frame)', () => {
    const many: MatrixPointLike[] = Array.from({ length: 20_000 }, (_, i) => ({
      risk: (i % 100) / 100,
      value_eur: (i % 500) + 0.25,
      returns_12m: i % 7,
    }));
    rebucket(many, { risk: 0.6, valueEur: 185 });
    const start = performance.now();
    rebucket(many, { risk: 0.55, valueEur: 200 });
    // One frame is 16 ms; the bound is loose so a slow CI machine does not flake.
    expect(performance.now() - start).toBeLessThan(100);
  });
});
