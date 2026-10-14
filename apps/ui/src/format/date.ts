// Dates and timestamps (docs/ui-design.md 4.7). Dates are shown as given (`27 Dec 2026`), never
// shifted across time zones; timestamps in UTC with the suffix (`30 Sep 2026, 18:20 UTC`).
// Month names are fixed here: Intl's en-GB "Sept" differs across ICU versions.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function parseDate(iso: string): { y: number; m: number; d: number } {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) throw new Error(`not an ISO date: ${iso}`);
  return { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) };
}

/** `2026-12-27` -> `27 Dec 2026`. */
export function formatDate(iso: string): string {
  const { y, m, d } = parseDate(iso);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** `2026-09-30T18:20:00Z` -> `30 Sep 2026, 18:20 UTC`. */
export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) throw new Error(`not an ISO timestamp: ${iso}`);
  const hh = String(date.getUTCHours()).padStart(2, '0');
  const mm = String(date.getUTCMinutes()).padStart(2, '0');
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}, ${hh}:${mm} UTC`;
}

/** `1 Oct 2025 – 30 Sep 2026`; one date when start equals end. */
export function formatPeriod(start: string, end: string): string {
  return start === end ? formatDate(start) : `${formatDate(start)} – ${formatDate(end)}`;
}

/** Whole days from `fromIso` to `toIso` (dates or timestamps), counted on UTC calendar days. */
export function daysBetween(fromIso: string, toIso: string): number {
  const a = parseDate(fromIso);
  const b = parseDate(toIso);
  return Math.round((Date.UTC(b.y, b.m - 1, b.d) - Date.UTC(a.y, a.m - 1, a.d)) / 86_400_000);
}
