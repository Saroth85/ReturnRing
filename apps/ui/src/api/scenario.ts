// The mock scenario (X-Mock-Scenario, docs/ui-design.md 11.5): `?scenario=cached` on any URL sets
// it for the tab; the dev ScenarioSwitcher changes it. Ignored by the real API.
import { MOCK_SCENARIOS } from './contract.gen';
import { UI_FIXTURE_SCENARIOS } from './adapters/fixtures';
import { createStore, safeStorage, useStore } from './store';

/**
 * The contract scenarios in their order, then the UI fixtures the contract does not ship
 * (adapters/fixtures.ts). A fixture named like a contract scenario is the contract's: one entry,
 * one key in the switcher.
 */
export function mergeScenarios(contract: readonly string[], fixtures: readonly string[]): string[] {
  return [...new Set([...contract, ...fixtures])];
}

export const SCENARIOS: readonly string[] = mergeScenarios(MOCK_SCENARIOS, UI_FIXTURE_SCENARIOS);
const KEY = 'returnring.mockScenario';

function initialScenario(): string {
  if (typeof window === 'undefined') return 'default';
  const fromUrl = new URLSearchParams(window.location.search).get('scenario');
  if (fromUrl && SCENARIOS.includes(fromUrl)) {
    safeStorage.set('session', KEY, fromUrl);
    return fromUrl;
  }
  const stored = safeStorage.get('session', KEY);
  return stored && SCENARIOS.includes(stored) ? stored : 'default';
}

export const scenarioStore = createStore<string>(initialScenario());

export function setScenario(scenario: string): void {
  if (!SCENARIOS.includes(scenario)) throw new Error(`unknown mock scenario ${scenario}`);
  safeStorage.set('session', KEY, scenario === 'default' ? null : scenario);
  scenarioStore.set(scenario);
}

export const useScenario = () => useStore(scenarioStore);
