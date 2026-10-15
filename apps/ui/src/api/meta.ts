// What the responses say about the service, independent of their bodies (docs/ui-design.md 5.2
// and 11.2): the X-ReturnRing-Mode header of every response, the last request id, the latest
// `as_of`, the cached label of /readyz, and whether the language model answered 503 recently.
// The mode banner reads it; so can a component outside the query provider (a store, not a query).
import { createStore, useStore } from './store';

export interface ApiMeta {
  /** Header modes seen since the last reset: a mix of both means some answers were cached. */
  modesSeen: { live: number; cached: number };
  lastMode: 'live' | 'cached' | null;
  lastRequestId: string | null;
  /** The latest `as_of` timestamp of a response body (the demo clock). */
  asOf: string | null;
  /**
   * /readyz `label` as served: where the cached outputs were made ("Precomputed on AMD MI300X", or
   * the rehearsal label; ADR-15 sections 16, 17). Null live, or until /readyz answers.
   */
  cachedLabel: string | null;
  /** Set when an endpoint answered 503 llm_unavailable; cleared by a later success of /readyz. */
  llmUnavailableAt: number | null;
  /** The last mock note (fallback or fixture), dev only. */
  mockNote: string | null;
}

const initial: ApiMeta = {
  modesSeen: { live: 0, cached: 0 },
  lastMode: null,
  lastRequestId: null,
  asOf: null,
  cachedLabel: null,
  llmUnavailableAt: null,
  mockNote: null,
};

export const metaStore = createStore<ApiMeta>(initial);

export function recordResponse(headers: Headers): void {
  const mode = headers.get('x-returnring-mode');
  const requestId = headers.get('x-request-id');
  const note = headers.get('x-mock-note');
  metaStore.set((prev) => ({
    ...prev,
    modesSeen:
      mode === 'live' || mode === 'cached' ? { ...prev.modesSeen, [mode]: prev.modesSeen[mode] + 1 } : prev.modesSeen,
    lastMode: mode === 'live' || mode === 'cached' ? mode : prev.lastMode,
    lastRequestId: requestId ?? prev.lastRequestId,
    mockNote: note ?? prev.mockNote,
  }));
}

export function recordAsOf(body: unknown): void {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return;
  const asOf = (body as Record<string, unknown>).as_of;
  if (typeof asOf !== 'string') return;
  metaStore.set((prev) => (prev.asOf !== null && prev.asOf >= asOf ? prev : { ...prev, asOf }));
}

/** The cached label of a /readyz answer, exactly as the service gives it. */
export function recordReadyz(body: { label: string | null }): void {
  metaStore.set((prev) => (prev.cachedLabel === body.label ? prev : { ...prev, cachedLabel: body.label }));
}

export function recordLlmUnavailable(): void {
  metaStore.set((prev) => ({ ...prev, llmUnavailableAt: Date.now() }));
}

/** Forget the header modes, e.g. after switching the mock scenario. */
export function resetModesSeen(): void {
  metaStore.set((prev) => ({ ...prev, modesSeen: { live: 0, cached: 0 }, lastMode: null, llmUnavailableAt: null }));
}

export const useApiMeta = () => useStore(metaStore);
