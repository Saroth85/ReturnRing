import { describe, expect, it } from 'vitest';
import { daysBetween, formatDate, formatDistinct, formatPeriod, formatTimestamp, formatValue, MINUS } from '@/format';

describe('formatValue (docs/ui-design.md 4.7)', () => {
  it.each([
    [42.1, 'eur', {}, '€42.10'],
    [1234.5, 'eur', {}, '€1,234.50'],
    [123.5, 'eur', { signed: true }, '+€123.50'],
    [-4.4, 'eur', { signed: true }, `${MINUS}€4.40`],
    [-192, 'eur', {}, `${MINUS}€192.00`],
    [0, 'eur', { signed: true }, '€0.00'],
    [0.87, 'probability', {}, '0.87'],
    [0.545, 'ratio', {}, '54.5%'],
    [7, 'count', {}, '7'],
    [1.8, 'expected_count', {}, '1.8 expected'],
    [6.86, 'z_score', {}, 'z = 6.86'],
    [6.1, 'percentage_points', {}, '6.1 pp'],
    [9, 'days', {}, '9 days'],
    [1, 'days', {}, '1 day'],
    [3900, 'milliseconds', {}, '3,900 ms'],
    [1450, 'tokens_per_second', {}, '1,450 tok/s'],
    [143, 'gib', {}, '143.0 GiB'],
    [535, 'watts', {}, '535 W'],
    [71, 'percent', {}, '71%'],
  ] as const)('%s %s -> %s', (value, unit, options, expected) => {
    expect(formatValue(value, unit, options)).toBe(expected);
  });

  it('never shows a probability as a percentage', () => {
    expect(formatValue(0.87, 'probability')).not.toContain('%');
  });

  it('uses a true minus sign, never a hyphen', () => {
    expect(formatValue(-1, 'z_score')).toBe(`z = ${MINUS}1.00`);
    expect(formatValue(-1, 'count')).not.toContain('-');
  });

  it('never prints a signed zero, and picks day or days from the printed number (UI41)', () => {
    expect(formatValue(-0.004, 'score', { signed: true })).toBe('0.00');
    expect(formatValue(0.004, 'score', { signed: true })).toBe('0.00');
    expect(formatValue(-0.003, 'eur')).toBe('€0.00');
    expect(formatValue(-0.04, 'percentage_points')).toBe('0.0 pp');
    expect(formatValue(-0.0004, 'ratio', { signed: true })).toBe('0.0%');
    expect(formatValue(1.04, 'days')).toBe('1 day');
    expect(formatValue(0.96, 'days')).toBe('1 day');
    expect(formatValue(1.5, 'days')).toBe('1.5 days');
    expect(formatValue(-0.006, 'score', { signed: true })).toBe(`${MINUS}0.01`);
  });
});

describe('maxFractionDigits (UI10)', () => {
  it('prints a float count with its decimal and a whole one without', () => {
    expect(formatValue(7.5, 'count', { maxFractionDigits: 1 })).toBe('7.5');
    expect(formatValue(240, 'count', { maxFractionDigits: 1 })).toBe('240');
  });
});

describe('formatDistinct (UI5)', () => {
  it('adds digits until a value and its threshold no longer print the same', () => {
    expect(formatDistinct(0.5962, 0.6, 'probability')).toBe('0.596');
    expect(formatDistinct(0.6, 0.5962, 'probability')).toBe('0.600');
    expect(formatDistinct(184.996, 185, 'eur')).toBe('€184.996');
    expect(formatDistinct(0.87, 0.6, 'probability')).toBe('0.87');
  });

  it('keeps the usual digits when the two values are equal', () => {
    expect(formatDistinct(0.6, 0.6, 'probability')).toBe('0.60');
    expect(formatDistinct(0.6, null, 'probability')).toBe('0.60');
  });
});

describe('dates (docs/ui-design.md 4.7)', () => {
  it('shows dates as given, never shifted across time zones', () => {
    expect(formatDate('2026-12-27')).toBe('27 Dec 2026');
    expect(formatDate('2026-09-30')).toBe('30 Sep 2026');
  });

  it('shows timestamps in UTC with the suffix', () => {
    expect(formatTimestamp('2026-09-30T18:20:00Z')).toBe('30 Sep 2026, 18:20 UTC');
    expect(formatTimestamp('2026-09-30T23:30:00+02:00')).toBe('30 Sep 2026, 21:30 UTC');
  });

  it('formats a period, with one date when start equals end', () => {
    expect(formatPeriod('2025-10-01', '2026-09-30')).toBe('1 Oct 2025 – 30 Sep 2026');
    expect(formatPeriod('2026-09-21', '2026-09-21')).toBe('21 Sep 2026');
  });

  it('counts calendar days', () => {
    expect(daysBetween('2026-09-27', '2026-09-30T18:00:00Z')).toBe(3);
  });
});
