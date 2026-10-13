import { useQueryClient } from '@tanstack/react-query';
import { useId } from 'react';
import { resetModesSeen } from '@/api/meta';
import { SCENARIOS, setScenario, useScenario } from '@/api/scenario';
import { MODE } from '@/copy/common';

/**
 * Mock-only switch of the X-Mock-Scenario header (docs/ui-design.md 11.5): the contract scenarios
 * (default, cached, stale_decision, invalid_request) and the UI fixtures. `?scenario=` does the same.
 */
export function ScenarioSwitcher() {
  const scenario = useScenario();
  const client = useQueryClient();
  const id = useId();
  return (
    <span className="ml-auto inline-flex items-center gap-1">
      <label htmlFor={id}>{MODE.scenarioLabel}</label>
      <select
        id={id}
        value={scenario}
        onChange={(event) => {
          setScenario(event.target.value);
          resetModesSeen();
          void client.resetQueries();
        }}
        className="rounded-chip border border-border-strong bg-surface px-1 text-caption text-ink-1"
      >
        {SCENARIOS.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    </span>
  );
}
