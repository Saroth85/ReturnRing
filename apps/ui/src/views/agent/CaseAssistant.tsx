// CaseAssistant: the single public export of the agent area (docs/ui-design.md 7), a 440 px right
// drawer mounted by the operator case card. Stub: the drawer and its fixed copy.
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { EntityRef } from '@/api/models';
import { AGENT } from './copy';

export interface CaseAssistantProps {
  returnId: string;
  open: boolean;
  onOpenChange(open: boolean): void;
  /** Scrolls to and pulses the cited link, cluster, customer or order on the case card. */
  onRefSelect?(ref: EntityRef): void;
}

export function CaseAssistant({ returnId, open, onOpenChange }: CaseAssistantProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-ink-1/20" />
        <Dialog.Content
          className="fixed top-0 right-0 z-50 flex h-full w-drawer max-w-full flex-col gap-3 border-l border-border bg-surface p-4 shadow-overlay"
          data-case-assistant={returnId}
        >
          <div className="flex items-start justify-between gap-2">
            <Dialog.Title className="text-h3">{AGENT.title(returnId)}</Dialog.Title>
            <Dialog.Close className="rounded-control p-1 hover:bg-surface-2" aria-label={AGENT.close}>
              <X aria-hidden="true" size={16} />
            </Dialog.Close>
          </div>
          <Dialog.Description className="text-body text-ink-2">{AGENT.readOnly}</Dialog.Description>
          <p className="rounded-card border border-dashed border-border-strong p-3 text-caption text-ink-3">{AGENT.placeholder}</p>
          <p className="mt-auto text-caption text-ink-3" data-constant>
            {AGENT.footer}
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
