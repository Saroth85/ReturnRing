// Small DOM helpers of the case assistant: where the drawer sits and how a cited ref behaves.
import { useEffect, useState, useSyncExternalStore, type MouseEvent } from 'react';
import { COMPACT_LAYOUT_QUERY, HEADER_OFFSET_QUERY, WIDE_LAYOUT_QUERY } from './limits';

function matches(query: string): boolean {
  try {
    return window.matchMedia(query).matches;
  } catch {
    return false;
  }
}

/** Whether the case card shows its evidence column next to the drawer (read at click time). */
export function isWideLayout(): boolean {
  return matches(WIDE_LAYOUT_QUERY);
}

/** A left click without modifiers: other clicks keep the browser's link behaviour (new tab). */
export function isPlainClick(event: MouseEvent): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

/** A phone-sized viewport, where the composer folds the suggested questions after the first one. */
export function useCompactLayout(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      let list: MediaQueryList | null = null;
      try {
        list = window.matchMedia(COMPACT_LAYOUT_QUERY);
        list.addEventListener?.('change', onChange);
      } catch {
        list = null;
      }
      return () => list?.removeEventListener?.('change', onChange);
    },
    () => matches(COMPACT_LAYOUT_QUERY),
    () => false,
  );
}

/**
 * Where the drawer starts: under the sticky mode banner, which says live, cached or contract
 * examples and must never be covered (US-901, docs/ui-design.md P7); on wide screens also under
 * the top bar while it is in view, so the LLM state, provenance mode and persona stay visible.
 */
export function useDrawerTop(active: boolean): number {
  const [top, setTop] = useState(0);
  useEffect(() => {
    if (!active) return;
    const banner = document.querySelector<HTMLElement>('[data-mode-banner]');
    if (!banner) return;
    const topBar = document.querySelector<HTMLElement>('[data-top-bar]');
    let frame = 0;
    const measure = () => {
      frame = 0;
      let bottom = banner.getBoundingClientRect().bottom;
      if (topBar && matches(HEADER_OFFSET_QUERY)) bottom = Math.max(bottom, topBar.getBoundingClientRect().bottom);
      setTop(Math.max(0, Math.ceil(bottom)));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    schedule();
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    observer?.observe(banner);
    if (topBar) observer?.observe(topBar);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      observer?.disconnect();
    };
  }, [active]);
  return top;
}
