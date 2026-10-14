// The decimal arithmetic of policy/simulator.py: Python's Decimal with its default context
// (28 significant digits, half-even) and quantize(0.01, ROUND_HALF_UP). Reference values were
// printed by CPython's decimal module.
import { describe, expect, it } from 'vitest';
import { add, dec, div, mul, neg, quantizeCents, toCentsString, toNumber, toPlainString } from './decimal';

describe('dec', () => {
  it.each([
    ['8.05', '8.05'],
    ['-0.5', '-0.5'],
    ['240', '240'],
    ['1e-7', '0.0000001'],
    ['1.5e+21', '1500000000000000000000'],
    ['.25', '0.25'],
  ])('parses %s', (input, plain) => {
    expect(toPlainString(dec(input))).toBe(plain);
  });

  it('parses a JSON number through its shortest representation, as Decimal(str(x)) does', () => {
    expect(toPlainString(dec(0.1))).toBe('0.1');
    expect(toPlainString(dec(6.5))).toBe('6.5');
    expect(toPlainString(dec(1e-7))).toBe('0.0000001');
    expect(toPlainString(dec(80))).toBe('80');
  });

  it('refuses what is not a finite number', () => {
    expect(() => dec(Number.NaN)).toThrow();
    expect(() => dec(Number.POSITIVE_INFINITY)).toThrow();
    expect(() => dec('8,05')).toThrow();
  });
});

describe('context arithmetic (28 significant digits, half-even)', () => {
  it('rounds a division to 28 significant digits', () => {
    expect(toPlainString(div(dec(1), dec(3)))).toBe('0.3333333333333333333333333333');
    expect(toPlainString(div(dec(2), dec(3)))).toBe('0.6666666666666666666666666667');
    expect(toPlainString(div(dec(2), dec(60)))).toBe('0.03333333333333333333333333333');
  });

  it('keeps an exact division exact', () => {
    expect(toPlainString(div(dec(10), dec(4)))).toBe('2.5');
    expect(toPlainString(div(dec('8.05'), dec(2)))).toBe('4.025');
  });

  it('rounds a product of rounded operands like Python (2/60 × 0.15 = 0.005)', () => {
    expect(toPlainString(mul(div(dec(2), dec(60)), dec('0.15')))).toBe('0.005');
    expect(toPlainString(mul(div(dec(1), dec(7)), dec(7)))).toBe('1');
  });

  it('rounds a long product to 28 digits', () => {
    expect(toPlainString(mul(dec('123456789012345678901234567890'), dec(1)))).toBe('123456789012345678901234567900');
  });

  it('adds and negates', () => {
    expect(toPlainString(add(dec('0.1'), dec('0.2')))).toBe('0.3');
    expect(toPlainString(add(dec('1e-7'), dec('1e30')))).toBe('1000000000000000000000000000000');
    expect(toPlainString(neg(dec('4.4')))).toBe('-4.4');
  });

  it('refuses a division by zero', () => {
    expect(() => div(dec(1), dec(0))).toThrow();
  });
});

describe('quantizeCents (ROUND_HALF_UP)', () => {
  it.each([
    ['12.075', '12.08'],
    ['0.0049999', '0.00'],
    ['0.005', '0.01'],
    ['-0.005', '-0.01'],
    ['36.666666', '36.67'],
    ['640', '640.00'],
  ])('%s -> %s', (input, cents) => {
    expect(toCentsString(quantizeCents(dec(input)))).toBe(cents);
  });

  it('converts to a JSON number without a negative zero', () => {
    expect(Object.is(toNumber(quantizeCents(neg(dec(0)))), 0)).toBe(true);
    expect(toNumber(quantizeCents(dec('-192')))).toBe(-192);
    expect(toNumber(quantizeCents(dec('100055544639.44')))).toBe(100055544639.44);
  });
});
