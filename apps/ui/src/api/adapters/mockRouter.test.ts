// The mock server answers every route of index.json with its example (docs/ui-design.md 11.5),
// enforces the endpoint roles, serves the contract's scenarios (cached 503, rate_limited 429 with
// Retry-After, conflict 409, read_only 403, ...) and the three UI fixtures the contract does not
// ship. Behaviour beyond the example table (examples per approver, the role check fallback) is
// also shown on small synthetic route tables.
import { describe, expect, it } from 'vitest';
import type { CaseCard, QueueResponse } from '@/api/models';
import { encodeSseFrame, parseSseBuffer, type AgentEvent } from '@/api/sse';
import { readExample, readExampleText, readIndex } from '@/test/examples';
import { CONTRACT_VERSION } from '../contract.gen';
import { compareContract } from '../contractVersion';
import { createMockRouter, findEndpoint, normaliseQuery, queueItemMatches, responseFile, retryAfterOf, type MockRoute } from './mockRouter';

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
    // A stream in a .sse file is compared with its .json sibling, the same events as a list.
    const expected = readExample(responseFile(r.response));
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
  it("refuses a warehouse approval with the contract's 403 for it", async () => {
    const body = { ...readExample<Record<string, unknown>>('decision_request.marco_approve.json'), approver: 'ahmed' };
    const result = await route({ method: 'POST', path: '/api/returns/RET-000102/decision', headers: {}, body: JSON.stringify(body) });
    expect(result.status).toBe(403);
    expect(result.example).toBe('error.forbidden_role.json');
    const value = (result.body as { value: { code: string; message: string } }).value;
    expect(value.code).toBe('forbidden_role');
    expect(value.message).toBe(readExample<{ message: string }>('error.forbidden_role.json').message);
  });

  it('refuses a warehouse rejection with the role check, as a UI fixture', async () => {
    const body = { ...readExample<Record<string, unknown>>('decision_request.reject.json'), approver: 'ahmed' };
    const result = await route({ method: 'POST', path: '/api/returns/RET-000107/decision', headers: {}, body: JSON.stringify(body) });
    expect(result.status).toBe(403);
    expect((result.body as { value: { message: string } }).value.message).toMatch(/^UI fixture:/);
  });

  it('refuses an inspection by an operator', async () => {
    const body = { ...readExample<Record<string, unknown>>('inspection_create_request.json'), inspector: 'sara' };
    const result = await route({ method: 'POST', path: '/api/inspections', headers: {}, body: JSON.stringify(body) });
    expect(result.status).toBe(403);
  });
});

describe('contract scenarios', () => {
  const regenerate = { method: 'POST', path: '/api/returns/RET-000101/regenerate', body: readExampleText('regenerate_request.json') };
  const ask = { method: 'POST', path: '/api/agent/ask', body: readExampleText('agent_ask_request.marco_link.json') };
  const evaluate = { method: 'POST', path: '/api/checkout/evaluate', body: readExampleText('checkout_evaluate_request.giulia.json') };
  const approve = { method: 'POST', path: '/api/returns/RET-000102/decision', body: readExampleText('decision_request.marco_approve.json') };
  const code = (result: { body: unknown }) => (result.body as { value: { code: string } }).value.code;

  it('cached: 503 llm_unavailable on regenerate and on the agent', async () => {
    for (const request of [regenerate, ask]) {
      const result = await route({ ...request, headers: { 'X-Mock-Scenario': 'cached' } });
      expect(result.status).toBe(503);
      expect(code(result)).toBe('llm_unavailable');
    }
  });

  it('rate_limited: 429 with Retry-After on the agent and the checkout evaluation only', async () => {
    const agent = await route({ ...ask, headers: { 'X-Mock-Scenario': 'rate_limited' } });
    expect(agent.status).toBe(429);
    expect(agent.headers['retry-after']).toBe('60');
    const checkout = await route({ ...evaluate, headers: { 'X-Mock-Scenario': 'rate_limited' } });
    expect(checkout.status).toBe(429);
    expect(checkout.headers['retry-after']).toBe('12');
    expect((await route({ ...regenerate, headers: { 'X-Mock-Scenario': 'rate_limited' } })).status).toBe(200);
    expect((await route({ ...regenerate, headers: {} })).headers['retry-after']).toBeUndefined();
  });

  it('a 429 or 503 of the scenario holds for any question, not only the examples', async () => {
    const free = { method: 'POST', path: '/api/agent/ask', body: JSON.stringify({ return_id: 'RET-000102', question: 'Anything else?', lang: 'en', asked_by: 'sara' }) };
    expect((await route({ ...free, headers: { 'X-Mock-Scenario': 'rate_limited' } })).status).toBe(429);
    expect((await route({ ...free, headers: { 'X-Mock-Scenario': 'cached' } })).status).toBe(503);
    expect((await route({ ...free, headers: {} })).status).toBe(200);
  });

  it('reads the wait of a 429 from its message, a minute otherwise', () => {
    expect(retryAfterOf(readExample('error.rate_limited_public.json'))).toBe(12);
    expect(retryAfterOf(readExample('error.rate_limited.json'))).toBe(60);
    expect(retryAfterOf(null)).toBe(60);
  });

  it('conflict, stale_proposal and refund_overdue: 409 on a decision', async () => {
    for (const scenario of ['conflict', 'stale_proposal', 'refund_overdue']) {
      const result = await route({ ...approve, headers: { 'X-Mock-Scenario': scenario } });
      expect(result.status).toBe(409);
      expect(code(result)).toBe('conflict');
    }
  });

  it('an early renewal is a 409 in the default scenario', async () => {
    const result = await route({
      method: 'POST',
      path: '/api/returns/RET-000102/decision',
      headers: {},
      body: readExampleText('decision_request.renew_early.json'),
    });
    expect(result.status).toBe(409);
    expect(result.example).toBe('error.conflict_early_renewal.json');
  });

  it('read_only: /readyz says so and every write but the agent and the checkout evaluation is a 403', async () => {
    const headers = { 'X-Mock-Scenario': 'read_only' };
    const readyz = await route({ method: 'GET', path: '/readyz', headers });
    expect((readyz.body as { value: { public_read_only: boolean } }).value.public_read_only).toBe(true);
    const refused = await route({ ...approve, headers });
    expect(refused.status).toBe(403);
    expect(code(refused)).toBe('forbidden_role');
    expect((refused.body as { value: { message: string } }).value.message).toContain('POST /api/returns/{return_id}/decision');
    const confirm = await route({ method: 'POST', path: '/api/checkout/confirm', headers, body: readExampleText('checkout_confirm_request.giulia.json') });
    expect(confirm.status).toBe(403);
    expect(confirm.example).toBe('error.read_only.json');
    expect((await route({ ...evaluate, headers })).status).toBe(200);
    expect((await route({ ...ask, headers })).status).toBe(200);
    expect((await route({ method: 'GET', path: '/readyz', headers: {} })).body).toMatchObject({ value: { public_read_only: false } });
  });

  it('empty: no demo reset (404 on the demo cases) and an empty queue', async () => {
    const cases = await route({ method: 'GET', path: '/api/demo/cases', headers: { 'X-Mock-Scenario': 'empty' } });
    expect(cases.status).toBe(404);
    const empty = await route({ method: 'GET', path: '/api/returns', headers: { 'X-Mock-Scenario': 'empty' } });
    expect((empty.body as { value: QueueResponse }).value.items).toEqual([]);
  });
});

describe('UI fixtures the contract does not ship', () => {
  it('contract_mismatch: /readyz names the next minor contract version (UI1)', async () => {
    const result = await route({ method: 'GET', path: '/readyz', headers: { 'X-Mock-Scenario': 'contract_mismatch' } });
    const version = (result.body as { value: { contract_version: string } }).value.contract_version;
    expect(version).not.toBe(CONTRACT_VERSION);
    expect(compareContract(version).status).toBe('mismatch');
  });

  it('a pending draft and an agent error event', async () => {
    const ask = { method: 'POST', path: '/api/agent/ask', body: readExampleText('agent_ask_request.marco_link.json') };
    const pending = await route({ method: 'GET', path: '/api/returns/RET-000102', headers: { 'X-Mock-Scenario': 'draft_pending' } });
    const card = (pending.body as { value: CaseCard }).value;
    expect(card.draft).toBeNull();
    expect(card.draft_status).toBe('pending');
    const error = await route({ ...ask, headers: { 'X-Mock-Scenario': 'agent_error' } });
    const events = (error.body as { events: AgentEvent[] }).events;
    expect(events.map((e) => e.type)).toEqual(['error', 'done']);
  });
});

type SyntheticRoute = Pick<MockRoute, 'method' | 'endpoint' | 'path' | 'response'> & Partial<MockRoute>;

/** A router over a small synthetic route table; `loaded` lists every file it read. */
function synthetic(routes: SyntheticRoute[], files: Record<string, unknown>) {
  const loaded: string[] = [];
  const table = routes.map((r) => ({ query: null, request: null, scenario: 'default', status: 200, media_type: 'application/json', ...r }));
  const router = createMockRouter(
    { as_of: '2026-10-14T00:00:00Z', routes: table },
    async (file) => {
      loaded.push(file);
      if (!(file in files)) throw new Error(`synthetic table: no file ${file}`);
      return structuredClone(files[file]);
    },
    { requestIdPrefix: 'syn' },
  );
  return { router, loaded };
}

const DECISION = '/api/returns/{return_id}/decision';
const decisionBody = (action: string, approver: string) => ({ decision_id: 'DEC-000102-1', action, approver });
const post = (path: string, body: unknown, scenario?: string) => {
  const headers: Record<string, string> = scenario ? { 'X-Mock-Scenario': scenario } : {};
  return { method: 'POST', path, headers, body: JSON.stringify(body) };
};
const valueOf = (result: { body: unknown }) => (result.body as { value: Record<string, unknown> }).value;

describe('streams in .sse files', () => {
  const events = readExample<AgentEvent[]>('agent_stream.marco_link.json');
  const ask = readExample<Record<string, unknown>>('agent_ask_request.marco_link.json');

  it('names the .json sibling of a .sse file and leaves other files alone', () => {
    expect(responseFile('agent_stream.marco_link.sse')).toBe('agent_stream.marco_link.json');
    expect(responseFile('agent_stream.marco_link.json')).toBe('agent_stream.marco_link.json');
    expect(responseFile('case_card.marco.json')).toBe('case_card.marco.json');
  });

  it('answers a .sse route with the event list of its .json sibling', async () => {
    const { router, loaded } = synthetic(
      [
        {
          method: 'POST',
          endpoint: '/api/agent/ask',
          path: '/api/agent/ask',
          request: 'ask.json',
          media_type: 'text/event-stream',
          response: 'agent_stream.ask.sse',
        },
      ],
      { 'ask.json': ask, 'agent_stream.ask.json': events },
    );
    const result = await router(post('/api/agent/ask', ask));
    expect(result.status).toBe(200);
    expect(result.body).toEqual({ kind: 'sse', events });
    expect(result.example).toBe('agent_stream.ask.sse');
    expect(result.headers['content-type']).toBe('text/event-stream');
    expect(result.note).toBeNull();
    expect(loaded).not.toContain('agent_stream.ask.sse');
    expect(loaded).toContain('agent_stream.ask.json');
  });
});

describe('a contract route for the request comes before the role check', () => {
  const forbidden = { code: 'forbidden_role', message: 'ahmed (warehouse) cannot decide a return', violations: [], request_id: 'x' };
  const { router } = synthetic(
    [
      { method: 'POST', endpoint: DECISION, path: '/api/returns/RET-000102/decision', request: 'req.sara.json', response: 'result.approved.json' },
      {
        method: 'POST',
        endpoint: DECISION,
        path: '/api/returns/RET-000102/decision',
        request: 'req.ahmed.json',
        status: 403,
        response: 'error.forbidden_role.json',
      },
      {
        method: 'POST',
        endpoint: '/api/inspections',
        path: '/api/inspections',
        request: 'inspection.ahmed.json',
        status: 201,
        response: 'inspection.json',
      },
    ],
    {
      'req.sara.json': decisionBody('approve', 'sara'),
      'req.ahmed.json': decisionBody('approve', 'ahmed'),
      'result.approved.json': { approved: true },
      'error.forbidden_role.json': forbidden,
      'inspection.ahmed.json': readExample('inspection_create_request.json'),
      'inspection.json': { inspection_id: 'INS-1' },
    },
  );

  it("serves the contract's 403 for the role it names", async () => {
    const result = await router(post('/api/returns/RET-000102/decision', decisionBody('approve', 'ahmed')));
    expect(result.status).toBe(403);
    expect(result.example).toBe('error.forbidden_role.json');
    expect(valueOf(result).message).toBe(forbidden.message);
    expect(result.note).toBeNull();
  });

  it('falls back to the role check when no contract route matches', async () => {
    const result = await router(post('/api/returns/RET-000101/decision', decisionBody('approve', 'ahmed')));
    expect(result.status).toBe(403);
    expect(result.example).toBeNull();
    expect(valueOf(result).message).toMatch(/^UI fixture:/);
    expect(result.note).toBe('role check');
  });

  it('needs the same actor: an example of another actor does not lift the role check', async () => {
    const body = { ...readExample<Record<string, unknown>>('inspection_create_request.json'), inspector: 'sara' };
    const refused = await router(post('/api/inspections', body));
    expect(refused.status).toBe(403);
    expect(refused.note).toBe('role check');
    const allowed = await router(post('/api/inspections', readExample('inspection_create_request.json')));
    expect(allowed.status).toBe(201);
    expect(allowed.example).toBe('inspection.json');
  });

  it('answers an allowed approver with the success example', async () => {
    const result = await router(post('/api/returns/RET-000102/decision', decisionBody('approve', 'sara')));
    expect(result.status).toBe(200);
    expect(result.example).toBe('result.approved.json');
  });

  it('serves the refusal of an endpoint without body keys to the actor it names, not a success of another', async () => {
    const update = readExample<Record<string, unknown>>('simulator_assumptions_update.json');
    const assumptions = { method: 'PUT', endpoint: '/api/simulator/assumptions', path: '/api/simulator/assumptions' };
    const table = synthetic(
      [
        { ...assumptions, request: 'update.luca.json', response: 'assumptions.v2.json' },
        { ...assumptions, request: 'update.sara.json', status: 403, response: 'error.forbidden_role.json' },
      ],
      {
        'update.luca.json': update,
        'update.sara.json': { ...update, author: 'sara' },
        'assumptions.v2.json': { version: 'assumptions-v2' },
        'error.forbidden_role.json': forbidden,
      },
    );
    const put = (author: string) => ({ method: 'PUT', path: '/api/simulator/assumptions', headers: {}, body: JSON.stringify({ ...update, author }) });
    const sara = await table.router(put('sara'));
    expect(sara.status).toBe(403);
    expect(sara.example).toBe('error.forbidden_role.json');
    const luca = await table.router(put('luca'));
    expect(luca.status).toBe(200);
    expect(luca.example).toBe('assumptions.v2.json');
  });
});

describe('decision examples by approver', () => {
  const route = (request: string, response: string, extra: Partial<MockRoute> = {}): SyntheticRoute => ({
    method: 'POST',
    endpoint: DECISION,
    path: '/api/returns/RET-000102/decision',
    request,
    response,
    ...extra,
  });
  const files = {
    'req.approve.sara.json': decisionBody('approve', 'sara'),
    'req.approve.luca.json': decisionBody('approve', 'luca'),
    'req.approve.ahmed.json': decisionBody('approve', 'ahmed'),
    'req.reject.sara.json': decisionBody('reject', 'sara'),
    'result.sara.json': { by: 'sara' },
    'result.luca.json': { by: 'luca' },
    'error.forbidden_role.json': { code: 'forbidden_role', message: 'refused', violations: [], request_id: 'x' },
    'error.conflict.json': { code: 'conflict', message: 'already decided', violations: [], request_id: 'x' },
  };
  const at = (approver: string, action = 'approve', scenario?: string) =>
    post('/api/returns/RET-000102/decision', decisionBody(action, approver), scenario);

  it('a different approver matches a different example', async () => {
    const { router } = synthetic([route('req.approve.sara.json', 'result.sara.json'), route('req.approve.luca.json', 'result.luca.json')], files);
    const luca = await router(at('luca'));
    expect(luca.example).toBe('result.luca.json');
    expect(luca.note).toBeNull();
    const sara = await router(at('sara'));
    expect(sara.example).toBe('result.sara.json');
    expect(sara.note).toBeNull();
  });

  it('keeps the action match for an approver without an example, with a note', async () => {
    const { router } = synthetic([route('req.approve.sara.json', 'result.sara.json')], files);
    const luca = await router(at('luca'));
    expect(luca.status).toBe(200);
    expect(luca.example).toBe('result.sara.json');
    expect(luca.note).toMatch(/no example for this request body/);
  });

  it("never answers with an error example of another approver's request", async () => {
    const { router } = synthetic(
      [
        route('req.approve.ahmed.json', 'error.forbidden_role.json', { status: 403 }),
        route('req.reject.sara.json', 'error.conflict.json', { status: 409 }),
        route('req.approve.sara.json', 'result.sara.json'),
      ],
      files,
    );
    const approve = await router(at('luca'));
    expect(approve.status).toBe(200);
    expect(approve.example).toBe('result.sara.json');
    const reject = await router(at('luca', 'reject'));
    expect(reject.status).toBe(200);
    expect(reject.note).toMatch(/no example for this request body/);
    const own = await router(at('sara', 'reject'));
    expect(own.status).toBe(409);
    expect(own.example).toBe('error.conflict.json');
  });

  it('serves the scenario asked for to any approver of the same action', async () => {
    const { router } = synthetic(
      [
        route('req.approve.sara.json', 'result.sara.json'),
        route('req.approve.sara.json', 'error.conflict.json', { status: 409, scenario: 'stale_proposal' }),
      ],
      files,
    );
    const result = await router(at('luca', 'approve', 'stale_proposal'));
    expect(result.status).toBe(409);
    expect(result.example).toBe('error.conflict.json');
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

describe('fallbacks never invent an error or borrow another scenario (UI2)', () => {
  const approve = (decisionId: string) =>
    JSON.stringify({
      decision_id: decisionId,
      action: 'approve',
      levers: [],
      message: { lang: 'it', text: null },
      override_reason: null,
      override_category: null,
      approver: 'sara',
    });

  it("Elena's guarantee approve is not answered with the legal guard refusal of her override example", async () => {
    const result = await route({ method: 'POST', path: '/api/returns/RET-000105/decision', headers: {}, body: approve('DEC-000105-1') });
    expect(result.status).toBeLessThan(400);
    expect(result.example).not.toBe('error.legal_guard.json');
    expect(result.note).toMatch(/no example for this request/);
  });

  it("Simone's approve in the default scenario is not answered with the invalid_request scenario", async () => {
    const result = await route({ method: 'POST', path: '/api/returns/RET-000111/decision', headers: {}, body: approve('DEC-000111-1') });
    expect(result.status).toBeLessThan(400);
    expect(result.example).not.toBe('error.invalid_request.json');
    expect(result.note).toMatch(/no example for this request/);
  });

  it('prefers a success example of the same action on another return', async () => {
    const result = await route({ method: 'POST', path: '/api/returns/RET-000101/decision', headers: {}, body: approve('DEC-000101-1') });
    expect(result.example).toBe('decision_result.marco_approved.json');
  });

  it('still serves an error example when its request matches or its scenario was asked for', async () => {
    const legal = await route({
      method: 'POST',
      path: '/api/returns/RET-000105/decision',
      headers: {},
      body: readExampleText('decision_request.elena_shipping.json'),
    });
    expect(legal.status).toBe(422);
    expect(legal.example).toBe('error.legal_guard.json');
    const invalid = await route({
      method: 'POST',
      path: '/api/returns/RET-000111/decision',
      headers: { 'X-Mock-Scenario': 'invalid_request' },
      body: approve('DEC-000111-1'),
    });
    expect(invalid.status).toBe(422);
    expect(invalid.example).toBe('error.invalid_request.json');
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

  it('keeps a CRLF that straddles two chunks as one line break (UI8)', () => {
    const frame = encodeSseFrame({ type: 'token', seq: 4, text: 'a link is a shared identifier' }).replace(/\n/g, '\r\n');
    const cut = frame.indexOf('\r\n') + 1; // the chunk ends between \r and \n
    const first = parseSseBuffer(frame.slice(0, cut));
    expect(first.messages).toEqual([]);
    const second = parseSseBuffer(first.rest + frame.slice(cut));
    expect(second.messages).toHaveLength(1);
    expect(second.messages[0]?.event).toBe('token');
    expect(JSON.parse(second.messages[0]?.data ?? '{}')).toMatchObject({ type: 'token', seq: 4 });
    // Every cut point of a CRLF stream gives the same single message.
    for (let i = 1; i < frame.length; i++) {
      const a = parseSseBuffer(frame.slice(0, i));
      const b = parseSseBuffer(a.rest + frame.slice(i));
      expect([...a.messages, ...b.messages].map((m) => m.event)).toEqual(['token']);
    }
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
