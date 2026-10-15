// Decision components on the contract examples (docs/ui-design.md 5.3): a person, a reason, a
// date (rule 5); restriction state from server fields only (T-SERVER-STATE); terms of this order
// against future orders; statement labels; the decision chain.
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CaseCard, DecisionResult } from '@/api/models';
import { ApproverLine, DecisionChain, LeverList, RestrictionBadge, StateBadge, StatementLabelChip, TermsDiff } from '@/components/decision';
import * as copy from '@/components/decision/copy';
import { findBannedTerms, UI_COPY_BANNED } from '@/copy/bannedTerms';
import { readExample } from '@/test/examples';
import { findUnprovenancedNumbers } from '@/test/scan';

const card = (name: string) => readExample<CaseCard>(`case_card.${name}.json`);
const result = (name: string) => readExample<DecisionResult>(`decision_result.${name}.json`).decision;

describe('RestrictionBadge', () => {
  afterEach(() => vi.useRealTimers());

  it('asks for a person on a proposed restriction, with the review date', () => {
    const { container } = render(<RestrictionBadge decision={card('marco').decision} />);
    expect(container.textContent).toBe("Restrictive · needs a person's approval · review 27 Dec 2026");
    expect(container.querySelector('[data-restriction="needs_approval"]')).toHaveClass('hatch-attn');
  });

  it('shows the lapse the server reports, and nothing else (T-SERVER-STATE)', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2027-03-01T00:00:00Z'));
    const approved = result('marco_approved');
    const { container, rerender } = render(<RestrictionBadge decision={approved} canRenew={false} />);
    expect(container.textContent).toBe('In force for future orders · review 27 Dec 2026');
    rerender(<RestrictionBadge decision={card('lorenzo').decision} canRenew={false} />);
    expect(container.textContent).toBe('Lapsed on 27 Sep 2026: no longer applies to new orders. A person reviews it.');
    rerender(<RestrictionBadge decision={card('lorenzo').decision} canRenew />);
    expect(container.textContent).toContain('Renew or lift.');
  });

  it('names a lift, a Q1 upgrade and no change', () => {
    const { container, rerender } = render(<RestrictionBadge decision={result('lift')} />);
    expect(container.textContent).toBe('Restriction lifted on 30 Sep 2026, 19:10 UTC by luca');
    rerender(<RestrictionBadge decision={card('giulia').decision} />);
    expect(container.textContent).toBe('Applies by itself · logged');
    rerender(<RestrictionBadge decision={card('rossi').decision} />);
    expect(container.textContent).toBe('No change to terms');
  });
});

describe('StateBadge and ApproverLine', () => {
  it('reads the state of each version', () => {
    const { container, rerender } = render(<StateBadge decision={card('marco').decision} versions={1} />);
    expect(container.textContent).toBe('Proposed·v1 of 1');
    rerender(<StateBadge decision={result('marco_approved')} versions={1} />);
    expect(container.textContent).toBe('Approved by sara · 30 Sep 2026, 18:20 UTC·v1 of 1');
    rerender(<StateBadge decision={result('reject')} versions={1} />);
    expect(container.textContent).toContain('Proposal rejected (the return proceeds)');
    rerender(<StateBadge decision={{ ...card('marco').decision, superseded_by: 'DEC-000102-2' }} versions={2} />);
    expect(container.textContent).toContain('Superseded by DEC-000102-2');
  });

  it('names the approver and their role, never for a proposal', () => {
    const { container, rerender } = render(<ApproverLine decision={result('marco_approved')} />);
    expect(container.textContent).toBe('Approved by sara(operator)·30 Sep 2026, 18:20 UTC');
    rerender(<ApproverLine decision={result('lift')} />);
    expect(container.textContent).toContain('Lifted by luca');
    rerender(<ApproverLine decision={card('marco').decision} />);
    expect(container.textContent).toBe('');
  });
});

describe('TermsDiff and LeverList', () => {
  it("shows Marco's order terms against the future terms, with the legal minimum", () => {
    const d = card('marco').decision;
    const { container } = render(
      <TermsDiff
        current={d.current_return_terms}
        future={d.future_terms}
        levers={d.levers}
        snapshotId="TS-005230"
        cluster={{ size: 7, provenance: d.inputs.provenance }}
      />,
    );
    const window = container.querySelector('[data-term-row="return_window_days"]') as HTMLElement;
    expect(window.textContent).toBe('Return window30 days14 days(legal minimum)less generous');
    // The count of linked accounts is data of the account graph: a plain number under the decision
    // inputs' provenance, with its footer, never a constant (rule 11).
    const paused = container.querySelector('[data-term-row="cluster_coupons_disabled"]') as HTMLElement;
    expect(paused.textContent).toContain('paused for7linked accounts');
    const count = paused.querySelector('[data-linked-accounts-count="7"]') as HTMLElement;
    expect(count.closest('[data-constant]')).toBeNull();
    expect(count.closest('[data-provenance-scope]')).toHaveAttribute('data-has-footer');
    expect(count.closest('[data-provenance-scope]')).toHaveAttribute('data-nature', 'synthetic');
    expect(container.querySelector('[data-term-row="free_size_exchange"]')?.getAttribute('data-change')).toBe('same');
    expect(container.querySelectorAll('[data-change="less"][data-term-row]')).toHaveLength(6);
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });

  it('lists only the changes when asked', () => {
    const d = card('giulia').decision;
    const { container } = render(<TermsDiff current={d.current_return_terms} future={d.future_terms} levers={d.levers} onlyChanges />);
    expect(Array.from(container.querySelectorAll('[data-term-row]')).map((r) => r.getAttribute('data-term-row'))).toEqual([
      'return_window_days',
      'priority_size_exchange',
    ]);
  });

  it('labels levers with their scope and marks the restrictive ones', () => {
    const c = card('marco');
    const { container } = render(<LeverList levers={c.decision.levers} base={c.order_terms.base_terms} />);
    expect(container.querySelectorAll('[data-direction="less"]')).toHaveLength(6);
    expect(screen.getByText('Coupons paused for the linked accounts')).toBeInTheDocument();
    expect(screen.getByText('Return window 14 days')).toBeInTheDocument();
    expect(screen.getByText('(legal minimum)')).toBeInTheDocument();
    render(<LeverList levers={[]} />);
    expect(screen.getByText('No change to terms')).toBeInTheDocument();
  });
});

describe('StatementLabelChip and DecisionChain', () => {
  it('labels the three kinds of statement', () => {
    render(
      <>
        <StatementLabelChip label="OBSERVED" />
        <StatementLabelChip label="SCORE" />
        <StatementLabelChip label="HYPOTHESIS" />
      </>,
    );
    expect(screen.getByText('Observed')).toBeInTheDocument();
    expect(screen.getByText('Score contribution')).toBeInTheDocument();
    expect(screen.getByText('To verify')).toBeInTheDocument();
  });

  it('ends with a person: pending, done, or not required', () => {
    const { container, rerender } = render(<DecisionChain decision={card('marco').decision} />);
    expect(container.textContent).toContain('A person approves (pending)');
    rerender(<DecisionChain decision={result('marco_approved')} />);
    expect(container.textContent).toContain('A person approves (approved by sara)');
    rerender(<DecisionChain decision={card('giulia').decision} />);
    expect(container.textContent).toContain('not required: logged');
    rerender(<DecisionChain decision={card('elena').decision} />);
    expect(container.textContent).toContain('Not scored (guarantee path)');
  });
});

describe('decision copy (COPY-LINT)', () => {
  it('uses no banned word and no number literal', () => {
    const strings: string[] = [];
    const collect = (value: unknown): void => {
      if (typeof value === 'string') strings.push(value);
      else if (typeof value === 'function') strings.push(String((value as (...a: unknown[]) => unknown)(7, 9)));
      else if (value && typeof value === 'object') Object.values(value).forEach(collect);
    };
    collect(copy);
    expect(findBannedTerms(strings.join('\n'), UI_COPY_BANNED)).toEqual([]);
    const literals = strings.filter((s) => /\d/.test(s.replace(/[79]/g, '')));
    expect(literals).toEqual([]);
  });
});
