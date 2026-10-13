// The simulator screen's state (US-602, ADR-04): a draft of the assumption inputs, the four
// sensitivity scenarios as NetBenefitViews, and the PUT body. While the draft equals the stored
// version, the server's views are shown (and checked against the browser port); once edited, the
// browser computes them with their own provenance: simulated, computed here, same period.
import type { NetBenefitView, Provenance } from '@/api/models';
import {
  INPUT_KEYS,
  inputsOf,
  resultOf,
  sameResult,
  scenarioLabel,
  sensitivity,
  toView,
  type AssumptionInputs,
  type InputKey,
  type SensitivityId,
} from '@/sim';
import { MANAGER } from '../copy';
import type { AssumptionVersion } from '../types';

export type Draft = Record<InputKey, string>;

/** Inputs that count things: whole numbers only (AssumptionInputs: int). */
export const COUNT_KEYS: ReadonlySet<InputKey> = new Set(['logistics_returns_avoided', 'undue_discounts_avoided']);

export function draftOf(version: AssumptionVersion): Draft {
  const a = version.net_benefit.assumptions;
  return Object.fromEntries(INPUT_KEYS.map((key) => [key, String(a[key])])) as Draft;
}

function parse(text: string): number | null {
  const normalised = text.trim().replace(',', '.');
  if (!/^\d+(\.\d+)?$|^\.\d+$/.test(normalised)) return null;
  const value = Number(normalised);
  return Number.isFinite(value) ? value : null;
}

/** Why a field is not a valid input, or null. */
export function fieldError(key: InputKey, text: string): string | null {
  const value = parse(text);
  if (value === null || value < 0) return MANAGER.simulator.form.invalid;
  if (COUNT_KEYS.has(key) && !Number.isInteger(value)) return MANAGER.simulator.form.invalid;
  return null;
}

/** The inputs of a draft; an invalid field keeps the stored value. */
export function inputsFromDraft(draft: Draft, version: AssumptionVersion): AssumptionInputs {
  const stored = inputsOf(version.net_benefit.assumptions);
  const out = { ...stored };
  for (const key of INPUT_KEYS) {
    if (fieldError(key, draft[key]) === null) out[key] = parse(draft[key]) as number;
  }
  return out;
}

export function isDirty(draft: Draft, version: AssumptionVersion): boolean {
  const inputs = inputsFromDraft(draft, version);
  const stored = version.net_benefit.assumptions;
  return INPUT_KEYS.some((key) => inputs[key] !== stored[key]);
}

export interface ScenarioView {
  id: SensitivityId;
  view: NetBenefitView;
  /** server: the stored version's own view; browser: computed here from an edited draft. */
  source: 'server' | 'browser';
  /** The browser port disagrees with the server for the same inputs (the server value is shown). */
  differs: boolean;
}

/** The provenance of a browser-computed net benefit: simulated, computed here, same period. */
export function browserProvenance(version: AssumptionVersion): Provenance {
  const server = version.net_benefit.total.provenance;
  return {
    origin: MANAGER.simulator.localOrigin(version.version),
    period_start: server.period_start,
    period_end: server.period_end,
    nature: 'simulated',
  };
}

export const UNSAVED = 'unsaved';

export function scenarioViews(draft: Draft, version: AssumptionVersion): ScenarioView[] {
  const inputs = inputsFromDraft(draft, version);
  const local = sensitivity(inputs);
  if (!isDirty(draft, version)) {
    return local.map((scenario) => {
      const server = version.sensitivity.find((s) => s.id === scenario.id)?.net_benefit;
      if (!server) {
        const labels = { ...scenarioLabel(version.version, scenario.id), basis: version.net_benefit.assumptions.basis };
        return { id: scenario.id, view: toView(scenario.inputs, labels, scenario.result, browserProvenance(version)), source: 'browser', differs: false };
      }
      return { id: scenario.id, view: server, source: 'server', differs: !sameResult(scenario.result, resultOf(server)) };
    });
  }
  const provenance = browserProvenance(version);
  return local.map((scenario) => {
    const label = scenario.id === 'base' ? UNSAVED : `${UNSAVED}/${scenario.id}`;
    const labels = { version: label, based_on: version.version, basis: version.net_benefit.assumptions.basis };
    return { id: scenario.id, view: toView(scenario.inputs, labels, scenario.result, provenance), source: 'browser', differs: false };
  });
}

/** PUT /api/simulator/assumptions: a new version from the current one, by the acting person. */
export function updateBody(draft: Draft, version: AssumptionVersion, note: string, author: string) {
  return {
    base_version: version.version,
    assumptions: { ...inputsFromDraft(draft, version), period: version.net_benefit.assumptions.period },
    note: note.trim(),
    author,
  };
}

/** "assumptions-v2" after "assumptions-v1", for the Save button (the server names the version). */
export function nextVersionLabel(version: string): string {
  const match = /^(.*?)(\d+)$/.exec(version);
  return match ? `${match[1]}${Number(match[2]) + 1}` : 'a new version';
}
