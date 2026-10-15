// The simulated checkout on the contract examples (US-401..403, docs/ui-design.md 9.1, E2E-02).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { CheckoutConfirmResponse } from '@/api/models';
import { setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { SHOP } from './copy';
import { renderApp } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';

afterEach(() => act(() => setPersona('sara')));

async function ready(preset: 'giulia' | 'marco') {
  renderApp(`/shop?preset=${preset}`);
  await waitFor(() => expect(document.querySelector('[data-evaluation-status="ready"]')).not.toBeNull());
  return {
    shop: document.querySelector('[data-shop-pane]') as HTMLElement,
    scenes: document.querySelector('[data-scenes-pane]') as HTMLElement,
  };
}

describe('shop view (customer-facing)', () => {
  it('never shows a quadrant, a probability, a score, a cluster, linked accounts or the model', async () => {
    const { shop } = await ready('marco');
    const text = shop.textContent ?? '';
    for (const word of [/\bQ[1-4]\b/, /0\.\d\d/, /risk/i, /score/i, /cluster/i, /linked/i, /model/i, /propensity/i]) {
      expect(text).not.toMatch(word);
    }
  });

  it('states the withdrawal right on every cart', async () => {
    const { shop } = await ready('marco');
    expect(within(shop).getByText('You can always withdraw within 14 days of delivery, without giving a reason.')).toBeInTheDocument();
  });

  it('gives Giulia the size guide on both sizes, no size as the safer one, and the upgraded terms (US-401)', async () => {
    // Contract 1.6.0: the store holds no measurements, so the checkout never suggests a size; both
    // sizes of the dress get the same probability and the measurement guide.
    const { shop } = await ready('giulia');
    expect(shop.querySelectorAll('[data-size-advice="measurement_guide"]')).toHaveLength(2);
    expect(shop.querySelector('[data-size-advice="specific"]')).toBeNull();
    expect(within(shop).queryByRole('button', { name: /^Switch to/ })).toBeNull();
    expect(within(shop).queryByRole('button', { name: /^Keep size/ })).toBeNull();
    expect(shop.querySelector('[data-shop-terms]')?.textContent).toContain('60-day return window');
    expect(shop.querySelector('[data-terms-until]')).toBeNull();
    expect(shop.querySelector('[data-coupon-paused]')).toBeNull();
  });

  it('shows Marco the terms for future orders with their review date and the coupon pause in neutral words (US-402)', async () => {
    const { shop } = await ready('marco');
    expect(shop.querySelector('[data-shop-terms]')?.textContent).toContain('14-day return window');
    expect(shop.querySelector('[data-terms-until]')?.textContent).toBe(
      'These return terms apply to your account until 27 Dec 2026, when they are reviewed.',
    );
    const paused = shop.querySelector('[data-coupon-paused]') as HTMLElement;
    expect(paused.textContent).toBe("Welcome coupons aren't available on this account until 27 Dec 2026.");
    expect(paused.closest('[role="alert"]')).toBeNull();
    expect(paused.className).not.toMatch(/danger/);
  });

});

describe('behind the scenes', () => {
  it('shows the line probabilities against the threshold and the advice rule', async () => {
    const { scenes } = await ready('giulia');
    const lines = scenes.querySelectorAll('[data-line-advice]');
    expect(Array.from(lines).map((l) => l.getAttribute('data-line-advice'))).toEqual(['above', 'above']);
    expect(lines[0]?.textContent).toContain('0.47');
    expect(lines[1]?.textContent).toContain('0.47');
    expect(scenes.textContent).toContain('threshold 0.35');
  });

  it('compares the shown terms with the base, less generous in words', async () => {
    const { scenes } = await ready('marco');
    const rows = Array.from(scenes.querySelectorAll('[data-term-row]')).map((r) => [r.getAttribute('data-term-row'), r.getAttribute('data-direction')]);
    expect(rows).toEqual([
      ['return_window_days', 'less'],
      ['free_return_shipping', 'less'],
      ['instant_refund', 'less'],
      ['free_size_exchange', 'same'],
      ['coupons_enabled', 'less'],
    ]);
    expect(scenes.querySelector('[data-term-row="return_window_days"]')?.textContent).toContain('legal minimum');
    expect(scenes.querySelector('[data-term-row="instant_refund"]')?.textContent).toContain('↓ less generous');
  });

  it('shows the coupon pause with its rule, its confirmed application, reversible, with a review date', async () => {
    const { scenes } = await ready('marco');
    const pause = scenes.querySelector('[data-coupon-pause]') as HTMLElement;
    await waitFor(() => expect(pause.textContent).toContain('confirmed by sara'));
    expect(pause.textContent).toContain('coupon-rule-v1');
    expect(pause.textContent).toContain('approved by luca');
    expect(pause.textContent).toContain('Welcome coupons and referrals are paused');
    expect(pause.textContent).toContain('CRA-000031');
    expect(pause.textContent).toContain('27 Dec 2026');
    expect(pause.textContent).toContain('Reversible at any time');
    expect(within(pause).getByRole('link', { name: 'Reverse it on Coupon rules' })).toHaveAttribute('href', '/coupons');
  });

  it('names the terms source and the snapshot stored before the answer', async () => {
    const { scenes } = await ready('marco');
    expect(scenes.textContent).toContain('DEC-000102-1');
    expect(scenes.textContent).toContain('TS-CHK-000982');
    expect(scenes.textContent).toContain('propensity-xgb-0.1.0');
    // The decision record of DEC-000102-1 (decision_record.marco_v1.json) names its state.
    await waitFor(() => expect(scenes.querySelector('[data-decision-source]')).not.toBeNull());
    expect(scenes.querySelector('[data-error-state]')).toBeNull();
  });

  it('measures the evaluation in this browser as a real figure', async () => {
    const { scenes } = await ready('giulia');
    await waitFor(() => expect(scenes.querySelector('[data-latency]')).not.toBeNull());
    const figure = scenes.querySelector('[data-latency] [data-figure]') as HTMLElement;
    expect(figure.getAttribute('data-nature')).toBe('real');
    expect(figure.textContent).toContain('Measured in this browser');
  });
});

describe('placing the order (US-403, E2E-02)', () => {
  it('binds the shown snapshot and confirms that the terms shown are the terms stored', async () => {
    const { shop, scenes } = await ready('giulia');
    fireEvent.click(within(shop).getByRole('button', { name: 'Place order' }));
    await waitFor(() => expect(shop.querySelector('[data-order-placed]')).not.toBeNull());
    expect(shop.querySelector('[data-order-placed]')?.textContent).toContain(
      'Order ORD-CHK-000001 placed. The terms above are saved with your order.',
    );
    const receipt = scenes.querySelector('[data-snapshot-receipt]') as HTMLElement;
    expect(receipt.getAttribute('data-snapshot-receipt')).toBe('ok');
    expect(receipt.textContent).toContain('The terms shown are the terms stored (TS-CHK-000981).');
  });

  it("confirms Marco's order with the snapshot shown (checkout_confirm_response.marco.json)", async () => {
    const { shop, scenes } = await ready('marco');
    fireEvent.click(within(shop).getByRole('button', { name: 'Place order' }));
    await waitFor(() => expect(scenes.querySelector('[data-snapshot-receipt]')).not.toBeNull());
    expect(scenes.querySelector('[data-snapshot-receipt]')?.getAttribute('data-snapshot-receipt')).toBe('ok');
    expect(scenes.querySelector('[data-snapshot-receipt]')?.textContent).toContain('TS-CHK-000982');
  });

  it('shows a stored snapshot that differs from the shown terms as a failure, never as a success', async () => {
    // A confirmation that stored another snapshot than the one shown (here Giulia's for Marco's cart).
    vi.spyOn(api, 'checkoutConfirm').mockResolvedValue(readExample<CheckoutConfirmResponse>('checkout_confirm_response.giulia.json'));
    const { shop, scenes } = await ready('marco');
    fireEvent.click(within(shop).getByRole('button', { name: 'Place order' }));
    await waitFor(() => expect(scenes.querySelector('[data-snapshot-receipt]')).not.toBeNull());
    const receipt = scenes.querySelector('[data-snapshot-receipt]') as HTMLElement;
    expect(receipt.getAttribute('data-snapshot-receipt')).toBe('differs');
    expect(within(receipt).getByRole('alert').textContent).toContain('snapshot id');
    // The customer view never says "placed" for terms other than the ones shown (UI7).
    expect(shop.querySelector('[data-order-placed]')).toBeNull();
    expect(shop.querySelector('[data-order-mismatch]')?.textContent).toContain(SHOP.orderMismatch);
  });

  it('starts the test cart again after an order', async () => {
    const { shop } = await ready('giulia');
    fireEvent.click(within(shop).getByRole('button', { name: 'Place order' }));
    const again = await within(shop).findByRole('button', { name: 'Start the test cart again' });
    fireEvent.click(again);
    await waitFor(() => expect(within(shop).getByRole('button', { name: 'Place order' })).toBeEnabled());
  });
});

describe('the page as a whole', () => {
  it.each(['giulia', 'marco'] as const)('%s: every number has a provenance and the wording is clean', async (preset) => {
    await ready(preset);
    await waitFor(() => expect(document.querySelector('[data-latency]')).not.toBeNull());
    expect(findUnprovenancedNumbers(document.body)).toEqual([]);
    expect(findBannedWording(document.body).errors).toEqual([]);
  });

  it('offers the two test carts of US-403', async () => {
    renderApp('/shop?preset=marco');
    const presets = await screen.findByRole('navigation', { name: 'Test cart' });
    expect(within(presets).getByRole('link', { name: /Giulia/ })).toHaveAttribute('href', '/shop?preset=giulia');
    expect(within(presets).getByRole('link', { name: /C-MARCO-1/ })).toHaveAttribute('aria-current', 'page');
  });
});
