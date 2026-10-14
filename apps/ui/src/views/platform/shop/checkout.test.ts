// Checkout logic (US-401..403, docs/ui-design.md 9.1), on the contract examples.
import { describe, expect, it } from 'vitest';
import type { CheckoutConfirmResponse, CheckoutEvaluateResponse } from '@/api/models';
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
import { presetCart, presetId } from './presets';

const giulia = readExample<CheckoutEvaluateResponse>('checkout_evaluate_response.giulia.json');
const marco = readExample<CheckoutEvaluateResponse>('checkout_evaluate_response.marco.json');
const giuliaConfirm = readExample<CheckoutConfirmResponse>('checkout_confirm_response.giulia.json');

const cartOf = (preset: 'giulia' | 'marco'): Cart => presetCart(preset);

describe('presets', () => {
  it('fall back to Giulia for an unknown preset', () => {
    expect(presetId('marco')).toBe('marco');
    expect(presetId('nobody')).toBe('giulia');
    expect(presetId(null)).toBe('giulia');
  });

  it('are the request examples the mock routes on', () => {
    expect(toEvaluateRequest(cartOf('giulia'))).toEqual(readExample('checkout_evaluate_request.giulia.json'));
    expect(toEvaluateRequest(cartOf('marco'))).toEqual(readExample('checkout_evaluate_request.marco.json'));
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
    const [m, l] = giulia.lines;
    expect(adviceFor(m!, giulia.p_return_threshold)).toEqual({ aboveThreshold: false, advice: null, consistent: true });
    expect(adviceFor(l!, giulia.p_return_threshold)).toEqual({
      aboveThreshold: true,
      advice: { kind: 'specific', suggested_size: 'M', text: 'Customers with your measurements kept size M of this dress' },
      consistent: true,
    });
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
      size_advice: { kind: 'measurement_guide' as const, suggested_size: null, text: 'Check the size guide' },
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
  it('is a real measurement in this browser, with the data source named', () => {
    const metric = latencyMetric(84.6, '2026-10-13T21:03:00.000Z', 'mock');
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
    expect(latencyMetric(12, '2026-10-13T21:03:00.000Z', 'live').provenance.origin).toContain('to the live API');
  });
});
