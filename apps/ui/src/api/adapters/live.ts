import type { ApiAdapter } from '../transport';

/** The real API on the same origin (/api, /healthz, /readyz behind the proxy). */
export const liveAdapter: ApiAdapter = {
  kind: 'live',
  fetch: ({ method, path, headers, body, signal }) =>
    fetch(path, { method, headers, body, signal, credentials: 'same-origin' }),
};
