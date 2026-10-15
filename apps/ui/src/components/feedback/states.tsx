import clsx from 'clsx';
import { Inbox, OctagonAlert, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { isApiError, isRefusal } from '@/api/errors';
import { FEEDBACK } from '@/copy/common';
import { IdText } from '@/components/provenance/IdText';
import { Button } from '@/components/ui/Button';

/**
 * A failed panel (docs/ui-design.md 5.8, 10.1): code, message and the request id to copy, plus
 * Retry. Panel-level, so one failing panel never blanks the page. A refusal (4xx) is titled as
 * one, never as a service that did not answer, and lists each violation the service named, so the
 * person can see what to correct.
 */
export function ErrorState({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title?: string }) {
  const api = isApiError(error) ? error : null;
  const message = api ? api.message : error instanceof Error ? error.message : String(error);
  const refused = isRefusal(error);
  return (
    <div role="alert" data-error-state={api?.code ?? 'error'} className="rounded-card border border-danger-fg bg-danger-bg p-4 text-danger-fg">
      <p className="flex items-center gap-2 font-medium">
        <OctagonAlert aria-hidden="true" size={16} />
        {title ?? (refused ? FEEDBACK.refusedTitle : FEEDBACK.errorTitle)}
      </p>
      <p className="mt-1 text-body text-ink-1">{message}</p>
      {api && api.violations.length > 0 && (
        <div className="mt-2 text-body text-ink-1" data-violations>
          <p className="text-caption text-ink-2">{FEEDBACK.violationsTitle}</p>
          <ul className="list-disc pl-5">
            {api.violations.map((v, i) => (
              <li key={`${v.code}-${i}`} data-violation={v.code}>
                <span data-verbatim>{v.message}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {api && (
        <dl className="mt-2 flex flex-wrap gap-x-4 text-caption text-ink-2">
          <div className="flex gap-1">
            <dt>{FEEDBACK.code}</dt>
            <dd className="font-mono">{api.code}</dd>
          </div>
          {api.requestId && (
            <div className="flex items-center gap-1">
              <dt>{FEEDBACK.requestId}</dt>
              <dd>
                <IdText id={api.requestId} />
              </dd>
            </div>
          )}
        </dl>
      )}
      {onRetry && (
        <Button className="mt-3" onClick={onRetry}>
          {FEEDBACK.retry}
        </Button>
      )}
    </div>
  );
}

/** Empty: an icon, one sentence, one next action. */
export function EmptyState({ message, icon: Icon = Inbox, action }: { message: string; icon?: LucideIcon; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-card border border-dashed border-border-strong p-8 text-center text-ink-2">
      <Icon aria-hidden="true" size={24} />
      <p>{message}</p>
      {action}
    </div>
  );
}

/** A shimmer block with the final dimensions; a figure placeholder is never a 0. */
export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={clsx('skeleton block rounded-chip', className ?? 'h-4 w-24')} />;
}

/** A loading region: skeleton rows with a screen-reader label. */
export function LoadingBlock({ rows = 3, label = FEEDBACK.loading }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-busy="true" className="space-y-2">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-5 w-full" />
      ))}
    </div>
  );
}
