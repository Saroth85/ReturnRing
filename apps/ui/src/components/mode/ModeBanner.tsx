import clsx from 'clsx';
import { TriangleAlert } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import { MODE } from '@/copy/common';
import { ClockSegment } from './ClockSegment';
import { ContractVersionNotice } from './ContractVersionNotice';
import { ScenarioSwitcher } from './ScenarioSwitcher';
import { useModeView, type ModeKind } from './useModeView';

/** The CSS variable with the banner's height, read by every fixed sheet and drawer. */
export const MODE_BANNER_HEIGHT_VAR = '--mode-banner-h';

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
  const ref = useRef<HTMLElement>(null);
  // Sheets and drawers start under the banner (top: var(--mode-banner-h)), so it is never covered.
  useLayoutEffect(() => {
    const banner = ref.current;
    if (!banner) return;
    const root = document.documentElement;
    const publish = () => root.style.setProperty(MODE_BANNER_HEIGHT_VAR, `${Math.ceil(banner.getBoundingClientRect().height)}px`);
    publish();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(publish);
    observer?.observe(banner);
    return () => {
      observer?.disconnect();
      root.style.removeProperty(MODE_BANNER_HEIGHT_VAR);
    };
  }, []);
  return (
    <section ref={ref} aria-label={MODE.bannerLabel} data-mode-banner={view.kind} className="sticky top-0 z-40">
      <div className={clsx('flex min-h-8 flex-wrap items-center gap-x-2 px-4 py-1 text-caption', STYLES[view.kind])}>
        <strong className="font-semibold tracking-wide">{view.title}</strong>
        {view.detail && <span>· {view.detail}</span>}
        {view.suffix.map((s) => (
          <span key={s}>· {s}</span>
        ))}
        <ClockSegment />
        {view.kind === 'live-cpu' && <span aria-hidden="true" className="hatch-cached ml-auto h-4 w-16 rounded-chip" />}
        {__MOCK_SCENARIOS__ && view.isMock && <ScenarioSwitcher />}
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
      <ContractVersionNotice />
    </section>
  );
}
