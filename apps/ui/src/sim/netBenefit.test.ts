// ECO-01-UI (docs/ui-design.md 14.2): the browser port of policy.simulator reproduces the spec 8.10
// example to the cent, the outputs of policy/simulator.py itself (parity/python-parity.json,
// written by parity/gen_parity.py), and every net benefit in the contract examples.
import { describe, expect, it } from 'vitest';
import type { NetBenefitView, SimulatorAssumptionsResponse } from '@/api/models';
import { exampleFiles, readExample } from '@/test/examples';
import {
  COMPONENT_IDS,
  inputsOf,
  netBenefit,
  resultOf,
  sameResult,
  scenarioInputs,
  scenarioLabel,
  SENSITIVITY_IDS,
  sensitivity,
  toView,
  type AssumptionInputs,
  type NetBenefitResult,
} from './netBenefit';
import parity from './parity/python-parity.json';

const SPEC_EXAMPLE: AssumptionInputs = {
  period: 'month, 1,000 return requests (illustrative)',
  logistics_returns_avoided: 80,
  logistics_cost_per_return_eur: 8,
  undue_discounts_avoided: 25,
  avg_undue_discount_eur: 12,
  minutes_saved: 240,
  hourly_cost_eur: 22,
  incentives_eur: 200,
  system_cost_eur: 120,
  error_cost_eur: 100,
  abandonment_margin_lost_eur: 240,
  incremental_margin_eur: 0,
};

const amounts = (r: NetBenefitResult) => Object.fromEntries(r.components.map((c) => [c.id, c.amount]));
/** Python prints "-0.00" for a cost of zero; a JSON number has no negative zero. */
const cents = (text: string) => Number(text) + 0;

describe('netBenefit: the spec 8.10 example (ECO-01)', () => {
  it('gives +368 with the eight disjoint components of the proposal', () => {
    const result = netBenefit(SPEC_EXAMPLE);
    expect(amounts(result)).toEqual({
      logistics_costs_avoided: 640,
      undue_discounts_avoided: 300,
      time_saved_value: 88,
      incremental_margin: 0,
      incentives: -200,
      system_cost: -120,
      error_cost: -100,
      abandonment_margin_lost: -240,
    });
    expect(result.total).toBe(368);
  });

  it('gives the sensitivity scenarios 368, 48, 128 and −192', () => {
    const scenarios = sensitivity(SPEC_EXAMPLE);
    expect(scenarios.map((s) => s.id)).toEqual(['base', 'logistics_halved', 'abandonment_doubled', 'both']);
    expect(scenarios.map((s) => s.result.total)).toEqual([368, 48, 128, -192]);
  });

  it('changes exactly the scenario inputs, nothing else', () => {
    expect(scenarioInputs(SPEC_EXAMPLE, 'logistics_halved')).toEqual({ ...SPEC_EXAMPLE, logistics_cost_per_return_eur: 4 });
    expect(scenarioInputs(SPEC_EXAMPLE, 'abandonment_doubled')).toEqual({ ...SPEC_EXAMPLE, abandonment_margin_lost_eur: 480 });
    expect(scenarioInputs(SPEC_EXAMPLE, 'both')).toEqual({
      ...SPEC_EXAMPLE,
      logistics_cost_per_return_eur: 4,
      abandonment_margin_lost_eur: 480,
    });
    expect(scenarioInputs(SPEC_EXAMPLE, 'base')).toEqual(SPEC_EXAMPLE);
  });

  it('has no refund component and no refund input (rule 9)', () => {
    expect(COMPONENT_IDS.some((id) => id.includes('refund'))).toBe(false);
    expect(Object.keys(SPEC_EXAMPLE).some((key) => key.includes('refund'))).toBe(false);
    const withRefund = { ...SPEC_EXAMPLE, refund_eur: 1000 } as AssumptionInputs;
    expect(netBenefit(withRefund).total).toBe(368);
  });

  it('labels scenarios <version>/<scenario>', () => {
    expect(scenarioLabel('assumptions-v1', 'both')).toEqual({ version: 'assumptions-v1/both', based_on: 'assumptions-v1' });
    expect(scenarioLabel('assumptions-v1', 'base')).toEqual({ version: 'assumptions-v1', based_on: null });
  });
});

interface ParityCase {
  name: string;
  inputs: AssumptionInputs;
  net_benefit: { components: Record<string, string>; total: string };
  sensitivity: Record<string, { components: Record<string, string>; total: string }>;
}

describe('netBenefit and sensitivity: parity with policy/simulator.py', () => {
  const cases = (parity as { cases: ParityCase[] }).cases;

  it('has the edge cases and the random cases', () => {
    expect(cases.length).toBeGreaterThan(100);
  });

  it.each(cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const expectResult = (result: NetBenefitResult, python: ParityCase['net_benefit']) => {
      expect(result.components.map((x) => x.id)).toEqual(Object.keys(python.components));
      expect(amounts(result)).toEqual(Object.fromEntries(Object.entries(python.components).map(([k, v]) => [k, cents(v)])));
      expect(result.total).toBe(cents(python.total));
    };
    expectResult(netBenefit(c.inputs), c.net_benefit);
    for (const scenario of sensitivity(c.inputs)) {
      const python = c.sensitivity[scenario.id];
      expect(python, scenario.id).toBeDefined();
      expectResult(scenario.result, python as ParityCase['net_benefit']);
    }
  });
});

/** Every NetBenefitView in an example: objects with assumptions, components and total. */
function netBenefitViews(value: unknown, path: string, out: [string, NetBenefitView][] = []): [string, NetBenefitView][] {
  if (Array.isArray(value)) value.forEach((v, i) => netBenefitViews(v, `${path}[${i}]`, out));
  else if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    if ('assumptions' in record && 'components' in record && 'total' in record) out.push([path, record as unknown as NetBenefitView]);
    for (const [key, child] of Object.entries(record)) netBenefitViews(child, `${path}.${key}`, out);
  }
  return out;
}

describe('netBenefit: every net benefit in the contract examples', () => {
  const views = exampleFiles().flatMap((file) => netBenefitViews(readExample(file), file));

  it('finds the simulator, KPI, case card and decision record net benefits', () => {
    const files = new Set(views.map(([path]) => path.split('.json')[0]));
    expect(files).toContain('simulator_assumptions.v1');
    expect(files).toContain('simulator_assumptions.v2');
    expect(files).toContain('kpi');
    expect(files).toContain('case_card.marco');
    expect(files).toContain('decision_record.chiara_v1');
  });

  it.each(views)('%s', (_path, view) => {
    expect(view.components.map((c) => c.id)).toEqual([...COMPONENT_IDS]);
    expect(sameResult(netBenefit(inputsOf(view.assumptions)), resultOf(view))).toBe(true);
  });
});

describe('sensitivity: the server scenarios of each stored version', () => {
  it.each(['simulator_assumptions.v1.json', 'simulator_assumptions.v2.json'])('%s', (file) => {
    const response = readExample<SimulatorAssumptionsResponse>(file);
    for (const version of response.history) {
      const local = sensitivity(inputsOf(version.net_benefit.assumptions));
      expect(local.map((s) => s.id)).toEqual(version.sensitivity.map((s) => s.id));
      local.forEach((scenario, i) => {
        const server = version.sensitivity[i]!.net_benefit;
        expect(sameResult(scenario.result, resultOf(server))).toBe(true);
        expect(inputsOf(server.assumptions)).toEqual(scenario.inputs);
        expect(server.assumptions.version).toBe(scenarioLabel(version.version, scenario.id).version);
        expect(server.assumptions.based_on).toBe(scenarioLabel(version.version, scenario.id).based_on);
      });
    }
    expect(SENSITIVITY_IDS).toEqual(['base', 'logistics_halved', 'abandonment_doubled', 'both']);
  });

  it('reproduces v2: +248 and its scenarios', () => {
    const v2 = readExample<SimulatorAssumptionsResponse>('simulator_assumptions.v2.json').current;
    expect(sensitivity(inputsOf(v2.net_benefit.assumptions)).map((s) => s.result.total)).toEqual([248, -12, 8, -252]);
  });
});

describe('toView', () => {
  it('builds a contract NetBenefitView with one provenance on every amount', () => {
    const provenance = { origin: 'Computed in your browser', period_start: '2026-10-01', period_end: '2026-10-31', nature: 'simulated' as const };
    const view = toView(SPEC_EXAMPLE, { version: 'assumptions-v1/both', basis: 'simulated', based_on: 'assumptions-v1' }, netBenefit(SPEC_EXAMPLE), provenance);
    expect(view.total).toEqual({ value: 368, unit: 'eur', provenance });
    expect(view.components.every((c) => c.amount.provenance === provenance && c.amount.unit === 'eur')).toBe(true);
    expect(view.assumptions.version).toBe('assumptions-v1/both');
    expect(inputsOf(view.assumptions)).toEqual(SPEC_EXAMPLE);
  });
});
