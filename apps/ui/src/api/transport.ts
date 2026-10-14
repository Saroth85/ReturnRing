/** One HTTP request as the client sends it; both adapters answer with a standard Response. */
export interface ApiRequest {
  method: string;
  /** Path and query string on the UI origin, e.g. `/api/returns?state=proposed`. */
  path: string;
  headers: Record<string, string>;
  body?: string;
  signal?: AbortSignal;
}

export interface ApiAdapter {
  kind: 'live' | 'mock';
  fetch(request: ApiRequest): Promise<Response>;
}
