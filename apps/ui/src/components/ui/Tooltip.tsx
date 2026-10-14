import * as RadixTooltip from '@radix-ui/react-tooltip';
import type { ReactNode } from 'react';

interface TooltipProps {
  content: ReactNode;
  /** A focusable trigger (a button); the tooltip opens on hover and focus. */
  children: ReactNode;
  side?: 'top' | 'right' | 'bottom' | 'left';
}

/** Radix tooltip with the overlay style of docs/ui-design.md 4.5. Text only, never actions. */
export function Tooltip({ content, children, side = 'top' }: TooltipProps) {
  return (
    <RadixTooltip.Provider delayDuration={300}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={6}
            collisionPadding={16}
            className="z-50 max-w-xs rounded-control border border-border bg-surface px-2 py-1 text-caption text-ink-1 shadow-overlay"
            data-tooltip
          >
            {content}
            <RadixTooltip.Arrow className="fill-surface" />
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}
