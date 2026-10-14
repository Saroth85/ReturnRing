// Coupon rules on the contract examples (US-402, US-607; docs/ui-design.md 9.3).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { setPersona } from '@/app/persona';
import { renderApp } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';

afterEach(() => act(() => setPersona('sara')));

async function open() {
  renderApp('/coupons');
  await waitFor(() => expect(document.querySelector('[data-current-rule]')).not.toBeNull());
}

describe('/coupons (US-402)', () => {
  it('shows the current rule verbatim with its approver, the applications and the history', async () => {
    await open();
    expect(document.querySelector('[data-current-rule]')?.getAttribute('data-current-rule')).toBe('coupon-rule-v1');
    expect(document.querySelector('[data-verbatim]')?.textContent).toContain('Welcome coupons and referrals are paused');
    expect(screen.getByText('luca', { selector: 'span' })).toBeInTheDocument();
    expect(document.querySelectorAll('[data-application-state]')).toHaveLength(2);
    expect(document.querySelectorAll('[data-rule-history] tbody tr')).toHaveLength(1);
    expect(findUnprovenancedNumbers(document.body)).toEqual([]);
    expect(findBannedWording(document.body).errors).toEqual([]);
  });

  it('lets only a manager approve a new version, with the reason shown to others', async () => {
    await open();
    const approve = screen.getByRole('button', { name: 'Approve as the new version' });
    expect(approve).toBeDisabled();
    expect(document.querySelector('[data-role-reason]')?.textContent).toContain('managers');
  });

  it('approves coupon-rule-v2 for Luca with Ctrl+Enter (POST, 201)', async () => {
    act(() => setPersona('luca'));
    await open();
    const field = screen.getByRole('textbox', { name: 'Rule text' });
    fireEvent.click(screen.getByRole('button', { name: 'Start from the current rule' }));
    expect((field as HTMLTextAreaElement).value).toContain('Welcome coupons');
    fireEvent.keyDown(field, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(document.querySelector('[data-rule-created="coupon-rule-v2"]')).not.toBeNull());
    expect(document.querySelector('[data-current-rule]')?.getAttribute('data-current-rule')).toBe('coupon-rule-v2');
    const history = document.querySelector('[data-rule-history]') as HTMLElement;
    expect(within(history).getAllByRole('row')).toHaveLength(3);
  });
});
