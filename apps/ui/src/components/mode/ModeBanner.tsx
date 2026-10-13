import clsx from 'clsx';
import { TriangleAlert } from 'lucide-react';
import { MODE } from '@/copy/common';
import { ScenarioSwitcher } from './ScenarioSwitcher';
import { useModeView, type ModeKind } from './useModeView';

const STYLES: Record<ModeKind, string> = {
  live: 'bg-live-bg text-live-fg',
  'live-cpu': 'bg-live-bg text-live-fg',
  cached: 'hatch-cached',
  contract: 'hatch-cached',
  mixed: 'hatch-attn',
  checking: 'bg-surface-2 text-ink-2',
  unknown: 'hatch-attn',
};

/**
 * The service mode, always visible and never dismissible (US-901, docs/ui-design.md 5.2): a dark
 * band when live, a yellow hatch when cached or on contract examples, so a cropped screenshot or
 * a video frame never confuses live with cached.
 */
export function ModeBanner() {
  const view = useModeView();
  return (
    <section aria-label={MODE.bannerLabel} data-mode-banner={view.kind} className="sticky top-0 z-40">
      <div className={clsx('flex min-h-8 flex-wrap items-center gap-x-2 px-4 py-1 text-caption', STYLES[view.kind])}>
        <strong className="font-semibold tracking-wide">{view.title}</strong>
        {view.detail && <span>· {view.detail}</span>}
        {view.suffix.map((s) => (
          <span key={s}>· {s}</span>
        ))}
        {view.kind === 'live-cpu' && <span aria-hidden="true" className="hatch-cached ml-auto h-4 w-16 rounded-chip" />}
        {view.isMock && <ScenarioSwitcher />}
      </div>
      {view.degraded.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-2 border-t border-attn-border bg-attn-bg px-4 py-1 text-caption text-attn-fg">
          <TriangleAlert aria-hidden="true" size={14} />
          <span>{MODE.degradedPrefix}</span>
          {view.degraded.map((c) => (
            <span key={c.component}>
              {c.component}
              {c.detail ? ` (${c.detail})` : ''}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
