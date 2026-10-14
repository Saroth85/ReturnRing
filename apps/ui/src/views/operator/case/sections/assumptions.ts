// Assumption labels (docs/ui-design.md 5.6 AssumptionLabel, 11.3 parseAssumptionLabel): the label
// of an assumption set says what it was computed for. Operator-local until the shared economics
// component exists (open issue); unknown patterns show the raw label.

export type AssumptionLabel =
  | { kind: 'decision'; decisionId: string; alternative: string; raw: string }
  | { kind: 'kpi'; week: string; raw: string }
  | { kind: 'scenario'; version: string; scenario: string; raw: string }
  | { kind: 'raw'; raw: string };

const DECISION = /^(DEC-[A-Za-z0-9-]+)\/([a-z_]+)$/;
const KPI = /^kpi\/(\d{4}-\d{2}-\d{2})$/;
const SCENARIO = /^([A-Za-z0-9.-]+)\/([A-Za-z0-9_.-]+)$/;

export function parseAssumptionLabel(label: string): AssumptionLabel {
  const decision = DECISION.exec(label);
  if (decision) return { kind: 'decision', decisionId: decision[1] ?? '', alternative: decision[2] ?? '', raw: label };
  const kpi = KPI.exec(label);
  if (kpi) return { kind: 'kpi', week: kpi[1] ?? '', raw: label };
  const scenario = SCENARIO.exec(label);
  if (scenario) return { kind: 'scenario', version: scenario[1] ?? '', scenario: scenario[2] ?? '', raw: label };
  return { kind: 'raw', raw: label };
}
