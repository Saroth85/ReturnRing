// The action bar of the decision rail (docs/ui-design.md 6.2.4, 6.3.1): actions by decision state,
// disabled with the reason, never hidden. A non-restrictive confirmation is two keystrokes (A,
// Enter) in a popover; a restriction opens a sheet where only an explicit confirm commits it.
import * as Popover from '@radix-ui/react-popover';
import { useRef } from 'react';
import { draftSources, messageFor } from '@/api/adapters/messageSegments';
import type { DecisionView } from '@/api/models';
import { usePersona } from '@/app/persona';
import { Button, Kbd } from '@/components/ui';
import type { ActionPlan, SecondaryAction } from '@/domain/decision';
import { OPERATOR } from '../../copy';
import { roleWord } from '../../format';
import { useCaseUi } from '../context';
import { ActionError } from '../sheets/ActionError';
import { StaleDecisionPanel } from '../sheets/common';
import { LangChoice } from '../../ui/fields';
import { InlineNotice } from '@/components/feedback';
import { CompactMessage } from '@/components/message';

export interface ActionBarProps {
  plan: ActionPlan;
  confirmOpen: boolean;
  onConfirmOpenChange(open: boolean): void;
  onConfirm(): void;
  confirming: boolean;
  confirmError: unknown;
  confirmMismatch: string | null;
  /** The decision the open confirm was shown, when a newer version has arrived since (UI38). */
  confirmStale?: DecisionView | null;
  onReload(): void;
}

const SECONDARY_KEY: Record<SecondaryAction, string | null> = { override: 'O', reject: 'R', lift: 'L', renew: null };

export function ActionBar({
  plan,
  confirmOpen,
  onConfirmOpenChange,
  onConfirm,
  confirming,
  confirmError,
  confirmMismatch,
  confirmStale = null,
  onReload,
}: ActionBarProps) {
  const { card, openSheet, gates, draftPending, sendLang, setSendLang } = useCaseUi();
  const persona = usePersona();
  const contentRef = useRef<HTMLDivElement>(null);
  const a = OPERATOR.actions;
  const s = OPERATOR.sheets;
  const blocked = !gates.decide.allowed || draftPending;
  const reason = !gates.decide.allowed ? gates.decideNote : draftPending ? a.draftPendingReason : null;
  const message = messageFor(card.draft, sendLang) ?? draftSources(card.draft)[0] ?? null;

  if (plan.readOnly) {
    return (
      <div className="border-t border-border px-4 py-3 text-body text-ink-2" data-action-bar="read-only">
        {card.decision.superseded_by ? OPERATOR.railCopy.supersededSummary : OPERATOR.railCopy.readOnlySummary}
      </div>
    );
  }

  const secondaryLabel: Record<SecondaryAction, string> = { override: a.override, reject: a.reject, lift: a.lift, renew: a.renew };
  const secondarySheet: Record<SecondaryAction, 'override' | 'reject' | 'review'> = {
    override: 'override',
    reject: 'reject',
    lift: 'review',
    renew: 'review',
  };

  return (
    <div
      className="sticky bottom-0 z-10 flex shrink-0 flex-col gap-2 rounded-b-card border-t border-border bg-surface px-4 py-3"
      data-action-bar
    >
      <div className="flex flex-wrap gap-2">
        {plan.primary === 'approve_restriction' && (
          <Button variant="primary" disabled={blocked} onClick={() => openSheet({ kind: 'approve' })} data-action="approve-restriction">
            {a.approveRestriction} <Kbd>A</Kbd>
          </Button>
        )}
        {plan.primary === 'review' && (
          <Button variant="primary" disabled={blocked} onClick={() => openSheet({ kind: 'review' })} data-action="review">
            {a.review} <Kbd>L</Kbd>
          </Button>
        )}
        {(plan.primary === 'confirm' || plan.primary === 'confirm_guarantee') && (
          <Popover.Root open={confirmOpen} onOpenChange={onConfirmOpenChange}>
            <Popover.Trigger asChild>
              <Button variant="primary" disabled={blocked} data-action="confirm">
                {plan.primary === 'confirm' ? a.confirm : a.confirmGuarantee} <Kbd>A</Kbd>
              </Button>
            </Popover.Trigger>
            <Popover.Portal>
              <Popover.Content
                side="top"
                align="start"
                sideOffset={8}
                collisionPadding={16}
                ref={contentRef}
                onOpenAutoFocus={(event) => {
                  // Enter confirms a non-restrictive proposal: the confirm button takes the focus.
                  event.preventDefault();
                  contentRef.current?.querySelector<HTMLElement>('[data-confirm-send]')?.focus();
                }}
                className="z-50 flex w-96 max-w-[calc(100vw-32px)] flex-col gap-3 rounded-card border border-border bg-surface p-4 text-body shadow-overlay"
                data-confirm-popover
              >
                <p className="font-medium">
                  {(plan.primary === 'confirm' ? s.confirmPopoverTitle : s.confirmGuaranteeTitle)(
                    s.langs[sendLang] ?? sendLang,
                    card.return_id,
                  )}
                </p>
                {message && <CompactMessage message={message} />}
                <LangChoice value={sendLang} onChange={setSendLang} />
                <p className="text-caption text-ink-3">
                  {OPERATOR.railCopy.actingAs} {persona.id} ({roleWord(persona.role)})
                </p>
                {confirmStale && <StaleDecisionPanel shown={confirmStale} onReload={onReload} />}
                {confirmError ? <ActionError error={confirmError} onLoadNew={onReload} onShowCurrent={onReload} /> : null}
                {confirmMismatch && <InlineNotice tone="attention">{confirmMismatch}</InlineNotice>}
                <div className="flex flex-wrap justify-end gap-2">
                  <Popover.Close asChild>
                    <Button>
                      {s.cancel} <Kbd>Esc</Kbd>
                    </Button>
                  </Popover.Close>
                  <Button variant="primary" disabled={confirming || blocked || confirmStale !== null} onClick={onConfirm} data-confirm-send>
                    {s.confirmSend} <Kbd>Enter</Kbd>
                  </Button>
                </div>
              </Popover.Content>
            </Popover.Portal>
          </Popover.Root>
        )}
        {plan.secondary.map((action) => (
          <Button key={action} disabled={blocked} onClick={() => openSheet({ kind: secondarySheet[action] })} data-action={action}>
            {secondaryLabel[action]} {SECONDARY_KEY[action] && <Kbd>{SECONDARY_KEY[action]}</Kbd>}
          </Button>
        ))}
      </div>
      {reason && (
        <p className="text-caption text-ink-3" data-action-reason>
          {reason}
        </p>
      )}
      <p className="text-caption text-ink-3">
        {OPERATOR.railCopy.actingAs} {persona.id} · {roleWord(persona.role)}
      </p>
    </div>
  );
}
