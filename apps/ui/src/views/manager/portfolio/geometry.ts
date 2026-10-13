// Geometry of the risk × value matrix (docs/ui-design.md 8.1): x is the risk score from 0 to 1,
// y the customer value in euros, linear, clamped to the 1st and 99th percentiles.

export type Pt = [number, number];

/** Linear interpolation at p (0..1) of sorted values. */
export function percentile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return 0;
  const at = (sorted.length - 1) * p;
  const lo = Math.floor(at);
  const hi = Math.ceil(at);
  const a = sorted[lo] ?? 0;
  const b = sorted[hi] ?? a;
  return a + (b - a) * (at - lo);
}

export interface ValueDomain {
  min: number;
  max: number;
  /** Customers whose value is outside the shown range (not drawn). */
  outside: number;
}

const LOW_PERCENTILE = 0.01;
const HIGH_PERCENTILE = 0.99;
const PAD = 0.05;

/** The 1st to 99th percentile of the values, widened to keep zero and the value threshold in view. */
export function valueDomain(values: readonly number[], threshold: number): ValueDomain {
  const sorted = [...values].sort((a, b) => a - b);
  const lo = Math.min(percentile(sorted, LOW_PERCENTILE), 0, threshold);
  const hi = Math.max(percentile(sorted, HIGH_PERCENTILE), threshold);
  const pad = (hi - lo) * PAD;
  const min = lo - pad;
  const max = hi + pad;
  const outside = sorted.filter((v) => v < min || v > max).length;
  return { min, max, outside };
}

export interface Plot {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Scales {
  plot: Plot;
  x(risk: number): number;
  y(value: number): number;
}

export const MARGIN = { left: 64, right: 12, top: 12, bottom: 40 };

export function createScales(size: { width: number; height: number }, domain: ValueDomain): Scales {
  const plot: Plot = {
    left: MARGIN.left,
    top: MARGIN.top,
    width: Math.max(1, size.width - MARGIN.left - MARGIN.right),
    height: Math.max(1, size.height - MARGIN.top - MARGIN.bottom),
  };
  const span = domain.max - domain.min || 1;
  return {
    plot,
    x: (risk) => plot.left + risk * plot.width,
    y: (value) => plot.top + plot.height - ((value - domain.min) / span) * plot.height,
  };
}

/** "Nice" tick values between min and max (about `count` of them). */
export function ticks(min: number, max: number, count = 5): number[] {
  const span = max - min;
  if (span <= 0) return [min];
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => span / s <= count) ?? power * 10;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

/** Andrew's monotone chain; one or two points come back as they are. */
export function convexHull(points: readonly Pt[]): Pt[] {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (sorted.length <= 2) return sorted;
  const lower: Pt[] = [];
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2]!, lower[lower.length - 1]!, p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: Pt[] = [];
  for (const p of [...sorted].reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2]!, upper[upper.length - 1]!, p) <= 0) upper.pop();
    upper.push(p);
  }
  return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

/** Index of the closest point within `radius` px, or -1. A linear scan: 20,000 points take < 1 ms. */
export function nearestIndex(xs: readonly number[], ys: readonly number[], x: number, y: number, radius: number): number {
  let best = -1;
  let bestD = radius * radius;
  for (let i = 0; i < xs.length; i += 1) {
    const dx = (xs[i] ?? 0) - x;
    const dy = (ys[i] ?? 0) - y;
    const d = dx * dx + dy * dy;
    if (d <= bestD) {
      bestD = d;
      best = i;
    }
  }
  return best;
}

export interface LabelRequest {
  /** The point the label belongs to. */
  x: number;
  y: number;
  text: string;
}

export interface PlacedLabel {
  x: number;
  y: number;
  anchor: 'start' | 'end';
}

interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const CHAR_WIDTH = 6.8;
const LINE_HEIGHT = 14;
const OFFSETS: [number, number][] = [
  [10, -10],
  [-10, -10],
  [10, 20],
  [-10, 20],
  [10, -26],
  [-10, -26],
  [10, 36],
  [-10, 36],
  [10, -42],
  [-10, -42],
];

const overlaps = (a: Box, b: Box) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

/**
 * Places annotation labels next to their points without overlapping each other and inside the
 * plot: the first free spot among a few offsets around the point (12 px text, estimated width).
 */
export function placeLabels(requests: readonly LabelRequest[], plot: Plot): PlacedLabel[] {
  const placed: Box[] = [];
  return requests.map((r) => {
    const width = r.text.length * CHAR_WIDTH + 4;
    const candidates = OFFSETS.map(([dx, dy]) => {
      const anchor: PlacedLabel['anchor'] = dx < 0 ? 'end' : 'start';
      const x = r.x + dx;
      const y = r.y + dy;
      const box: Box =
        anchor === 'start'
          ? { x0: x, y0: y - LINE_HEIGHT + 3, x1: x + width, y1: y + 3 }
          : { x0: x - width, y0: y - LINE_HEIGHT + 3, x1: x, y1: y + 3 };
      const inside = box.x0 >= plot.left && box.x1 <= plot.left + plot.width && box.y0 >= plot.top && box.y1 <= plot.top + plot.height;
      return { label: { x, y, anchor }, box, inside };
    });
    const free =
      candidates.find((c) => c.inside && !placed.some((b) => overlaps(b, c.box))) ?? candidates.find((c) => c.inside) ?? candidates[0]!;
    placed.push(free.box);
    return free.label;
  });
}
