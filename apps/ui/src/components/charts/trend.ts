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
