const DAY_MS = 86_400_000;
const WEEK_DAYS = 7;

/** The Monday `weeks` weeks before or after `week` (an ISO date, computed on UTC days). */
export function shiftWeek(week: string, weeks: number): string {
  const [y, m, d] = week.split('-').map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d) + weeks * WEEK_DAYS * DAY_MS).toISOString().slice(0, 10);
}
