// Layout pieces of the platform screens: the page header and the bordered panels. Local to the
// area (docs/ui-design.md 13.3): built from tokens, no shared component forked.
import clsx from 'clsx';
import type { ReactNode } from 'react';

interface PageHeaderProps {
  title: string;
  intro?: ReactNode;
  /** Story ids, e.g. "US-401 · US-402 · US-403". */
  stories?: string;
  /** Right side: status pills, actions. */
  aside?: ReactNode;
}

export function PageHeader({ title, intro, stories, aside }: PageHeaderProps) {
  return (
    <header className="flex flex-col gap-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-page">{title}</h1>
        {aside}
      </div>
      {intro && <p className="max-w-3xl text-body text-ink-2">{intro}</p>}
      {stories && (
        <p className="text-caption text-ink-3">
          <span data-id>{stories}</span>
        </p>
      )}
    </header>
  );
}

interface PanelProps {
  title?: ReactNode;
  /** Heading level of the title. */
  level?: 2 | 3;
  id?: string;
  className?: string;
  children: ReactNode;
  /** Extra content on the right of the title. */
  aside?: ReactNode;
}

/** A bordered card section: 16 px padding, an h2 or h3 title (docs/ui-design.md 4.5). */
export function Panel({ title, level = 2, id, className, children, aside }: PanelProps) {
  const Heading = level === 2 ? 'h2' : 'h3';
  return (
    <section id={id} className={clsx('flex min-w-0 flex-col gap-3 rounded-card border border-border bg-surface p-4', className)}>
      {(title || aside) && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          {title && <Heading className={level === 2 ? 'text-h3' : 'text-title'}>{title}</Heading>}
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

/** A label and its value in a definition list row. */
export function Fact({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={clsx('flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1', className)}>
      <dt className="text-caption text-ink-3">{label}</dt>
      <dd className="min-w-0 text-body text-ink-1">{children}</dd>
    </div>
  );
}
