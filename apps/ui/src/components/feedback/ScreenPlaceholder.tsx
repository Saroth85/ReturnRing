import type { UseQueryResult } from '@tanstack/react-query';
import { CircleCheck, Hammer, LoaderCircle } from 'lucide-react';
import type { ReactNode } from 'react';
import { FEEDBACK } from '@/copy/common';
import { ErrorState } from './states';

const OWNERS = {
  operator: 'Operator area · Eng 1',
  agent: 'Agent area · Eng 2',
  manager: 'Manager area · Eng 3',
  platform: 'Platform area · Eng 4',
} as const;

interface ScreenPlaceholderProps {
  title: string;
  area: keyof typeof OWNERS;
  stories: string[];
  /** The design section that specifies the screen, e.g. "docs/ui-design.md 6.1". */
  spec: string;
  children?: ReactNode;
}

/**
 * Scaffolding for a screen that is not built yet: its title, owner, stories and design section,
 * plus whatever the stub renders. Each area replaces it with the real screen.
 */
export function ScreenPlaceholder({ title, area, stories, spec, children }: ScreenPlaceholderProps) {
  return (
    <div className="mx-auto flex max-w-content flex-col gap-6" data-placeholder={area}>
      <header className="flex flex-col gap-2">
        <h1 className="text-page">{title}</h1>
        <p className="flex flex-wrap items-center gap-2 text-caption text-ink-3">
          <Hammer aria-hidden="true" size={14} />
          <span data-id>{OWNERS[area]}</span>
          <span>·</span>
          <span data-id>{spec}</span>
          {stories.map((story) => (
            <span key={story} className="rounded-chip border border-border px-1 font-mono" data-id>
              {story}
            </span>
          ))}
        </p>
        <p className="text-body text-ink-2">{FEEDBACK.placeholderNote}</p>
      </header>
      {children}
    </div>
  );
}

/** One API call of a placeholder: the route and whether it answered (with the error if not). */
export function DataCheck({ label, result }: { label: string; result: Pick<UseQueryResult, 'status' | 'error' | 'refetch'> }) {
  if (result.status === 'error') {
    return <ErrorState error={result.error} onRetry={() => void result.refetch()} title={label} />;
  }
  const loaded = result.status === 'success';
  return (
    <p className="flex items-center gap-2 text-caption text-ink-2" data-data-check={result.status}>
      {loaded ? <CircleCheck aria-hidden="true" size={14} /> : <LoaderCircle aria-hidden="true" size={14} />}
      <span className="font-mono" data-id>
        {label}
      </span>
      <span>{loaded ? FEEDBACK.dataLoaded : FEEDBACK.dataLoading}</span>
    </p>
  );
}
