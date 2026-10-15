// Checkout logic (US-401..403, docs/ui-design.md 9.1), on the contract examples.
import { describe, expect, it } from 'vitest';
import type { CaseCard, CheckoutConfirmResponse, CheckoutEvaluateResponse, DemoCasesResponse, DemoCartKey } from '@/api/models';
import { cartOf as demoCartOf } from '@/domain/demoCases';
import { readExample } from '@/test/examples';
import {
  adviceFor,
  checkReceipt,
  compareTerms,
  confirmRequest,
  evaluationMatchesCart,
  latencyMetric,
  lessGenerous,
  removeLine,
  switchSize,
  toEvaluateRequest,
  type Cart,
} from './checkout';
import { linkedCart, presetCart, presetId, shopLang } from './presets';

const giulia = readExample<CheckoutEvaluateResponse>('checkout_evaluate_response.giulia.json');
const marco = readExample<CheckoutEvaluateResponse>('checkout_evaluate_response.marco.json');
const giuliaConfirm = readExample<CheckoutConfirmResponse>('checkout_confirm_response.giulia.json');

const cases = readExample<DemoCasesResponse>('demo_cases.json');
/** The carts of the demo reset (demo_cases.json): `marco` is the old name of the ring cart. */
function cartOf(preset: DemoCartKey | 'marco'): Cart {
  const cart = demoCartOf(cases, presetId(preset) as DemoCartKey);
  if (!cart) throw new Error(`no ${preset} cart in demo_cases.json`);
  return presetCart(cart);
}

describe('presets', () => {
  it('are the carts of the demo reset, marco an alias of the ring cart, Giulia for an unknown preset', () => {
    expect(presetId('ring')).toBe('ring');
    expect(presetId('marco')).toBe('ring');
    expect(presetId('giulia')).toBe('giulia');
    expect(presetId('nobody')).toBe('giulia');
    expect(presetId(null)).toBe('giulia');
  });

  it('send each demo cart as the API gave it: the request examples the mock routes on', () => {
    expect(toEvaluateRequest(cartOf('giulia'))).toEqual(readExample('checkout_evaluate_request.giulia.json'));
    expect(toEvaluateRequest(cartOf('ring'))).toEqual(readExample('checkout_evaluate_request.marco.json'));
  });

  it('read a cart without quantity or coupon as one item and no coupon (CartLine defaults)', () => {
    const cart = presetCart({ key: 'giulia', request: { customer_id: 'C-X', lines: [{ variant_id: 'V-1', size: 'S' }] } });
    expect(cart).toEqual({ customer_id: 'C-X', lines: [{ variant_id: 'V-1', size: 'S', quantity: 1 }], coupon_code: null });
  });

  it('copy the cart, so an edit never changes the demo cases', () => {
    const cart = cartOf('giulia');
    cart.lines[0]!.size = 'XS';
    expect(cartOf('giulia').lines[0]?.size).toBe('M');
  });

  it('build the cart of another linked account from the Marco card: same item, no coupon', () => {
    const card = readExample<CaseCard>('case_card.marco.json');
    const ring = cases.carts.find((c) => c.key === 'ring')!;
    const other = card.linked_accounts!.accounts.find((a) => !a.is_case_customer)!;
    expect(linkedCart(ring, card)).toEqual({ ...cartOf('ring'), customer_id: other.customer_id, coupon_code: null });
    expect(linkedCart(ring, undefined)).toBeNull();
    expect(linkedCart(ring, { ...card, linked_accounts: null })).toBeNull();
    expect(presetId('linked')).toBe('linked');
  });

  it('show the customer texts in Italian unless English is asked', () => {
    expect(shopLang(null)).toBe('it');
    expect(shopLang('en')).toBe('en');
    expect(shopLang('de')).toBe('it');
  });
});

describe('compareTerms', () => {
  it('marks every term of the Q1 upgrade as more generous or unchanged', () => {
    const rows = compareTerms(giulia.base_terms, giulia.terms);
    expect(rows.map((r) => [r.key, r.direction])).toEqual([
      ['return_window_days', 'more'],
      ['free_return_shipping', 'same'],
      ['instant_refund', 'same'],
      ['free_size_exchange', 'same'],
      ['coupons_enabled', 'same'],
    ]);
    expect(lessGenerous(rows)).toBe(false);
  });

  it('marks the Q4 terms as less generous, with the legal minimum window', () => {
    const rows = compareTerms(marco.base_terms, marco.terms);
    expect(rows.map((r) => [r.key, r.direction])).toEqual([
      ['return_window_days', 'less'],
      ['free_return_shipping', 'less'],
      ['instant_refund', 'less'],
      ['free_size_exchange', 'same'],
      ['coupons_enabled', 'less'],
    ]);
    expect(rows[0]?.legalMinimum).toBe(true);
    expect(rows.slice(1).every((r) => !r.legalMinimum)).toBe(true);
    expect(lessGenerous(rows)).toBe(true);
  });
});

describe('adviceFor (US-401)', () => {
  it('gives advice exactly when the line is at or above the threshold', () => {
    // Both sizes of Giulia's dress at 0.47, above 0.35: the measurement guide on each (1.6.0).
    for (const line of giulia.lines) {
      const view = adviceFor(line, giulia.p_return_threshold);
      expect(view).toEqual({ aboveThreshold: true, advice: line.size_advice, consistent: true });
      expect(view.advice?.kind).toBe('measurement_guide');
      expect(view.advice?.suggested_size).toBeNull();
    }
    const marco = readExample<CheckoutEvaluateResponse>('checkout_evaluate_response.marco.json');
    const [jacket] = marco.lines;
    expect(adviceFor(jacket!, marco.p_return_threshold)).toEqual({ aboveThreshold: false, advice: null, consistent: true });
  });

  it('treats the threshold itself as above (p >= threshold)', () => {
    const line = { variant_id: 'V-X-1', size: 'S', p_return: 0.35, size_advice: null };
    expect(adviceFor(line, 0.35)).toEqual({ aboveThreshold: true, advice: null, consistent: false });
  });

  it('reports advice below the threshold as inconsistent', () => {
    const line = {
      variant_id: 'V-X-1',
      size: 'S',
      p_return: 0.1,
      size_advice: {
        kind: 'measurement_guide' as const,
        suggested_size: null,
        text: { it: 'Controlla la guida alle taglie', en: 'Check the size guide' },
      },
    };
    expect(adviceFor(line, 0.35).consistent).toBe(false);
  });
});

describe('cart edits', () => {
  it('switches the size of one line and keeps the others', () => {
    const cart = switchSize(cartOf('giulia'), 1, 'M');
    expect(cart.lines.map((l) => l.size)).toEqual(['M', 'M']);
    expect(cartOf('giulia').lines.map((l) => l.size)).toEqual(['M', 'L']);
  });

  it('removes a line but never the last one', () => {
    expect(removeLine(cartOf('giulia'), 0).lines.map((l) => l.size)).toEqual(['L']);
    expect(removeLine(cartOf('marco'), 0).lines).toHaveLength(1);
  });

  it('sends a blank coupon code as null', () => {
    expect(toEvaluateRequest({ ...cartOf('marco'), coupon_code: '  ' }).coupon_code).toBeNull();
    expect(toEvaluateRequest({ ...cartOf('marco'), coupon_code: ' WELCOME-Z9K3 ' }).coupon_code).toBe('WELCOME-Z9K3');
  });
});

describe('evaluationMatchesCart', () => {
  it('matches the examples to their presets', () => {
    expect(evaluationMatchesCart(cartOf('giulia'), giulia)).toBe(true);
    expect(evaluationMatchesCart(cartOf('marco'), marco)).toBe(true);
  });

  it('detects an answer for another cart or customer', () => {
    expect(evaluationMatchesCart(switchSize(cartOf('giulia'), 1, 'M'), giulia)).toBe(false);
    expect(evaluationMatchesCart(removeLine(cartOf('giulia'), 0), giulia)).toBe(false);
    expect(evaluationMatchesCart(cartOf('marco'), giulia)).toBe(false);
  });
});

describe('confirmRequest (US-403)', () => {
  it('binds the shown snapshot to the cart: the confirm request example', () => {
    expect(confirmRequest(cartOf('giulia'), giulia)).toEqual(readExample('checkout_confirm_request.giulia.json'));
  });
});

describe('checkReceipt (E2E-02)', () => {
  it('finds the shown terms equal to the stored snapshot', () => {
    expect(checkReceipt(giulia, giuliaConfirm.terms_snapshot)).toEqual({ ok: true, mismatches: [] });
  });

  it('names every difference between shown and stored', () => {
    const stored = { ...giuliaConfirm.terms_snapshot, terms: { ...giuliaConfirm.terms_snapshot.terms, return_window_days: 30 } };
    expect(checkReceipt(giulia, stored)).toEqual({ ok: false, mismatches: ['terms'] });
    expect(checkReceipt(marco, giuliaConfirm.terms_snapshot)).toEqual({
      ok: false,
      mismatches: ['snapshot', 'customer', 'source', 'terms', 'review_at', 'shown_at'],
    });
  });
});

describe('latencyMetric', () => {
  it('is a real measurement in this browser, with the data source named, dated with the service day', () => {
    const metric = latencyMetric(84.6, '2026-10-13', 'mock');
    expect(metric).toEqual({
      value: 85,
      unit: 'milliseconds',
      provenance: {
        origin: 'Measured in this browser: round trip of POST /api/checkout/evaluate to the mock adapter on the contract examples',
        period_start: '2026-10-13',
        period_end: '2026-10-13',
        nature: 'real',
      },
    });
    expect(latencyMetric(12, '2026-10-13', 'live').provenance.origin).toContain('to the live API');
  });
});
