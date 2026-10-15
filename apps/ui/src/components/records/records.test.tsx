// Records (docs/ui-design.md 5.7, US-604, US-607): human actions name a person, a restrictive
// approval shows its review date, the audit diff marks what changed, coupon pauses are reversible
// with a reason, and lapses come from server fields only (T-SERVER-STATE).
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AUDIT_ACTIONS } from '@/api/contract.gen';
import type { AuditEntry, AuditPage, CouponRulesResponse } from '@/api/models';
import { setPersona } from '@/app/persona';
import { readExample } from '@/test/examples';
import { renderWithProviders } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';
import {
  ActorBadge,
  auditCsvFilename,
  AuditDiff,
  AuditEntryList,
  CouponApplicationsTable,
  entityHref,
  HUMAN_ONLY_ACTIONS,
  keepHumanRows,
  leverText,
  matchesAuditFilters,
  RECORDS,
  restrictiveApproval,
  sinceOf,
  toAuditQuery,
  toLapseVM,
  untilOf,
} from './index';

const audit = readExample<AuditPage>('audit_page.json');
const coupons = readExample<CouponRulesResponse>('coupon_rules.json');
const entry = (action: AuditEntry['action']) => audit.items.find((e) => e.action === action)!;

afterEach(() => act(() => setPersona('sara')));

describe('audit helpers', () => {
  it('labels every audit action of the contract', () => {
    for (const action of AUDIT_ACTIONS) expect(RECORDS.action[action], action).toBeTruthy();
    for (const action of HUMAN_ONLY_ACTIONS) expect(AUDIT_ACTIONS).toContain(action);
  });

  it('finds the restrictive approval with its review date (rule 5)', () => {
    expect(restrictiveApproval(entry('decision_approved'))).toEqual({ reviewAt: '2026-12-27' });
    expect(restrictiveApproval(entry('decision_rejected'))).toBeNull();
  });

  it('links each entity by URL', () => {
    expect(entityHref(entry('decision_approved'))).toBe('/returns/RET-000102/versions/DEC-000102-1');
    expect(entityHref(entry('assumptions_updated'))).toBe('/simulator');
    expect(entityHref(entry('catalog_task_opened'))).toBe('/products/tasks');
    expect(entityHref(entry('inspection_confirmed'))).toBe('/inspection/RET-000110');
    expect(entityHref(entry('link_disputed'))).toBe('/returns/RET-000106?link=C-10233~C-20871');
  });

  it('filters entries like the API, plus human actions only', () => {
    const humans = audit.items.filter((e) => matchesAuditFilters(e, { humanOnly: true }));
    expect(humans.every((e) => e.actor_role !== 'system')).toBe(true);
    expect(audit.items.filter((e) => matchesAuditFilters(e, { actor: 'luca' })).map((e) => e.entry_id)).toEqual([62, 61, 60, 59, 51, 36, 23, 6, 5, 3]);
    const day = audit.items.filter((e) => matchesAuditFilters(e, { since: sinceOf('2026-09-30'), until: untilOf('2026-09-30') }));
    expect(day.every((e) => e.at.startsWith('2026-09-30'))).toBe(true);
    expect(day.length).toBe(audit.items.filter((e) => e.at.startsWith('2026-09-30')).length);
    expect(day.length).toBeGreaterThan(0);
    expect(toAuditQuery({ actor: 'luca', action: [], humanOnly: true })).toEqual({ actor: 'luca' });
  });

  it('names the CSV after the filters', () => {
    expect(auditCsvFilename({})).toBe('returnring-audit-all.csv');
    expect(auditCsvFilename({ return_id: 'RET-000102', action: ['decision_approved'], humanOnly: true })).toBe(
      'returnring-audit-RET-000102-decision_approved-human.csv',
    );
  });

  it('keeps only the human rows of a CSV export when asked', () => {
    const csv = 'entry_id,at,actor,actor_role,action\n1,t,system,system,decision_proposed\n3,t,sara,operator,decision_approved\n';
    expect(keepHumanRows(csv)).toBe('entry_id,at,actor,actor_role,action\n3,t,sara,operator,decision_approved\n');
  });

  it('reads lever ids as terms', () => {
    expect(leverText('return_window_days:14')).toMatchObject({ label: 'Return window 14 days (legal minimum)', restrictive: true });
    expect(leverText('return_window_days:60')).toMatchObject({ restrictive: false });
    expect(leverText('cluster_coupons_disabled:true')).toMatchObject({ label: 'Coupons paused for the linked accounts', restrictive: true });
    expect(leverText('mystery:1').label).toBe('mystery:1');
  });
});

describe('toLapseVM (server fields only)', () => {
  it('knows nothing without the pending fields, whatever the clock says', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2027-06-01T00:00:00Z'));
    expect(toLapseVM({ review_at: '2026-12-27' })).toEqual({ state: 'unknown', reviewAt: '2026-12-27' });
    vi.useRealTimers();
  });

  it('reads review_due and in_force when the server sends them', () => {
    expect(toLapseVM({ review_at: '2026-09-27', review_due: true }).state).toBe('lapsed');
    expect(toLapseVM({ review_at: '2026-09-27', in_force: false }).state).toBe('lapsed');
    expect(toLapseVM({ review_at: '2026-12-27', in_force: true }).state).toBe('in_force');
  });
});

describe('ActorBadge', () => {
  it('shows a person with the role, and the system without one', () => {
    renderWithProviders(
      <>
        <ActorBadge actor="sara" actorRole="operator" />
        <ActorBadge actor="system" actorRole="system" action="decision_proposed" />
      </>,
    );
    expect(screen.getByText('sara')).toBeInTheDocument();
    expect(screen.getByText('System')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('flags a human action recorded as the system (integrity)', () => {
    renderWithProviders(<ActorBadge actor="system" actorRole="system" action="decision_approved" />);
    expect(screen.getByRole('alert')).toHaveTextContent('integrity check failed');
  });
});

describe('AuditDiff', () => {
  it('marks what a lift changed, without printing an unprovenanced risk', () => {
    const lift = entry('restriction_lifted');
    const { container } = renderWithProviders(<AuditDiff before={lift.before} after={lift.after} recordHref={entityHref(lift)} />);
    const changed = Array.from(container.querySelectorAll('[data-changed]')).map((r) => r.getAttribute('data-diff-row'));
    expect(changed).toEqual(expect.arrayContaining(['version', 'state', 'levers', 'approval', 'review', 'category']));
    expect(changed).not.toContain('quadrant');
    expect(changed).not.toContain('risk');
    expect(screen.getByText('Refund within the legal terms')).toBeInTheDocument();
    expect(screen.getByText('same as before')).toBeInTheDocument();
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });

  it('shows a recompute as lower risk', () => {
    const recompute = audit.items.find((e) => e.action === 'case_recomputed' && e.entity_id === 'DEC-000106-2')!;
    renderWithProviders(<AuditDiff before={recompute.before} after={recompute.after} />);
    expect(screen.getByText('lower than before')).toBeInTheDocument();
  });

  it('shows a generic payload, a Metric with its provenance and a plain number kept in its record', () => {
    const inspection = entry('inspection_confirmed');
    const { container } = renderWithProviders(<AuditDiff before={inspection.before} after={inspection.after} />);
    expect(screen.getByText('DV-RULE-1')).toBeInTheDocument();
    expect(container.querySelector('[data-figure][data-unit="eur"]')).not.toBeNull();
    expect(findUnprovenancedNumbers(container)).toEqual([]);
    const generation = entry('draft_generated');
    renderWithProviders(<AuditDiff before={generation.before} after={generation.after} />);
    expect(screen.getAllByText(RECORDS.diff.numberHidden).length).toBeGreaterThan(0);
  });
});

describe('AuditEntryList', () => {
  it('shows the human approval pill and the reasons as quotes', () => {
    const { container } = renderWithProviders(<AuditEntryList entries={audit.items} />);
    const approval = container.querySelector('[data-audit-entry="decision_approved"]') as HTMLElement;
    expect(within(approval).getByText('27 Dec 2026')).toBeInTheDocument();
    expect(screen.getByText('The customer asked to keep the standard terms')).toBeInTheDocument();
    expect(findUnprovenancedNumbers(container)).toEqual([]);
    expect(findBannedWording(container).errors).toEqual([]);
  });
});

describe('CouponApplicationsTable', () => {
  it('lists the pauses with review dates and reversals', () => {
    const { container } = renderWithProviders(<CouponApplicationsTable applications={coupons.applications} reversible />);
    expect(container.querySelector('[data-application="CRA-000031"] [data-application-state="confirmed"]')).not.toBeNull();
    expect(screen.getByText('The linked accounts share a pickup point only: application reversed')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Reverse…' })).toHaveLength(1);
    expect(container.querySelector('[data-lapsed]')).toBeNull();
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });

  it('shows a lapse when the server says so', () => {
    const lapsed = { ...coupons.applications[0]!, in_force: false };
    const { container } = renderWithProviders(<CouponApplicationsTable applications={[lapsed]} />);
    expect(container.querySelector('[data-lapsed]')).toHaveTextContent('Lapsed');
  });

  it('reverses a pause with a reason, as the acting person', async () => {
    act(() => setPersona('luca'));
    const done = vi.fn();
    // The contract's reverse example answers for CRA-000027: offer that one as still confirmed.
    const reversed = coupons.applications.find((a) => a.application_id === 'CRA-000027')!;
    const confirmed = { ...reversed, state: 'confirmed', reversed_by: null, reversed_at: null, reversal_reason: null } as typeof reversed;
    renderWithProviders(<CouponApplicationsTable applications={[confirmed]} reversible onReversed={done} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reverse…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reverse the coupon pause' });
    const confirm = within(dialog).getByRole('button', { name: 'Reverse the pause' });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Reason (required)'), { target: { value: 'Shared pickup point only' } });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(done.mock.calls[0]?.[0]).toMatchObject({ state: 'reversed', application_id: 'CRA-000027' });
  });

  it('never reports the reversal of another pause as done (UI7)', async () => {
    act(() => setPersona('luca'));
    const done = vi.fn();
    renderWithProviders(<CouponApplicationsTable applications={coupons.applications} reversible onReversed={done} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reverse…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reverse the coupon pause' });
    fireEvent.change(within(dialog).getByLabelText('Reason (required)'), { target: { value: 'Shared pickup point only' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reverse the pause' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert.textContent).toContain('CRA-000027');
    expect(alert.textContent).toContain('CRA-000031');
    expect(done).not.toHaveBeenCalled();
  });

  it('disables the reversal for the warehouse role, with the reason', () => {
    act(() => setPersona('ahmed'));
    renderWithProviders(<CouponApplicationsTable applications={coupons.applications} reversible />);
    expect(screen.getByRole('button', { name: 'Reverse…' })).toBeDisabled();
    expect(screen.getByText('This action is for operators and managers (warehouse can view).')).toBeInTheDocument();
  });
});
