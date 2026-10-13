// The question input of the case assistant (docs/ui-design.md 7): the three suggested questions,
// a free question with Ask (Enter), Stop while an answer streams. Disabled with the reason for a
// role that cannot ask, after a 429, and for free text in cached mode.
import { useId, useState, type FormEvent, type Ref } from 'react';
import { Button, Kbd } from '@/components/ui';
import { AGENT } from './copy';
import { useCompactLayout } from './layout';
import { QUESTION_MAX_LENGTH } from './limits';
import { SuggestedQuestions } from './SuggestedQuestions';

interface ComposerProps {
  inputRef?: Ref<HTMLInputElement>;
  onAsk(question: string): void;
  onStop(): void;
  streaming: boolean;
  /** Why no question can be asked (role, read-only demo), or null. */
  blockedReason: string | null;
  coolingDown: boolean;
  /** Cached mode: only the suggested questions have precomputed answers. */
  cached: boolean;
  /** Room for the mode corner tag under the drawer's footer. */
  cornerTagRoom?: boolean;
  /** Whether a question was asked already (phones fold the suggestions then). */
  hasTurns: boolean;
}

export function Composer({ inputRef, onAsk, onStop, streaming, blockedReason, coolingDown, cached, cornerTagRoom = false, hasTurns }: ComposerProps) {
  const inputId = useId();
  const notesId = useId();
  const [draft, setDraft] = useState('');
  const compact = useCompactLayout();
  // null: folded on a phone once a question was asked; the disclosure overrides it until the next question.
  const [suggestionsOpen, setSuggestionsOpen] = useState<boolean | null>(null);
  const suggestionsExpanded = suggestionsOpen ?? !hasTurns;
  const canAsk = blockedReason === null && !coolingDown;
  const canType = canAsk && !cached;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const question = draft.trim();
    if (!canType || !question) return;
    setSuggestionsOpen(null);
    onAsk(question);
    setDraft('');
  };
  return (
    <form
      onSubmit={submit}
      className={`flex flex-col gap-2 border-t border-border bg-surface px-4 pt-3 ${cornerTagRoom ? 'pb-9' : 'pb-3'}`}
      aria-label={AGENT.composer.label}
      data-agent-composer
    >
      <SuggestedQuestions
        onAsk={(question) => {
          setSuggestionsOpen(null);
          onAsk(question);
        }}
        disabled={!canAsk}
        collapsible={compact}
        expanded={suggestionsExpanded}
        onToggle={() => setSuggestionsOpen(!suggestionsExpanded)}
      />
      <div className="flex items-center gap-2">
        <label htmlFor={inputId} className="sr-only">
          {AGENT.composer.label}
        </label>
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          maxLength={QUESTION_MAX_LENGTH}
          placeholder={AGENT.composer.placeholder}
          disabled={!canType}
          aria-describedby={notesId}
          autoComplete="off"
          data-autofocus
          className="min-h-8 min-w-0 flex-1 rounded-control border border-border-strong bg-surface px-2 text-body text-ink-1 placeholder:text-ink-3 disabled:cursor-not-allowed disabled:bg-surface-2"
        />
        <Button type="submit" variant="primary" disabled={!canType || draft.trim() === ''}>
          {AGENT.composer.ask} <Kbd>{AGENT.composer.askKey}</Kbd>
        </Button>
        {streaming && (
          <Button type="button" onClick={onStop}>
            {AGENT.composer.stop}
          </Button>
        )}
      </div>
      <div id={notesId} className="flex flex-col gap-1 text-caption text-ink-3">
        {blockedReason && <p data-role-reason>{blockedReason}</p>}
        {coolingDown && <p data-cooldown>{AGENT.composer.coolingDown}</p>}
        {cached && canAsk && (
          <p data-constant data-cached-note>
            {AGENT.composer.cached}
          </p>
        )}
        <p data-constant>{AGENT.footer}</p>
      </div>
    </form>
  );
}
