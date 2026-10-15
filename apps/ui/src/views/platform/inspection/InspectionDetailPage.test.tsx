// Warehouse inspection on the contract examples (US-501..503, docs/ui-design.md 9.2).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { renderApp } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';

afterEach(() => act(() => setPersona('sara')));

/**
 * The photo of a request example: the mock routes on it (docs/ui-design.md 11.5). The awaiting
 * example carries a photo the mock vision reads as damaged, the manual review one a photo it reads
 * as unclear (contract 1.6.0).
 */
function examplePhoto(returnId: string): File {
  const file = returnId === 'RET-000111' ? 'inspection_create_request.manual_review.json' : 'inspection_create_request.json';
  const { photo } = readExample<{ photo: { data_base64: string } }>(file);
  const bytes = Uint8Array.from(atob(photo.data_base64), (c) => c.charCodeAt(0));
  return new File([bytes], 'item.jpg', { type: 'image/jpeg' });
}

async function sendPhoto(returnId: string) {
  act(() => setPersona('ahmed'));
  renderApp(`/inspection/${returnId}`);
  await screen.findByText(returnId, {});
  const input = document.querySelector('[data-photo-input]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [examplePhoto(returnId)] } });
  const send = await screen.findByRole('button', { name: 'Suggest a checklist from the photo' });
  await waitFor(() => expect(send).toBeEnabled());
  fireEvent.click(send);
  await waitFor(() => expect(document.querySelector('[data-checklist-row]')).not.toBeNull());
}

const row = (item: string) => document.querySelector(`[data-checklist-row="${item}"]`) as HTMLElement;
const answer = (item: string, label: 'Yes' | 'No' | 'Not observable') => fireEvent.click(within(row(item)).getByLabelText(label));

describe('checklist (US-501)', () => {
  it('shows each suggestion as a hint with its declared confidence, never as an answer', async () => {
    await sendPhoto('RET-000110');
    for (const item of ['tag_attached', 'visible_damage', 'photo_quality']) {
      expect(row(item).getAttribute('data-answer')).toBe('none');
      expect(within(row(item)).getAllByRole('radio').every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    }
    expect(row('tag_attached').textContent).toContain('Suggested: no · No tag visible');
    expect(row('tag_attached').querySelector('[data-confidence]')?.textContent).toBe(
      'model confidence 0.78 · declared by the model, not calibrated',
    );
    expect(document.querySelector('[role="progressbar"], progress, meter')).toBeNull();
  });

  it('accepts "not observable" and the Y, N, U keys on a focused row', async () => {
    await sendPhoto('RET-000110');
    const radios = within(row('tag_attached')).getAllByRole('radio');
    fireEvent.keyDown(radios[0] as HTMLElement, { key: 'u' });
    expect(row('tag_attached').getAttribute('data-answer')).toBe('not_observable');
    fireEvent.keyDown(radios[0] as HTMLElement, { key: 'N' });
    expect(row('tag_attached').getAttribute('data-answer')).toBe('no');
  });
});

describe('proposal and confirmation (US-502)', () => {
  it('holds only after Ahmed confirms the damage, starts off, and the claim comes back with its message', async () => {
    await sendPhoto('RET-000110');
    expect(document.querySelector('[data-proposal="pending"]')).not.toBeNull();
    answer('tag_attached', 'No');
    answer('visible_damage', 'No');
    answer('photo_quality', 'Yes');
    expect(document.querySelector('[data-proposal-contradicted]')?.textContent).toBe('No claim: visible damage not confirmed.');
    answer('visible_damage', 'Yes');
    const apply = document.querySelector('[data-apply-compensation]') as HTMLInputElement;
    expect(apply.checked).toBe(false);
    fireEvent.click(apply);
    const confirm = screen.getByRole('button', { name: /Confirm the observations/ });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    const result = await waitFor(
      () => {
        const el = document.querySelector('[data-inspection-result]');
        expect(el).not.toBeNull();
        return el as HTMLElement;
      },
    );
    expect(result.getAttribute('data-inspection-result')).toBe('claim');
    expect(result.querySelector('[data-claim]')?.textContent).toContain('€17.98');
    expect(result.querySelector('[data-claim]')?.textContent).toContain('DEC-000110-1');
    expect(result.querySelector('[data-claim]')?.textContent).toContain('MSG-000110-1');
    // The message states the refund (the price paid for the line less the claim) and the deduction.
    expect(result.querySelector('[data-template-field="amount"]')?.textContent).toContain('71,92 €');
    expect(result.querySelector('[data-template-field="deduction"]')?.textContent).toContain('17,98 €');
  });

  it('confirms with Ctrl+Enter but never with a bare Enter', async () => {
    await sendPhoto('RET-000110');
    answer('tag_attached', 'No');
    answer('visible_damage', 'Yes');
    answer('photo_quality', 'Yes');
    fireEvent.click(document.querySelector('[data-apply-compensation]') as HTMLElement);
    fireEvent.keyDown(window, { key: 'Enter' });
    await new Promise((r) => setTimeout(r, 50));
    expect(document.querySelector('[data-inspection-result]')).toBeNull();
    fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(document.querySelector('[data-inspection-result]')).not.toBeNull());
  });

  it('reports a claim the API attached although no compensation was applied (the mock always answers with the claim)', async () => {
    await sendPhoto('RET-000110');
    answer('tag_attached', 'No');
    answer('visible_damage', 'Yes');
    answer('photo_quality', 'Yes');
    fireEvent.click(screen.getByRole('button', { name: /Confirm the observations/ }));
    await waitFor(() => expect(document.querySelector('[data-answer-problem="unexpected_claim"]')).not.toBeNull());
    expect(document.querySelector('[data-inspection-result]')).toBeNull();
  });

  it('waits for every answer before confirming', async () => {
    await sendPhoto('RET-000110');
    answer('tag_attached', 'No');
    expect(screen.getByRole('button', { name: /Confirm the observations/ })).toBeDisabled();
    expect(screen.getByText('Answer every checklist item first.')).toBeInTheDocument();
    expect(document.querySelector('[data-checklist-missing]')?.textContent).toBe('Still to answer: Visible damage, Photo quality is good enough');
  });
});

describe('manual review (US-503)', () => {
  it('stops with the reason: no proposal and no compensation control', async () => {
    await sendPhoto('RET-000111');
    const notice = document.querySelector('[data-manual-review]') as HTMLElement;
    expect(notice.textContent).toContain('The system stopped: verify by hand.');
    expect(notice.textContent).toContain('The photo is not clear enough to check the item: verify by hand');
    expect(document.querySelector('[data-proposal="none"]')).not.toBeNull();
    expect(document.querySelector('[data-apply-compensation]')).toBeNull();
  });

  it('shows an answer for another inspection as a failure (the contract has no manual-review confirm example)', async () => {
    await sendPhoto('RET-000111');
    answer('tag_attached', 'Not observable');
    answer('visible_damage', 'Not observable');
    answer('photo_quality', 'No');
    fireEvent.click(screen.getByRole('button', { name: /Confirm the observations/ }));
    await waitFor(() => expect(document.querySelector('[data-answer-problem="other_inspection"]')).not.toBeNull());
    expect(document.querySelector('[data-inspection-result]')).toBeNull();
  });
});

describe('roles and photos', () => {
  it('lets the operator view the steps with every write disabled and the reason', async () => {
    renderApp('/inspection/RET-000110');
    await screen.findByText('RET-000110', {});
    expect(screen.getByRole('button', { name: 'Suggest a checklist from the photo' })).toBeDisabled();
    expect(screen.getAllByText('This action is for warehouses (operator can view).').length).toBeGreaterThan(0);
    expect((document.querySelector('[data-photo-input]') as HTMLInputElement).disabled).toBe(true);
  });

  it('refuses a file that is not a JPEG, PNG or WebP photo before any upload', async () => {
    act(() => setPersona('ahmed'));
    renderApp('/inspection/RET-000110');
    await screen.findByText('RET-000110', {});
    const input = document.querySelector('[data-photo-input]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['GIF89a'], 'x.gif', { type: 'image/gif' })] } });
    expect(document.querySelector('[data-photo-error]')?.textContent).toContain('not a JPEG, PNG or WebP photo');
    expect(screen.getByRole('button', { name: 'Suggest a checklist from the photo' })).toBeDisabled();
  });

  it('shows the guarantee-path note on a guarantee return', async () => {
    renderApp('/inspection/RET-000105');
    await waitFor(() => expect(document.querySelector('[data-guarantee-note]')).not.toBeNull());
    expect(document.querySelector('[data-guarantee-note]')?.textContent).toContain('no diminished-value claim applies (art. 14(2)');
  });

  it('keeps every number with a provenance and the wording clean through the flow', async () => {
    await sendPhoto('RET-000110');
    answer('tag_attached', 'No');
    answer('visible_damage', 'Yes');
    answer('photo_quality', 'Yes');
    fireEvent.click(document.querySelector('[data-apply-compensation]') as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: /Confirm the observations/ }));
    await waitFor(() => expect(document.querySelector('[data-inspection-result]')).not.toBeNull());
    expect(findUnprovenancedNumbers(document.body)).toEqual([]);
    expect(findBannedWording(document.body).errors).toEqual([]);
  });
});

describe('the list (find the return)', () => {
  it('lists the withdrawal-path returns only, each opening its inspection', async () => {
    renderApp('/inspection');
    await waitFor(() => expect(document.querySelector('[data-inspection-list]')).not.toBeNull());
    const ids = Array.from(document.querySelectorAll('[data-inspection-row]')).map((r) => r.getAttribute('data-inspection-row'));
    expect(ids).toContain('RET-000110');
    expect(ids).toContain('RET-000111');
    expect(ids).not.toContain('RET-000105');
    expect(document.querySelector('[data-inspection-row="RET-000110"]')).toHaveAttribute('href', '/inspection/RET-000110');
  });

  it('searches through the queue query', async () => {
    renderApp('/inspection?q=Valentina');
    await waitFor(() => expect(document.querySelectorAll('[data-inspection-row]')).toHaveLength(1));
  });
});
