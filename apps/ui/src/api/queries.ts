// Read hooks over the typed client (TanStack Query). Keys and freshness follow
// docs/ui-design.md 11.2; views use these hooks instead of calling the client directly.
import { keepPreviousData, QueryClient, useQuery } from '@tanstack/react-query';
import { api, type QueryOf } from './client';
import { isApiError } from './errors';

const SECOND = 1000;

export const queryKeys = {
  healthz: ['healthz'] as const,
  readyz: ['readyz'] as const,
  queue: (query: QueueQueryParams) => ['queue', query] as const,
  queueCount: (tab: string, query: QueueQueryParams) => ['queueCount', tab, query] as const,
  case: (returnId: string) => ['case', returnId] as const,
  decision: (decisionId: string) => ['decision', decisionId] as const,
  audit: (query: AuditQueryParams) => ['audit', query] as const,
  outbox: (query: OutboxQueryParams) => ['outbox', query] as const,
  matrix: ['matrix'] as const,
  assumptions: ['assumptions'] as const,
  kpi: (week: string | null) => ['kpi', week] as const,
  fairness: ['fairness'] as const,
  anomalies: (query: AnomaliesQueryParams) => ['anomalies', query] as const,
  tasks: ['tasks'] as const,
  couponRules: ['couponRules'] as const,
  benchmarks: ['benchmarks'] as const,
  telemetry: ['telemetry'] as const,
  demoCases: ['demoCases'] as const,
};

export type QueueQueryParams = QueryOf<'GET /api/returns'>;
export type AuditQueryParams = QueryOf<'GET /api/audit'>;
export type OutboxQueryParams = QueryOf<'GET /api/outbox'>;
export type AnomaliesQueryParams = QueryOf<'GET /api/products/anomalies'>;

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Client errors (404, 403, 409, 422) are answers, not glitches: never retried.
        retry: (count, error) => !(isApiError(error) && error.status >= 400 && error.status < 500) && count < 2,
        refetchOnWindowFocus: true,
        staleTime: 30 * SECOND,
      },
      mutations: { retry: false },
    },
  });
}

export const useHealthz = () =>
  useQuery({ queryKey: queryKeys.healthz, queryFn: ({ signal }) => api.healthz({ signal }), refetchInterval: 30 * SECOND });

export const useReadyz = () =>
  useQuery({ queryKey: queryKeys.readyz, queryFn: ({ signal }) => api.readyz({ signal }), refetchInterval: 30 * SECOND });

export const useQueue = (query: QueueQueryParams = {}, options: { enabled?: boolean } = {}) =>
  useQuery({
    queryKey: queryKeys.queue(query),
    queryFn: ({ signal }) => api.listReturns(query, { signal }),
    enabled: options.enabled ?? true,
    staleTime: 15 * SECOND,
    refetchInterval: 30 * SECOND,
    placeholderData: keepPreviousData,
  });

/** The `total` of a filtered queue with page_size 1 (tab and nav badge counts, design 3.3). */
export const useQueueCount = (tab: string, query: QueueQueryParams) =>
  useQuery({
    queryKey: queryKeys.queueCount(tab, query),
    queryFn: ({ signal }) => api.listReturns({ ...query, page: 1, page_size: 1 }, { signal }),
    select: (page) => ({ total: page.total, provenance: page.provenance, asOf: page.as_of }),
    staleTime: 30 * SECOND,
    refetchInterval: 30 * SECOND,
  });

export const useCase = (returnId: string | undefined) =>
  useQuery({
    queryKey: queryKeys.case(returnId ?? ''),
    queryFn: ({ signal }) => api.getCase(returnId as string, { signal }),
    enabled: Boolean(returnId),
    staleTime: 60 * SECOND,
  });

/** Loads a case card into the cache ahead of navigation (queue rows, the next cases of a view). */
export const prefetchCase = (client: QueryClient, returnId: string) =>
  client.prefetchQuery({
    queryKey: queryKeys.case(returnId),
    queryFn: ({ signal }) => api.getCase(returnId, { signal }),
    staleTime: 60 * SECOND,
  });

export const useDecisionRecord = (decisionId: string | undefined) =>
  useQuery({
    queryKey: queryKeys.decision(decisionId ?? ''),
    queryFn: ({ signal }) => api.getDecision(decisionId as string, { signal }),
    enabled: Boolean(decisionId),
    staleTime: Infinity,
  });

export const useAudit = (query: AuditQueryParams = {}) =>
  useQuery({ queryKey: queryKeys.audit(query), queryFn: ({ signal }) => api.getAudit(query, { signal }) });

export const useOutbox = (query: OutboxQueryParams = {}) =>
  useQuery({ queryKey: queryKeys.outbox(query), queryFn: ({ signal }) => api.getOutbox(query, { signal }) });

export const useMatrix = () =>
  useQuery({ queryKey: queryKeys.matrix, queryFn: ({ signal }) => api.getMatrix({ signal }), staleTime: Infinity });

export const useAssumptions = () =>
  useQuery({ queryKey: queryKeys.assumptions, queryFn: ({ signal }) => api.getAssumptions({ signal }) });

export const useKpi = (week: string | null) =>
  useQuery({
    queryKey: queryKeys.kpi(week),
    queryFn: ({ signal }) => api.getKpi(week ? { week_start: week } : {}, { signal }),
  });

export const useFairness = () =>
  useQuery({ queryKey: queryKeys.fairness, queryFn: ({ signal }) => api.getFairness({ signal }) });

export const useAnomalies = (query: AnomaliesQueryParams = {}) =>
  useQuery({ queryKey: queryKeys.anomalies(query), queryFn: ({ signal }) => api.getAnomalies(query, { signal }) });

export const useCatalogTasks = () =>
  useQuery({ queryKey: queryKeys.tasks, queryFn: ({ signal }) => api.listCatalogTasks({ signal }) });

export const useCouponRules = () =>
  useQuery({ queryKey: queryKeys.couponRules, queryFn: ({ signal }) => api.getCouponRules({ signal }) });

export const useBenchmarks = () =>
  useQuery({ queryKey: queryKeys.benchmarks, queryFn: ({ signal }) => api.getBenchmarks({ signal }) });

/**
 * The golden cases of the demo with their live ids (GET /api/demo/cases). They change only with a
 * reset, so they are loaded once; a 404 (no reset yet) is an answer, never retried. Every role
 * reads them, so the shell can load them ahead of the views.
 */
export const useDemoCases = () =>
  useQuery({
    queryKey: queryKeys.demoCases,
    queryFn: ({ signal }) => api.demoCases({ signal }),
    staleTime: Infinity,
    retry: (count, error) => !(isApiError(error) && error.status === 404) && count < 2,
  });

/** Telemetry polls every `interval_s` of the response while the tab is visible (design 9.4). */
export const useTelemetry = (enabled = true) =>
  useQuery({
    queryKey: queryKeys.telemetry,
    queryFn: ({ signal }) => api.getTelemetry({ signal }),
    enabled,
    refetchInterval: (query) => (query.state.data?.interval_s ?? 5) * SECOND,
    refetchIntervalInBackground: false,
  });
