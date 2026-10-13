import { Suspense, useEffect, useState } from 'react';
import { Outlet, useMatches } from 'react-router';
import { LoadingBlock } from '@/components/feedback';
import { ModeBanner, ModeCornerTag, useModeView } from '@/components/mode';
import { ContractExampleBanner, toggleProvenanceMode } from '@/components/provenance';
import { APP_NAME, MODE, SHELL } from '@/copy/common';
import { SHELL_SLOTS } from './composition';
import { useDisplaySettings, usePresent } from './display';
import { useHotkeys } from './hotkeys';
import { SideNav } from './SideNav';
import { TopBar } from './TopBar';

/** `[Cached] [Mock] ReturnRing · Returns queue` (docs/ui-design.md 3.4, MODE-UI). */
function useDocumentTitle(): void {
  const matches = useMatches();
  const mode = useModeView();
  const last = matches[matches.length - 1];
  const page = (last?.handle as { title?: string } | undefined)?.title ?? '';
  const prefix = [mode.isCached ? MODE.titleCached : null, mode.isMock ? MODE.titleMock : null].filter(Boolean).join(' ');
  const title = `${prefix ? `${prefix} ` : ''}${APP_NAME}${page ? ` · ${page}` : ''}`;
  useEffect(() => {
    document.title = title;
  }, [title]);
}

/** The app shell (docs/ui-design.md 3.4): mode banner, top bar, navigation, page, slots. */
export function Shell() {
  useDisplaySettings();
  useDocumentTitle();
  const present = usePresent();
  const [gpuDock, setGpuDock] = useState(false);
  useHotkeys({
    p: toggleProvenanceMode,
    t: () => setGpuDock((open) => !open),
    '/': () => document.querySelector<HTMLElement>('[data-search]')?.focus(),
  });
  const { GpuDock, DemoRail } = SHELL_SLOTS;

  return (
    <div className="flex min-h-screen flex-col bg-bg text-ink-1">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-control focus:bg-surface focus:px-3 focus:py-2"
      >
        {SHELL.skipToContent}
      </a>
      <ModeBanner />
      <ContractExampleBanner />
      <TopBar />
      <div className="flex min-w-0 flex-1 flex-col lg:flex-row">
        <SideNav collapsed={present} />
        {/* Bottom padding: the last row of a page scrolls clear of the fixed mode corner tag. */}
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 px-4 pt-6 pb-12 md:px-6">
          <Suspense fallback={<LoadingBlock rows={6} />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      <GpuDock open={gpuDock} onClose={() => setGpuDock(false)} />
      {present && <DemoRail />}
      <ModeCornerTag />
      <div aria-live="polite" className="sr-only" data-toast-region />
    </div>
  );
}
