// One question and its answer (docs/ui-design.md 7): who asked, the tool-call count through
// Figure (rule 11), the steps, then the answer, a refusal or a failure, and any deviation of the
// stream from the contract.
import { useId, useMemo, type Ref } from 'react';
import { API_MODE } from '@/api/adapters';
import type { EntityRef } from '@/api/models';
import { personaName } from '@/app/persona';
import { Figure } from '@/components/provenance';
import { AgentAnswer } from './AgentAnswer';
import { AgentRequestError, AgentStreamError, AgentStreamIssues } from './AgentProblem';
import { AgentRefusal } from './AgentRefusal';
import { AgentStepList } from './AgentStepList';
import type { Turn } from './conversation';
import { AGENT } from './copy';
import type { RefActivate } from './RefChip';
import { foldAgentEvents, stepsUsedMetric } from './stream';

interface AgentTurnProps {
  ref?: Ref<HTMLElement>;
  turn: Turn;
  returnId: string;
  onActivateRef?: RefActivate;
  onRefSelect?(entity: EntityRef): void;
  onDismiss?(): void;
  /** Asks the same question again (after a failure). */
  onRetry?(question: string): void;
}

export function AgentTurn({ ref, turn, returnId, onActivateRef, onRefSelect, onDismiss, onRetry }: AgentTurnProps) {
  const questionId = useId();
  const stepsId = useId();
  const streaming = turn.status === 'streaming';
  const view = useMemo(() => foldAgentEvents(turn.events, { ended: turn.status === 'done' }), [turn.events, turn.status]);
  const { final, done, steps } = view;
  const retry = onRetry ? () => onRetry(turn.question) : undefined;
  const answer = final?.kind === 'answer' ? final : null;
  const stepsSettled = steps.length > 0 && steps.every((s) => s.status !== 'running');
  const showAnswer = answer !== null || view.streamedText !== '' || (streaming && final === null && stepsSettled);
  const showSteps = steps.length > 0 || (streaming && final === null && view.streamedText === '');

  return (
    <article ref={ref} aria-labelledby={questionId} className="flex flex-col gap-3" data-turn={turn.status}>
      <header className="flex flex-col gap-1">
        <p className="flex flex-wrap items-center gap-x-2 text-caption text-ink-3">
          <span>{AGENT.turn.askedBy(personaName(turn.askedBy))}</span>
          {done && (
            <span className="inline-flex flex-wrap items-baseline gap-1" data-tool-calls>
              <span aria-hidden="true">·</span>
              {AGENT.turn.toolCalls}
              <Figure metric={stepsUsedMetric(done, API_MODE)} label={AGENT.turn.toolCallsLabel} />
              <span data-constant>{AGENT.turn.stepLimit}</span>
            </span>
          )}
        </p>
        <p id={questionId} className="rounded-control bg-surface-2 px-3 py-2 text-body font-medium whitespace-pre-wrap text-ink-1" data-verbatim>
          {turn.question}
        </p>
      </header>

      {showSteps && (
        <section aria-labelledby={stepsId} className="flex flex-col gap-2">
          <h3 id={stepsId} className="text-micro uppercase text-ink-3">
            {AGENT.turn.stepsTitle}
          </h3>
          {steps.length > 0 ? (
            <AgentStepList steps={steps} returnId={returnId} onActivateRef={onActivateRef} />
          ) : (
            <p className="text-caption text-ink-3">{AGENT.turn.running}</p>
          )}
        </section>
      )}

      {final?.kind === 'refusal' && (
        <AgentRefusal refusal={final} generation={done?.generation ?? null} returnId={returnId} onRefSelect={onRefSelect} onDismiss={onDismiss} />
      )}
      {final?.kind === 'error' && <AgentStreamError error={final} onRetry={retry} />}
      {showAnswer && (
        <AgentAnswer
          answer={answer}
          streamedText={view.streamedText}
          streaming={streaming}
          generation={done?.generation ?? null}
          returnId={returnId}
          onActivateRef={onActivateRef}
        />
      )}

      {turn.status === 'stopped' && <p className="text-caption text-ink-3">{AGENT.turn.stopped}</p>}
      {turn.status === 'failed' && <AgentRequestError error={turn.error} returnId={returnId} onRetry={retry} />}
      <AgentStreamIssues issues={view.issues} />
    </article>
  );
}
