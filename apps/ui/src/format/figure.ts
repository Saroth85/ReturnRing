// Number formatting by Metric unit (docs/ui-design.md 4.7): en-GB, a true minus sign (U+2212),
// tabular numerals in the components. A probability is never shown as a percentage.
import type { Unit } from '@/api/models';

export const MINUS = '−';

const cache = new Map<string, Intl.NumberFormat>();
function nf(min: number, max: number): Intl.NumberFormat {
  const key = `${min}:${max}`;
  let format = cache.get(key);
  if (!format) {
    format = new Intl.NumberFormat('en-GB', { minimumFractionDigits: min, maximumFractionDigits: max });
    cache.set(key, format);
  }
  return format;
}

function signOf(value: number, signed: boolean): string {
  if (value < 0) return MINUS;
  if (signed && value > 0) return '+';
  return '';
}

export interface FormatOptions {
  /** Signed totals show "+" on gains (net benefit components and totals). */
  signed?: boolean;
}

/** The figure as text, e.g. `€42.10`, `0.87`, `54.5%`, `z = 6.86`, `1.8 expected`. */
export function formatValue(value: number, unit: Unit, options: FormatOptions = {}): string {
  const signed = options.signed ?? false;
  const abs = Math.abs(value);
  const sign = signOf(value, signed);
  switch (unit) {
    case 'eur':
      return `${sign}€${nf(2, 2).format(abs)}`;
    case 'usd':
      return `${sign}$${nf(2, 2).format(abs)}`;
    case 'probability':
    case 'score':
      return `${sign}${nf(2, 2).format(abs)}`;
    case 'ratio':
      return `${sign}${nf(1, 1).format(abs * 100)}%`;
    case 'percent':
      return `${sign}${nf(0, 1).format(abs)}%`;
    case 'count':
      return `${sign}${nf(0, 0).format(abs)}`;
    case 'expected_count':
      return `${sign}${nf(1, 1).format(abs)} expected`;
    case 'z_score':
      return `z = ${sign}${nf(2, 2).format(abs)}`;
    case 'percentage_points':
      return `${sign}${nf(1, 1).format(abs)} pp`;
    case 'days':
      return `${sign}${nf(0, 1).format(abs)} ${abs === 1 ? 'day' : 'days'}`;
    case 'seconds':
      return `${sign}${nf(0, 1).format(abs)} s`;
    case 'milliseconds':
      return `${sign}${nf(0, 0).format(abs)} ms`;
    case 'tokens_per_second':
      return `${sign}${nf(0, 0).format(abs)} tok/s`;
    case 'gib':
      return `${sign}${nf(1, 1).format(abs)} GiB`;
    case 'watts':
      return `${sign}${nf(0, 0).format(abs)} W`;
    default: {
      const unknownUnit: never = unit;
      throw new Error(`no format for unit ${String(unknownUnit)}`);
    }
  }
}

/** Words for a unit, for screen readers and table headers. */
export const UNIT_WORDS: Record<Unit, string> = {
  eur: 'euros',
  usd: 'US dollars',
  count: 'count',
  expected_count: 'expected count',
  ratio: 'share',
  probability: 'probability',
  percent: 'percent',
  percentage_points: 'percentage points',
  z_score: 'z-score',
  score: 'score',
  days: 'days',
  seconds: 'seconds',
  milliseconds: 'milliseconds',
  tokens_per_second: 'tokens per second',
  gib: 'gibibytes',
  watts: 'watts',
};
