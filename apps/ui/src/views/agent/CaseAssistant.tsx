// CaseAssistant: the single public export of the agent area (US-701, US-702; docs/ui-design.md 7),
// a 440 px right drawer mounted by the operator case card. Read-only: it asks POST /api/agent/ask
// and shows the streamed steps, the answer, the evidence consulted and missing; nothing in it can
// change the decision (rule 1). The drawer is not modal, so the case card stays readable and a
// cited ref can scroll and pulse it; it starts under the mode banner, which is never covered.
import * as Dialog from '@radix-ui/react-dialog';
import { Search, X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { EntityRef } from '@/api/models';
import { usePersona } from '@/app/persona';
import { AssistantPanel } from './AssistantPanel';
import { AGENT } from './copy';
import { useDrawerTop } from './layout';
import { useAssistantModel } from './model';
import { useAgentStream, type AgentConversation } from './useAgentStream';

export interface CaseAssistantProps {
  returnId: string;
  open: boolean;
  onOpenChange(open: boolean): void;
  /** Scrolls to and pulses the cited link, cluster, customer or order on the case card. */
  onRefSelect?(ref: EntityRef): void;
}

export function CaseAssistant({ returnId, open, onOpenChange, onRefSelect }: CaseAssistantProps) {
  const persona = usePersona();
  // The history lives here, outside the drawer content, so it survives closing and reopening.
  const conversation = useAgentStream(returnId, persona.id);
  return <AssistantDrawer conversation={conversation} returnId={returnId} open={open} onOpenChange={onOpenChange} onRefSelect={onRefSelect} />;
}

interface AssistantDrawerProps extends CaseAssistantProps {
  conversation: AgentConversation;
}

/** The drawer around a conversation (also used by the dev harness). */
export function AssistantDrawer({ conversation, returnId, open, onOpenChange, onRefSelect }: AssistantDrawerProps) {
  const top = useDrawerTop(open);
  const modelName = useAssistantModel();
  const input = useRef<HTMLInputElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const { stop } = conversation;

  // Closing the drawer cancels the answer being streamed (docs/ui-design.md 7).
  useEffect(() => {
    if (!open) stop();
  }, [open, stop]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <Dialog.Portal>
        <Dialog.Content
          ref={content}
          style={{ top }}
          className="fixed right-0 bottom-0 z-30 flex w-drawer max-w-full flex-col border-l border-border bg-surface shadow-overlay"
          data-case-assistant={returnId}
          // The card stays usable beside the drawer: clicks and focus outside do not close it.
          onInteractOutside={(event) => event.preventDefault()}
          onOpenAutoFocus={(event) => {
            returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            const target =
              (input.current && !input.current.disabled ? input.current : null) ??
              content.current?.querySelector<HTMLButtonElement>('button[data-suggested]:not(:disabled)') ??
              null;
            if (target) {
              event.preventDefault();
              // Never scrolls: a cited ref may be scrolling the card to its target right now.
              target.focus({ preventScroll: true });
            }
          }}
          onCloseAutoFocus={(event) => {
            const back = returnFocus.current;
            const now = document.activeElement;
            // A cited ref or "Go to the decision panel" already moved focus to the card: keep it there.
            if (now instanceof HTMLElement && now !== document.body && !content.current?.contains(now)) {
              event.preventDefault();
              return;
            }
            // Back to the element that opened the drawer; when it is gone or was the page itself
            // (the I shortcut), to the "Ask about this case" button (UI24, WCAG 2.4.3).
            const usable = back?.isConnected && back !== document.body && back.id !== 'main' ? back : null;
            const target = usable ?? document.querySelector<HTMLElement>('[data-ask-button]');
            if (target) {
              event.preventDefault();
              // Never scrolls: a cited ref may be scrolling the card to its target right now.
              target.focus({ preventScroll: true });
            }
          }}
        >
          <div className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
            <div className="flex min-w-0 flex-col gap-0.5">
              <Dialog.Title className="text-title text-ink-1">
                <Search aria-hidden="true" size={16} className="mr-2 inline-block align-[-2px] text-ink-2" />
                {AGENT.title(returnId)}{' '}
                {modelName && (
                  <span className="font-normal text-ink-3" data-constant data-assistant-model>
                    {modelName}
                  </span>
                )}
              </Dialog.Title>
              <Dialog.Description className="text-body text-ink-2">{AGENT.readOnly}</Dialog.Description>
            </div>
            <Dialog.Close className="rounded-control p-1.5 text-ink-2 hover:bg-surface-2" aria-label={AGENT.close} title={AGENT.close}>
              <X aria-hidden="true" size={18} />
            </Dialog.Close>
          </div>
          <AssistantPanel
            conversation={conversation}
            returnId={returnId}
            onRefSelect={onRefSelect}
            onDismiss={() => onOpenChange(false)}
            variant="drawer"
            inputRef={input}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
