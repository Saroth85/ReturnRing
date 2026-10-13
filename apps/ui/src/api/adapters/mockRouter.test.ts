// The mock server answers every route of index.json with its example (docs/ui-design.md 11.5),
// enforces the endpoint roles, and serves the UI fixtures for 403, 409, 429 and 503.
import { describe, expect, it } from 'vitest';
import type { CaseCard, QueueResponse } from '@/api/models';
import { encodeSseFrame, parseSseBuffer, type AgentEvent } from '@/api/sse';
import { readExample, readExampleText, readIndex } from '@/test/examples';
import { createMockRouter, findEndpoint, normaliseQuery, queueItemMatches, type MockRoute } from './mockRouter';

const index = readIndex();
const route = createMockRouter(index, async (file) => readExample(file), { requestIdPrefix: 'test' });

function queryString(file: string | null): string {
  if (!file) return '';
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(readExample<Record<string, unknown>>(file))) {
    if (value === null) continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(name, String(v)));
    else search.append(name, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

function requestFor(r: MockRoute) {
  const headers: Record<string, string> = r.scenario === 'default' ? {} : { 'X-Mock-Scenario': r.scenario };
  const body = r.request ? JSON.stringify(readExample(r.request)) : r.method === 'GET' ? undefined : JSON.stringify({ approver: 'sara' });
  return { method: r.method, path: `${r.path}${queryString(r.query)}`, headers, body };
}

describe('every route of index.json', () => {
  it.each(index.routes.map((r) => [`${r.method} ${r.path} [${r.scenario}] -> ${r.response}`, r] as const))('%s', async (_name, r) => {
    const result = await route(requestFor(r));
    expect(result.status).toBe(r.status);
    expect(result.example).toBe(r.response);
    const expected = readExample(r.response);
    if (r.media_type === 'text/event-stream') {
      expect(result.body).toEqual({ kind: 'sse', events: expected });
    } else {
      expect(result.body.kind).toBe('json');
      const value = (result.body as { value: unknown }).value as Record<string, unknown>;
      // Error bodies carry the request id of this answer; everything else is the example as is.
      if (r.status >= 400) expect({ ...value, request_id: null }).toEqual({ ...(expected as object), request_id: null });
      else expect(value).toEqual(expected);
    }
    expect(result.headers['x-returnring-mode']).toBe(r.scenario === 'cached' ? 'cached' : 'live');
    expect(result.headers['x-request-id']).toMatch(/^test-\d{6}$/);
  });
});

describe('scenarios the contract ships', () => {
  it('409 stale_decision on the evidence route', async () => {
    const result = await route({
      method: 'POST',
      path: '/api/returns/RET-000104/evidence',
      headers: { 'X-Mock-Scenario': 'stale_decision' },
      body: readExampleText('evidence_request.rossi_card.json'),
    });
    expect(result.status).toBe(409);
    expect((result.body as { value: { code: string } }).value.code).toBe('stale_decision');
  });

  it('serves default examples with the cached header in the cached scenario', async () => {
    const result = await route({ method: 'GET', path: '/api/returns/RET-000102', headers: { 'x-mock-scenario': 'cached' } });
    expect(result.status).toBe(200);
    expect(result.headers['x-returnring-mode']).toBe('cached');
  });
});

describe('roles from endpoints.json (ADR-15 section 5)', () => {
  it('refuses a warehouse approval with a 403', async () => {
    const body = { ...readExample<Record<string, unknown>>('decision_request.marco_approve.json'), approver: 'ahmed' };
    const result = await route({ method: 'POST', path: '/api/returns/RET-000102/decision', headers: {}, body: JSON.stringify(body) });
    expect(result.status).toBe(403);
    const value = (result.body as { value: { code: string; message: string } }).value;
    expect(value.code).toBe('forbidden_role');
    expect(value.message).toMatch(/^UI fixture:/);
  });

  it('refuses an inspection by an operator', async () => {
    const body = { ...readExample<Record<string, unknown>>('inspection_create_request.json'), inspector: 'sara' };
    const result = await route({ method: 'POST', path: '/api/inspections', headers: {}, body: JSON.stringify(body) });
    expect(result.status).toBe(403);
  });
});

describe('UI fixtures (open question O-1)', () => {
  const regenerate = { method: 'POST', path: '/api/returns/RET-000101/regenerate', body: readExampleText('regenerate_request.json') };
  const ask = { method: 'POST', path: '/api/agent/ask', body: readExampleText('agent_ask_request.marco_link.json') };

  it('403 forbidden_role on writes', async () => {
    const result = await route({ ...regenerate, headers: { 'X-Mock-Scenario': 'forbidden_role' } });
    expect(result.status).toBe(403);
  });

  it('503 llm_unavailable on regenerate and on the agent', async () => {
    for (const request of [regenerate, ask]) {
      const result = await route({ ...request, headers: { 'X-Mock-Scenario': 'llm_unavailable' } });
      expect(result.status).toBe(503);
      expect((result.body as { value: { code: string } }).value.code).toBe('llm_unavailable');
    }
  });

  it('429 rate_limited on the agent only', async () => {
    expect((await route({ ...ask, headers: { 'X-Mock-Scenario': 'rate_limited' } })).status).toBe(429);
    expect((await route({ ...regenerate, headers: { 'X-Mock-Scenario': 'rate_limited' } })).status).toBe(200);
  });

  it('409 conflict on a decision', async () => {
    const result = await route({
      method: 'POST',
      path: '/api/returns/RET-000102/decision',
      headers: { 'X-Mock-Scenario': 'conflict' },
      body: readExampleText('decision_request.marco_approve.json'),
    });
    expect(result.status).toBe(409);
    expect((result.body as { value: { code: string } }).value.code).toBe('conflict');
  });

  it('an empty queue, a pending draft and an agent error event', async () => {
    const empty = await route({ method: 'GET', path: '/api/returns', headers: { 'X-Mock-Scenario': 'empty' } });
    expect((empty.body as { value: QueueResponse }).value.items).toEqual([]);
    const pending = await route({ method: 'GET', path: '/api/returns/RET-000102', headers: { 'X-Mock-Scenario': 'draft_pending' } });
    const card = (pending.body as { value: CaseCard }).value;
    expect(card.draft).toBeNull();
    expect(card.draft_status).toBe('pending');
    const error = await route({ ...ask, headers: { 'X-Mock-Scenario': 'agent_error' } });
    const events = (error.body as { events: AgentEvent[] }).events;
    expect(events.map((e) => e.type)).toEqual(['error', 'done']);
  });
});

describe('queue filters (QueueQuery.matches)', () => {
  const base = readExample<QueueResponse>('queue_response.json');

  it('counts each tab with page_size=1 like the full answer', async () => {
    const approval = readExample<QueueResponse>('queue_response.approval.json');
    const result = await route({
      method: 'GET',
      path: '/api/returns?state=proposed&quadrant=Q3&quadrant=Q4&requires_approval=true&page_size=1',
      headers: {},
    });
    const page = (result.body as { value: QueueResponse }).value;
    expect(page.total).toBe(approval.total);
    expect(page.items).toHaveLength(1);
    expect(result.note).toMatch(/filtered by the mock/);
  });

  it('filters the guarantee path and not enough evidence', async () => {
    const guarantee = await route({ method: 'GET', path: '/api/returns?path=guarantee', headers: {} });
    const items = (guarantee.body as { value: QueueResponse }).value.items;
    expect(items.length).toBeGreaterThan(0);
    expect(items.every((i) => i.path === 'guarantee')).toBe(true);
    const insufficient = await route({ method: 'GET', path: '/api/returns?evidence_quality=insufficient', headers: {} });
    expect((insufficient.body as { value: QueueResponse }).value.items.every((i) => i.quadrant === null)).toBe(true);
  });

  it('applies the reference semantics to every item', () => {
    const q = normaliseQuery('queue_query', new URLSearchParams('quadrant=none'));
    expect(base.items.filter((i) => queueItemMatches(i, q)).every((i) => i.quadrant === null)).toBe(true);
  });
});

describe('unknown requests', () => {
  it('404 for an id without an example and for a path outside the contract', async () => {
    expect((await route({ method: 'GET', path: '/api/returns/RET-123456', headers: {} })).status).toBe(404);
    expect((await route({ method: 'GET', path: '/api/nothing', headers: {} })).status).toBe(404);
    expect(findEndpoint('GET', '/api/links/C-1~C-2/dispute')).toBeNull();
    expect(findEndpoint('POST', '/api/links/C-1~C-2/dispute')?.path).toBe('/api/links/{link_id}/dispute');
  });

  it('falls back to the first example of the route, with a note', async () => {
    const body = JSON.stringify({ ...readExample<Record<string, unknown>>('agent_ask_request.marco_link.json'), question: 'Anything else?' });
    const result = await route({ method: 'POST', path: '/api/agent/ask', headers: {}, body });
    expect(result.status).toBe(200);
    expect(result.note).toMatch(/no example for this request body/);
  });
});

describe('SSE frames (ADR-15 section 4, sse_frame)', () => {
  it('writes the frames of the contract stream file', () => {
    const events = readExample<AgentEvent[]>('agent_stream.marco_link.json');
    const written = events.map(encodeSseFrame).join('');
    const expected = parseSseBuffer(readExampleText('agent_stream.marco_link.sse'));
    const ours = parseSseBuffer(written);
    expect(ours.rest).toBe('');
    expect(ours.messages.map((m) => [m.event, m.id, JSON.parse(m.data)])).toEqual(
      expected.messages.map((m) => [m.event, m.id, JSON.parse(m.data)]),
    );
  });

  it('parses frames split across chunks', () => {
    const frame = encodeSseFrame({ type: 'token', seq: 4, text: 'a link is a shared identifier' });
    const first = parseSseBuffer(frame.slice(0, 10));
    expect(first.messages).toEqual([]);
    const second = parseSseBuffer(first.rest + frame.slice(10));
    expect(second.messages).toHaveLength(1);
    expect(second.messages[0]?.event).toBe('token');
  });
});
