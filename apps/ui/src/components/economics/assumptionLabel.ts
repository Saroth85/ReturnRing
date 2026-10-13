// Labels of assumption sets (ADR-15 section 2, docs/ui-design.md 11.3 parseAssumptionLabel):
// a stored version (`assumptions-v1`), a decision alternative (`<decision_id>/<kind>`), a KPI
// week (`kpi/<week>`) or a sensitivity scenario (`<version>/<scenario>`). Unknown patterns stay raw.
import { SENSITIVITY_IDS, type SensitivityId } from '@/sim';

export type ParsedAssumptionLabel =
  | { kind: 'stored'; version: string }
  | { kind: 'decision'; decisionId: string; alternative: string; returnId: string | null }
  | { kind: 'kpi'; week: string }
  | { kind: 'scenario'; version: string; scenario: SensitivityId }
  | { kind: 'raw'; label: string };

const DECISION = /^(DEC-([A-Za-z0-9]+)-[A-Za-z0-9]+)\/([a-z_]+)$/;
const KPI = /^kpi\/(\d{4}-\d{2}-\d{2})$/;
const SCENARIO = /^([A-Za-z0-9][A-Za-z0-9._:-]*)\/([a-z_]+)$/;
const STORED = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;

export function parseAssumptionLabel(label: string): ParsedAssumptionLabel {
  const decision = DECISION.exec(label);
  if (decision) {
    const [, decisionId = '', number = '', alternative = ''] = decision;
    // Decision ids name their return: DEC-000102-1 is a version of RET-000102.
    return { kind: 'decision', decisionId, alternative, returnId: number ? `RET-${number}` : null };
  }
  const kpi = KPI.exec(label);
  if (kpi) return { kind: 'kpi', week: kpi[1] ?? '' };
  const scenario = SCENARIO.exec(label);
  if (scenario && (SENSITIVITY_IDS as readonly string[]).includes(scenario[2] ?? '')) {
    return { kind: 'scenario', version: scenario[1] ?? '', scenario: scenario[2] as SensitivityId };
  }
  if (STORED.test(label)) return { kind: 'stored', version: label };
  return { kind: 'raw', label };
}
