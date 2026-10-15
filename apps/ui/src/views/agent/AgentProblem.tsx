// Every way a question can fail, each with an explicit state (docs/ui-design.md P10, 7, 10.1):
// an `error` event or 503 -> the language model is unavailable, the decision is unaffected;
// 429 -> wait the seconds of its Retry-After; 403 -> the role cannot ask; others -> the error with
// its request id.
// Stream deviations from the contract are listed, never repaired silently.
import { usePersona } from '@/app/persona';
import { isApiError } from '@/api/errors';
import { ErrorState, ForbiddenRoleNotice, InlineNotice, LlmUnavailableNotice } from '@/components/feedback';
import { IdText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { AGENT } from './copy';
import type { FinalView, StreamIssue } from './stream';

type StreamError = Extract<FinalView, { kind: 'error' }>;

function RequestId({ id }: { id: string | null }) {
  if (!id) return null;
  return (
    <p className="flex items-center gap-1 text-caption text-ink-3" data-request-id>
      {AGENT.errors.requestId} <IdText id={id} />
    </p>
  );
}

function Unaffected() {
  return <p className="text-body text-ink-2">{AGENT.errors.unaffected}</p>;
}

/** The language model is down: the shared notice (its retry countdown is marked as a named constant). */
function LlmDown({ onRetry }: { onRetry?(): void }) {
  return (
    <div data-agent-problem="llm_unavailable">
      <LlmUnavailableNotice onRetry={onRetry} />
    </div>
  );
}

/** An `error` event in the stream (the request itself succeeded). */
export function AgentStreamError({ error, onRetry }: { error: StreamError; onRetry?(): void }) {
  const message = (
    <p className="text-caption text-ink-3">
      {AGENT.errors.serviceSaid}: <span data-verbatim>{error.message}</span>
    </p>
  );
  if (error.code === 'llm_unavailable') {
    return (
      <div className="flex flex-col gap-2">
        <LlmDown onRetry={onRetry} />
        <Unaffected />
        {message}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2" data-agent-problem={error.code}>
      <InlineNotice tone={error.code === 'internal' ? 'danger' : 'attention'} title={error.code === 'timeout' ? AGENT.errors.timeout : AGENT.errors.internal}>
        <p>{AGENT.errors.unaffected}</p>
      </InlineNotice>
      {message}
      {onRetry && (
        <div>
          <Button onClick={onRetry}>{AGENT.errors.askAgain}</Button>
        </div>
      )}
    </div>
  );
}

/** A request that failed before or while streaming (HTTP status or network). */
export function AgentRequestError({ error, returnId, onRetry }: { error: unknown; returnId: string; onRetry?(): void }) {
  const persona = usePersona();
  if (!isApiError(error)) return <ErrorState error={error} onRetry={onRetry} />;
  switch (error.code) {
    case 'llm_unavailable':
      return (
        <div className="flex flex-col gap-2">
          <LlmDown onRetry={onRetry} />
          <Unaffected />
          <RequestId id={error.requestId} />
        </div>
      );
    case 'rate_limited':
      return (
        <div className="flex flex-col gap-2" data-agent-problem="rate_limited">
          <InlineNotice tone="attention">
            <p>{AGENT.composer.rateLimited}</p>
            {error.retryAfterS !== null && (
              <p data-constant data-retry-after={error.retryAfterS}>
                {AGENT.composer.retryAfter(error.retryAfterS)}
              </p>
            )}
            <p className="text-caption" data-verbatim>
              {error.message}
            </p>
          </InlineNotice>
          <RequestId id={error.requestId} />
        </div>
      );
    case 'forbidden_role':
      return (
        <div className="flex flex-col gap-2" data-agent-problem="forbidden_role">
          <ForbiddenRoleNotice role={persona.role} action={AGENT.errors.forbiddenAction} />
          <RequestId id={error.requestId} />
        </div>
      );
    case 'not_found':
      return <ErrorState error={error} title={AGENT.errors.notFound(returnId)} />;
    default:
      return <ErrorState error={error} onRetry={onRetry} />;
  }
}

/** Deviations from the contract's stream order, listed as they were found. */
export function AgentStreamIssues({ issues }: { issues: StreamIssue[] }) {
  if (issues.length === 0) return null;
  return (
    <div data-agent-issues={issues.map((i) => i.code).join(' ')}>
      <InlineNotice tone="attention" title={AGENT.issues.title}>
        <ul className="list-disc pl-4" data-constant>
          {issues.map((issue) => (
            <li key={issue.code}>{AGENT.issues[issue.code]}</li>
          ))}
        </ul>
      </InlineNotice>
    </div>
  );
}
