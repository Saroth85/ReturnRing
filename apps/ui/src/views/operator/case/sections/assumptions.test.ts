// Assumption labels of the contract (ADR-15, docs/ui-design.md 11.3) and the alternatives' net
// benefit: disjoint components that add up to the total, never counting the refund as a saving.
import { describe, expect, it } from 'vitest';
import type { CaseCard } from '@/api/models';
import { exampleFiles, readExample } from '@/test/examples';
import { parseAssumptionLabel } from './assumptions';

describe('parseAssumptionLabel', () => {
  it('reads decision, KPI and scenario labels, and keeps anything else raw', () => {
    expect(parseAssumptionLabel('DEC-000102-1/proposal')).toEqual({
      kind: 'decision',
      decisionId: 'DEC-000102-1',
      alternative: 'proposal',
      raw: 'DEC-000102-1/proposal',
    });
    expect(parseAssumptionLabel('kpi/2026-09-21')).toMatchObject({ kind: 'kpi', week: '2026-09-21' });
    expect(parseAssumptionLabel('assumptions-v1/logistics_halved')).toMatchObject({
      kind: 'scenario',
      version: 'assumptions-v1',
      scenario: 'logistics_halved',
    });
    expect(parseAssumptionLabel('assumptions-v1')).toEqual({ kind: 'raw', raw: 'assumptions-v1' });
  });

  it('labels every alternative of every case example with its decision and kind', () => {
    for (const file of exampleFiles('case_card.')) {
      const card = readExample<CaseCard>(file);
      for (const alt of card.alternatives) {
        const label = parseAssumptionLabel(alt.net_benefit.assumptions.version);
        expect(label, file).toMatchObject({ kind: 'decision', decisionId: card.decision.decision_id, alternative: alt.kind });
        // The components add up to the total (policy.simulator.net_benefit).
        const sum = alt.net_benefit.components.reduce((s, c) => s + c.amount.value, 0);
        expect(Math.abs(sum - alt.net_benefit.total.value), file).toBeLessThan(0.005);
        expect(alt.net_benefit.total.provenance.nature, file).toBe('simulated');
      }
    }
  });
});
