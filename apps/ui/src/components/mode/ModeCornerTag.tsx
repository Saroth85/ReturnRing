import clsx from 'clsx';
import { MODE } from '@/copy/common';
import { useModeView } from './useModeView';

/**
 * The mode tag in cached and mock modes, so a cropped screenshot keeps the mode (5.2). From 1024 px
 * it sits at the bottom left, over the navigation column, which holds no page content, so it never
 * covers a restrictive label on the right of a page (UI20); below 1024 px at the bottom right. The
 * demo rail shows it inline in presenter mode (`inline`, UI30).
 */
export function ModeCornerTag({ inline = false }: { inline?: boolean }) {
  const view = useModeView();
  if (!view.isCached && !view.isMock) return null;
  const words = [view.isCached ? MODE.cornerCached : null, view.isMock ? MODE.cornerContract : null].filter(Boolean);
  return (
    <div
      data-mode-corner={inline ? 'inline' : 'fixed'}
      className={clsx(
        'hatch-cached pointer-events-none rounded-chip border border-cached-stripe px-2 py-0.5 text-micro uppercase',
        inline ? 'inline-block' : 'fixed right-2 bottom-2 z-50 max-w-[calc(100vw-1rem)] lg:right-auto lg:left-2 lg:max-w-[216px]',
      )}
    >
      {words.join(' · ')}
    </div>
  );
}
