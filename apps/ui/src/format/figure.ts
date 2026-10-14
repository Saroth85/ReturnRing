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

export interface FormatOptions {
  /** Signed totals show "+" on gains (net benefit components and totals). */
  signed?: boolean;
  /** Fraction digits added to the unit's usual ones (formatDistinct). */
  extraDigits?: number;
  /** At most this many fraction digits when the unit shows fewer (a float count: 7.5 minutes). */
  maxFractionDigits?: number;
}

interface UnitSpec {
  min: number;
  max: number;
  scale?: number;
  /** Before the sign (z = −1.00). */
  lead?: string;
  /** After the sign, before the digits (€, $). */
  prefix?: string;
  /** After the digits; a function picks singular or plural from the printed digits. */
  suffix?: string | ((digits: string) => string);
}

const UNIT_SPECS: Record<Unit, UnitSpec> = {
  eur: { min: 2, max: 2, prefix: '€' },
  usd: { min: 2, max: 2, prefix: '$' },
  probability: { min: 2, max: 2 },
  score: { min: 2, max: 2 },
  ratio: { min: 1, max: 1, scale: 100, suffix: '%' },
  percent: { min: 0, max: 1, suffix: '%' },
  count: { min: 0, max: 0 },
  expected_count: { min: 1, max: 1, suffix: ' expected' },
  z_score: { min: 2, max: 2, lead: 'z = ' },
  percentage_points: { min: 1, max: 1, suffix: ' pp' },
  days: { min: 0, max: 1, suffix: (digits) => (digits === '1' ? ' day' : ' days') },
  seconds: { min: 0, max: 1, suffix: ' s' },
  milliseconds: { min: 0, max: 0, suffix: ' ms' },
  tokens_per_second: { min: 0, max: 0, suffix: ' tok/s' },
  gib: { min: 1, max: 1, suffix: ' GiB' },
  watts: { min: 0, max: 0, suffix: ' W' },
};

/**
 * The figure as text, e.g. `€42.10`, `0.87`, `54.5%`, `z = 6.86`, `1.8 expected`. The sign and the
 * unit word follow the printed digits, not the raw value (UI41): a value that rounds to zero has no
 * sign, and 1.04 days prints "1 day".
 */
export function formatValue(value: number, unit: Unit, options: FormatOptions = {}): string {
  const spec = UNIT_SPECS[unit];
  if (!spec) throw new Error(`no format for unit ${String(unit)}`);
  const extra = Math.max(0, options.extraDigits ?? 0);
  const max = Math.max(spec.max + extra, options.maxFractionDigits ?? 0);
  const digits = nf(spec.min + extra, max).format(Math.abs(value) * (spec.scale ?? 1));
  const zero = !/[1-9]/.test(digits);
  const sign = zero ? '' : value < 0 ? MINUS : options.signed && value > 0 ? '+' : '';
  const suffix = typeof spec.suffix === 'function' ? spec.suffix(digits) : (spec.suffix ?? '');
  return `${spec.lead ?? ''}${sign}${spec.prefix ?? ''}${digits}${suffix}`;
}

const MAX_EXTRA_DIGITS = 4;

/**
 * A value printed with enough digits to differ from the value it is compared with (UI5): a risk of
 * 0.5962 against a threshold of 0.60 prints 0.596, never "0.60 · threshold 0.60 · below".
 */
export function formatDistinct(value: number, other: number | null | undefined, unit: Unit, options: FormatOptions = {}): string {
  if (other === null || other === undefined || value === other) return formatValue(value, unit, options);
  for (let extra = 0; extra <= MAX_EXTRA_DIGITS; extra++) {
    if (formatValue(value, unit, { extraDigits: extra }) !== formatValue(other, unit, { extraDigits: extra })) {
      return formatValue(value, unit, { ...options, extraDigits: extra });
    }
  }
  return formatValue(value, unit, { ...options, extraDigits: MAX_EXTRA_DIGITS });
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
