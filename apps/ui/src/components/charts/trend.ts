/** One week of a variant's sales and returns for one reason (WeeklyReturnRate of the contract). */
export interface TrendWeek {
  week: string;
  sales: number;
  returns: number;
  return_rate: number;
}

/** The first week that starts after the correction date (the correction lands inside a week). */
export function firstWeekAfter(trend: TrendWeek[], at: string | null | undefined): string | null {
  if (!at) return null;
  const day = at.slice(0, 10);
  return trend.find((w) => w.week > day)?.week ?? null;
}

/** Room one week label needs on the axis: "29 Sep" at 12 px is about 42 px wide, plus a gap. */
export const WEEK_LABEL_PX = 52;

/**
 * Which week labels the x axis draws (by index), so no two overlap however many weeks the series
 * has (the live store sends 53, the contract examples 8). Labels are centred on their week, so two
 * labels fit when their weeks are at least WEEK_LABEL_PX apart. The flagged week comes first, then
 * the last week, then a regular grid counted back from the last week; a label too close to one
 * already kept is dropped. The flagged week is therefore always labelled, the last week whenever
 * it does not collide with the flagged one.
 */
export function weekTickIndices(count: number, plotWidth: number, flaggedIndex: number | null = null): Set<number> {
  if (count <= 0) return new Set();
  const slot = plotWidth > 0 ? plotWidth / count : 0;
  const minGap = slot > 0 ? Math.max(1, Math.ceil(WEEK_LABEL_PX / slot)) : count;
  const last = count - 1;
  const order: number[] = [];
  if (flaggedIndex !== null && flaggedIndex >= 0 && flaggedIndex <= last) order.push(flaggedIndex);
  for (let i = last; i >= 0; i -= minGap) order.push(i);
  const kept: number[] = [];
  for (const i of order) {
    if (kept.every((k) => Math.abs(k - i) >= minGap)) kept.push(i);
  }
  return new Set(kept);
}
