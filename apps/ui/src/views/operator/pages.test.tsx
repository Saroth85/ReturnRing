// Operator pages on the contract examples: the compare page's default pair from the demo cases
// (GET /api/demo/cases), the decision record's days of the engine inputs (snapshot_on, scored_on:
// rule 11), and the recompute comparison that says when the decision is unchanged (ADR-15 18).
import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { ApiError } from '@/api/errors';
import type { RecomputeResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { renderApp, renderWithProviders } from '@/test/render';
import { RecomputeComparison } from './case/sheets/RecomputeComparison';
import { OPERATOR } from './copy';

describe('ComparePage', () => {
  it('without ?a and ?b compares the demo cases Giulia and Marco by their ids', async () => {
    renderApp('/compare');
    // demo_cases.json: giulia RET-000101, marco RET-000102.
    await waitFor(() => expect(document.querySelector('[data-compare-column="RET-000101"]')).not.toBeNull());
    expect(document.querySelector('[data-compare-column="RET-000102"]')).not.toBeNull();
    expect(await screen.findByText(OPERATOR.compare.closing)).toBeInTheDocument();
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
