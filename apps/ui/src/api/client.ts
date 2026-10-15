// The typed API client (docs/ui-design.md 11.2): one function per route of ADR-15. Types come
// from the contract: each route's response, body and query are the models endpoints.json names,
// so a contract change breaks the compile here first.
import { adapter } from './adapters';
import { ENDPOINTS } from './contract.gen';
import { ApiError, isErrorResponse, parseRetryAfter } from './errors';
import { recordAsOf, recordLlmUnavailable, recordResponse } from './meta';
import { scenarioStore } from './scenario';
import { readAgentEvents, type AgentEvent } from './sse';
import type { ApiSchemas } from './types.gen';

type Endpoint = (typeof ENDPOINTS)[number];
type EndpointByKey = { [E in Endpoint as `${E['method']} ${E['path']}`]: E };
export type EndpointKey = keyof EndpointByKey;

type ModelOf<Name> = Name extends keyof ApiSchemas ? ApiSchemas[Name] : never;
export type ResponseOf<K extends EndpointKey> = ModelOf<EndpointByKey[K]['response']>;
export type BodyOf<K extends EndpointKey> = ModelOf<EndpointByKey[K]['request']>;
export type QueryOf<K extends EndpointKey> = Partial<ModelOf<EndpointByKey[K]['query']>>;

type PathOf<K extends string> = K extends `${string} ${infer P}` ? P : never;
type ParamNames<P extends string> = P extends `${string}{${infer Name}}${infer Rest}` ? Name | ParamNames<Rest> : never;
export type PathParams<K extends EndpointKey> = Record<ParamNames<PathOf<K>>, string>;

export interface CallOptions {
  signal?: AbortSignal;
}

interface CallInit<K extends EndpointKey> extends CallOptions {
  params?: PathParams<K>;
  query?: QueryOf<K> | Record<string, unknown>;
  body?: BodyOf<K>;
  accept?: 'json' | 'text' | 'stream';
}

/** Every route the client calls, so a test can compare it with endpoints.json. */
export const CLIENT_ROUTES = new Set<EndpointKey>();

function endpointFor<K extends EndpointKey>(key: K): Endpoint {
  const [method, path] = key.split(' ') as [string, string];
  const endpoint = ENDPOINTS.find((e) => e.method === method && e.path === path);
  if (!endpoint) throw new Error(`client: ${key} is not in endpoints.json`);
  return endpoint;
}

/** Query string with repeated list parameters (`quadrant=Q3&quadrant=Q4`), nulls left out. */
export function toQueryString(query: Record<string, unknown> | undefined): string {
  if (!query) return '';
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) for (const item of value) search.append(name, String(item));
    else search.append(name, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

function fillPath(template: string, params: Record<string, string> | undefined): string {
  return template.replace(/\{([^}]+)\}/g, (_, name: string) => {
    const value = params?.[name];
    if (value === undefined) throw new Error(`client: missing path parameter ${name} for ${template}`);
    return encodeURIComponent(value);
  });
}

async function send<K extends EndpointKey>(key: K, init: CallInit<K>): Promise<Response> {
  const endpoint = endpointFor(key);
  const path = fillPath(endpoint.path, init.params as Record<string, string> | undefined) + toQueryString(init.query as Record<string, unknown>);
  const headers: Record<string, string> = {
    Accept: init.accept === 'stream' ? 'text/event-stream' : init.accept === 'text' ? 'text/csv' : 'application/json',
  };
  if (init.body !== undefined) headers['Content-Type'] = 'application/json';
  const scenario = scenarioStore.get();
  if (scenario !== 'default') headers['X-Mock-Scenario'] = scenario;

  let response: Response;
  try {
    response = await adapter.fetch({
      method: endpoint.method,
      path,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: init.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError({ status: 0, code: 'aborted', message: 'The request was cancelled.' });
    }
    throw new ApiError({ status: 0, code: 'network', message: 'The service did not answer.' });
  }
  recordResponse(response.headers);
  if (!response.ok) throw await toApiError(response);
  return response;
}

/**
 * A 401 comes from the proxy, not the API: on a public server Caddy protects the admin data
 * (/api/telemetry) with basic auth, and every write when the views are not read-only
 * (infra/Caddyfile). The browser asks for the password itself; this is what is left if it did not.
 */
export const PROTECTED_MESSAGE =
  'Protected by the admin sign-in of this server (basic auth, user admin): retry and enter the password when the browser asks.';

async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('x-request-id');
  if (response.status === 401) {
    return new ApiError({ status: 401, code: 'protected', message: PROTECTED_MESSAGE, requestId });
  }
  const retryAfterS = response.status === 429 ? parseRetryAfter(response.headers.get('retry-after')) : null;
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (isErrorResponse(body)) {
    if (body.code === 'llm_unavailable') recordLlmUnavailable();
    return new ApiError({
      status: response.status,
      code: body.code,
      message: body.message,
      violations: body.violations,
      requestId: body.request_id ?? requestId,
      retryAfterS,
    });
  }
  return new ApiError({
    status: response.status,
    code: 'unexpected_response',
    message: `The service answered ${response.status} without an error body.`,
    requestId,
    retryAfterS,
  });
}

/** Low-level call: JSON in and out, ApiError on any non-2xx (docs/ui-design.md 11.2). */
export async function apiFetch<K extends EndpointKey>(key: K, init: CallInit<K> = {}): Promise<ResponseOf<K>> {
  CLIENT_ROUTES.add(key);
  const response = await send(key, { ...init, accept: 'json' });
  const type = response.headers.get('content-type') ?? '';
  if (!type.includes('application/json')) {
    throw new ApiError({
      status: response.status,
      code: 'unexpected_response',
      message: `Expected JSON, got ${type || 'no content type'}.`,
      requestId: response.headers.get('x-request-id'),
    });
  }
  const body = (await response.json()) as ResponseOf<K>;
  recordAsOf(body);
  return body;
}

function route<K extends EndpointKey>(key: K): K {
  endpointFor(key);
  CLIENT_ROUTES.add(key);
  return key;
}

const R = {
  healthz: route('GET /healthz'),
  readyz: route('GET /readyz'),
  queue: route('GET /api/returns'),
  case: route('GET /api/returns/{return_id}'),
  decide: route('POST /api/returns/{return_id}/decision'),
  decision: route('GET /api/decisions/{decision_id}'),
  regenerate: route('POST /api/returns/{return_id}/regenerate'),
  evidence: route('POST /api/returns/{return_id}/evidence'),
  dispute: route('POST /api/links/{link_id}/dispute'),
  checkoutEvaluate: route('POST /api/checkout/evaluate'),
  checkoutConfirm: route('POST /api/checkout/confirm'),
  inspectionCreate: route('POST /api/inspections'),
  inspectionConfirm: route('POST /api/inspections/{inspection_id}/confirm'),
  matrix: route('GET /api/portfolio/matrix'),
  assumptions: route('GET /api/simulator/assumptions'),
  assumptionsUpdate: route('PUT /api/simulator/assumptions'),
  kpi: route('GET /api/kpi'),
  fairness: route('GET /api/fairness'),
  audit: route('GET /api/audit'),
  outbox: route('GET /api/outbox'),
  anomalies: route('GET /api/products/anomalies'),
  tasks: route('GET /api/catalog-tasks'),
  taskCreate: route('POST /api/catalog-tasks'),
  taskUpdate: route('PATCH /api/catalog-tasks/{task_id}'),
  couponRules: route('GET /api/coupon-rules'),
  couponRuleCreate: route('POST /api/coupon-rules'),
  couponReverse: route('POST /api/coupon-rules/applications/{application_id}/reverse'),
  agentAsk: route('POST /api/agent/ask'),
  benchmarks: route('GET /api/benchmarks'),
  telemetry: route('GET /api/telemetry'),
  demoCases: route('GET /api/demo/cases'),
} as const;

type Q<K extends EndpointKey> = QueryOf<K>;
type B<K extends EndpointKey> = BodyOf<K>;

/** Every route of ADR-15 section 3 as a typed function. Actor fields are the caller's to fill. */
export const api = {
  healthz: (o?: CallOptions) => apiFetch(R.healthz, o),
  readyz: (o?: CallOptions) => apiFetch(R.readyz, o),

  listReturns: (query?: Q<typeof R.queue>, o?: CallOptions) => apiFetch(R.queue, { query, ...o }),
  getCase: (returnId: string, o?: CallOptions) => apiFetch(R.case, { params: { return_id: returnId }, ...o }),
  decide: (returnId: string, body: B<typeof R.decide>, o?: CallOptions) =>
    apiFetch(R.decide, { params: { return_id: returnId }, body, ...o }),
  getDecision: (decisionId: string, o?: CallOptions) => apiFetch(R.decision, { params: { decision_id: decisionId }, ...o }),
  regenerate: (returnId: string, body: B<typeof R.regenerate>, o?: CallOptions) =>
    apiFetch(R.regenerate, { params: { return_id: returnId }, body, ...o }),
  addEvidence: (returnId: string, body: B<typeof R.evidence>, o?: CallOptions) =>
    apiFetch(R.evidence, { params: { return_id: returnId }, body, ...o }),
  disputeLink: (linkId: string, body: B<typeof R.dispute>, o?: CallOptions) =>
    apiFetch(R.dispute, { params: { link_id: linkId }, body, ...o }),

  checkoutEvaluate: (body: B<typeof R.checkoutEvaluate>, o?: CallOptions) => apiFetch(R.checkoutEvaluate, { body, ...o }),
  checkoutConfirm: (body: B<typeof R.checkoutConfirm>, o?: CallOptions) => apiFetch(R.checkoutConfirm, { body, ...o }),

  createInspection: (body: B<typeof R.inspectionCreate>, o?: CallOptions) => apiFetch(R.inspectionCreate, { body, ...o }),
  confirmInspection: (inspectionId: string, body: B<typeof R.inspectionConfirm>, o?: CallOptions) =>
    apiFetch(R.inspectionConfirm, { params: { inspection_id: inspectionId }, body, ...o }),

  getMatrix: (o?: CallOptions) => apiFetch(R.matrix, o),
  getAssumptions: (o?: CallOptions) => apiFetch(R.assumptions, o),
  updateAssumptions: (body: B<typeof R.assumptionsUpdate>, o?: CallOptions) => apiFetch(R.assumptionsUpdate, { body, ...o }),
  getKpi: (query?: Q<typeof R.kpi>, o?: CallOptions) => apiFetch(R.kpi, { query, ...o }),
  getFairness: (o?: CallOptions) => apiFetch(R.fairness, o),
  getAudit: (query?: Q<typeof R.audit>, o?: CallOptions) => apiFetch(R.audit, { query: { ...query, format: null }, ...o }),
  /** The audit log as CSV (`format=csv`), for the download. */
  getAuditCsv: async (query?: Q<typeof R.audit>, o?: CallOptions): Promise<string> => {
    const response = await send(R.audit, { query: { ...query, format: 'csv' }, accept: 'text', ...o });
    return response.text();
  },
  getOutbox: (query?: Q<typeof R.outbox>, o?: CallOptions) => apiFetch(R.outbox, { query, ...o }),

  getAnomalies: (query?: Q<typeof R.anomalies>, o?: CallOptions) => apiFetch(R.anomalies, { query, ...o }),
  listCatalogTasks: (o?: CallOptions) => apiFetch(R.tasks, o),
  createCatalogTask: (body: B<typeof R.taskCreate>, o?: CallOptions) => apiFetch(R.taskCreate, { body, ...o }),
  updateCatalogTask: (taskId: string, body: B<typeof R.taskUpdate>, o?: CallOptions) =>
    apiFetch(R.taskUpdate, { params: { task_id: taskId }, body, ...o }),

  getCouponRules: (o?: CallOptions) => apiFetch(R.couponRules, o),
  createCouponRule: (body: B<typeof R.couponRuleCreate>, o?: CallOptions) => apiFetch(R.couponRuleCreate, { body, ...o }),
  reverseCouponApplication: (applicationId: string, body: B<typeof R.couponReverse>, o?: CallOptions) =>
    apiFetch(R.couponReverse, { params: { application_id: applicationId }, body, ...o }),

  /**
   * POST /api/agent/ask: the answer streams as server-sent events, read with fetch (EventSource
   * cannot POST). Abort with the signal when the drawer closes or a new question is asked.
   */
  askAgent: async function* (body: B<typeof R.agentAsk>, o?: CallOptions): AsyncGenerator<AgentEvent> {
    const response = await send(R.agentAsk, { body, accept: 'stream', ...o });
    if (!response.body) throw new ApiError({ status: response.status, code: 'unexpected_response', message: 'The stream has no body.' });
    yield* readAgentEvents(response.body, o?.signal);
  },

  getBenchmarks: (o?: CallOptions) => apiFetch(R.benchmarks, o),
  getTelemetry: (o?: CallOptions) => apiFetch(R.telemetry, o),

  /** The golden cases of the demo with their live ids (404 before a reset: python -m apps.api.reset). */
  demoCases: (o?: CallOptions) => apiFetch(R.demoCases, o),
};

export type Api = typeof api;
