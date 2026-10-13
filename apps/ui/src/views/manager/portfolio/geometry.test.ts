// Geometry of the risk × value matrix (docs/ui-design.md 8.1): the value domain clamped to the 1st
// and 99th percentiles, scales, hulls for linked accounts, and the nearest point under the pointer.
import { describe, expect, it } from 'vitest';
import type { PortfolioMatrix } from '@/api/models';
import { readExample } from '@/test/examples';
import { convexHull, createScales, nearestIndex, percentile, placeLabels, valueDomain } from './geometry';

const matrix = readExample<PortfolioMatrix>('portfolio_matrix.json');

describe('percentile', () => {
  it('interpolates on sorted values', () => {
    expect(percentile([1, 2, 3, 4, 5], 0)).toBe(1);
    expect(percentile([1, 2, 3, 4, 5], 1)).toBe(5);
    expect(percentile([1, 2, 3, 4, 5], 0.5)).toBe(3);
    expect(percentile([0, 10], 0.25)).toBe(2.5);
    expect(percentile([], 0.5)).toBe(0);
  });
});

describe('valueDomain', () => {
  it('clamps to the 1st and 99th percentiles, keeps the threshold and zero inside, and counts what is outside', () => {
    const scored = matrix.points.filter((p) => p.risk !== null);
    const domain = valueDomain(scored.map((p) => p.value_eur), matrix.thresholds.value_eur.value);
    expect(domain.min).toBeLessThanOrEqual(0);
    expect(domain.max).toBeGreaterThan(matrix.thresholds.value_eur.value);
    const outside = scored.filter((p) => p.value_eur < domain.min || p.value_eur > domain.max).length;
    expect(domain.outside).toBe(outside);
    expect(outside).toBeGreaterThan(0);
    expect(outside).toBeLessThan(scored.length * 0.05);
  });
});

describe('createScales', () => {
  it('maps risk 0..1 left to right and value bottom to top', () => {
    const s = createScales({ width: 600, height: 400 }, { min: 0, max: 200, outside: 0 });
    expect(s.x(0)).toBe(s.plot.left);
    expect(s.x(1)).toBe(s.plot.left + s.plot.width);
    expect(s.y(0)).toBe(s.plot.top + s.plot.height);
    expect(s.y(200)).toBe(s.plot.top);
  });
});

describe('convexHull', () => {
  it('returns the outer points in order', () => {
    const hull = convexHull([
      [0, 0],
      [2, 0],
      [1, 1],
      [2, 2],
      [0, 2],
    ]);
    expect(hull).toHaveLength(4);
    expect(hull).not.toContainEqual([1, 1]);
  });

  it('copes with one or two points', () => {
    expect(convexHull([[1, 1]])).toEqual([[1, 1]]);
    expect(convexHull([
      [1, 1],
      [3, 3],
    ])).toHaveLength(2);
  });
});

describe('placeLabels', () => {
  const plot = { left: 0, top: 0, width: 600, height: 400 };
  it('keeps two labels of nearby points apart', () => {
    const [a, b] = placeLabels(
      [
        { x: 200, y: 200, text: 'CL-000233 · 3 linked accounts' },
        { x: 230, y: 205, text: 'CL-000588 · 3 linked accounts' },
      ],
      plot,
    );
    const sameLine = Math.abs((a?.y ?? 0) - (b?.y ?? 0)) < 14;
    expect(sameLine && a?.anchor === b?.anchor).toBe(false);
  });

  it('keeps a label inside the plot near the right edge', () => {
    const [label] = placeLabels([{ x: 590, y: 200, text: 'CL-000417 · 7 linked accounts' }], plot);
    expect(label?.anchor).toBe('end');
  });
});

describe('nearestIndex', () => {
  it('finds the closest point within the radius, or none', () => {
    const xs = [10, 50, 90];
    const ys = [10, 50, 90];
    expect(nearestIndex(xs, ys, 52, 47, 8)).toBe(1);
    expect(nearestIndex(xs, ys, 30, 30, 8)).toBe(-1);
  });
});
