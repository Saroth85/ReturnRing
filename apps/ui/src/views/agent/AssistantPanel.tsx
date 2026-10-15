// The case assistant's body (US-701, US-702; docs/ui-design.md 7), shared by the drawer the case
// card mounts and the dev harness page: the questions and answers of this case (earlier ones
// above the latest), then the composer. Cited refs focus the case card through onRefSelect. The
// suggested questions come from the case card (questions.ts). In the cached demo no model runs and
// the agent answers 503 to every question: the panel says so up front and asks nothing.
import { CloudOff, Search } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, type Ref } from 'react';
import type { EntityRef } from '@/api/models';
import { useCase } from '@/api/queries';
import { InlineNotice, useRoleGate } from '@/components/feedback';
import { useModeView } from '@/components/mode';
import { AgentTurn } from './AgentTurn';
import { turnAnnouncement } from './announce';
import { Composer } from './Composer';
import { AGENT } from './copy';
import { isPlainClick, isWideLayout } from './layout';
import { suggestedQuestions } from './questions';
import type { RefActivate } from './RefChip';
import type { AgentConversation } from './useAgentStream';

export interface AssistantPanelProps {
  conversation: AgentConversation;
  returnId: string;
  /** Scrolls to and pulses the cited ref on the case card (CaseAssistantProps.onRefSelect). */
  onRefSelect?(entity: EntityRef): void;
  /** Closes the drawer: before focusing a ref on a narrow screen, and for the decision panel. */
  onDismiss?(): void;
  variant: 'drawer' | 'page';
  inputRef?: Ref<HTMLInputElement>;
}

export function AssistantPanel({ conversation, returnId, onRefSelect, onDismiss, variant, inputRef }: AssistantPanelProps) {
  const gate = useRoleGate('POST /api/agent/ask');
  const mode = useModeView();
  const scroller = useRef<HTMLElement>(null);
  const latest = useRef<HTMLElement>(null);
  const { turns, ask, stop } = conversation;
  const card = useCase(returnId);
  const questions = useMemo(() => suggestedQuestions(card.data), [card.data]);

  // A new question scrolls its turn into view; streamed tokens never move the page.
  useEffect(() => {
    const box = scroller.current;
    const turn = latest.current;
    if (!box || !turn || typeof box.scrollTo !== 'function') return;
    // The scroller is `relative`, so offsetTop is measured from its top.
    box.scrollTo({ top: Math.max(0, turn.offsetTop - 8) });
  }, [turns.length]);

  const activateRef = useCallback<RefActivate>(
    (entity, event) => {
      if (!isPlainClick(event)) return;
      // Narrow screens: the drawer covers the card, so it closes before the card scrolls.
      if (!isWideLayout()) onDismiss?.();
      if (onRefSelect) {
        event.preventDefault();
        onRefSelect(entity);
      }
    },
    [onRefSelect, onDismiss],
  );

  const canAsk = gate.allowed && !conversation.coolingDown && !mode.isCached;
  const retry = useCallback((question: string) => void ask(question), [ask]);

  return (
    <div className="@container flex min-h-0 flex-1 flex-col" data-agent-panel={variant} data-agent-stream={conversation.status}>
      <section aria-label={AGENT.conversationLabel} ref={scroller} className="relative min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {mode.isCached && (
          <div className="mb-4" data-agent-unavailable="cached">
            <InlineNotice tone="info" title={AGENT.composer.cachedTitle}>
              <p className="flex items-center gap-2">
                <CloudOff aria-hidden="true" size={14} className="shrink-0" />
                {AGENT.composer.cached}
              </p>
            </InlineNotice>
          </div>
        )}
        {turns.length === 0 ? (
          <Intro />
        ) : (
          <ol className="flex flex-col gap-6">
            {turns.map((turn, index) => (
              <li key={turn.id} className={index > 0 ? 'border-t border-border pt-6' : undefined}>
                <AgentTurn
                  ref={index === turns.length - 1 ? latest : undefined}
                  turn={turn}
                  returnId={returnId}
                  onActivateRef={activateRef}
                  onRefSelect={onRefSelect}
                  onDismiss={onDismiss}
                  onRetry={canAsk ? retry : undefined}
                />
              </li>
            ))}
          </ol>
        )}
      </section>
      <p aria-live="polite" className="sr-only" data-verbatim data-agent-announcer>
        {turnAnnouncement(turns[turns.length - 1])}
      </p>
      <Composer
        inputRef={inputRef}
        questions={questions}
        onAsk={(question) => void ask(question)}
        onStop={stop}
        streaming={conversation.streaming}
        blockedReason={gate.allowed ? null : gate.reason}
        coolingDown={conversation.coolingDown}
        cooldownSeconds={conversation.cooldownSeconds}
        cached={mode.isCached}
        cornerTagRoom={variant === 'drawer' && (mode.isCached || mode.isMock)}
        hasTurns={turns.length > 0}
      />
    </div>
  );
}

function Intro() {
  return (
    <div className="flex flex-col gap-4 text-body" data-agent-intro>
      <div className="flex flex-col gap-1">
        <p className="flex items-center gap-2 text-title text-ink-1">
          <Search aria-hidden="true" size={16} />
          {AGENT.intro.title}
        </p>
        <p className="text-ink-2">{AGENT.intro.body}</p>
      </div>
      <div className="flex flex-col gap-2">
        <p className="text-micro uppercase text-ink-3">{AGENT.intro.toolsTitle}</p>
        <ul className="flex flex-col gap-1.5">
          {Object.entries(AGENT.tools).map(([tool, { label, reads }]) => (
            <li key={tool} className="flex flex-wrap gap-x-2">
              <span className="font-medium text-ink-1">{label}</span>
              <span className="text-ink-2">{reads}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
