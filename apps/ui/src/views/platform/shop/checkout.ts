// Checkout logic (US-401..403, docs/ui-design.md 9.1): the cart the presenter edits, the terms
// shown against the shop's base terms, the size advice of each line and the snapshot receipt
// (E2E-02: the terms shown are the terms stored). Pure functions, tested on the contract examples.
import { LEGAL_MINIMUM_WINDOW_DAYS } from '@/api/contract.gen';
import type { CheckoutConfirmRequest, CheckoutConfirmResponse, CheckoutEvaluateRequest, CheckoutEvaluateResponse, Metric, Terms } from '@/api/models';

export type CartLine = CheckoutEvaluateRequest['lines'][number];
export type CheckoutLine = CheckoutEvaluateResponse['lines'][number];
export type SizeAdvice = NonNullable<CheckoutLine['size_advice']>;
export type TermsSnapshot = CheckoutConfirmResponse['terms_snapshot'];

/** The cart as the presenter edits it: one line at least, the coupon code as typed. */
export interface Cart {
  customer_id: string;
  lines: CartLine[];
  coupon_code: string | null;
}

export type TermKey = keyof Terms;
/** Catalogue order of the terms (contracts/levers.py, docs/ui-design.md 5.3). */
export const TERM_KEYS: readonly TermKey[] = [
  'return_window_days',
  'free_return_shipping',
  'instant_refund',
  'free_size_exchange',
  'coupons_enabled',
];

export type Direction = 'less' | 'more' | 'same';

export interface TermRow {
  key: TermKey;
  base: number | boolean;
  shown: number | boolean;
  /** Less or more generous than the base for the customer. */
  direction: Direction;
  /** A 14-day window: the legal minimum (rule 3). */
  legalMinimum: boolean;
}

function directionOf(base: number | boolean, shown: number | boolean): Direction {
  // Every boolean term is a concession when true; a longer window is more generous.
  const a = Number(base);
  const b = Number(shown);
  if (b < a) return 'less';
  if (b > a) return 'more';
  return 'same';
}

/** The shown terms against the shop's base terms, in catalogue order. */
export function compareTerms(base: Terms, shown: Terms): TermRow[] {
  return TERM_KEYS.map((key) => ({
    key,
    base: base[key],
    shown: shown[key],
    direction: directionOf(base[key], shown[key]),
    legalMinimum: key === 'return_window_days' && shown[key] === LEGAL_MINIMUM_WINDOW_DAYS,
  }));
}

/** Whether any shown term is less generous than the base (a restriction in force). */
export const lessGenerous = (rows: readonly TermRow[]) => rows.some((r) => r.direction === 'less');

export interface AdviceView {
  /** p_return at or above the threshold (ADR-15: advice exactly then). */
  aboveThreshold: boolean;
  advice: SizeAdvice | null;
  /** False when the advice and the threshold disagree: shown, never hidden (P10). */
  consistent: boolean;
}

export function adviceFor(line: CheckoutLine, threshold: number): AdviceView {
  const aboveThreshold = line.p_return >= threshold;
  return { aboveThreshold, advice: line.size_advice, consistent: aboveThreshold === (line.size_advice !== null) };
}

/** The same cart with one line in another size (the "Switch to M" of US-401). */
export function switchSize(cart: Cart, index: number, size: string): Cart {
  return { ...cart, lines: cart.lines.map((line, i) => (i === index ? { ...line, size } : line)) };
}

/** The same cart without one line; a cart keeps at least one line. */
export function removeLine(cart: Cart, index: number): Cart {
  if (cart.lines.length <= 1) return cart;
  return { ...cart, lines: cart.lines.filter((_, i) => i !== index) };
}

const couponOf = (code: string | null) => (code && code.trim() ? code.trim() : null);

export function toEvaluateRequest(cart: Cart): CheckoutEvaluateRequest {
  if (cart.lines.length === 0) throw new Error('a cart has at least one line');
  return {
    customer_id: cart.customer_id,
    lines: cart.lines.map((l) => ({ ...l })) as CheckoutEvaluateRequest['lines'],
    coupon_code: couponOf(cart.coupon_code),
  };
}

/** Whether an evaluation answers this cart: same customer, same lines in the same order. */
export function evaluationMatchesCart(cart: Cart, evaluation: CheckoutEvaluateResponse): boolean {
  return (
    evaluation.customer_id === cart.customer_id &&
    evaluation.lines.length === cart.lines.length &&
    evaluation.lines.every((line, i) => line.variant_id === cart.lines[i]?.variant_id && line.size === cart.lines[i]?.size)
  );
}

/** POST /api/checkout/confirm: the cart with the snapshot id it was shown (US-403). */
export function confirmRequest(cart: Cart, evaluation: CheckoutEvaluateResponse): CheckoutConfirmRequest {
  return { terms_snapshot_id: evaluation.terms_snapshot_id, ...toEvaluateRequest(cart) };
}

export type ReceiptField = 'snapshot' | 'customer' | 'source' | 'terms' | 'base_terms' | 'review_at' | 'shown_at';

export interface ReceiptCheck {
  ok: boolean;
  mismatches: ReceiptField[];
}

const sameTerms = (a: Terms, b: Terms) => TERM_KEYS.every((k) => a[k] === b[k]);

/** E2E-02: the stored snapshot holds exactly what was shown, field by field. */
export function checkReceipt(shown: CheckoutEvaluateResponse, stored: TermsSnapshot): ReceiptCheck {
  const checks: Array<[ReceiptField, boolean]> = [
    ['snapshot', shown.terms_snapshot_id === stored.terms_snapshot_id],
    ['customer', shown.customer_id === stored.customer_id],
    ['source', shown.terms_source === stored.source],
    ['terms', sameTerms(shown.terms, stored.terms)],
    ['base_terms', sameTerms(shown.base_terms, stored.base_terms)],
    ['review_at', shown.review_at === stored.review_at],
    ['shown_at', shown.shown_at === stored.shown_at],
  ];
  const mismatches = checks.filter(([, same]) => !same).map(([field]) => field);
  return { ok: mismatches.length === 0, mismatches };
}

/**
 * The evaluate round trip measured in this browser (US-403 target under one second): a real
 * measurement, whose origin names the data source so a mock answer is never taken for the API. Its
 * period is the service's day (`day`, YYYY-MM-DD from the demo clock of /readyz), like every period
 * that ends now on screen: never the browser's date.
 */
export function latencyMetric(ms: number, day: string, source: 'mock' | 'live'): Metric {
  const target = source === 'mock' ? 'the mock adapter on the contract examples' : 'the live API';
  return {
    value: Math.round(ms),
    unit: 'milliseconds',
    provenance: {
      origin: `Measured in this browser: round trip of POST /api/checkout/evaluate to ${target}`,
      period_start: day,
      period_end: day,
      nature: 'real',
    },
  };
}
