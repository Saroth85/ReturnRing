// The agent stream folded into what the panel shows (US-701, ADR-15 section 4): steps paired by
// number, streamed text, exactly one final event, done; and every deviation from the contract
// order reported, never hidden (docs/ui-design.md P10).
import { describe, expect, it } from 'vitest';
import { AGENT_MAX_STEPS } from '@/api/contract.gen';
import type { AgentEvent } from '@/api/models';
import { exampleFiles, readExample } from '@/test/examples';
import { foldAgentEvents, stepsUsedMetric, type IssueCode } from './stream';

const link = () => readExample<AgentEvent[]>('agent_stream.marco_link.json');
const codes = (events: AgentEvent[], ended = true): IssueCode[] => foldAgentEvents(events, { ended }).issues.map((i) => i.code);

/** Renumbers seq from 0, so a forged stream only breaks what the test means to break. */
const reseq = (events: AgentEvent[]): AgentEvent[] => events.map((e, seq) => ({ ...e, seq }));

describe('foldAgentEvents on the contract examples', () => {
  it.each(exampleFiles('agent_stream.'))('%s folds without an issue', (file) => {
    const view = foldAgentEvents(readExample<AgentEvent[]>(file), { ended: true });
    expect(view.issues).toEqual([]);
    expect(view.done).not.toBeNull();
    expect(view.final).not.toBeNull();
  });

  it('pairs tool calls and results by step, in order', () => {
    const view = foldAgentEvents(link(), { ended: true });
    expect(view.steps.map((s) => [s.step, s.tool, s.status])).toEqual([
      [1, 'get_cluster', 'ok'],
      [2, 'get_orders', 'ok'],
    ]);
    expect(view.steps[0]?.arguments).toEqual([['customer_id', 'C-MARCO-1']]);
    expect(view.steps[0]?.summary).toContain('Cluster CL-000417');
    expect(view.steps[0]?.refs).toEqual([
      { kind: 'cluster', id: 'CL-000417' },
      { kind: 'link', id: 'C-MARCO-1~C-MARCO-3' },
    ]);
  });

  it('keeps the answer, the evidence consulted and the evidence missing apart', () => {
    const view = foldAgentEvents(link(), { ended: true });
    expect(view.final).toMatchObject({
      kind: 'answer',
      evidenceConsulted: [
        { kind: 'cluster', id: 'CL-000417' },
        { kind: 'link', id: 'C-MARCO-1~C-MARCO-3' },
        { kind: 'customer', id: 'C-MARCO-3' },
      ],
      evidenceMissing: ['No contact with the customer about the shared card'],
      otherRefs: [],
    });
    expect(view.done).toMatchObject({ stepsUsed: 2, generation: { generation_id: 'GEN-AGENT-000001' } });
  });

  it('a refusal carries its reason and text, and no steps', () => {
    const view = foldAgentEvents(readExample<AgentEvent[]>('agent_stream.write_refused.json'), { ended: true });
    expect(view.steps).toEqual([]);
    expect(view.final).toEqual({
      kind: 'refusal',
      reason: 'write_request',
      text: 'I can only read the case. Approvals and changes are made by an operator on the case card.',
    });
  });

  it('lists refs cited in the answer but not among the evidence consulted', () => {
    const events = link().map((e) =>
      e.type === 'answer' ? { ...e, refs: [...e.refs, { kind: 'order' as const, id: 'ORD-005230' }, { kind: 'cluster' as const, id: 'CL-000417' }] } : e,
    );
    const view = foldAgentEvents(events, { ended: true });
    expect(view.final?.kind === 'answer' && view.final.otherRefs).toEqual([{ kind: 'order', id: 'ORD-005230' }]);
  });
});

describe('foldAgentEvents while streaming', () => {
  it('shows a running step before its result arrives', () => {
    const view = foldAgentEvents(link().slice(0, 3), { ended: false });
    expect(view.steps.map((s) => s.status)).toEqual(['ok', 'running']);
    expect(view.final).toBeNull();
    expect(view.issues).toEqual([]);
  });

  it('concatenates the tokens until the answer replaces them', () => {
    const partial = foldAgentEvents(link().slice(0, 6), { ended: false });
    expect(partial.streamedText).toBe(
      'C-MARCO-1 and C-MARCO-3 are linked by a payment card, a device and a delivery address, all shared since 2026-09-03, when C-MARCO-3 was ',
    );
    expect(partial.final).toBeNull();
  });

  it('reports nothing missing until the stream has ended', () => {
    expect(codes(link().slice(0, 9), false)).toEqual([]);
  });
});

describe('foldAgentEvents reports contract deviations (fail visibly)', () => {
  it('a stream that ends without done', () => {
    expect(codes(link().slice(0, 10))).toEqual(['no_done']);
  });

  it('done without an answer, refusal or error', () => {
    expect(codes(reseq(link().filter((e) => e.type !== 'answer')))).toEqual(['no_final']);
  });

  it('a tool call left without its result', () => {
    expect(codes(reseq(link().filter((e) => !(e.type === 'tool_result' && e.step === 2))))).toContain('unpaired_tool_call');
  });

  it('more steps than the contract allows', () => {
    const calls: AgentEvent[] = [];
    for (let step = 1; step <= AGENT_MAX_STEPS + 1; step += 1) {
      calls.push({ type: 'tool_call', seq: 0, step, tool: 'get_case', arguments: { return_id: 'RET-000102' } });
      calls.push({ type: 'tool_result', seq: 0, step, tool: 'get_case', ok: true, summary: 'ok', refs: [] });
    }
    const tail = link().filter((e) => e.type === 'answer' || e.type === 'done');
    const events = reseq([...calls, ...tail.map((e) => (e.type === 'done' ? { ...e, steps_used: AGENT_MAX_STEPS + 1 } : e))]);
    expect(codes(events)).toEqual(['step_limit_exceeded']);
  });

  it('a steps_used that differs from the steps received', () => {
    expect(codes(link().map((e) => (e.type === 'done' ? { ...e, steps_used: 3 } : e)))).toEqual(['steps_used_mismatch']);
  });

  it('an answer whose text is not the concatenation of the tokens', () => {
    expect(codes(link().map((e) => (e.type === 'answer' ? { ...e, text: `${e.text} And more.` } : e)))).toEqual(['text_mismatch']);
  });

  it('events out of the contract order', () => {
    const events = link();
    const [first, second, third, fourth, ...rest] = events;
    // A token before the steps, then a tool call after it.
    const shuffled = reseq([rest[0] as AgentEvent, first as AgentEvent, second as AgentEvent, third as AgentEvent, fourth as AgentEvent, ...rest.slice(1)]);
    expect(codes(shuffled)).toContain('out_of_order');
  });

  it('a second final event, and events after done', () => {
    const events = link();
    const answer = events.find((e) => e.type === 'answer') as AgentEvent;
    const refusal: AgentEvent = { type: 'refusal', seq: 0, reason: 'out_of_scope', text: 'No.' };
    expect(codes(reseq([...events.slice(0, 10), refusal, ...events.slice(10)]))).toContain('out_of_order');
    expect(codes(reseq([...events, answer]))).toContain('out_of_order');
  });

  it('a gap in seq', () => {
    expect(codes(link().map((e) => (e.seq >= 4 ? { ...e, seq: e.seq + 1 } : e)))).toEqual(['seq_gap']);
  });

  it('a failed tool result is a failed step, not an issue', () => {
    const events = link().map((e) => (e.type === 'tool_result' && e.step === 2 ? { ...e, ok: false } : e));
    const view = foldAgentEvents(events, { ended: true });
    expect(view.steps.map((s) => s.status)).toEqual(['ok', 'failed']);
    expect(view.issues).toEqual([]);
  });

  it('an error event is the final event', () => {
    const events: AgentEvent[] = [
      { type: 'error', seq: 0, code: 'llm_unavailable', message: 'The language model stopped answering' },
      { ...(link().at(-1) as Extract<AgentEvent, { type: 'done' }>), seq: 1, steps_used: 0 },
    ];
    const view = foldAgentEvents(events, { ended: true });
    expect(view.final).toEqual({ kind: 'error', code: 'llm_unavailable', message: 'The language model stopped answering' });
    expect(view.issues).toEqual([]);
  });
});

describe('stepsUsedMetric (the tool-call count through the provenance component)', () => {
  const done = () => foldAgentEvents(link(), { ended: true }).done!;

  it('is synthetic and a contract example on the mock adapter', () => {
    const metric = stepsUsedMetric(done(), 'mock');
    expect(metric).toMatchObject({ value: 2, unit: 'count', provenance: { nature: 'synthetic', period_start: '2026-09-30', period_end: '2026-09-30' } });
    expect(metric.provenance.origin).toMatch(/^Contract example: /);
    expect(metric.provenance.origin).toContain('GEN-AGENT-000001');
  });

  it('is synthetic when the service runs the mock language model', () => {
    const metric = stepsUsedMetric(done(), 'live');
    expect(metric.provenance.nature).toBe('synthetic');
    expect(metric.provenance.origin).toContain('hand-written stand-in');
  });

  it('is real when Gemma ran on vLLM, and says when it was precomputed', () => {
    const live = { ...done(), generation: { ...done().generation, llm_mode: 'vllm' as const, model: 'google/gemma-4-31b-it' } };
    expect(stepsUsedMetric(live, 'live').provenance).toMatchObject({ nature: 'real' });
    // The model of the generation record, never a fixed name (rules 11, 13).
    expect(stepsUsedMetric(live, 'live').provenance.origin).toMatch(/^google\/gemma-4-31b-it on vLLM: /);
    const cached = { ...live, generation: { ...live.generation, served_from: 'cached' as const } };
    expect(stepsUsedMetric(cached, 'live').provenance.origin).toMatch(/, precomputed$/);
    expect(stepsUsedMetric(cached, 'live').provenance.origin).not.toContain('MI300X');
  });
  it('names AMD Token Factory for shared inference', () => {
    const shared = { ...done(), generation: { ...done().generation, llm_mode: 'tokenfactory' as const, model: 'Gemma-4-31B-IT' } };
    expect(stepsUsedMetric(shared, 'live').provenance.origin).toContain('via AMD Token Factory');
    expect(stepsUsedMetric(shared, 'live').provenance.origin).not.toContain('vLLM');
  });
});
