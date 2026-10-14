// The keyboard shortcut list (docs/ui-design.md 3.5): `?` opens it from anywhere (UI35).
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { Kbd } from '@/components/ui';
import { SHELL } from '@/copy/common';

export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange(open: boolean): void }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-x-0 top-[var(--mode-banner-h,0px)] bottom-0 z-40 bg-ink-1/20" />
        <Dialog.Content
          data-shortcuts
          className="fixed top-[calc(50%_+_var(--mode-banner-h,0px)_/_2)] left-1/2 z-50 flex max-h-[calc(100vh_-_2rem_-_var(--mode-banner-h,0px))] w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 overflow-y-auto rounded-card border border-border bg-surface p-4 text-ink-1 shadow-overlay"
        >
          <div className="flex items-start justify-between gap-3">
            <Dialog.Title className="text-h3">{SHELL.shortcuts}</Dialog.Title>
            <Dialog.Close className="rounded-control p-1 text-ink-2 hover:bg-surface-2" aria-label={SHELL.shortcutsClose}>
              <X aria-hidden="true" size={16} />
            </Dialog.Close>
          </div>
          <Dialog.Description className="text-caption text-ink-3">{SHELL.shortcutsHint}</Dialog.Description>
          {SHELL.shortcutGroups.map((group) => (
            <section key={group.scope} className="flex flex-col gap-1">
              <h3 className="text-title">{group.scope}</h3>
              <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1 text-body">
                {group.keys.map(([keys, action]) => (
                  <div key={keys} className="contents">
                    <dt>
                      <Kbd>{keys}</Kbd>
                    </dt>
                    <dd className="text-ink-2">{action}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
