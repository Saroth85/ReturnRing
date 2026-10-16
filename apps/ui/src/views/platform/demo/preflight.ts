// Preflight of the pitch (docs/ui-design.md 9.5): green or red, each red line names its fix. Pure
// logic over the API answers, so the page and the tests read the same rules. The golden cases and
// their decisions come from GET /api/demo/cases (ADR-15 section 15): Marco must be proposed at the
// decision the reset made, Rossi's latest decision the one the reset made.
import { compareContract, contractVersionOf } from '@/api/contractVersion';
import { isApiError } from '@/api/errors';
import type { CaseCard, ComponentCheck, DemoCasesResponse, Metric, ReadyzResponse, SimulatorAssumptionsResponse } from '@/api/models';
import { caseOf } from '@/domain/demoCases';
import { PLATFORM } from '../copy';
import { DEMO_ASSUMPTIONS_VERSION, PREFLIGHT_LOAD_TARGET_MS } from '../constants';

export type ExpectedMode = 'live' | 'cached';
export type CheckState = 'pass' | 'fail' | 'pending';

export interface PreflightCheck {
  id: 'adapter' | 'readiness' | 'components' | 'contract' | 'cases' | 'marco' | 'rossi' | 'assumptions' | 'load';
  label: string;
  state: CheckState;
  /** What was found, in words (shown on red lines and as a hint on green ones). */
  found: string | null;
  /** How to make a red line green. */
  fix: string | null;
}

/** An answer of the API as the page holds it: the data, or that the call failed (and why). */
export interface Answer<T> {
  data: T | undefined;
  failed: boolean;
  error?: unknown;
}

export interface PreflightInputs {
  expected: ExpectedMode;
  /** The UI's adapter: on `mock` every answer is a contract example, so the run is never ready. */
  adapter: 'mock' | 'live';
  readyz: Answer<ReadyzResponse>;
  /** GET /api/demo/cases: the golden cases of the reset (404 before any reset). */
  cases: Answer<DemoCasesResponse>;
  /** The case cards of the Marco and Rossi card cases named by `cases`. */
  marco: Answer<CaseCard>;
  rossi: Answer<CaseCard>;
  assumptions: Answer<SimulatorAssumptionsResponse>;
  /** The measured load of the golden cards and the matrix, in milliseconds. */
  load: Answer<Metric>;
}

const C = PLATFORM.demo.checks;
const PENDING: Answer<never> = { data: undefined, failed: false };

function check(
  id: PreflightCheck['id'],
  label: string,
  answer: Answer<unknown>,
  ok: boolean,
  found: string | null,
  fix: string,
): PreflightCheck {
  if (answer.failed) return { id, label, state: 'fail', found: PLATFORM.demo.unreachable, fix };
  if (answer.data === undefined) return { id, label, state: 'pending', found: null, fix: null };
  return { id, label, state: ok ? 'pass' : 'fail', found, fix: ok ? null : fix };
}

/** The API's own words for a failed call (a 404 no_demo_reset names the reset), else "no answer". */
export function failureText(error: unknown): string {
  return isApiError(error) && error.status < 500 ? error.message : PLATFORM.demo.unreachable;
}

/**
 * Whether a component check is fine for the run expected: ok, or off on purpose in a live run on
 * the mock language model (vLLM) or on a CPU (telemetry: no amd-smi sample).
 */
export function componentReady(r: ReadyzResponse, component: ComponentCheck['component']): boolean {
  const status = r.checks.find((k) => k.component === component)?.status;
  if (status === 'ok') return true;
  if (status !== 'disabled') return false;
  if (component === 'vllm') return r.llm_mode === 'mock';
  if (component === 'telemetry') return r.device === 'cpu';
  return false;
}

/** A case check: the case named by the demo cases, then its card. */
function caseCheck(
  id: 'marco' | 'rossi',
  label: string,
  cases: Answer<DemoCasesResponse>,
  decisionId: string | null,
  card: Answer<CaseCard>,
  ok: (card: CaseCard, decisionId: string) => boolean,
  found: (card: CaseCard) => string,
  copy: { fix: string; missing: string },
): PreflightCheck {
  if (cases.failed) return { id, label, state: 'fail', found: PLATFORM.demo.noCases, fix: C.cases.fix };
  if (!cases.data) return check(id, label, PENDING, false, null, copy.fix);
  if (decisionId === null) return { id, label, state: 'fail', found: copy.missing, fix: C.cases.fix };
  const data = card.data;
  return check(id, label, card, data ? ok(data, decisionId) : false, data ? found(data) : null, copy.fix);
}

/** The checks of docs/ui-design.md 9.5, in order, after the adapter and before the cases. */
export function preflightChecks({ expected, adapter, readyz, cases, marco, rossi, assumptions, load }: PreflightInputs): PreflightCheck[] {
  const r = readyz.data;
  const components = expected === 'live' ? (['vllm', 'telemetry'] as const) : (['demo_cache'] as const);
  const notReady = r ? components.filter((c) => !componentReady(r, c)) : [];
  const current = assumptions.data?.current.version;
  const contract = compareContract(contractVersionOf(r));
  const onMock = adapter === 'mock';
  const marcoDecision = caseOf(cases.data, 'marco')?.decision_id ?? null;
  const rossiDecision = (caseOf(cases.data, 'rossi_card') ?? caseOf(cases.data, 'rossi'))?.decision_id ?? null;
  const casesCheck: PreflightCheck = cases.failed
    ? { id: 'cases', label: C.cases.label, state: 'fail', found: failureText(cases.error), fix: C.cases.fix }
    : check('cases', C.cases.label, cases, true, cases.data ? cases.data.cases.map((c) => c.return_id).join(' · ') : null, C.cases.fix);
  return [
    {
      id: 'adapter',
      label: C.adapter.label,
      state: onMock ? 'fail' : 'pass',
      found: onMock ? C.adapter.foundMock : C.adapter.foundLive,
      fix: onMock ? C.adapter.fix : null,
    },
    check(
      'readiness',
      C.readiness.label,
      readyz,
      r?.status === 'ready' && r.mode === expected,
      r ? `${r.status} · ${r.mode}` : null,
      expected === 'live' ? C.readiness.fixLive : C.readiness.fixCached,
    ),
    check(
      'components',
      expected === 'live' ? C.components.live : C.components.cached,
      readyz,
      notReady.length === 0,
      r ? components.map((c) => `${C.components.names[c]} ${r.checks.find((k) => k.component === c)?.status ?? PLATFORM.demo.unreachable}`).join(' · ') : null,
      C.components.fix,
    ),
    check(
      'contract',
      C.contract.label(contract.ui),
      readyz,
      contract.status === 'same' || contract.status === 'patch',
      r ? (contract.server ?? PLATFORM.demo.unreachable) : null,
      C.contract.fix,
    ),
    casesCheck,
    caseCheck(
      'marco',
      C.marco.label,
      cases,
      marcoDecision,
      marco,
      (card, decisionId) => card.decision.state === 'proposed' && card.decision.decision_id === decisionId,
      (card) => `${card.decision.state} · ${card.decision.decision_id}`,
      C.marco,
    ),
    caseCheck(
      'rossi',
      C.rossi.label,
      cases,
      rossiDecision,
      rossi,
      (card, decisionId) => card.decision.decision_id === decisionId,
      (card) => card.decision.decision_id,
      C.rossi,
    ),
    check('assumptions', C.assumptions.label, assumptions, current === DEMO_ASSUMPTIONS_VERSION, current ?? null, C.assumptions.fix),
    check('load', C.load.label, load, (load.data?.value ?? Infinity) < PREFLIGHT_LOAD_TARGET_MS, null, C.load.fix),
  ];
}

/** Green only when every check passed; pending while any answer is missing. */
export function preflightState(checks: readonly PreflightCheck[]): CheckState {
  if (checks.some((c) => c.state === 'fail')) return 'fail';
  if (checks.some((c) => c.state === 'pending')) return 'pending';
  return 'pass';
}

/**
 * The load of the golden cards and the matrix, measured in this browser: a real figure, dated with
 * the service's day (`day`, the demo clock of /readyz), like every period that ends now.
 */
export function loadMetric(ms: number, day: string, source: 'mock' | 'live'): Metric {
  const target = source === 'mock' ? PLATFORM.demo.loadTargetMock : PLATFORM.demo.loadTargetLive;
  return {
    value: Math.round(ms),
    unit: 'milliseconds',
    provenance: { origin: PLATFORM.demo.loadOrigin(target), period_start: day, period_end: day, nature: 'real' },
  };
}
