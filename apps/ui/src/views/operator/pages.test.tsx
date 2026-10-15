// Operator pages on the contract examples: the compare page's default pair from the demo cases
// (GET /api/demo/cases), the decision record's days of the engine inputs (snapshot_on, scored_on:
// rule 11), and the recompute comparison that says when the decision is unchanged (ADR-15 18).
import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { ApiError } from '@/api/errors';
import type { CaseCard, RecomputeResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { renderApp, renderWithProviders } from '@/test/render';
import { findUnprovenancedNumbers } from '@/test/scan';
import { RecomputeComparison } from './case/sheets/RecomputeComparison';
import { OPERATOR } from './copy';
import { noticeSignal } from './compare/signal';

/** A contract example card with the live store's request days and order counts (spec 16). */
function withHistory(file: string, days: number, returned: number, lines: number): CaseCard {
  const card = readExample<CaseCard>(file);
  const o = card.orders;
  return {
    ...card,
    request: { ...card.request, days_since_delivery: days },
    orders: {
      ...o,
      lines: { ...o.lines, value: lines },
      returned_lines: { ...o.returned_lines, value: returned },
      return_rate: { ...o.return_rate, value: Math.round((returned / lines) * 10_000) / 10_000 },
    },
  };
}

async function compareBand(): Promise<HTMLElement> {
  return waitFor(() => {
    const found = document.querySelector('[data-compare-band]');
    expect(found).not.toBeNull();
    return found as HTMLElement;
  });
}

describe('ComparePage', () => {
  it('without ?a and ?b compares the demo cases Giulia and Marco by their ids', async () => {
    renderApp('/compare');
    // demo_cases.json: giulia RET-000101, marco RET-000102.
    await waitFor(() => expect(document.querySelector('[data-compare-column="RET-000101"]')).not.toBeNull());
    expect(document.querySelector('[data-compare-column="RET-000102"]')).not.toBeNull();
    expect(await screen.findByText(OPERATOR.compare.closing)).toBeInTheDocument();
  });

  it('the band is the notice-days signal; the return rate is a row that differs (live values, spec 16)', async () => {
    // The live store: Giulia 4 of 16 lines (25.0%), notice 4 days after delivery; Marco 6 of 7 (85.7%), 3 days.
    const cards: Record<string, CaseCard> = {
      'RET-000101': withHistory('case_card.giulia.json', 4, 4, 16),
      'RET-000102': withHistory('case_card.marco.json', 3, 6, 7),
    };
    vi.spyOn(api, 'getCase').mockImplementation((id: string) => Promise.resolve(cards[id] as CaseCard));
    renderApp('/compare?a=RET-000101&b=RET-000102');
    const band = await compareBand();
    expect([...band.querySelectorAll('[data-compare-days]')].map((e) => e.getAttribute('data-compare-days'))).toEqual(['4', '3']);
    expect(band.querySelector('[data-same-signal]')).toHaveAttribute('data-same-signal', 'same');
    expect(band.querySelector('[data-same-signal]')).toHaveTextContent(OPERATOR.compare.sameSignal);
    // The rates are not in the band and never called the same indicator.
    expect(band.querySelector('[data-compare-rate]')).toBeNull();
    expect(band).not.toHaveTextContent('%');
    await screen.findByRole('heading', { name: OPERATOR.titles.compareSame });
    // Outside presenter mode the rows below the band show at once.
    const rates = [...document.querySelectorAll('[data-compare-rate]')].map((e) => e.textContent ?? '');
    expect(rates).toHaveLength(2);
    expect(rates[0]).toContain('25.0%');
    expect(rates[0]).toContain(OPERATOR.compare.lines('4', '16'));
    expect(rates[1]).toContain('85.7%');
    expect(rates[1]).toContain(OPERATOR.compare.lines('6', '7'));
    expect(document.querySelector('[data-compare-closing]')).toHaveTextContent(OPERATOR.compare.closing);
  });

  it('names the difference when the two returns do not share the signal', async () => {
    const cards: Record<string, CaseCard> = {
      'RET-000101': withHistory('case_card.giulia.json', 2, 6, 11),
      'RET-000102': withHistory('case_card.marco.json', 13, 6, 11),
    };
    vi.spyOn(api, 'getCase').mockImplementation((id: string) => Promise.resolve(cards[id] as CaseCard));
    renderApp('/compare?a=RET-000101&b=RET-000102');
    const band = await compareBand();
    expect(band.querySelector('[data-same-signal]')).toHaveAttribute('data-same-signal', 'different');
    expect(band.querySelector('[data-same-signal]')).toHaveTextContent(OPERATOR.compare.differentSignal);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(OPERATOR.titles.compare);
    // Outside presenter mode the rows below the band show at once.
    expect(document.querySelector('[data-compare-closing]')).toHaveTextContent(OPERATOR.compare.closingDifferent);
    expect(document.body).not.toHaveTextContent(OPERATOR.compare.closing);
  });

  it('without demo cases (no reset yet) asks for two returns and shows the service message verbatim', async () => {
    vi.spyOn(api, 'demoCases').mockRejectedValue(
      new ApiError({ status: 404, code: 'not_found', message: 'no demo reset yet: run python -m apps.api.reset' }),
    );
    renderApp('/compare');
    const pick = await waitFor(() => {
      const found = document.querySelector('[data-compare-pick]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(pick).toHaveTextContent(OPERATOR.compare.noDemoCases);
    expect(pick.querySelector('[data-verbatim]')).toHaveTextContent('no demo reset yet: run python -m apps.api.reset');
    expect(screen.queryByRole('link', { name: OPERATOR.compare.pitchPair })).toBeNull();
  });
});

describe('noticeSignal', () => {
  const req = (days: number) => ({ request: { days_since_delivery: days } }) as Pick<CaseCard, 'request'>;
  it('is the same signal only when both returns are within the soon-after-delivery days', () => {
    expect(noticeSignal(req(4), req(3))).toEqual({ days: [4, 3], same: true });
    expect(noticeSignal(req(7), req(0)).same).toBe(true);
    expect(noticeSignal(req(8), req(3)).same).toBe(false);
    expect(noticeSignal(req(3), req(13)).same).toBe(false);
  });
});

describe('DecisionRecordPage', () => {
  it('dates the engine inputs: the customer snapshot and the day the risk was scored (rule 11)', async () => {
    renderApp('/returns/RET-000102/versions/DEC-000102-1');
    const record = await waitFor(() => {
      const found = document.querySelector('[data-decision-record]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(record).toHaveTextContent(`${OPERATOR.record.snapshotOn}1 Sep 2026`);
    expect(record).toHaveTextContent(`${OPERATOR.record.scoredOn}28 Sep 2026`);
  });

  it('a guarantee case has no risk scored and no refund deadline', async () => {
    renderApp('/returns/RET-000105/versions/DEC-000105-1');
    const record = await waitFor(() => {
      const found = document.querySelector('[data-decision-record]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(record).toHaveTextContent(`${OPERATOR.record.scoredOn}${OPERATOR.record.notScored}`);
    expect(record).not.toHaveTextContent(OPERATOR.record.refundBy);
  });
});

describe('RecomputeComparison', () => {
  it('says "decision unchanged" when the quadrant and the levers are the same', () => {
    const recompute = readExample<RecomputeResponse>('recompute_response.rossi_card.json');
    renderWithProviders(<RecomputeComparison recompute={recompute} alsoRecomputed={[]} versions={2} onOpenVersion={() => undefined} />);
    const line = document.querySelector('[data-decision-unchanged]') as HTMLElement;
    expect(line).toHaveAttribute('data-decision-unchanged', 'true');
    expect(line).toHaveTextContent(OPERATOR.recompute.unchanged);
  });

  it('says the decision changed when the quadrant moved', () => {
    const recompute = readExample<RecomputeResponse>('recompute_response.rossi_card.json');
    const moved: RecomputeResponse = { ...recompute, after: { ...recompute.after, quadrant: 'Q1' } };
    renderWithProviders(<RecomputeComparison recompute={moved} alsoRecomputed={[]} versions={2} onOpenVersion={() => undefined} />);
    expect(document.querySelector('[data-decision-unchanged]')).toHaveAttribute('data-decision-unchanged', 'false');
  });
});

describe('the case card of a product issue', () => {
  it("opens the anomaly with the case's return, so a catalogue task can name it (Elena)", async () => {
    renderApp('/returns/RET-000105');
    const link = await waitFor(() => {
      const found = document.querySelector('[data-open-in-products]');
      expect(found).not.toBeNull();
      return found as HTMLAnchorElement;
    });
    expect(link.getAttribute('href')).toMatch(/^\/products\/[^?]+\?return=RET-000105$/);
  });
});

describe('OutboxPage', () => {
  it('shows the refund a message states as a figure with its provenance (rule 11, ADR-15 section 19)', async () => {
    renderApp('/outbox');
    const row = await waitFor(() => {
      const found = document.querySelector('[data-outbox-row="MSG-000110-1"] button');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    row.click();
    const detail = await waitFor(() => {
      const found = document.querySelector('[data-outbox-detail="MSG-000110-1"]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    const stated = detail.querySelector('[data-refund-stated]') as HTMLElement;
    expect(stated).not.toBeNull();
    expect(stated.querySelector('[data-figure][data-unit="eur"]')).toHaveAttribute('data-nature', 'synthetic');
    expect(stated).toHaveTextContent('€71.92');
    expect(findUnprovenancedNumbers(detail)).toEqual([]);
  });

  it('a message without a refund states none', async () => {
    renderApp('/outbox');
    const detail = await waitFor(() => {
      const found = document.querySelector('[data-outbox-detail]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(detail.getAttribute('data-outbox-detail')).toBe('MSG-000107-1');
    expect(detail.querySelector('[data-refund-stated]')).toBeNull();
  });
});
