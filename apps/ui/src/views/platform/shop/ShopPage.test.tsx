// The simulated checkout on the contract examples (US-401..403, docs/ui-design.md 9.1, E2E-02).
// The carts come from GET /api/demo/cases (demo_cases.json), never from ids in the UI.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { ApiError } from '@/api/errors';
import type { CaseCard, CheckoutConfirmResponse, CheckoutEvaluateResponse, DemoCasesResponse } from '@/api/models';
import { setScenario } from '@/api/scenario';
import { setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { SHOP } from './copy';
import { renderApp } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';

const cases = readExample<DemoCasesResponse>('demo_cases.json');
const giuliaAnswer = readExample<CheckoutEvaluateResponse>('checkout_evaluate_response.giulia.json');
const ringAnswer = readExample<CheckoutEvaluateResponse>('checkout_evaluate_response.marco.json');
const ringCustomer = cases.carts.find((c) => c.key === 'ring')?.request.customer_id ?? '';
const marcoCluster = cases.cases.find((c) => c.key === 'marco')?.cluster_id ?? '';

afterEach(() => {
  vi.restoreAllMocks();
  act(() => {
    setPersona('sara');
    setScenario('default');
  });
});

async function ready(preset: string, extra = '') {
  const view = renderApp(`/shop?preset=${preset}${extra}`);
  await waitFor(() => expect(document.querySelector('[data-evaluation-status="ready"]')).not.toBeNull());
  return {
    ...view,
    shop: document.querySelector('[data-shop-pane]') as HTMLElement,
    scenes: document.querySelector('[data-scenes-pane]') as HTMLElement,
  };
}

describe('test carts from the demo reset (ADR-15 section 15)', () => {
  it('offers the giulia and ring carts of GET /api/demo/cases; ?preset=marco opens the ring cart', async () => {
    await ready('marco');
    const presets = screen.getByRole('navigation', { name: 'Test cart' });
    expect(within(presets).getByRole('link', { name: /Giulia/ })).toHaveAttribute('href', '/shop?preset=giulia');
    const ring = within(presets).getByRole('link', { name: /Linked account/ });
    expect(ring).toHaveAttribute('href', '/shop?preset=ring');
    expect(ring).toHaveAttribute('aria-current', 'page');
    expect(ring.textContent).toContain(ringCustomer);
    expect(ring.textContent).toContain(marcoCluster);
    expect(document.querySelector('[data-checkout]')?.getAttribute('data-checkout')).toBe('ring');
  });

  it('sends the cart of the demo cases as it is (the request the mock routes on)', async () => {
    const evaluate = vi.spyOn(api, 'checkoutEvaluate');
    await ready('giulia');
    const giulia = cases.carts.find((c) => c.key === 'giulia')!.request;
    expect(evaluate.mock.calls[0]?.[0]).toEqual({ ...giulia, coupon_code: giulia.coupon_code ?? null });
  });

  it('says to run the reset, in the API words, when the store has no demo cases (404)', async () => {
    act(() => setScenario('empty'));
    renderApp('/shop?preset=giulia');
    const notice = await waitFor(() => {
      const el = document.querySelector('[data-no-demo-reset]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(notice.textContent).toContain('no demo reset yet: run python -m apps.api.reset');
    expect(document.querySelector('[data-checkout]')).toBeNull();
  });
});

describe('shop view (customer-facing)', () => {
  it('never shows a quadrant, a probability, a score, a cluster, linked accounts or the model', async () => {
    const { shop } = await ready('ring');
    const text = shop.textContent ?? '';
    for (const word of [/\bQ[1-4]\b/, /0\.\d\d/, /risk/i, /score/i, /cluster/i, /linked/i, /model/i, /propensity/i]) {
      expect(text).not.toMatch(word);
    }
  });

  it('states the withdrawal right on every cart', async () => {
    const { shop } = await ready('ring');
    expect(within(shop).getByText('You can always withdraw within 14 days of delivery, without giving a reason.')).toBeInTheDocument();
  });

  it('gives Giulia the measurement guide of the API, in Italian by default, and no size as the safer one (US-401)', async () => {
    // Contract 1.6.0: the store holds no measurements, so the checkout never suggests a size.
    const { shop } = await ready('giulia');
    const tips = shop.querySelectorAll('[data-size-advice="measurement_guide"]');
    expect(tips).toHaveLength(2);
    const text = tips[0]?.querySelector('[data-customer-text]') as HTMLElement;
    expect(text.getAttribute('lang')).toBe('it');
    expect(text.textContent).toBe(giuliaAnswer.lines[0]?.size_advice?.text.it);
    expect(shop.querySelector('[data-size-advice="specific"]')).toBeNull();
    expect(within(shop).queryByRole('button', { name: /^Switch to/ })).toBeNull();
    expect(within(shop).queryByRole('button', { name: /^Keep size/ })).toBeNull();
    expect(shop.querySelector('[data-shop-terms]')?.textContent).toContain('60-day return window');
    expect(shop.querySelector('[data-terms-until]')).toBeNull();
    expect(shop.querySelector('[data-coupon-paused]')).toBeNull();
  });

  it('switches the customer texts to English with the language picker', async () => {
    const { shop, router } = await ready('giulia');
    fireEvent.click(screen.getByRole('button', { name: 'English' }));
    await waitFor(() => expect(router.state.location.search).toContain('lang=en'));
    const text = shop.querySelector('[data-size-advice] [data-customer-text]') as HTMLElement;
    expect(text.getAttribute('lang')).toBe('en');
    expect(text.textContent).toBe(giuliaAnswer.lines[0]?.size_advice?.text.en);
  });

  it('offers "Switch to" only for a specific size the API names', async () => {
    const specific: CheckoutEvaluateResponse = {
      ...giuliaAnswer,
      lines: giuliaAnswer.lines.map((l, i) =>
        i === 1 ? { ...l, size_advice: { kind: 'specific', suggested_size: 'M', text: { it: 'Prendi la M.', en: 'Take the M.' } } } : l,
      ),
    };
    vi.spyOn(api, 'checkoutEvaluate').mockResolvedValue(specific);
    const { shop } = await ready('giulia', '&lang=en');
    expect(within(shop).getByRole('button', { name: 'Switch to M' })).toBeInTheDocument();
    expect(shop.querySelector('[data-size-advice="specific"] [data-customer-text]')?.textContent).toBe('Take the M.');
  });

  it('shows the ring account the terms for future orders with their review date and the coupon pause in the API words (US-402)', async () => {
    const { shop } = await ready('ring');
    expect(shop.querySelector('[data-shop-terms]')?.textContent).toContain('14-day return window');
    expect(shop.querySelector('[data-terms-until]')?.textContent).toBe(
      'These return terms apply to your account until 27 Dec 2026, when they are reviewed.',
    );
    const paused = shop.querySelector('[data-coupon-paused]') as HTMLElement;
    expect(paused.textContent).toBe(ringAnswer.coupon_block?.text.it);
    expect(paused.getAttribute('lang')).toBe('it');
    expect(paused.closest('[role="alert"]')).toBeNull();
    expect(paused.className).not.toMatch(/danger/);
  });

  it('shows another linked account (from the Marco card) the neutral pause text the API gives, never a cause', async () => {
    const neutral = {
      it: 'I codici sconto non sono utilizzabili sugli ordini effettuati prima del 27 dicembre 2026.',
      en: 'Discount codes cannot be used on orders placed before 27 December 2026.',
    };
    const card = readExample<CaseCard>('case_card.marco.json');
    const other = card.linked_accounts!.accounts.find((a) => !a.is_case_customer)!.customer_id;
    const evaluate = vi
      .spyOn(api, 'checkoutEvaluate')
      .mockResolvedValue({ ...ringAnswer, customer_id: other, coupon_block: { ...ringAnswer.coupon_block!, text: neutral } });
    const { shop, scenes } = await ready('linked', '&lang=en');
    expect(evaluate.mock.calls[0]?.[0]).toMatchObject({ customer_id: other, coupon_code: null });
    expect(screen.getByRole('link', { name: /Another linked account/ })).toHaveAttribute('aria-current', 'page');
    expect(shop.querySelector('[data-coupon-paused]')?.textContent).toBe(neutral.en);
    expect(shop.textContent).not.toContain('previous returns');
    expect(scenes.querySelector('[data-coupon-text]')?.textContent).toContain(neutral.it);
    expect(findUnprovenancedNumbers(shop)).toEqual([]);
  });
});

describe('behind the scenes', () => {
  it('shows the line probabilities against the threshold and the advice in both languages', async () => {
    const { scenes } = await ready('giulia');
    const lines = scenes.querySelectorAll('[data-line-advice]');
    expect(Array.from(lines).map((l) => l.getAttribute('data-line-advice'))).toEqual(['above', 'above']);
    expect(lines[0]?.textContent).toContain('0.47');
    expect(lines[0]?.querySelector('[lang="it"]')?.textContent).toContain(giuliaAnswer.lines[0]?.size_advice?.text.it);
    expect(lines[0]?.querySelector('[lang="en"]')?.textContent).toContain(giuliaAnswer.lines[0]?.size_advice?.text.en);
    expect(scenes.textContent).toContain('threshold 0.35');
  });

  it('compares the shown terms with the base, less generous in words', async () => {
    const { scenes } = await ready('ring');
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

  it('shows the coupon pause with its rule, its confirmed application, both customer texts and which account reads which', async () => {
    const { scenes } = await ready('ring');
    const pause = scenes.querySelector('[data-coupon-pause]') as HTMLElement;
    await waitFor(() => expect(pause.textContent).toContain('confirmed by sara'));
    expect(pause.textContent).toContain('coupon-rule-v1');
    expect(pause.textContent).toContain('approved by luca');
    expect(pause.textContent).toContain('CRA-000031');
    expect(pause.textContent).toContain('27 Dec 2026');
    expect(pause.textContent).toContain('Reversible at any time');
    const text = pause.querySelector('[data-coupon-text]') as HTMLElement;
    expect(text.querySelector('[lang="it"]')?.textContent).toContain(ringAnswer.coupon_block?.text.it);
    expect(text.querySelector('[lang="en"]')?.textContent).toContain(ringAnswer.coupon_block?.text.en);
    expect(text.textContent).toContain('every other linked account reads a neutral text that gives no cause');
    expect(within(pause).getByRole('link', { name: 'Reverse it on Coupon rules' })).toHaveAttribute('href', '/coupons');
  });

  it('names the terms source and the snapshot stored before the answer', async () => {
    const { scenes } = await ready('ring');
    expect(scenes.textContent).toContain(ringAnswer.terms_source);
    expect(scenes.textContent).toContain(ringAnswer.terms_snapshot_id);
    expect(scenes.textContent).toContain(ringAnswer.model_version);
    await waitFor(() => expect(scenes.querySelector('[data-decision-source]')).not.toBeNull());
    expect(scenes.querySelector('[data-error-state]')).toBeNull();
  });

  it('measures the evaluation in this browser as a real figure, dated with the service clock', async () => {
    const { scenes } = await ready('giulia');
    await waitFor(() => expect(scenes.querySelector('[data-latency]')).not.toBeNull());
    const figure = scenes.querySelector('[data-latency] [data-figure]') as HTMLElement;
    expect(figure.getAttribute('data-nature')).toBe('real');
    expect(figure.textContent).toContain('Measured in this browser');
    // The readyz example's clock stands at 30 Sep 2026: the period is that day, not the browser's.
    expect(figure.textContent).toContain('30 Sep 2026');
    expect(scenes.querySelector('[data-latency]')?.textContent).toContain('dated with the service clock');
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

  it("confirms the ring account's order with the snapshot shown (checkout_confirm_response.marco.json)", async () => {
    const { shop, scenes } = await ready('ring');
    fireEvent.click(within(shop).getByRole('button', { name: 'Place order' }));
    await waitFor(() => expect(scenes.querySelector('[data-snapshot-receipt]')).not.toBeNull());
    expect(scenes.querySelector('[data-snapshot-receipt]')?.getAttribute('data-snapshot-receipt')).toBe('ok');
    expect(scenes.querySelector('[data-snapshot-receipt]')?.textContent).toContain(ringAnswer.terms_snapshot_id);
  });

  it('shows a stored snapshot that differs from the shown terms as a failure, never as a success', async () => {
    // A confirmation that stored another snapshot than the one shown (here Giulia's for the ring cart).
    vi.spyOn(api, 'checkoutConfirm').mockResolvedValue(readExample<CheckoutConfirmResponse>('checkout_confirm_response.giulia.json'));
    const { shop, scenes } = await ready('ring');
    fireEvent.click(within(shop).getByRole('button', { name: 'Place order' }));
    await waitFor(() => expect(scenes.querySelector('[data-snapshot-receipt]')).not.toBeNull());
    const receipt = scenes.querySelector('[data-snapshot-receipt]') as HTMLElement;
    expect(receipt.getAttribute('data-snapshot-receipt')).toBe('differs');
    expect(within(receipt).getByRole('alert').textContent).toContain('snapshot id');
    // The customer view never says "placed" for terms other than the ones shown (UI7).
    expect(shop.querySelector('[data-order-placed]')).toBeNull();
    expect(shop.querySelector('[data-order-mismatch]')?.textContent).toContain(SHOP.orderMismatch);
  });

  it('evaluates the cart again when the shown terms lapsed before the order (409), quoting the API', async () => {
    act(() => setScenario('conflict'));
    const evaluate = vi.spyOn(api, 'checkoutEvaluate');
    const { shop } = await ready('ring');
    expect(evaluate).toHaveBeenCalledTimes(1);
    fireEvent.click(within(shop).getByRole('button', { name: 'Place order' }));
    const notice = await waitFor(() => {
      const el = shop.querySelector('[data-checkout-notice="terms_changed"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(notice.textContent).toContain('The terms changed before the order was placed');
    expect(notice.textContent).toContain('can no longer be confirmed');
    await waitFor(() => expect(evaluate).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(document.querySelector('[data-evaluation-status="ready"]')).not.toBeNull());
    expect(shop.querySelector('[data-order-placed]')).toBeNull();
    expect(shop.querySelector('[data-error-state]')).toBeNull();
    expect(within(shop).getByRole('button', { name: 'Place order' })).toBeEnabled();
  });

  it('waits for Retry-After when the public limit answers 429, then evaluates again', async () => {
    act(() => setScenario('rate_limited'));
    renderApp('/shop?preset=giulia');
    const notice = await waitFor(() => {
      const el = document.querySelector('[data-checkout-notice="rate_limited"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    // error.rate_limited_public.json: "try again in 12 s"; the mock sends Retry-After: 12.
    expect(notice.textContent).toMatch(/the terms are checked again in 1[0-2] s/);
    expect(notice.textContent).toContain('at most 30 requests of the public demo per client');
    expect(document.querySelector('[data-evaluation-status]')?.getAttribute('data-evaluation-status')).toBe('waiting');
  });

  it('never places an order on the read-only public demo: the button says why', async () => {
    act(() => setScenario('read_only'));
    const { shop } = await ready('giulia');
    const place = within(shop).getByRole('button', { name: 'Place order' });
    await waitFor(() => expect(place).toBeDisabled());
    expect(within(shop).getByText('Read-only public demo')).toBeInTheDocument();
  });

  it('shows the read-only notice when the API refuses the order with 403', async () => {
    vi.spyOn(api, 'checkoutConfirm').mockRejectedValue(
      new ApiError({ status: 403, code: 'forbidden_role', message: 'the public demo is read-only (PUBLIC_READ_ONLY)' }),
    );
    const { shop } = await ready('giulia');
    fireEvent.click(within(shop).getByRole('button', { name: 'Place order' }));
    const notice = await waitFor(() => {
      const el = shop.querySelector('[data-checkout-notice="read_only"]');
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(notice.textContent).toContain('Read-only public demo: the order was not placed.');
    expect(notice.textContent).toContain('the public demo is read-only (PUBLIC_READ_ONLY)');
    expect(shop.querySelector('[data-order-placed]')).toBeNull();
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
  it.each(['giulia', 'ring'] as const)('%s: every number has a provenance and the wording is clean', async (preset) => {
    await ready(preset);
    await waitFor(() => expect(document.querySelector('[data-latency]')).not.toBeNull());
    expect(findUnprovenancedNumbers(document.body)).toEqual([]);
    expect(findBannedWording(document.body).errors).toEqual([]);
  });
});
