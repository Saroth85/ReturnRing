// A right-side sheet over the decision rail (docs/ui-design.md 6.3), on Radix Dialog (MIT): focus
// trapped and returned to the trigger. Ctrl/Cmd+Enter confirms; a bare Enter never does (a
// restrictive approval must be deliberate). Esc closes, asking first when something was typed.
// Operator-local until the shared Sheet primitive exists (src/components/ui, open issue).
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { useState, type ReactNode } from 'react';

/**
 * Where focus goes back when a sheet closes and the element that opened it is gone or was the page
 * itself (a shortcut): the button of the same action on the rail (UI24).
 */
const RETURN_FOCUS: Record<string, string> = {
  approve: '[data-action="approve-restriction"]',
  override: '[data-action="override"]',
  reject: '[data-action="reject"]',
  review: '[data-action="review"], [data-action="lift"]',
  evidence: '[data-add-evidence], [data-explain-link]',
  dispute: '[data-dispute-link]',
};
import { OPERATOR } from '../copy';

interface SheetProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  title: ReactNode;
  /** One line under the title (ids of the case). */
  description?: ReactNode;
  children: ReactNode;
  /** Buttons and the reason a confirm is disabled. */
  footer?: ReactNode;
  /** Called on Ctrl/Cmd+Enter. */
  onConfirm?(): void;
  /** Something was typed: Esc asks before discarding it. */
  dirty?: boolean;
  /** Data attribute naming the sheet, for tests. */
  name: string;
  width?: 'sheet' | 'record';
  /** A non-modal drawer leaves the page behind it readable (the decision record over the card). */
  modal?: boolean;
}

export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  onConfirm,
  dirty = false,
  name,
  width = 'sheet',
  modal = true,
}: SheetProps) {
  const [escArmed, setEscArmed] = useState(false);
  // The element that opened the sheet: focus returns there on close (docs/ui-design.md 10.3.3,
  // WCAG 2.4.3). The sheets open through state, so Radix has no Dialog.Trigger to return to (UI24).
  const [opener] = useState(() => (typeof document === 'undefined' ? null : (document.activeElement as HTMLElement | null)));
  const change = (next: boolean) => {
    if (!next) setEscArmed(false);
    onOpenChange(next);
  };
  return (
    <Dialog.Root open={open} onOpenChange={change} modal={modal}>
      <Dialog.Portal>
        {modal && <Dialog.Overlay className="fixed inset-x-0 top-[var(--mode-banner-h,0px)] bottom-0 z-40 bg-ink-1/20" />}
        <Dialog.Content
          data-sheet={name}
          {...(description ? {} : { 'aria-describedby': undefined })}
          onOpenAutoFocus={(event) => {
            // Focus starts on the close button (Esc closes, Tab moves into the sheet), never on a
            // copy-id button of the description.
            event.preventDefault();
            (event.currentTarget as HTMLElement | null)?.querySelector<HTMLElement>('[data-sheet-close]')?.focus();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const usable = opener?.isConnected && opener !== document.body && opener.id !== 'main' ? opener : null;
            const fallback = RETURN_FOCUS[name] ? document.querySelector<HTMLElement>(RETURN_FOCUS[name]) : null;
            (usable ?? fallback)?.focus({ preventScroll: true });
          }}
          onEscapeKeyDown={(event) => {
            if (dirty && !escArmed) {
              event.preventDefault();
              setEscArmed(true);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && onConfirm) {
              event.preventDefault();
              onConfirm();
            }
          }}
          className={
            width === 'record'
              ? 'fixed top-[var(--mode-banner-h,0px)] right-0 bottom-0 z-50 flex w-record max-w-full flex-col border-l border-border bg-surface shadow-overlay'
              : 'fixed top-[var(--mode-banner-h,0px)] right-0 bottom-0 z-50 flex w-sheet max-w-full flex-col border-l border-border bg-surface shadow-overlay'
          }
        >
          <div className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <Dialog.Title className="text-h3">{title}</Dialog.Title>
              {description && (
                <Dialog.Description asChild>
                  <div className="mt-1 text-caption text-ink-3">{description}</div>
                </Dialog.Description>
              )}
            </div>
            <Dialog.Close className="rounded-control p-1 hover:bg-surface-2" aria-label={OPERATOR.sheets.close} data-sheet-close>
              <X aria-hidden="true" size={18} />
            </Dialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{children}</div>
          {(footer || escArmed) && (
            <div className="border-t border-border bg-surface px-4 py-3">
              {escArmed && (
                <p role="status" className="mb-2 text-caption text-attn-fg">
                  {OPERATOR.sheets.escAgain}
                </p>
              )}
              {footer}
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
