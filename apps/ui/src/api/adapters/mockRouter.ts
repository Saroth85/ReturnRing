// The mock server's routing table (docs/ui-design.md 11.5), shared by the browser mock adapter
// (adapters/mock.ts) and the Vite dev-server middleware (mock/returnring-mock.ts). Pure code: it
// reads examples through an injected loader and returns a MockResult, never touching the DOM or
// the file system. Relative imports only: the Vite config bundles this file too.
import { CONTRACT_VERSION, DEMO_USERS, ENDPOINTS, QUERY_PARAMS } from '../contract.gen';
import type { AgentEvent } from '../sse';
import type { AuditPage, CaseCard, ErrorResponse, OutboxResponse, QueueItem, QueueResponse } from '../types.gen';
import { UI_FIXTURE_SCENARIOS } from './fixtures';

export interface MockRoute {
  method: string;
  endpoint: string;
  path: string;
  query: string | null;
  request: string | null;
  scenario: string;
  status: number;
  media_type: string;
  response: string;
}

export interface MockIndex {
  as_of: string;
  routes: MockRoute[];
}

/** Loads a parsed example by file name (e.g. `case_card.marco.json`). */
export type ExampleLoader = (file: string) => Promise<unknown>;

export interface MockRequest {
  method: string;
  /** Path and query string, e.g. `/api/returns?review_due=true`. */
  path: string;
  headers: Record<string, string>;
  body?: string;
}

export type MockBody = { kind: 'json'; value: unknown } | { kind: 'sse'; events: AgentEvent[] } | { kind: 'text'; value: string };

export interface MockResult {
  status: number;
  body: MockBody;
  headers: Record<string, string>;
  /** The example file served, when one was. */
  example: string | null;
  /** Why the answer is not an exact contract route (fallback, filter, UI fixture), if so. */
  note: string | null;
}

export { UI_FIXTURE_SCENARIOS };

export type Endpoint = (typeof ENDPOINTS)[number];

const SCENARIO_HEADER = 'x-mock-scenario';

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** `/api/returns/{return_id}` -> a regex matching one concrete path. */
export function templateRegex(template: string): RegExp {
  const source = template
    .split(/(\{[^}]+\})/)
    .map((part) => (part.startsWith('{') ? '([^/]+)' : escapeRegex(part)))
    .join('');
  return new RegExp(`^${source}$`);
}

export function findEndpoint(method: string, pathname: string): Endpoint | null {
  const upper = method.toUpperCase();
  return ENDPOINTS.find((e) => e.method === upper && templateRegex(e.path).test(pathname)) ?? null;
}

function decodePath(pathname: string): string {
  return pathname
    .split('/')
    .map((segment) => {
      try {
        return decodeURIComponent(segment);
      } catch {
        return segment;
      }
    })
    .join('/');
}

type QueryModel = keyof typeof QUERY_PARAMS;

/** The query string parsed into its model with the defaults of the schema (ADR-15). */
export function normaliseQuery(model: QueryModel, search: URLSearchParams): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, spec] of Object.entries(QUERY_PARAMS[model])) {
    const values = search.getAll(name);
    if (values.length === 0) {
      out[name] = spec.default;
    } else if (spec.kind === 'array') {
      out[name] = values;
    } else if (spec.kind === 'number') {
      out[name] = Number(values[0]);
    } else if (spec.kind === 'boolean') {
      out[name] = values[0] === 'true';
    } else {
      out[name] = values[0];
    }
  }
  return out;
}

function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    const sa = [...a].map(String).sort();
    const sb = [...b].map(String).sort();
    return sa.every((x, i) => x === sb[i]);
  }
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const ka = Object.keys(a as object);
    const kb = Object.keys(b as object);
    if (ka.length !== kb.length) return false;
    return ka.every((k) => sameValue((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
  }
  return a === b;
}

/** Fields compared between a request body and the request example (free text never is). */
const BODY_KEYS: Record<string, (body: Record<string, unknown> | null) => unknown> = {
  'POST /api/returns/{return_id}/decision': (b) => b?.action,
  'POST /api/agent/ask': (b) => b?.question,
  'POST /api/checkout/evaluate': (b) => b?.customer_id,
  'POST /api/checkout/confirm': (b) => b?.customer_id,
  // The two inspection examples carry the same photo, so the return decides (RET-000111 is the
  // manual review), together with the photo of docs/ui-design.md 11.5.
  'POST /api/inspections': (b) => [b?.return_id, (b?.photo as Record<string, unknown> | undefined)?.data_base64],
};

/** QueueQuery.matches of apps/api/schemas.py, the reference meaning of the queue filters. */
export function queueItemMatches(item: QueueItem, q: Record<string, unknown>): boolean {
  const quadrant = item.quadrant ?? 'none';
  const text = typeof q.q === 'string' ? q.q.toLowerCase() : null;
  const fields = [item.return_id, item.order_id, item.customer_id, item.display_name ?? ''];
  const list = (v: unknown) => (Array.isArray(v) ? (v as string[]) : null);
  const state = list(q.state);
  const quadrants = list(q.quadrant);
  const paths = list(q.path);
  return (
    (state === null || state.includes(item.state)) &&
    (quadrants === null || quadrants.includes(quadrant)) &&
    (paths === null || paths.includes(item.path)) &&
    (q.evidence_quality == null || item.evidence_quality === q.evidence_quality) &&
    (q.requires_approval == null || item.requires_approval === q.requires_approval) &&
    (q.review_due == null || item.review_due === q.review_due) &&
    (text === null || fields.some((f) => f.toLowerCase().includes(text)))
  );
}

function sortQueue(items: QueueItem[], sort: string): QueueItem[] {
  const byId = (a: QueueItem, b: QueueItem) => a.return_id.localeCompare(b.return_id);
  const sorted = [...items];
  switch (sort) {
    case 'expected_net_benefit':
      return sorted.sort((a, b) => a.expected_net_benefit.value - b.expected_net_benefit.value || byId(a, b));
    case '-requested_at':
      return sorted.sort((a, b) => b.requested_at.localeCompare(a.requested_at) || byId(a, b));
    case 'requested_at':
      return sorted.sort((a, b) => a.requested_at.localeCompare(b.requested_at) || byId(a, b));
    case '-risk':
      // Unscored items (guarantee path, not enough evidence) last.
      return sorted.sort((a, b) => (b.risk?.value ?? -1) - (a.risk?.value ?? -1) || byId(a, b));
    default:
      return sorted.sort((a, b) => b.expected_net_benefit.value - a.expected_net_benefit.value || byId(a, b));
  }
}

export function filterQueue(base: QueueResponse, q: Record<string, unknown>): QueueResponse {
  const matching = sortQueue(
    base.items.filter((item) => queueItemMatches(item, q)),
    String(q.sort ?? '-expected_net_benefit'),
  );
  const page = Math.max(1, Number(q.page ?? 1));
  const pageSize = Math.max(1, Number(q.page_size ?? 25));
  return {
    ...base,
    items: matching.slice((page - 1) * pageSize, page * pageSize),
    total: matching.length,
    page,
    page_size: pageSize,
    sort: String(q.sort ?? base.sort) as QueueResponse['sort'],
  };
}

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** CSV of audit entries for `format=csv` (UI fixture: the contract ships no CSV example). */
export function auditCsv(page: AuditPage): string {
  const columns = page.items.length > 0 ? Object.keys(page.items[0] as object) : [];
  const rows = page.items.map((entry) => columns.map((c) => csvCell((entry as unknown as Record<string, unknown>)[c])).join(','));
  return `${[columns.join(','), ...rows].join('\n')}\n`;
}

export interface MockRouterOptions {
  /** Prefix of generated request ids, so tests can tell mock answers apart. */
  requestIdPrefix?: string;
}

export function createMockRouter(index: MockIndex, load: ExampleLoader, options: MockRouterOptions = {}) {
  let counter = 0;
  const prefix = options.requestIdPrefix ?? 'mock-req';
  const knownScenarios = new Set<string>([...index.routes.map((r) => r.scenario), ...UI_FIXTURE_SCENARIOS]);

  function nextRequestId(): string {
    counter += 1;
    return `${prefix}-${String(counter).padStart(6, '0')}`;
  }

  function fixtureError(code: ErrorResponse['code'], message: string, requestId: string): MockBody {
    const body: ErrorResponse = { code, message: `UI fixture: ${message}`, violations: [], request_id: requestId };
    return { kind: 'json', value: body };
  }

  return async function route(request: MockRequest): Promise<MockResult> {
    const url = new URL(request.path, 'http://mock.local');
    const pathname = decodePath(url.pathname);
    const method = request.method.toUpperCase();
    const headers = Object.fromEntries(Object.entries(request.headers).map(([k, v]) => [k.toLowerCase(), v]));
    const requested = headers[SCENARIO_HEADER] || 'default';
    const scenario = knownScenarios.has(requested) ? requested : 'default';
    const requestId = nextRequestId();
    let body: Record<string, unknown> | null = null;
    if (request.body) {
      try {
        body = JSON.parse(request.body) as Record<string, unknown>;
      } catch {
        body = null;
      }
    }

    const notes: string[] = [];
    if (scenario !== requested) notes.push(`unknown scenario ${requested}: served default`);
    const reply = (status: number, replyBody: MockBody, example: string | null, note: string | null = null): MockResult => {
      if (note) notes.push(note);
      const mediaType =
        replyBody.kind === 'sse' ? 'text/event-stream' : replyBody.kind === 'text' ? 'text/csv; charset=utf-8' : 'application/json';
      const replyHeaders: Record<string, string> = {
        'content-type': mediaType,
        'x-returnring-mode': scenario === 'cached' ? 'cached' : 'live',
        'x-request-id': requestId,
      };
      if (example) replyHeaders['x-mock-example'] = example;
      if (notes.length > 0) replyHeaders['x-mock-note'] = notes.join('; ');
      return { status, body: replyBody, headers: replyHeaders, example, note: notes.length > 0 ? notes.join('; ') : null };
    };
    const withRequestId = (value: unknown): unknown =>
      value && typeof value === 'object' && 'request_id' in (value as object) && 'code' in (value as object)
        ? { ...(value as object), request_id: requestId }
        : value;

    const endpoint = findEndpoint(method, pathname);
    if (!endpoint) {
      const notFound = (await load('error.not_found.json')) as ErrorResponse;
      return reply(404, { kind: 'json', value: { ...notFound, message: `${method} ${pathname} is not a route of the contract`, request_id: requestId } }, 'error.not_found.json', 'no such route');
    }
    const key = `${endpoint.method} ${endpoint.path}`;

    // Roles (ADR-15 section 5): the actor field of the body names a demo user and their role.
    if (endpoint.actorField && method !== 'GET') {
      const actor = body?.[endpoint.actorField];
      const user = DEMO_USERS.find((u) => u.id === actor);
      if (!user || !(endpoint.roles as readonly string[]).includes(user.role)) {
        const who = user ? `${user.id} (${user.role})` : `actor ${String(actor ?? 'missing')}`;
        return reply(
          403,
          fixtureError('forbidden_role', `${who} cannot call ${key}; allowed roles: ${endpoint.roles.join(', ')}`, requestId),
          null,
          'role check',
        );
      }
    }

    // UI-only scenarios (open question O-1).
    const errors = endpoint.errors as readonly number[];
    if (scenario === 'forbidden_role' && method !== 'GET' && errors.includes(403)) {
      return reply(403, fixtureError('forbidden_role', `your role cannot call ${key}`, requestId), null, 'UI fixture');
    }
    if (scenario === 'llm_unavailable' && errors.includes(503)) {
      return reply(503, fixtureError('llm_unavailable', 'the language model is unavailable; templates still work', requestId), null, 'UI fixture');
    }
    if (scenario === 'rate_limited' && errors.includes(429)) {
      return reply(429, fixtureError('rate_limited', 'too many questions in a short time', requestId), null, 'UI fixture');
    }
    if (scenario === 'conflict' && method !== 'GET' && errors.includes(409)) {
      return reply(409, fixtureError('conflict', 'already decided by luca at 18:20 UTC; nothing was saved', requestId), null, 'UI fixture');
    }

    // Example lookup: method and concrete path, then scenario, then query or body keys.
    const samePath = index.routes.filter((r) => r.method === method && r.path === pathname);
    const endpointRoutes = index.routes.filter((r) => r.method === method && r.endpoint === endpoint.path);
    const notFoundReply = async (note: string, message?: string) => {
      const notFound = (await load('error.not_found.json')) as ErrorResponse;
      return reply(404, { kind: 'json', value: { ...notFound, ...(message ? { message } : {}), request_id: requestId } }, 'error.not_found.json', note);
    };

    let chosen: MockRoute | null = null;
    let filterQuery: Record<string, unknown> | null = null;
    if (method === 'GET') {
      let candidates = samePath;
      let fallbackNote: string | null = null;
      if (candidates.length === 0) {
        if (endpoint.path.includes('{')) return notFoundReply('no example for this id', `${pathname} not found (no contract example for it)`);
        candidates = endpointRoutes;
        if (candidates.length > 0) fallbackNote = `no example for ${pathname}; served the first example of ${endpoint.path}`;
      }
      if (candidates.length === 0) return notFoundReply('no example for this route');
      // The requested scenario, else the default one: never a route of another scenario.
      const inScenario = candidates.filter((r) => r.scenario === scenario);
      const choices = inScenario.length > 0 ? inScenario : candidates.filter((r) => r.scenario === 'default');
      if (choices.length === 0) return notFoundReply(`no example in the ${scenario} scenario`);
      const model = endpoint.query as QueryModel | null;
      const normalised = model ? normaliseQuery(model, url.searchParams) : null;
      if (normalised) {
        for (const r of choices.filter((c) => c.query)) {
          if (sameValue(normalised, await load(r.query as string))) {
            chosen = r;
            break;
          }
        }
      }
      if (!chosen) {
        chosen = choices.find((c) => !c.query) ?? null;
        if (chosen && normalised && endpoint.path === '/api/returns' && url.searchParams.toString() !== '') {
          filterQuery = normalised;
        } else if (chosen && normalised && url.searchParams.toString() !== '' && endpoint.path !== '/api/audit' && endpoint.path !== '/api/outbox') {
          notes.push('query not in the examples: served the example without query');
        }
      }
      if (!chosen) {
        chosen = choices[0] ?? null;
        if (chosen && url.searchParams.toString() !== '') notes.push(`no example for this query; served ${chosen.response}`);
      }
      if (fallbackNote) notes.push(fallbackNote);
    } else {
      // Writes (UI2): an error example is served only when its request matches the body or its
      // scenario was asked for. Otherwise a success example of the same endpoint, with a note, so
      // a valid action is never answered with a refusal that belongs to another request.
      const keyOf = BODY_KEYS[key];
      const wanted = keyOf ? keyOf(body) : undefined;
      const matches = async (r: MockRoute) =>
        keyOf ? r.request !== null && sameValue(wanted, keyOf((await load(r.request)) as Record<string, unknown>)) : true;
      const firstOf = async (routes: MockRoute[], ok: (r: MockRoute) => Promise<boolean> | boolean) => {
        for (const r of routes) if (await ok(r)) return r;
        return null;
      };
      const asked = scenario === 'default' ? [] : samePath.filter((r) => r.scenario === scenario);
      const askedEndpoint = scenario === 'default' ? [] : endpointRoutes.filter((r) => r.scenario === scenario);
      const defaults = samePath.filter((r) => r.scenario === 'default');
      chosen =
        (await firstOf(asked, (r) => r.request === null || matches(r))) ??
        (await firstOf(askedEndpoint, (r) => r.request === null || matches(r))) ??
        (await firstOf(defaults, async (r) => (keyOf ? matches(r) : r.status < 400))) ??
        null;
      if (!chosen) {
        const successes = endpointRoutes.filter((r) => r.scenario === 'default' && r.status < 400);
        chosen = (await firstOf(successes, matches)) ?? successes[0] ?? null;
        if (chosen) {
          const where = chosen.path === pathname ? '' : ` (an example for ${chosen.path})`;
          notes.push(`no example for this request body on ${pathname}; served ${chosen.response}${where}`);
        }
      }
    }
    if (!chosen) {
      const notFound = (await load('error.not_found.json')) as ErrorResponse;
      return reply(404, { kind: 'json', value: { ...notFound, request_id: requestId } }, 'error.not_found.json', 'no example matched');
    }

    const example = await load(chosen.response);

    // Streams: the contract's event list, written frame by frame by the adapter.
    if (chosen.media_type === 'text/event-stream') {
      let events = example as AgentEvent[];
      if (scenario === 'agent_error') {
        const done = events.find((e) => e.type === 'done');
        events = [
          { type: 'error', seq: 0, code: 'llm_unavailable', message: 'UI fixture: the language model stopped answering' },
          ...(done ? [{ ...done, seq: 1, steps_used: 0 }] : []),
        ];
        notes.push('UI fixture');
      }
      return reply(chosen.status, { kind: 'sse', events }, chosen.response);
    }

    let value: unknown = withRequestId(example);
    if (endpoint.path === '/api/returns' && method === 'GET') {
      const base = value as QueueResponse;
      if (scenario === 'empty') {
        value = { ...base, items: [], total: 0 };
        notes.push('UI fixture: empty queue');
      } else if (filterQuery) {
        value = filterQueue(base, filterQuery);
        notes.push('filtered by the mock with QueueQuery.matches');
      }
    }
    if (endpoint.path === '/readyz' && scenario === 'contract_mismatch') {
      const [major = '1', minor = '0'] = CONTRACT_VERSION.split('.');
      value = { ...(value as object), contract_version: `${major}.${Number(minor) + 1}.0` };
      notes.push('UI fixture: another contract version');
    }
    if (endpoint.path === '/api/returns/{return_id}' && method === 'GET' && scenario === 'draft_pending' && chosen.status === 200) {
      value = { ...(value as CaseCard), draft: null, draft_status: 'pending' };
      notes.push('UI fixture: draft pending');
    }
    const returnId = url.searchParams.get('return_id');
    if (endpoint.path === '/api/outbox' && returnId) {
      const page = value as OutboxResponse;
      const items = page.items.filter((m) => m.return_id === returnId);
      value = { ...page, items, total: items.length };
    }
    if (endpoint.path === '/api/audit') {
      let page = value as AuditPage;
      if (returnId) {
        const items = page.items.filter((e) => (e as unknown as Record<string, unknown>).return_id === returnId);
        page = { ...page, items, total: items.length };
      }
      value = page;
      if (url.searchParams.get('format') === 'csv') {
        return reply(chosen.status, { kind: 'text', value: auditCsv(page) }, chosen.response, 'UI fixture: CSV built from the JSON example');
      }
    }
    return reply(chosen.status, { kind: 'json', value }, chosen.response);
  };
}

export type MockRouter = ReturnType<typeof createMockRouter>;
