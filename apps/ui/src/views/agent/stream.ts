// The agent stream folded into what the panel shows (US-701; ADR-15 section 4, agent). Contract
// order: tool_call/tool_result pairs for steps 1..n (n <= AGENT_MAX_STEPS), token deltas, exactly
// one answer, refusal or error, then done; seq counts from 0 without gaps; the answer text is the
// concatenation of the tokens. Any deviation becomes an issue the panel prints (design P10):
// what arrived is still shown, never silently repaired.
import { AGENT_MAX_STEPS } from '@/api/contract.gen';
import type { AgentEvent, EntityRef, GenerationInfo, Metric } from '@/api/models';
import { AGENT } from './copy';

type EventOf<T extends AgentEvent['type']> = Extract<AgentEvent, { type: T }>;
export type AgentTool = EventOf<'tool_call'>['tool'];
export type RefusalReason = EventOf<'refusal'>['reason'];
export type AgentErrorCode = EventOf<'error'>['code'];

export interface StepView {
  step: number;
  tool: AgentTool;
  /** The tool arguments in the order received, e.g. [['customer_id', 'C-MARCO-1']]. */
  arguments: [string, string][];
  /** running: the call arrived, the result not yet. */
  status: 'running' | 'ok' | 'failed';
  /** The tool's summary, shown verbatim; null while running. */
  summary: string | null;
  refs: EntityRef[];
}

export type FinalView =
  | {
      kind: 'answer';
      text: string;
      evidenceConsulted: EntityRef[];
      evidenceMissing: string[];
      /** Refs cited by the answer that are not among the evidence consulted. */
      otherRefs: EntityRef[];
    }
  | { kind: 'refusal'; reason: RefusalReason; text: string }
  | { kind: 'error'; code: AgentErrorCode; message: string };

export interface DoneView {
  stepsUsed: number;
  generation: GenerationInfo;
}

export type IssueCode =
  | 'out_of_order'
  | 'seq_gap'
  | 'step_limit_exceeded'
  | 'unpaired_tool_call'
  | 'steps_used_mismatch'
  | 'text_mismatch'
  | 'no_final'
  | 'no_done';

export interface StreamIssue {
  code: IssueCode;
  /** The seq of the event that showed it, when one did. */
  seq: number | null;
}

export interface TurnView {
  steps: StepView[];
  /** The token deltas concatenated, as they arrive. */
  streamedText: string;
  final: FinalView | null;
  done: DoneView | null;
  issues: StreamIssue[];
}

/** Contract phases: steps, then tokens, then the final event, then done. */
const PHASE: Record<AgentEvent['type'], number> = {
  tool_call: 0,
  tool_result: 0,
  token: 1,
  answer: 2,
  refusal: 2,
  error: 2,
  done: 3,
};

const refKey = (ref: EntityRef) => `${ref.kind}:${ref.id}`;

/** Refs without repeats, first occurrence first. */
export function uniqueRefs(refs: readonly EntityRef[]): EntityRef[] {
  const seen = new Set<string>();
  const out: EntityRef[] = [];
  for (const ref of refs) {
    const key = refKey(ref);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(ref);
  }
  return out;
}

/**
 * Folds the events received so far. `ended` says the response body is closed, so what is still
 * missing (a result, the final event, done) is an issue rather than "not yet".
 */
export function foldAgentEvents(events: readonly AgentEvent[], { ended }: { ended: boolean }): TurnView {
  const steps: StepView[] = [];
  const tokens: string[] = [];
  let final: FinalView | null = null;
  let done: DoneView | null = null;
  const issues: StreamIssue[] = [];
  const reported = new Set<IssueCode>();
  const report = (code: IssueCode, seq: number | null) => {
    if (reported.has(code)) return;
    reported.add(code);
    issues.push({ code, seq });
  };

  let phase = 0;
  let expectedSeq = 0;
  for (const event of events) {
    if (event.seq !== expectedSeq) report('seq_gap', event.seq);
    expectedSeq = event.seq + 1;

    const eventPhase = PHASE[event.type];
    // Anything after done, a second final event, or a step or token after a later phase.
    if (done !== null || eventPhase < phase || (eventPhase === 2 && final !== null)) report('out_of_order', event.seq);
    phase = Math.max(phase, eventPhase);

    switch (event.type) {
      case 'tool_call': {
        if (event.step > AGENT_MAX_STEPS) report('step_limit_exceeded', event.seq);
        if (steps.some((s) => s.step === event.step)) report('out_of_order', event.seq);
        steps.push({
          step: event.step,
          tool: event.tool,
          arguments: Object.entries(event.arguments).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
          status: 'running',
          summary: null,
          refs: [],
        });
        break;
      }
      case 'tool_result': {
        const step = steps.find((s) => s.step === event.step && s.status === 'running' && s.tool === event.tool);
        if (!step) {
          // A result without its call: shown as its own step, reported.
          report('out_of_order', event.seq);
          if (event.step > AGENT_MAX_STEPS) report('step_limit_exceeded', event.seq);
          steps.push({ step: event.step, tool: event.tool, arguments: [], status: 'running', summary: null, refs: [] });
        }
        const target = step ?? (steps[steps.length - 1] as StepView);
        target.status = event.ok ? 'ok' : 'failed';
        target.summary = event.summary;
        target.refs = uniqueRefs(event.refs);
        break;
      }
      case 'token':
        tokens.push(event.text);
        break;
      case 'answer': {
        const consulted = uniqueRefs(event.evidence_consulted);
        const consultedKeys = new Set(consulted.map(refKey));
        final = {
          kind: 'answer',
          text: event.text,
          evidenceConsulted: consulted,
          evidenceMissing: event.evidence_missing,
          otherRefs: uniqueRefs(event.refs).filter((r) => !consultedKeys.has(refKey(r))),
        };
        if (tokens.length > 0 && tokens.join('') !== event.text) report('text_mismatch', event.seq);
        break;
      }
      case 'refusal':
        final = { kind: 'refusal', reason: event.reason, text: event.text };
        break;
      case 'error':
        final = { kind: 'error', code: event.code, message: event.message };
        break;
      case 'done':
        done = { stepsUsed: event.steps_used, generation: event.generation };
        if (event.steps_used !== steps.length) report('steps_used_mismatch', event.seq);
        if (final === null) report('no_final', event.seq);
        break;
    }
  }

  if (steps.length > AGENT_MAX_STEPS) report('step_limit_exceeded', null);
  if (done !== null || ended) {
    if (steps.some((s) => s.status === 'running')) report('unpaired_tool_call', null);
  }
  if (ended && done === null) report('no_done', null);

  return { steps, streamedText: tokens.join(''), final, done, issues };
}

/**
 * The tool-call count as a Metric, so it renders through Figure with its origin, period and
 * nature (rule 11, US-906). Contract examples are synthetic; a mock language model writes
 * hand-written stand-ins, also synthetic; Gemma on vLLM is a real count of what the agent did.
 */
export function stepsUsedMetric(done: DoneView, apiMode: 'mock' | 'live'): Metric {
  const { generation } = done;
  const day = generation.created_at.slice(0, 10);
  const id = generation.generation_id;
  let nature: Metric['provenance']['nature'] = 'synthetic';
  let origin: string;
  if (apiMode === 'mock') origin = AGENT.steps.originExample(id);
  else if (generation.llm_mode === 'mock') origin = AGENT.steps.originMockModel(id);
  else {
    nature = 'real';
    origin = AGENT.steps.originLive(id, generation.served_from === 'cached');
  }
  return { value: done.stepsUsed, unit: 'count', provenance: { nature, origin, period_start: day, period_end: day } };
}
