import clsx from 'clsx';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useId, type ReactNode } from 'react';
import { OPERATOR } from '../copy';
import { toggleSection, useCollapsed } from '../state/prefs';

interface SectionProps {
  /** Anchor id (`?focus=` and SectionNav); also the key of the remembered collapsed state. */
  id: string;
  title: ReactNode;
  /** Right side of the heading: badges, actions. */
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Sections that must stay open (the value legend never collapses). */
  collapsible?: boolean;
}

/** A case card section (docs/ui-design.md 6.2.3): collapsible, state remembered per browser. */
export function Section({ id, title, aside, children, className, collapsible = true }: SectionProps) {
  const collapsedList = useCollapsed();
  const collapsed = collapsible && collapsedList.includes(id);
  const bodyId = useId();
  return (
    <section
      id={`section-${id}`}
      data-section={id}
      aria-labelledby={`${bodyId}-title`}
      className={clsx('scroll-mt-12 rounded-card border border-border bg-surface', className)}
    >
      <header className="flex flex-wrap items-center gap-2 px-4 pt-3 pb-2">
        <h2 id={`${bodyId}-title`} className="text-h3">
          {collapsible ? (
            <button
              type="button"
              aria-expanded={!collapsed}
              aria-controls={bodyId}
              onClick={() => toggleSection(id, !collapsed)}
              className="inline-flex items-center gap-1 rounded-control text-left hover:text-accent"
            >
              {collapsed ? <ChevronRight aria-hidden="true" size={18} /> : <ChevronDown aria-hidden="true" size={18} />}
              {title}
              <span className="sr-only"> · {collapsed ? OPERATOR.case.expand : OPERATOR.case.collapse}</span>
            </button>
          ) : (
            title
          )}
        </h2>
        {aside && <div className="ml-auto flex flex-wrap items-center gap-2">{aside}</div>}
      </header>
      <div id={bodyId} hidden={collapsed} className="px-4 pb-4">
        {children}
      </div>
    </section>
  );
}
