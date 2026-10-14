// The typed client covers every route of ADR-15 and behaves the same on both adapters: these
// tests run it on the mock adapter (VITE_API_MODE=mock in tests).
import { afterEach, describe, expect, it } from 'vitest';
import { API_MODE } from './adapters';
import { api, apiFetch, CLIENT_ROUTES, toQueryString } from './client';
import { ENDPOINTS } from './contract.gen';
import { ApiError } from './errors';
import { metaStore, resetModesSeen } from './meta';
import type { AgentEvent, DecisionRequest } from './models';
import { setScenario } from './scenario';
import { readExample } from '@/test/examples';

afterEach(() => {
  setScenario('default');
  resetModesSeen();
});

describe('api client', () => {
  it('runs on the mock adapter in tests', () => {
    expect(API_MODE).toBe('mock');
  });

  it('has one function for every route of endpoints.json', () => {
    const contract = new Set(ENDPOINTS.map((e) => `${e.method} ${e.path}`));
    expect(new Set(CLIENT_ROUTES)).toEqual(contract);
  });

  it('repeats list parameters and leaves nulls out', () => {
    expect(toQueryString({ quadrant: ['Q3', 'Q4'], state: null, requires_approval: true })).toBe(
      '?quadrant=Q3&quadrant=Q4&requires_approval=true',
    );
    expect(toQueryString({})).toBe('');
  });

  it('returns typed bodies and records the mode header, the request id and as_of', async () => {
    const card = await api.getCase('RET-000102');
    expect(card).toEqual(readExample('case_card.marco.json'));
    const meta = metaStore.get();
    expect(meta.lastMode).toBe('live');
    expect(meta.lastRequestId).toMatch(/^mock-req-/);
    expect(meta.asOf).toBe(card.as_of);
  });

  it('maps a non-2xx answer to ApiError with code, message and request id', async () => {
    const error = await api.getCase('RET-999999').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, code: 'not_found' });
    expect((error as ApiError).requestId).toBeTruthy();
  });

  it('sends the scenario header: 409 stale_decision and 422 legal_guard keep their violations', async () => {
    setScenario('stale_decision');
    const stale = await api
      .addEvidence('RET-000104', readExample('evidence_request.rossi_card.json'))
      .catch((e: unknown) => e as ApiError);
    expect(stale).toMatchObject({ status: 409, code: 'stale_decision' });
    setScenario('default');
    const refused = await api
      .decide('RET-000105', readExample<DecisionRequest>('decision_request.elena_shipping.json'))
      .catch((e: unknown) => e as ApiError);
    expect(refused).toMatchObject({ status: 422, code: 'legal_guard' });
    expect((refused as ApiError).violations[0]?.code).toBe('GUARANTEE_RESTRICTED');
  });

  it('approves Marco with the request of the contract example', async () => {
    const result = await api.decide('RET-000102', readExample<DecisionRequest>('decision_request.marco_approve.json'));
    expect(result.decision.state).toBe('approved');
    expect(result.decision.approver).toBe('sara');
  });

  it('records a 503 from the language model', async () => {
    setScenario('llm_unavailable');
    const error = await api.regenerate('RET-000101', readExample('regenerate_request.json')).catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 503, code: 'llm_unavailable' });
    expect(metaStore.get().llmUnavailableAt).not.toBeNull();
  });

  it('streams the agent answer in contract order', async () => {
    const events: AgentEvent[] = [];
    for await (const event of api.askAgent(readExample('agent_ask_request.marco_link.json'))) events.push(event);
    expect(events).toEqual(readExample('agent_stream.marco_link.json'));
    const types = events.map((e) => e.type);
    expect(types[0]).toBe('tool_call');
    expect(types.at(-1)).toBe('done');
    expect(types.filter((t) => t === 'answer' || t === 'refusal' || t === 'error')).toHaveLength(1);
  });

  it('cancels the stream with an AbortController', async () => {
    const controller = new AbortController();
    const seen: AgentEvent[] = [];
    const stream = api.askAgent(readExample('agent_ask_request.marco_link.json'), { signal: controller.signal });
    for await (const event of stream) {
      seen.push(event);
      controller.abort();
    }
    expect(seen.length).toBeLessThan(readExample<AgentEvent[]>('agent_stream.marco_link.json').length);
  });

  it('downloads the audit log as CSV', async () => {
    const csv = await api.getAuditCsv({ return_id: 'RET-000102' });
    expect(csv.split('\n')[0]).toContain('entry_id');
  });

  it('refuses a JSON call on a route that answers something else', async () => {
    await expect(apiFetch('GET /api/audit', { query: { format: 'csv' } })).rejects.toMatchObject({ code: 'unexpected_response' });
  });
});
