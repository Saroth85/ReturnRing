// The economic simulator in the browser (ADR-04, spec 8.10, US-602): a pure port of
// policy.simulator.net_benefit and sensitivity. Disjoint components, costs negative, euros
// quantized half-up to the cent exactly as the server does (src/sim/decimal.ts). The refund owed
// to the customer is never a saving, so it has no input and no component (CLAUDE.md rule 9).
// Parity: netBenefit.test.ts (ECO-01-UI) against parity/python-parity.json and every example.
import type { BodyOf } from '@/api/client';
import type { NetBenefitView, Provenance, SimulatorAssumptionsResponse } from '@/api/models';
import { add, dec, div, mul, neg, quantizeCents, toNumber, type Dec } from './decimal';

/** The inputs of a PUT: no labels, no refund field (AssumptionInputs of the contract). */
export type AssumptionInputs = BodyOf<'PUT /api/simulator/assumptions'>['assumptions'];
/** A labelled assumption set of a NetBenefitView (version, basis, based_on). */
export type AssumptionSet = NetBenefitView['assumptions'];
export type ComponentId = NetBenefitView['components'][number]['id'];
export type SensitivityId = SimulatorAssumptionsResponse['current']['sensitivity'][number]['id'];
export type AssumptionLabels = Pick<AssumptionSet, 'version' | 'basis' | 'based_on'>;

/** Contract order of the components (policy.simulator.net_benefit). */
export const COMPONENT_IDS = [
  'logistics_costs_avoided',
  'undue_discounts_avoided',
  'time_saved_value',
  'incremental_margin',
  'incentives',
  'system_cost',
  'error_cost',
  'abandonment_margin_lost',
] as const satisfies readonly ComponentId[];

/** Scenarios, not confidence intervals (policy.simulator.sensitivity). */
export const SENSITIVITY_IDS = ['base', 'logistics_halved', 'abandonment_doubled', 'both'] as const satisfies readonly SensitivityId[];

/** Every numeric input, in the order of the contract. */
export const INPUT_KEYS = [
  'logistics_returns_avoided',
  'logistics_cost_per_return_eur',
  'undue_discounts_avoided',
  'avg_undue_discount_eur',
  'minutes_saved',
  'hourly_cost_eur',
  'incentives_eur',
  'system_cost_eur',
  'error_cost_eur',
  'abandonment_margin_lost_eur',
  'incremental_margin_eur',
] as const satisfies readonly (keyof AssumptionInputs)[];
export type InputKey = (typeof INPUT_KEYS)[number];

export interface NetBenefitResult {
  components: { id: ComponentId; amount: number }[];
  total: number;
}

type DecInputs = Record<InputKey, Dec>;

const MINUTES_PER_HOUR = dec(60);
const TWO = dec(2);

function toDec(a: AssumptionInputs): DecInputs {
  return Object.fromEntries(INPUT_KEYS.map((key) => [key, dec(a[key])])) as DecInputs;
}

/** policy.simulator.net_benefit on decimals: each component quantized, then the total. */
function compute(a: DecInputs): NetBenefitResult {
  const eur = (x: Dec) => quantizeCents(x);
  const parts: [ComponentId, Dec][] = [
    ['logistics_costs_avoided', eur(mul(a.logistics_returns_avoided, a.logistics_cost_per_return_eur))],
    ['undue_discounts_avoided', eur(mul(a.undue_discounts_avoided, a.avg_undue_discount_eur))],
    ['time_saved_value', eur(mul(div(a.minutes_saved, MINUTES_PER_HOUR), a.hourly_cost_eur))],
    ['incremental_margin', eur(a.incremental_margin_eur)],
    ['incentives', neg(eur(a.incentives_eur))],
    ['system_cost', neg(eur(a.system_cost_eur))],
    ['error_cost', neg(eur(a.error_cost_eur))],
    ['abandonment_margin_lost', neg(eur(a.abandonment_margin_lost_eur))],
  ];
  const total = eur(parts.reduce((sum, [, amount]) => add(sum, amount), dec(0)));
  return { components: parts.map(([id, amount]) => ({ id, amount: toNumber(amount) })), total: toNumber(total) };
}

export function netBenefit(a: AssumptionInputs): NetBenefitResult {
  return compute(toDec(a));
}

function scenarioDec(a: DecInputs, id: SensitivityId): DecInputs {
  const halved = id === 'logistics_halved' || id === 'both';
  const doubled = id === 'abandonment_doubled' || id === 'both';
  return {
    ...a,
    logistics_cost_per_return_eur: halved ? div(a.logistics_cost_per_return_eur, TWO) : a.logistics_cost_per_return_eur,
    abandonment_margin_lost_eur: doubled ? mul(a.abandonment_margin_lost_eur, TWO) : a.abandonment_margin_lost_eur,
  };
}

/** The inputs of one sensitivity scenario: halved logistics cost, doubled abandonment, or both. */
export function scenarioInputs(a: AssumptionInputs, id: SensitivityId): AssumptionInputs {
  const changed = scenarioDec(toDec(a), id);
  return { ...a, ...Object.fromEntries(INPUT_KEYS.map((key) => [key, toNumber(changed[key])])) };
}

/** The label of a scenario set: `<version>/<scenario>` based on the version; base is the version. */
export function scenarioLabel(version: string, id: SensitivityId): Pick<AssumptionSet, 'version' | 'based_on'> {
  return id === 'base' ? { version, based_on: null } : { version: `${version}/${id}`, based_on: version };
}

export interface ScenarioResult {
  id: SensitivityId;
  inputs: AssumptionInputs;
  result: NetBenefitResult;
}

/** policy.simulator.sensitivity: the four scenarios in contract order. */
export function sensitivity(a: AssumptionInputs): ScenarioResult[] {
  const base = toDec(a);
  return SENSITIVITY_IDS.map((id) => ({ id, inputs: scenarioInputs(a, id), result: compute(scenarioDec(base, id)) }));
}

/** The inputs of a labelled set (drops version, basis and based_on). */
export function inputsOf(set: AssumptionSet | AssumptionInputs): AssumptionInputs {
  const inputs: AssumptionInputs = { period: set.period } as AssumptionInputs;
  for (const key of INPUT_KEYS) inputs[key] = set[key];
  return inputs;
}

/** The amounts of a server NetBenefitView. */
export function resultOf(view: NetBenefitView): NetBenefitResult {
  return {
    components: view.components.map((c) => ({ id: c.id, amount: c.amount.value })),
    total: view.total.value,
  };
}

const sameCents = (a: number, b: number) => Math.round(a * 100) === Math.round(b * 100);

/** Same components in the same order and the same amounts to the cent. */
export function sameResult(a: NetBenefitResult, b: NetBenefitResult): boolean {
  return (
    a.components.length === b.components.length &&
    a.components.every((c, i) => c.id === b.components[i]?.id && sameCents(c.amount, b.components[i]?.amount ?? Number.NaN)) &&
    sameCents(a.total, b.total)
  );
}

/** A contract NetBenefitView for a result computed here, every amount with one provenance. */
export function toView(inputs: AssumptionInputs, labels: AssumptionLabels, result: NetBenefitResult, provenance: Provenance): NetBenefitView {
  return {
    assumptions: { ...inputsOf(inputs), ...labels },
    components: result.components.map((c) => ({ id: c.id, amount: { value: c.amount, unit: 'eur', provenance } })),
    total: { value: result.total, unit: 'eur', provenance },
  };
}
