// DemoRail (shell slot, presenter mode only; docs/ui-design.md 3.4, 9.5). Stub: the six beats.
import clsx from 'clsx';
import { Link, useLocation } from 'react-router';
import { PLATFORM } from '../copy';

export function DemoRail() {
  const location = useLocation();
  const here = `${location.pathname}${location.search}`;
  return (
    <nav aria-label={PLATFORM.demo.rail} className="sticky bottom-0 z-30 border-t border-border bg-surface px-4 py-2" data-demo-rail>
      <ol className="flex flex-wrap gap-3 text-body">
        {PLATFORM.demo.beatList.map((beat) => (
          <li key={beat.to}>
            <Link
              to={beat.to}
              aria-current={here === beat.to ? 'step' : undefined}
              className={clsx('rounded-control px-2 py-1', here === beat.to ? 'bg-accent-bg font-medium' : 'text-ink-2 hover:bg-surface-2')}
            >
              {beat.label}
            </Link>
          </li>
        ))}
      </ol>
    </nav>
  );
}
