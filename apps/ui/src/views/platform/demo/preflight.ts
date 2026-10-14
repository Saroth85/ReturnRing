// Preflight of the pitch (docs/ui-design.md 9.5): green or red, each red line names its fix. Pure
// logic over the API answers, so the page and the tests read the same rules.
import { compareContract, contractVersionOf } from '@/api/contractVersion';
import type { CaseCard, Metric, ReadyzResponse, SimulatorAssumptionsResponse } from '@/api/models';
import { PLATFORM } from '../copy';
import {
  DEMO_ASSUMPTIONS_VERSION,
  MARCO_FIRST_VERSION,
  PREFLIGHT_LOAD_TARGET_MS,
  ROSSI_FIRST_DECISION_ID,
} from '../constants';

export type ExpectedMode = 'live' | 'cached';
export type CheckState = 'pass' | 'fail' | 'pending';

export interface PreflightCheck {
  id: 'adapter' | 'readiness' | 'components' | 'contract' | 'marco' | 'rossi' | 'assumptions' | 'load';
  label: string;
  state: CheckState;
  /** What was found, in words (shown on red lines and as a hint on green ones). */
  found: string | null;
  /** How to make a red line green. */
  fix: string | null;
}

/** An answer of the API as the page holds it: the data, or that the call failed. */
export interface Answer<T> {
  data: T | undefined;
  failed: boolean;
}

export interface PreflightInputs {
  expected: ExpectedMode;
  /** The UI's adapter: on `mock` every answer is a contract example, so the run is never ready. */
  adapter: 'mock' | 'live';
  readyz: Answer<ReadyzResponse>;
  marco: Answer<CaseCard>;
  rossi: Answer<CaseCard>;
  assumptions: Answer<SimulatorAssumptionsResponse>;
  /** The measured load of the golden cards and the matrix, in milliseconds. */
  load: Answer<Metric>;
}

const C = PLATFORM.demo.checks;

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

/** The checks of docs/ui-design.md 9.5, in order, after the adapter and before the cases. */
export function preflightChecks({ expected, adapter, readyz, marco, rossi, assumptions, load }: PreflightInputs): PreflightCheck[] {
  const r = readyz.data;
  const components = expected === 'live' ? (['vllm', 'telemetry'] as const) : (['demo_cache'] as const);
  const notOk = r ? components.filter((c) => r.checks.find((k) => k.component === c)?.status !== 'ok') : [];
  const m = marco.data?.decision;
  const latestRossi = rossi.data?.decision.decision_id;
  const current = assumptions.data?.current.version;
  const contract = compareContract(contractVersionOf(r));
  const onMock = adapter === 'mock';
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
      notOk.length === 0,
      r ? components.map((c) => `${c} ${r.checks.find((k) => k.component === c)?.status ?? PLATFORM.demo.unreachable}`).join(' · ') : null,
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
    check(
      'marco',
      C.marco.label,
      marco,
      m?.state === 'proposed' && m.version === MARCO_FIRST_VERSION,
      m ? `${m.state} · ${m.decision_id}` : null,
      C.marco.fix,
    ),
    check('rossi', C.rossi.label, rossi, latestRossi === ROSSI_FIRST_DECISION_ID, latestRossi ?? null, C.rossi.fix),
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

/** The load of the golden cards and the matrix, measured in this browser: a real figure. */
export function loadMetric(ms: number, measuredAt: string, source: 'mock' | 'live'): Metric {
  const day = measuredAt.slice(0, 10);
  const target = source === 'mock' ? PLATFORM.demo.loadTargetMock : PLATFORM.demo.loadTargetLive;
  return {
    value: Math.round(ms),
    unit: 'milliseconds',
    provenance: { origin: PLATFORM.demo.loadOrigin(target), period_start: day, period_end: day, nature: 'real' },
  };
}
