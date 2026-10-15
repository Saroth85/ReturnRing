// Layout pieces of the manager area: the page frame, cards, a query wrapper with the states of
// docs/ui-design.md 10.1, and the right drawer (Radix dialog) until the shared Sheet lands.
import * as Dialog from '@radix-ui/react-dialog';
import type { UseQueryResult } from '@tanstack/react-query';
import clsx from 'clsx';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
import type { EndpointKey } from '@/api/client';
import { isApiError } from '@/api/errors';
import { usePersona } from '@/app/persona';
import { EmptyState, ErrorState, ForbiddenRoleNotice, InlineNotice, LoadingBlock, useReadOnly, type Gate } from '@/components/feedback';
import { FEEDBACK } from '@/copy/common';
import { MANAGER } from './copy';
import { useWriteGate } from './gate';

interface ManagerPageProps {
  title: string;
  intro?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** A data attribute naming the screen, for tests. */
  screen: string;
}

export function ManagerPage({ title, intro, actions, children, screen }: ManagerPageProps) {
  return (
    <div className="mx-auto flex w-full max-w-content min-w-0 flex-col gap-6" data-manager-page={screen}>
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex min-w-0 max-w-3xl flex-col gap-1">
          <h1 className="text-page">{title}</h1>
          {intro && <div className="text-body text-ink-2">{intro}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
      {children}
    </div>
  );
}

interface CardProps {
  title?: ReactNode;
  /** Heading level of the title (default 2). */
  level?: 2 | 3;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Attention border: a person must look (amber family, with words and an icon in the body). */
  attention?: boolean;
  id?: string;
}

export function Card({ title, level = 2, aside, children, className, attention = false, id }: CardProps) {
  const Heading = level === 2 ? 'h2' : 'h3';
  return (
    <section
      id={id}
      className={clsx(
        'flex min-w-0 flex-col gap-3 rounded-card border bg-surface p-4',
        attention ? 'border-2 border-attn-border' : 'border-border',
        className,
      )}
    >
      {(title || aside) && (
        <header className="flex flex-wrap items-start justify-between gap-2">
          {title && <Heading className={level === 2 ? 'text-h3' : 'text-title'}>{title}</Heading>}
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

interface LoadableProps<T> {
  query: UseQueryResult<T>;
  /** What failed, e.g. "GET /api/kpi". */
  label: string;
  rows?: number;
  /** A 404 that is an answer, not a failure (an offline report not in the snapshot): its empty state. */
  notFound?: (message: string) => ReactNode;
  children: (data: T) => ReactNode;
}

/** Loading skeleton, panel-level error with the request id and Retry, or the content. */
export function Loadable<T>({ query, label, rows = 4, notFound, children }: LoadableProps<T>) {
  if (query.isPending) return <LoadingBlock rows={rows} label={MANAGER.loading} />;
  if (query.isError) {
    if (notFound && isApiError(query.error) && query.error.status === 404) return <>{notFound(query.error.message)}</>;
    return <ErrorState error={query.error} title={`${label}: ${MANAGER.failed}`} onRetry={() => void query.refetch()} />;
  }
  return <>{children(query.data)}</>;
}

/**
 * The offline evaluation report is not in the serving snapshot (404 report_unavailable, ADR-15
 * section 15): an empty state with the API's reason verbatim, never retried.
 */
export function ReportUnavailable({ message }: { message: string }) {
  return (
    <div data-report-unavailable>
      <EmptyState
        message={MANAGER.reportUnavailable}
        action={
          <p className="max-w-prose text-caption text-ink-2" data-verbatim>
            {message}
          </p>
        }
      />
    </div>
  );
}

/** RoleGate with the read-only demo of /readyz: the control, and its reason under it. */
export function WriteGate({ action, children }: { action: EndpointKey; children: (gate: Gate) => ReactNode }) {
  const gate = useWriteGate(action);
  return (
    <div data-role-gate={gate.allowed ? 'allowed' : 'blocked'} className="inline-flex flex-col gap-1">
      {children(gate)}
      {gate.reason && (
        <p className="text-caption text-ink-3" data-role-reason>
          {gate.reason}
        </p>
      )}
    </div>
  );
}

interface SideDrawerProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
}

/** A right drawer, 560 px or the full width on small screens; focus trapped, Esc closes. */
export function SideDrawer({ open, onOpenChange, title, description, children }: SideDrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-x-0 top-[var(--mode-banner-h,0px)] bottom-0 z-40 bg-[rgb(20_26_38/0.24)]" />
        <Dialog.Content
          className="fixed top-[var(--mode-banner-h,0px)] right-0 bottom-0 z-50 flex w-full flex-col sm:w-sheet border-l border-border bg-surface text-ink-1 shadow-overlay"
          data-side-drawer
        >
          <header className="flex items-start justify-between gap-3 border-b border-border p-4">
            <div className="flex min-w-0 flex-col gap-1">
              <Dialog.Title className="text-h3">{title}</Dialog.Title>
              {description ? (
                <Dialog.Description className="text-caption text-ink-2">{description}</Dialog.Description>
              ) : (
                <Dialog.Description className="sr-only">{title}</Dialog.Description>
              )}
            </div>
            <Dialog.Close asChild>
              <button type="button" aria-label={MANAGER.close} className="rounded-control p-1 text-ink-2 hover:bg-surface-2">
                <X aria-hidden="true" size={18} />
              </button>
            </Dialog.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/**
 * A refused write: the read-only public demo (403 while /readyz says read-only) in its own words,
 * a role the endpoint does not allow with the personas to switch to, anything else as an error.
 */
export function WriteError({ error, action }: { error: unknown; action: string }) {
  const persona = usePersona();
  const readOnly = useReadOnly();
  if (readOnly && isApiError(error) && error.status === 403) {
    return (
      <div data-read-only-refusal>
        <InlineNotice tone="attention" title={FEEDBACK.readOnlyDemo}>
          <p data-verbatim>{error.message}</p>
        </InlineNotice>
      </div>
    );
  }
  if (isApiError(error) && error.code === 'forbidden_role') return <ForbiddenRoleNotice role={persona.role} action={action} />;
  return <ErrorState error={error} />;
}
