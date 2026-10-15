import type { ErrorResponse, ViolationView } from './types.gen';

/** Every error the client raises: an ErrorResponse of the API, or a transport problem. */
export type ApiErrorCode = ErrorResponse['code'] | 'network' | 'protected' | 'unexpected_response' | 'aborted';

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly violations: ViolationView[];
  readonly requestId: string | null;
  /** The whole seconds of a 429's Retry-After header (ADR-15 section 16), null without one. */
  readonly retryAfterS: number | null;

  constructor(init: {
    status: number;
    code: ApiErrorCode;
    message: string;
    violations?: ViolationView[];
    requestId?: string | null;
    retryAfterS?: number | null;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.violations = init.violations ?? [];
    this.requestId = init.requestId ?? null;
    this.retryAfterS = init.retryAfterS ?? null;
  }
}

/**
 * Retry-After as whole seconds (RFC 9110: delay-seconds or an HTTP date). The API sends whole
 * seconds, at least 1, on every 429 rate_limited; a date is turned into seconds from `now`.
 * Null when the header is missing or unreadable.
 */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (value === null) return null;
  const text = value.trim();
  if (/^\d+$/.test(text)) return Math.max(1, Number(text));
  const at = Date.parse(text);
  if (Number.isNaN(at)) return null;
  return Math.max(1, Math.ceil((at - now) / 1000));
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Whether a body is the contract's ErrorResponse shape. */
export function isErrorResponse(body: unknown): body is ErrorResponse {
  if (!body || typeof body !== 'object') return false;
  const b = body as Record<string, unknown>;
  return typeof b.code === 'string' && typeof b.message === 'string' && Array.isArray(b.violations);
}
