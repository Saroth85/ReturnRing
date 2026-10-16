// Assumption labels of the contract (ADR-15, docs/ui-design.md 11.3) and the alternatives' net
// benefit: disjoint components that add up to the total, never counting the refund as a saving.
import { describe, expect, it } from 'vitest';
import type { CaseCard } from '@/api/models';
import { parseAssumptionLabel } from '@/components/economics';
import { exampleFiles, readExample } from '@/test/examples';

describe('alternative assumptions', () => {
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
