// Decision sheets on the whole case page (docs/ui-design.md 6.3, 6.4): every action sends the
// decision it was shown (UI38), restrictive terms are never composed on a not-enough-evidence case
// (UI13, US-307), and a restriction is never approved without a review date (UI16, rule 5).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { CaseCard } from '@/api/models';
import { setPersona } from '@/app/persona';
import { renderApp } from '@/test/render';
import { OPERATOR } from '../../copy';
import { resetOverlays } from '../../state/caseOverlay';

afterEach(() => {
  resetOverlays();
  act(() => setPersona('sara'));
});

/** From the `from`-th load on, the case answers with `change(card)` (a newer version, a field). */
function answerFrom(from: number, change: (card: CaseCard) => CaseCard) {
  const real = api.getCase.bind(api);
  let calls = 0;
  return vi.spyOn(api, 'getCase').mockImplementation(async (...args: Parameters<typeof api.getCase>) => {
    const card = await real(...args);
    calls += 1;
    return calls >= from ? change(card) : card;
  });
}
const answerAfterFirstLoad = (change: (card: CaseCard) => CaseCard) => answerFrom(2, change);

const nextVersion = (card: CaseCard): CaseCard => ({
  ...card,
  decision: {
    ...card.decision,
    decision_id: card.decision.decision_id.replace(/-1$/, '-2'),
    version: card.decision.version + 1,
    supersedes: card.decision.decision_id,
  },
});

async function openCase(returnId: string) {
  const view = renderApp(`/returns/${returnId}`);
  await screen.findByRole('heading', { level: 1 });
  await waitFor(() => expect(document.querySelector('[data-action-bar]')).not.toBeNull());
  return view;
}

const sheet = (name: string) => document.querySelector<HTMLElement>(`[data-sheet="${name}"]`) as HTMLElement;
const confirmButton = (name: string) => document.querySelector<HTMLButtonElement>(`[data-sheet-confirm="${name}"]`) as HTMLButtonElement;

describe('a sheet acts on the decision it was shown (UI38, docs/ui-design.md 6.3 and 6.4)', () => {
  it('blocks the confirm when a newer version arrives, then sends the new id after "Load the new version", keeping the reason', async () => {
    answerAfterFirstLoad(nextVersion);
    const decide = vi.spyOn(api, 'decide');
    await openCase('RET-000107');
    fireEvent.click(document.querySelector('[data-action="reject"]') as HTMLElement);
    await waitFor(() => expect(sheet('reject')).not.toBeNull());
    // Opening the sheet loaded the card again: version 2 is not the version on screen.
    const panel = await within(sheet('reject')).findByText(OPERATOR.sheets.stale.title, {});
    expect(panel.closest('[data-stale-panel]')?.textContent).toContain('DEC-000107-2');
    expect(confirmButton('reject')).toBeDisabled();
    fireEvent.change(within(sheet('reject')).getByRole('textbox'), { target: { value: 'The customer called about the size' } });
    fireEvent.change(within(sheet('reject')).getByRole('combobox'), { target: { value: 'customer_context' } });
    expect(confirmButton('reject')).toBeDisabled();
    fireEvent.click(within(sheet('reject')).getByRole('button', { name: OPERATOR.sheets.stale.load }));
    await waitFor(() => expect(sheet('reject').querySelector('[data-stale-panel]')).toBeNull());
    expect((within(sheet('reject')).getByRole('textbox') as HTMLTextAreaElement).value).toBe('The customer called about the size');
    expect(confirmButton('reject')).toBeEnabled();
    fireEvent.click(confirmButton('reject'));
    await waitFor(() => expect(decide).toHaveBeenCalled());
    expect(decide.mock.calls[0]?.[1]).toMatchObject({ decision_id: 'DEC-000107-2', action: 'reject' });
  });

  it('builds an override from the version shown, never from a newer proposal that arrived meanwhile', async () => {
    answerAfterFirstLoad(nextVersion);
    await openCase('RET-000102');
    fireEvent.click(document.querySelector('[data-action="override"]') as HTMLElement);
    await waitFor(() => expect(sheet('override')).not.toBeNull());
    await within(sheet('override')).findByText(OPERATOR.sheets.stale.title, {});
    expect(within(sheet('override')).getByText(OPERATOR.sheets.staleReason, { exact: false })).toBeInTheDocument();
  });
});

describe('not enough evidence: standard terms only (UI13, US-307)', () => {
  it('locks every lever at the base terms and names the rule', async () => {
    await openCase('RET-000109');
    fireEvent.click(document.querySelector('[data-action="override"]') as HTMLElement);
    await waitFor(() => expect(sheet('override')).not.toBeNull());
    const editor = sheet('override').querySelector('[data-lever-editor]') as HTMLElement;
    const inputs = Array.from(editor.querySelectorAll('input'));
    expect(inputs.length).toBeGreaterThan(0);
    expect(inputs.every((input) => input.disabled)).toBe(true);
    expect(editor.textContent).toContain(OPERATOR.sheets.override.insufficientLocked);
    expect(sheet('override').querySelector('[data-guard-preview]')?.textContent).toContain(OPERATOR.sheets.override.insufficientRule);
  });
});

describe('approval needs a review date (UI16, rule 5)', () => {
  it('disables "Approve restriction" with the reason when the engine gave no review date', async () => {
    answerFrom(1, (card) => ({ ...card, decision: { ...card.decision, review_at: null } }));
    await openCase('RET-000102');
    fireEvent.click(document.querySelector('[data-action="approve-restriction"]') as HTMLElement);
    await waitFor(() => expect(sheet('approve')).not.toBeNull());
    await within(sheet('approve')).findAllByText(OPERATOR.sheets.approve.noReviewDate, { exact: false });
    expect(confirmButton('approve')).toBeDisabled();
  });
});
