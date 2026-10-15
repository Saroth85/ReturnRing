// The decision acts on the whole case page against the service's state (contract 1.8.0, ADR-15
// sections 15 to 20): the refund deadline on the service's clock (banner, only reject or the
// standard terms, no message, the case stays overdue; never a deadline on the guarantee path),
// the 409 recoveries (the case loaded again, the message verbatim), the review sheet (lift with a
// reason and no message, renew from the review date with no reason and no message), the
// grant-only levers of the override editor, the read-only public demo and the cached demo.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { resetModesSeen } from '@/api/meta';
import type { DecisionResult, ReadyzResponse } from '@/api/models';
import { setScenario } from '@/api/scenario';
import { setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { renderApp } from '@/test/render';
import { findUnprovenancedNumbers } from '@/test/scan';
import { OPERATOR } from '../../copy';
import { resetOverlays } from '../../state/caseOverlay';
import { noticeStore } from '../../state/prefs';

afterEach(() => {
  resetOverlays();
  act(() => {
    setPersona('sara');
    setScenario('default');
    resetModesSeen();
    noticeStore.set(null);
  });
});

/** The service's clock stands at `now` (a fixed clock: the client never advances it). */
function clockAt(now: string) {
  const readyz = readExample<ReadyzResponse>('readyz_response.live.json');
  return vi.spyOn(api, 'readyz').mockResolvedValue({ ...readyz, clock: { ...readyz.clock, kind: 'fixed', now } });
}

async function openCase(returnId: string) {
  const view = renderApp(`/returns/${returnId}`);
  await screen.findByRole('heading', { level: 1 });
  await waitFor(() => expect(document.querySelector('[data-action-bar]')).not.toBeNull());
  return view;
}

const sheet = (name: string) => document.querySelector<HTMLElement>(`[data-sheet="${name}"]`) as HTMLElement;
const confirmButton = (name: string) => document.querySelector<HTMLButtonElement>(`[data-sheet-confirm="${name}"]`) as HTMLButtonElement;
const action = (name: string) => document.querySelector<HTMLButtonElement>(`[data-action="${name}"]`) as HTMLButtonElement;

describe('past the refund deadline (ADR-15 sections 19, 20)', () => {
  it('Marco: a banner says the refund is overdue and owed; approving is off, with the reason', async () => {
    clockAt('2026-10-20T10:00:00Z');
    const { container } = await openCase('RET-000102');
    const banner = await waitFor(() => {
      const found = document.querySelector('[data-refund-overdue-banner="proposed"]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(banner).toHaveTextContent('Refund overdue · It was due by 12 Oct 2026; the service clock is at 20 Oct 2026.');
    expect(banner).toHaveTextContent('The refund is owed all the same.');
    expect(banner).toHaveTextContent('Rejecting the proposal or setting the standard terms goes on without a message');
    await waitFor(() => expect(action('approve-restriction')).toBeDisabled());
    expect(document.querySelector('[data-action-reason]')).toHaveTextContent(OPERATOR.refund.primaryReason);
    expect(action('reject')).toBeEnabled();
    expect(action('override')).toBeEnabled();
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });

  it('no banner before the deadline, and never on the guarantee path, which states no deadline (rule 4)', async () => {
    clockAt('2026-10-12T23:00:00Z');
    await openCase('RET-000102');
    await waitFor(() => expect(action('approve-restriction')).toBeEnabled());
    expect(document.querySelector('[data-refund-overdue-banner]')).toBeNull();
  });

  it('Elena (guarantee path): never overdue, however late the clock', async () => {
    clockAt('2027-06-01T10:00:00Z');
    await openCase('RET-000105');
    await waitFor(() => expect(document.querySelector('[data-guarantee-banner]')).not.toBeNull());
    expect(document.querySelector('[data-refund-overdue-banner]')).toBeNull();
    expect(action('confirm')).toBeEnabled();
  });

  it('a late rejection sends no message: the sheet says so, and the notice says the case stays overdue', async () => {
    clockAt('2026-10-20T10:00:00Z');
    const reject = readExample<DecisionResult>('decision_result.reject.json');
    const decide = vi.spyOn(api, 'decide').mockImplementation(async (_returnId, body) => ({
      ...reject,
      decision: {
        ...reject.decision,
        return_id: 'RET-000102',
        decision_id: body.decision_id,
        refund_due_by: '2026-10-12',
        approved_at: '2026-10-20T10:00:00Z',
      },
      outbox_message: null,
    }));
    await openCase('RET-000102');
    await waitFor(() => expect(action('approve-restriction')).toBeDisabled());
    fireEvent.click(action('reject'));
    await waitFor(() => expect(sheet('reject')).not.toBeNull());
    expect(sheet('reject').querySelector('[data-no-message="late"]')).toHaveTextContent(OPERATOR.refund.noMessage);
    expect(within(sheet('reject')).queryByRole('radio', { name: 'Italiano' })).toBeNull();
    fireEvent.change(within(sheet('reject')).getByRole('combobox'), { target: { value: 'customer_context' } });
    fireEvent.change(within(sheet('reject')).getByRole('textbox'), { target: { value: 'The refund is paid by hand today' } });
    fireEvent.click(confirmButton('reject'));
    await waitFor(() => expect(decide).toHaveBeenCalled());
    // The request still carries its message choice, unused by the service (ADR-15 section 19).
    expect(decide.mock.calls[0]?.[1]).toMatchObject({ action: 'reject', message: { lang: 'it', sentence_ids: null } });
    const notice = await waitFor(() => {
      const found = document.querySelector('[data-notice-no-message]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(notice).toHaveTextContent(OPERATOR.result.noMessageLate);
    expect(document.querySelector('[data-action-notice]')).not.toHaveTextContent('MSG-');
  });

  it('a late override can only set the standard terms: the editor is locked and the request has no lever', async () => {
    clockAt('2026-10-20T10:00:00Z');
    const decide = vi.spyOn(api, 'decide');
    await openCase('RET-000102');
    await waitFor(() => expect(action('approve-restriction')).toBeDisabled());
    fireEvent.click(action('override'));
    await waitFor(() => expect(sheet('override')).not.toBeNull());
    const editor = sheet('override').querySelector('[data-lever-editor]') as HTMLElement;
    expect(editor.querySelector('[data-late-lock]')).toHaveTextContent(OPERATOR.sheets.override.lateLocked);
    expect(Array.from(editor.querySelectorAll('input')).every((input) => input.disabled)).toBe(true);
    expect(confirmButton('override')).toHaveTextContent(OPERATOR.sheets.override.submitStandard);
    expect(sheet('override').querySelector('[data-no-message="late"]')).not.toBeNull();
    fireEvent.change(within(sheet('override')).getByRole('combobox', { name: /Category/ }), { target: { value: 'customer_context' } });
    fireEvent.change(within(sheet('override')).getByRole('textbox'), { target: { value: 'The refund is paid by hand today' } });
    fireEvent.click(confirmButton('override'));
    await waitFor(() => expect(decide).toHaveBeenCalled());
    expect(decide.mock.calls[0]?.[1]).toMatchObject({ action: 'override', levers: [] });
  });
});

describe('409 recoveries: the case is loaded again, the message is shown verbatim', () => {
  it('a refund past its deadline (scenario refund_overdue) refuses the approval', async () => {
    act(() => setScenario('refund_overdue'));
    const getCase = vi.spyOn(api, 'getCase');
    await openCase('RET-000102');
    fireEvent.click(action('approve-restriction'));
    await waitFor(() => expect(sheet('approve')).not.toBeNull());
    fireEvent.click(sheet('approve').querySelector('[data-coupon-ack-box]') as HTMLElement);
    const loads = getCase.mock.calls.length;
    fireEvent.click(confirmButton('approve'));
    const message = await within(sheet('approve')).findByText(/the refund of RET-000102 was due by 2026-10-10/);
    expect(message).toHaveAttribute('data-conflict-message');
    expect(message.textContent).toBe(
      'the refund of RET-000102 was due by 2026-10-10 (14 days from the notice, rule 3): it is overdue; no message states a past deadline',
    );
    expect(sheet('approve')).toHaveTextContent(OPERATOR.sheets.nothingSaved);
    expect(sheet('approve')).toHaveTextContent(OPERATOR.sheets.conflict.reloaded);
    await waitFor(() => expect(getCase.mock.calls.length).toBeGreaterThan(loads));
  });

  it('a stale proposal (scenario stale_proposal): the service decided the case again, said verbatim', async () => {
    act(() => setScenario('stale_proposal'));
    await openCase('RET-000102');
    fireEvent.click(action('approve-restriction'));
    await waitFor(() => expect(sheet('approve')).not.toBeNull());
    fireEvent.click(sheet('approve').querySelector('[data-coupon-ack-box]') as HTMLElement);
    fireEvent.click(confirmButton('approve'));
    expect(await within(sheet('approve')).findByText(/was stale \(its review date passed before anyone acted on it\)/)).toHaveAttribute(
      'data-conflict-message',
    );
  });
});

describe('review sheet: lift and renew send no message (ADR-15 section 12)', () => {
  it('an approved restriction before its review date: renew waits for the date, lift is open', async () => {
    await openCase('RET-000077');
    await waitFor(() => expect(action('renew')).toBeDisabled());
    expect(document.querySelector('[data-renew-reason]')).toHaveTextContent('Renewal opens on the review date, 28 Dec 2026');
    expect(action('lift')).toBeEnabled();
    fireEvent.click(action('lift'));
    await waitFor(() => expect(sheet('review')).not.toBeNull());
    const renew = sheet('review').querySelector('[data-renew-choice]') as HTMLInputElement;
    expect(renew).toBeDisabled();
    expect(sheet('review').querySelector('[data-no-message="review"]')).toHaveTextContent(OPERATOR.sheets.review.noMessage);
  });

  it('a lift needs a reason and a category and sends no message', async () => {
    const decide = vi.spyOn(api, 'decide');
    await openCase('RET-000088');
    fireEvent.click(action('review'));
    await waitFor(() => expect(sheet('review')).not.toBeNull());
    expect(confirmButton('review')).toBeDisabled();
    expect(sheet('review').querySelector('[data-confirm-reasons]')).toHaveTextContent(OPERATOR.sheets.reasonTooShort);
    fireEvent.change(within(sheet('review')).getByRole('textbox'), { target: { value: 'Reviewed at the due date: returns back in line' } });
    fireEvent.click(confirmButton('review'));
    await waitFor(() => expect(decide).toHaveBeenCalled());
    expect(decide.mock.calls[0]?.[1]).toMatchObject({
      action: 'lift',
      message: null,
      levers: null,
      override_category: 'periodic_review',
      override_reason: 'Reviewed at the due date: returns back in line',
    });
    const notice = await waitFor(() => {
      const found = document.querySelector('[data-action-notice]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(notice).toHaveTextContent('Lifted by luca'); // the approver of the contract example
    expect(notice.querySelector('[data-notice-no-message]')).toHaveTextContent(OPERATOR.result.noMessageReview);
    expect(notice.querySelector('[data-notice-superseded]')).toHaveTextContent('DEC-000088-1');
  });

  it('from the review date a renewal needs no reason and sends no message (scenario renewed)', async () => {
    act(() => setScenario('renewed'));
    const decide = vi.spyOn(api, 'decide');
    await openCase('RET-000088');
    fireEvent.click(action('review'));
    await waitFor(() => expect(sheet('review')).not.toBeNull());
    const renew = sheet('review').querySelector('[data-renew-choice]') as HTMLInputElement;
    await waitFor(() => expect(renew).toBeEnabled());
    fireEvent.click(renew);
    expect(sheet('review').querySelector('[data-renew-no-reason]')).toHaveTextContent(OPERATOR.sheets.review.renewNoReason);
    expect(within(sheet('review')).queryByRole('textbox')).toBeNull();
    expect(confirmButton('review')).toHaveTextContent(OPERATOR.sheets.review.submitRenew);
    fireEvent.click(confirmButton('review'));
    await waitFor(() => expect(decide).toHaveBeenCalled());
    expect(decide.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({ action: 'renew', message: null, levers: null, override_reason: null, override_category: null, approver: 'sara' }),
    );
    const notice = await waitFor(() => {
      const found = document.querySelector('[data-action-notice]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(notice).toHaveTextContent('Renewed by');
    expect(notice.querySelector('[data-notice-no-message]')).toHaveTextContent(OPERATOR.result.noMessageReview);
  });
});

describe('override editor: a lever of scope all_orders is only ever granted (contracts/levers.py)', () => {
  it('free size exchange, granted by the base terms, cannot be withdrawn; priority size exchange can be granted', async () => {
    await openCase('RET-000108');
    fireEvent.click(action('override'));
    await waitFor(() => expect(sheet('override')).not.toBeNull());
    const row = (id: string) => sheet('override').querySelector(`[data-lever-row="${id}"]`) as HTMLElement;
    const exchange = row('free_size_exchange').querySelector('input') as HTMLInputElement;
    expect(exchange).toBeChecked();
    expect(exchange).toBeDisabled();
    expect(row('free_size_exchange').querySelector('[data-grant-only]')).toHaveTextContent(OPERATOR.sheets.override.grantOnly);
    const priority = row('priority_size_exchange').querySelector('input') as HTMLInputElement;
    expect(priority).toBeEnabled();
    fireEvent.click(priority);
    expect(priority).toBeChecked();
    fireEvent.click(priority);
    expect(priority).not.toBeChecked();
  });
});

describe('the read-only public demo and the cached demo', () => {
  it('read-only (readyz public_read_only): every decision write is off with the read-only notice', async () => {
    act(() => setScenario('read_only'));
    await openCase('RET-000102');
    await waitFor(() => expect(document.querySelector('[data-read-only-banner="public"]')).not.toBeNull());
    expect(document.querySelector('[data-read-only-banner]')).toHaveTextContent('Read-only public demo');
    expect(action('approve-restriction')).toBeDisabled();
    expect(action('override')).toBeDisabled();
    expect(action('reject')).toBeDisabled();
    expect(document.querySelector('[data-action-reason]')).toHaveTextContent('Read-only public demo');
    expect(document.querySelector('[data-regenerate]')).toBeDisabled();
    expect(document.querySelector('[data-add-evidence]')).toBeDisabled();
  });

  it('cached: regenerating is shown unavailable up front (the service answers 503)', async () => {
    act(() => setScenario('cached'));
    const regenerate = vi.spyOn(api, 'regenerate');
    await openCase('RET-000101');
    await waitFor(() => expect(document.querySelector('[data-regenerate-unavailable="cached"]')).not.toBeNull());
    expect(document.querySelector('[data-regenerate-unavailable]')).toHaveTextContent(OPERATOR.explanation.cachedUnavailable);
    expect(document.querySelector('[data-regenerate]')).toBeDisabled();
    expect(regenerate).not.toHaveBeenCalled();
  });
});
