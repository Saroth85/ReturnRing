import type { ReactNode } from 'react';

/** A keyboard hint next to the button that has the shortcut (docs/ui-design.md 3.5). */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-chip border border-border bg-surface-2 px-1 font-mono text-micro text-ink-2">{children}</kbd>
  );
}
