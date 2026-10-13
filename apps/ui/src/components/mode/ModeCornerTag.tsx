import { MODE } from '@/copy/common';
import { useModeView } from './useModeView';

/** Bottom-right tag in cached and mock modes, so a cropped screenshot keeps the mode (5.2). */
export function ModeCornerTag() {
  const view = useModeView();
  if (!view.isCached && !view.isMock) return null;
  const words = [view.isCached ? MODE.cornerCached : null, view.isMock ? MODE.cornerContract : null].filter(Boolean);
  return (
    <div
      data-mode-corner
      className="hatch-cached pointer-events-none fixed right-2 bottom-2 z-50 rounded-chip border border-cached-stripe px-2 py-0.5 text-micro uppercase"
    >
      {words.join(' · ')}
    </div>
  );
}
