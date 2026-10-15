// What an action's error means for the operator (docs/ui-design.md 6.3, 6.4, 10.1): each code has
// its own explicit state with the request id (P10). Nothing is ever optimistic: when an action
// fails, nothing was saved, and the sheet says so. A 409 conflict shows the service's message
// verbatim (it names the reason: already decided, a stale proposal decided again, a refund past
// its deadline, a renewal before the review date); the case is loaded again meanwhile
// (useDecisionAction). A 403 on the read-only public demo says so instead of offering personas.
import { OctagonAlert } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { isApiError } from '@/api/errors';
import { usePersona } from '@/app/persona';
import { ErrorState, ForbiddenRoleNotice, InlineNotice, LlmUnavailableNotice, useReadOnly } from '@/components/feedback';
import { IdText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { FEEDBACK } from '@/copy/common';
import { OPERATOR } from '../../copy';

export function RequestId({ id }: { id: string | null }) {
  if (!id) return null;
  return (
    <span className="inline-flex items-center gap-1 text-caption text-ink-2">
      {FEEDBACK.requestId} <IdText id={id} />
    </span>
  );
}

interface ActionErrorProps {
  error: unknown;
  /** 409 stale_decision: reload the case, keeping what was typed. */
  onLoadNew?(): void;
  /** 409 conflict: show the current state. */
  onShowCurrent?(): void;
  /** 503: retry after the wait. */
  onRetry?(): void;
  /** What changed, for a stale decision (a short before → after list). */
  changes?: string[] | null;
}

/** The error, scrolled into view when it appears: the confirm button may be far below it. */
export function ActionError(props: ActionErrorProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }, [props.error]);
  return (
    <div ref={ref} className="scroll-mt-4" data-action-error>
      <ActionErrorBody {...props} />
    </div>
  );
}

function ActionErrorBody({ error, onLoadNew, onShowCurrent, onRetry, changes }: ActionErrorProps) {
  const persona = usePersona();
  const readOnly = useReadOnly();
  const s = OPERATOR.sheets;
  if (!isApiError(error)) return <ErrorState error={error} />;
  switch (error.code) {
    case 'stale_decision':
      return (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-body text-attn-fg"
          data-stale-panel
        >
          <p className="font-medium">{s.stale.title}</p>
          <p className="text-ink-1">{error.message}</p>
          <p className="font-medium text-ink-1">{s.nothingSaved}</p>
          {changes && (
            <div className="text-ink-1">
              <p className="text-caption text-ink-3">{s.stale.whatChanged}</p>
              {changes.length === 0 ? (
                <p className="text-caption">{s.stale.nothing}</p>
              ) : (
                <ul className="list-disc pl-5 text-caption">
                  {changes.map((c) => (
                    <li key={c} data-verbatim>
                      {c}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <span className="flex flex-wrap items-center gap-2">
            {onLoadNew && (
              <Button variant="secondary" onClick={onLoadNew} data-load-new-version>
                {s.stale.load}
              </Button>
            )}
            <span className="text-caption text-ink-2">{s.stale.kept}</span>
          </span>
          <RequestId id={error.requestId} />
        </div>
      );
    case 'conflict':
      return (
        <div data-conflict>
          <InlineNotice tone="attention" title={s.conflict.title}>
            <p data-verbatim data-conflict-message>
              {error.message}
            </p>
            <p className="font-medium">{s.nothingSaved}</p>
            <p className="text-caption text-ink-2">{s.conflict.reloaded}</p>
            <span className="mt-1 flex flex-wrap items-center gap-2">
              {onShowCurrent && <Button onClick={onShowCurrent}>{s.conflict.showCurrent}</Button>}
              <RequestId id={error.requestId} />
            </span>
          </InlineNotice>
        </div>
      );
    case 'legal_guard':
      return (
        <div
          role="alert"
          className="flex flex-col gap-1 rounded-control border border-danger-fg bg-danger-bg px-3 py-2 text-body text-danger-fg"
          data-legal-guard-violations
        >
          <p className="flex items-center gap-2 font-medium">
            <OctagonAlert aria-hidden="true" size={16} />
            {s.override.guardViolations}
          </p>
          <ul className="list-disc pl-5 text-ink-1">
            {error.violations.map((v) => (
              <li key={v.code}>
                <span className="font-mono text-dense" data-id>
                  {v.code}
                </span>
                : <span data-verbatim>{v.message}</span>
                {s.override.guardPlain[v.code] && <span className="block">{s.override.guardPlain[v.code]}</span>}
              </li>
            ))}
          </ul>
          <p className="font-medium">{s.nothingSaved}</p>
          <RequestId id={error.requestId} />
        </div>
      );
    case 'invalid_request':
      return (
        <div
          role="alert"
          className="flex flex-col gap-1 rounded-control border border-danger-fg bg-danger-bg px-3 py-2 text-body text-danger-fg"
          data-invalid-request
        >
          <p className="font-medium">{s.invalid}</p>
          <p className="text-ink-1" data-verbatim>
            {error.message}
          </p>
          <ul className="list-disc pl-5 text-ink-1">
            {error.violations.map((v, i) => (
              <li key={`${v.code}-${i}`} data-verbatim>
                {v.message}
              </li>
            ))}
          </ul>
          <p className="font-medium">{s.nothingSaved}</p>
          <RequestId id={error.requestId} />
        </div>
      );
    case 'forbidden_role':
      if (readOnly) {
        return (
          <div data-read-only-refusal>
            <InlineNotice tone="attention" title={s.readOnly}>
              <p data-verbatim>{error.message}</p>
              <p className="font-medium">{s.nothingSaved}</p>
              <RequestId id={error.requestId} />
            </InlineNotice>
          </div>
        );
      }
      return <ForbiddenRoleNotice role={persona.role} action={s.forbiddenAction} />;
    case 'llm_unavailable':
      return <LlmUnavailableNotice onRetry={onRetry} />;
    default:
      return <ErrorState error={error} onRetry={onRetry} />;
  }
}
