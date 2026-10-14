import type { ErrorResponse, ViolationView } from './types.gen';

/** Every error the client raises: an ErrorResponse of the API, or a transport problem. */
export type ApiErrorCode = ErrorResponse['code'] | 'network' | 'protected' | 'unexpected_response' | 'aborted';

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly violations: ViolationView[];
  readonly requestId: string | null;

  constructor(init: {
    status: number;
    code: ApiErrorCode;
    message: string;
    violations?: ViolationView[];
    requestId?: string | null;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.violations = init.violations ?? [];
    this.requestId = init.requestId ?? null;
  }
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
