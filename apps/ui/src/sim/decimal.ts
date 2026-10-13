// Decimal arithmetic of policy/simulator.py in the browser (ADR-04): Python's `decimal` with the
// default context (28 significant digits, ROUND_HALF_EVEN) for +, × and ÷, and
// quantize(Decimal("0.01"), ROUND_HALF_UP) for euros. Values are BigInt coefficients with a power
// of ten, so the browser and the server agree to the cent (ECO-01-UI), floats never round money.
// Only values matter here, not Python's exponent bookkeeping: every result is quantized to cents.

export interface Dec {
  /** Coefficient: the value is `c × 10^e`. */
  readonly c: bigint;
  readonly e: number;
}

/** Python's default context precision. */
export const PRECISION = 28;

const TEN = 10n;
const pow10 = (n: number): bigint => TEN ** BigInt(n);
const abs = (n: bigint): bigint => (n < 0n ? -n : n);
const digits = (n: bigint): number => (n === 0n ? 1 : abs(n).toString().length);

const NUMBER = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;

/** Trailing zeros moved into the exponent; zero is `0 × 10^0`. */
function normalise(c: bigint, e: number): Dec {
  if (c === 0n) return { c: 0n, e: 0 };
  let coefficient = c;
  let exponent = e;
  while (coefficient % TEN === 0n) {
    coefficient /= TEN;
    exponent += 1;
  }
  return { c: coefficient, e: exponent };
}

/**
 * A decimal from a string or a JSON number. A number goes through its shortest round-trip text,
 * which is what the API does with `Decimal(str(x))` (apps/api/schemas.py `_dec`).
 */
export function dec(x: number | string | bigint): Dec {
  if (typeof x === 'bigint') return normalise(x, 0);
  if (typeof x === 'number' && !Number.isFinite(x)) throw new Error(`not a finite number: ${x}`);
  const text = String(x).trim();
  const match = NUMBER.exec(text);
  if (!match || (match[2] === '' && (match[3] ?? '') === '')) throw new Error(`not a decimal number: ${text}`);
  const [, sign, whole = '', fraction = '', exponent = '0'] = match;
  const coefficient = BigInt(`${whole}${fraction}` || '0');
  return normalise(sign === '-' ? -coefficient : coefficient, Number(exponent) - fraction.length);
}

/** Half-even rounding of `n / 10^drop` (n may be negative), with an optional extra remainder. */
function roundHalfEven(n: bigint, drop: number): bigint {
  const unit = pow10(drop);
  const q = abs(n) / unit;
  const r = abs(n) % unit;
  const twice = 2n * r;
  const up = twice > unit || (twice === unit && q % 2n === 1n);
  const rounded = up ? q + 1n : q;
  return n < 0n ? -rounded : rounded;
}

/** The value rounded to PRECISION significant digits, half-even (the context of every operation). */
function inContext(c: bigint, e: number): Dec {
  const n = digits(c);
  if (c === 0n || n <= PRECISION) return normalise(c, e);
  const drop = n - PRECISION;
  return normalise(roundHalfEven(c, drop), e + drop);
}

/** Both coefficients on the smaller exponent. */
function aligned(a: Dec, b: Dec): [bigint, bigint, number] {
  const e = Math.min(a.e, b.e);
  return [a.c * pow10(a.e - e), b.c * pow10(b.e - e), e];
}

export function add(a: Dec, b: Dec): Dec {
  const [x, y, e] = aligned(a, b);
  return inContext(x + y, e);
}

export function neg(a: Dec): Dec {
  return { c: -a.c, e: a.e };
}

export function mul(a: Dec, b: Dec): Dec {
  return inContext(a.c * b.c, a.e + b.e);
}

/** a / b rounded to PRECISION significant digits, half-even (exact when it fits). */
export function div(a: Dec, b: Dec): Dec {
  if (b.c === 0n) throw new Error('division by zero');
  if (a.c === 0n) return { c: 0n, e: 0 };
  const negative = a.c < 0n !== b.c < 0n;
  const num = abs(a.c);
  const den = abs(b.c);
  // Scale so the integer quotient has more than PRECISION digits, then round with the remainder.
  const shift = Math.max(0, PRECISION + digits(den) - digits(num) + 1);
  const scaled = num * pow10(shift);
  const q = scaled / den;
  const rem = scaled % den;
  const drop = digits(q) - PRECISION;
  const unit = pow10(drop);
  const head = q / unit;
  // The part dropped, as a fraction of one unit of the last kept digit: ((q % unit) + rem/den) / unit.
  const tail2 = 2n * ((q % unit) * den + rem);
  const half = unit * den;
  const up = tail2 > half || (tail2 === half && head % 2n === 1n);
  const coefficient = up ? head + 1n : head;
  return normalise(negative ? -coefficient : coefficient, a.e - b.e - shift + drop);
}

/** quantize(Decimal("0.01"), rounding=ROUND_HALF_UP): halves go away from zero. */
export function quantizeCents(a: Dec): Dec {
  if (a.e >= -2) return { c: a.c * pow10(a.e + 2), e: -2 };
  const drop = -2 - a.e;
  const unit = pow10(drop);
  const q = abs(a.c) / unit;
  const r = abs(a.c) % unit;
  const rounded = 2n * r >= unit ? q + 1n : q;
  return { c: a.c < 0n ? -rounded : rounded, e: -2 };
}

/** The exact value as plain text, without exponent or trailing zeros: `0.005`, `-4.4`, `640`. */
export function toPlainString(a: Dec): string {
  const { c, e } = normalise(a.c, a.e);
  const sign = c < 0n ? '-' : '';
  const body = abs(c).toString();
  if (e >= 0) return `${sign}${body}${'0'.repeat(e)}`;
  const point = body.length + e;
  const text = point > 0 ? `${body.slice(0, point)}.${body.slice(point)}` : `0.${'0'.repeat(-point)}${body}`;
  return `${sign}${text}`;
}

/** Two decimals, as Python prints a quantized amount: `640.00`, `-192.00`, `12.08`. */
export function toCentsString(a: Dec): string {
  const cents = quantizeCents(a).c;
  const sign = cents < 0n ? '-' : '';
  const body = abs(cents).toString().padStart(3, '0');
  return `${sign}${body.slice(0, -2)}.${body.slice(-2)}`;
}

/** The JSON number the API sends (`float(Decimal)`), never a negative zero. */
export function toNumber(a: Dec): number {
  const value = Number(toPlainString(a));
  return value === 0 ? 0 : value;
}
