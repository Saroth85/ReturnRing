// DemoRail (shell slot, presenter mode only; docs/ui-design.md 3.4, 9.5): the six beats in order,
// the current one highlighted, Alt+← and Alt+→ to move, and the persona of each beat.
import clsx from 'clsx';
import { useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import type { DemoUserId } from '@/api/models';
import { setPresent } from '@/app/display';
import { setPersona } from '@/app/persona';
import { ModeCornerTag } from '@/components/mode';
import { Button } from '@/components/ui';
import { PLATFORM } from '../copy';
import { BEATS as beats, currentBeat } from './beats';

export function DemoRail() {
  const location = useLocation();
  const navigate = useNavigate();
  const current = currentBeat(location.pathname, location.search);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!event.altKey || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
      const next = event.key === 'ArrowRight' ? current + 1 : current - 1;
      const beat = beats[next];
      if (next < 0 || !beat) return;
      event.preventDefault();
      setPersona(beat.persona as DemoUserId);
      void navigate(beat.to);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [current, navigate]);

  return (
    <nav aria-label={PLATFORM.demo.rail} className="sticky bottom-0 z-30 border-t border-border bg-surface px-4 py-2" data-demo-rail>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ol className="flex flex-wrap gap-2 text-body">
          {beats.map((beat, index) => (
            <li key={beat.to}>
              <Link
                to={beat.to}
                onClick={() => setPersona(beat.persona as DemoUserId)}
                aria-current={index === current ? 'step' : undefined}
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-control px-2 py-1',
                  index === current ? 'bg-accent-bg font-medium text-ink-1' : 'text-ink-2 hover:bg-surface-2',
                )}
              >
                <span className="text-caption text-ink-3" data-constant>
                  {index + 1}
                </span>
                {beat.label}
              </Link>
            </li>
          ))}
        </ol>
        <span className="flex flex-wrap items-center gap-3">
          {/* The key hint is for the presenter: only on very wide screens, so the rail stays one line. */}
          <span className="hidden text-caption text-ink-3 2xl:inline">{PLATFORM.demo.railKeys}</span>
          <ModeCornerTag inline />
          {/* Presenter mode persists for the session: the way out is always on screen (UI23). */}
          <Button onClick={() => setPresent(false)} data-leave-presenter>
            {PLATFORM.demo.leavePresenter}
          </Button>
        </span>
      </div>
    </nav>
  );
}
