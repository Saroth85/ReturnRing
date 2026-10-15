// Operator screens on the contract examples (docs/ui-design.md 6, 14.2): the draft echo check
// (T-ECHO), labelled statements (US-305), the queue rows (US-301), and the operator copy
// (COPY-LINT: no banned word, no number literal).
import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { CaseCard, QueueResponse } from '@/api/models';
import { findBannedTerms, UI_COPY_BANNED } from '@/copy/bannedTerms';
import { readExample } from '@/test/examples';
import { renderWithProviders } from '@/test/render';
import { findBannedWording, findUnprovenancedNumbers } from '@/test/scan';
import { CaseUiContext, type CaseUi } from './case/context';
import { ExplanationSection } from './case/sections/ExplanationSection';
import { RiskSection } from './case/sections/RiskSection';
import { OPERATOR } from './copy';
import { QueueTable } from './queue/QueueTable';
import { resetOverlays } from './state/caseOverlay';

const card = (name: string) => readExample<CaseCard>(`case_card.${name}.json`);
const allowed = { allowed: true, reason: null };

function ui(c: CaseCard): CaseUi {
  return {
    card: c,
    openSheet: () => undefined,
    selectedLinkId: null,
    selectLink: () => undefined,
    selectRef: () => undefined,
    sendLang: 'it',
    setSendLang: () => undefined,
    gates: {
      decide: allowed,
      evidence: allowed,
      dispute: allowed,
      regenerate: allowed,
      decideNote: null,
      evidenceNote: null,
      regenerateNote: null,
    },
    sentMessage: null,
    draftPending: false,
  };
}

function renderInCase(c: CaseCard, node: React.ReactNode) {
  return renderWithProviders(<CaseUiContext.Provider value={ui(c)}>{node}</CaseUiContext.Provider>);
}

afterEach(() => resetOverlays());

describe('ExplanationSection', () => {
  it('labels statements observed, score contribution and to verify, each citing its figure', () => {
    const marco = card('marco');
    const { container } = renderInCase(marco, <ExplanationSection card={marco} draftTimedOut={false} onReload={() => undefined} />);
    expect(screen.getByText('Generated explanation and message · cannot change the decision')).toBeInTheDocument();
    expect(container.querySelector('[data-statement-group="OBSERVED"]')).not.toBeNull();
    expect(container.querySelector('[data-statement-group="SCORE"]')).not.toBeNull();
    expect(container.querySelector('[data-statement-group="HYPOTHESIS"]')).not.toBeNull();
    const citations = Array.from(container.querySelectorAll('[data-fact-citation]')).map((c) => c.textContent);
    // The SCORE statement cites the risk score (dated the day it was scored, contract 1.8.0) and a
    // cluster fact: each with its entity, metric, value, period and nature.
    expect(citations[1]).toContain('C-MARCO-1');
    expect(citations[1]).toContain('· risk_score · 0.87 · as of 28 Sep 2026');
    expect(citations[1]).toContain('CL-000417');
    expect(citations[1]).toContain('· welcome_coupons_per_account · 1 · as of 1 Sep 2026');
    expect(citations[1]).toContain('Synthetic');
    expect(citations[2]).toContain('no figure cited');
    // The values are plain numbers under the draft's provenance, with a footer (rule 11).
    const scope = container.querySelector('[data-provenance-scope][data-covers="' + OPERATOR.explanation.factsCover + '"]') as HTMLElement;
    expect(scope).toHaveAttribute('data-has-footer');
    expect(scope.querySelectorAll('[data-fact] [data-plain-number]').length).toBeGreaterThan(0);
    expect(scope.querySelector('[data-provenance-footer]')).toHaveTextContent(marco.draft!.provenance.origin);
    // The facts are dated with the customer snapshot and the day the risk was scored (1.7.0, 1.8.0).
    expect(container.querySelector('[data-fact-dates]')).toHaveTextContent(
      'Facts dated with the customer snapshot of 1 Sep 2026; the risk score with the day it was scored, 28 Sep 2026.',
    );
    expect(container.querySelector('[data-echo="match"]')?.textContent).toContain('6 levers');
    expect(findUnprovenancedNumbers(container)).toEqual([]);
    expect(findBannedWording(container).errors).toEqual([]);
  });

  it('withholds a draft whose echo does not match the engine decision (T-ECHO)', () => {
    const marco = card('marco');
    const forged: CaseCard = { ...marco, draft: { ...marco.draft!, decision_echo: { ...marco.draft!.decision_echo, quadrant: 'Q1' } } };
    const { container } = renderInCase(forged, <ExplanationSection card={forged} draftTimedOut={false} onReload={() => undefined} />);
    expect(screen.getByText('Draft withheld: it does not match the engine decision')).toBeInTheDocument();
    expect(container.querySelector('[data-message-preview]')).toBeNull();
    expect(container.querySelector('[data-template-field]')).toBeNull();
  });

  it('hides a score statement on the guarantee path, which is never scored', () => {
    const elena = card('elena');
    const withScore: CaseCard = {
      ...elena,
      draft: {
        ...elena.draft!,
        explanation: [
          ...elena.draft!.explanation,
          { label: 'SCORE', fact_ids: [], text: 'A forged score', facts: [] },
        ],
      },
    };
    renderInCase(withScore, <ExplanationSection card={withScore} draftTimedOut={false} onReload={() => undefined} />);
    expect(screen.queryByText('A forged score')).toBeNull();
    expect(screen.getByText(OPERATOR.explanation.scoreOnGuarantee)).toBeInTheDocument();
  });

  it('waits for a pending draft and says so', () => {
    const pending: CaseCard = { ...card('giulia'), draft: null, draft_status: 'pending' };
    renderInCase(pending, <ExplanationSection card={pending} draftTimedOut={false} onReload={() => undefined} />);
    expect(screen.getByText(OPERATOR.banners.draftWait)).toBeInTheDocument();
  });
});

describe('RiskSection', () => {
  it('shows contributions to the score, not causes, and both sides of the evidence', () => {
    const marco = card('marco');
    const { container } = renderInCase(marco, <RiskSection card={marco} />);
    expect(screen.getByText('Contributions to the score, not causes')).toBeInTheDocument();
    expect(container.querySelectorAll('[data-factor]')).toHaveLength(3);
    const favourable = container.querySelector('[data-balance="favourable"]') as HTMLElement;
    const unfavourable = container.querySelector('[data-balance="unfavourable"]') as HTMLElement;
    expect(within(favourable).getByText('Kept 5 of 11 items in 12 months')).toBeInTheDocument();
    expect(within(unfavourable).getByText('The linked accounts may be managed together')).toBeInTheDocument();
    expect(container.querySelector('[data-band="high"]')?.textContent).toBe('above the threshold');
    expect(findUnprovenancedNumbers(container)).toEqual([]);
  });

  it('never shows a risk number for the guarantee path or with not enough evidence (US-307, US-309)', () => {
    for (const name of ['elena', 'insufficient']) {
      const c = card(name);
      const { container, unmount } = renderInCase(c, <RiskSection card={c} />);
      expect(container.querySelector('[data-figure]')).toBeNull();
      expect(container.querySelector('[data-not-scored]')).not.toBeNull();
      unmount();
    }
  });
});

describe('QueueTable', () => {
  it('shows outcome, status and figures; an unscored row reads "not scored", never 0', () => {
    const page = readExample<QueueResponse>('queue_response.json');
    const { container } = renderWithProviders(
      <QueueTable page={page} tab="all" selected={0} onSelect={() => undefined} onPrefetch={() => undefined} newIds={new Set()} />,
    );
    const marco = container.querySelector('[data-return-row="RET-000102"]') as HTMLElement;
    expect(marco.querySelector('[data-rail="approval"]')).not.toBeNull();
    expect(marco.querySelector('[data-approval-flag]')).not.toBeNull();
    expect(marco.textContent).toContain('6 changes · restricts future orders');
    expect(marco.textContent).toContain('6 other accounts');
    const elena = container.querySelector('[data-return-row="RET-000105"]') as HTMLElement;
    expect(elena.querySelector('[data-rail="guarantee"]')).not.toBeNull();
    expect(elena.textContent).toContain('not scored');
    expect(elena.textContent).toContain('Product issue');
    const lorenzo = container.querySelector('[data-return-row="RET-000088"]') as HTMLElement;
    expect(lorenzo.querySelector('[data-review-due]')).not.toBeNull();
    expect(container.querySelector('[data-return-row="RET-000109"] [data-rail="insufficient"]')).not.toBeNull();
    expect(findBannedWording(container).errors).toEqual([]);
  });
});

describe('QueueTable refund deadlines (ADR-15 sections 15, 19, 20)', () => {
  it('shows the refund day, never one on the guarantee path, and the server flag refund_overdue', () => {
    const base = readExample<QueueResponse>('queue_response.json');
    const page: QueueResponse = {
      ...base,
      items: base.items.map((i) => (i.return_id === 'RET-000101' ? { ...i, refund_overdue: true } : i)) as QueueResponse['items'],
    };
    const { container } = renderWithProviders(
      <QueueTable page={page} tab="all" selected={0} onSelect={() => undefined} onPrefetch={() => undefined} newIds={new Set()} />,
    );
    const row = (id: string) => container.querySelector(`[data-return-row="${id}"]`) as HTMLElement;
    expect(row('RET-000102').querySelector('[data-refund-due="2026-10-12"]')).toHaveTextContent('12 Oct 2026');
    expect(row('RET-000102').querySelector('[data-refund-overdue]')).toBeNull();
    // Elena: the guarantee path states no refund deadline (rule 4).
    expect(row('RET-000105').querySelector('[data-refund-due="none"]')).toHaveTextContent(OPERATOR.queue.noRefundDeadline);
    expect(row('RET-000105').querySelector('[data-refund-due] time')).toBeNull();
    expect(row('RET-000101').querySelector('[data-refund-overdue]')).toHaveTextContent(OPERATOR.queue.refundOverdue);
    // A deadline is a date, never a number without provenance.
    for (const cell of container.querySelectorAll('[data-refund-due]')) expect(findUnprovenancedNumbers(cell)).toEqual([]);
    expect(screen.getByRole('columnheader', { name: OPERATOR.queue.columns.refundDue })).toBeInTheDocument();
  });
});

describe('operator copy (COPY-LINT)', () => {
  it('uses no banned word and no number literal', () => {
    const strings: string[] = [];
    const collect = (value: unknown): void => {
      if (typeof value === 'string') strings.push(value);
      else if (typeof value === 'function') strings.push(String((value as (...a: unknown[]) => unknown)('x', 'y', 'z', 'w')));
      else if (value && typeof value === 'object') Object.values(value).forEach(collect);
    };
    collect(OPERATOR);
    expect(findBannedTerms(strings.join('\n'), UI_COPY_BANNED)).toEqual([]);
    // Digits come only from named constants (HISTORY_MONTHS, LEGAL_MINIMUM_WINDOW_DAYS, REVIEW_HORIZON_DAYS).
    const literal = strings.filter((s) => /\d/.test(s.replace(/\b(12|14|90)\b/g, '')));
    expect(literal).toEqual([]);
  });
});
