// The scenario list of the dev ScenarioSwitcher (docs/ui-design.md 11.5): one entry per name.
import { describe, expect, it } from 'vitest';
import { UI_FIXTURE_SCENARIOS } from './adapters/fixtures';
import { MOCK_SCENARIOS } from './contract.gen';
import { mergeScenarios, SCENARIOS } from './scenario';

describe('mergeScenarios', () => {
  it('keeps the contract scenarios first and drops a fixture named like one of them', () => {
    const contract = ['default', 'cached', 'conflict', 'empty', 'rate_limited'];
    const fixtures = ['forbidden_role', 'rate_limited', 'conflict', 'empty', 'draft_pending'];
    expect(mergeScenarios(contract, fixtures)).toEqual([
      'default',
      'cached',
      'conflict',
      'empty',
      'rate_limited',
      'forbidden_role',
      'draft_pending',
    ]);
  });

  it('lists every scenario once, the contract ones first', () => {
    expect(new Set(SCENARIOS).size).toBe(SCENARIOS.length);
    expect(SCENARIOS.slice(0, MOCK_SCENARIOS.length)).toEqual([...MOCK_SCENARIOS]);
    for (const fixture of UI_FIXTURE_SCENARIOS) expect(SCENARIOS).toContain(fixture);
  });
});
