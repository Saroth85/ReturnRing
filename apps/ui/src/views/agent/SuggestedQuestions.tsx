// SuggestedQuestions (US-702, docs/ui-design.md 7): the three questions that always work, as the
// contract examples ask them. In cached mode they are the only questions enabled (O-15). On a
// phone they fold behind a disclosure once a question was asked, so the answer keeps the room.
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useId } from 'react';
import { AGENT } from './copy';

interface SuggestedQuestionsProps {
  onAsk(question: string): void;
  disabled?: boolean;
  /** Folded behind a disclosure button (phones). */
  collapsible?: boolean;
  expanded?: boolean;
  onToggle?(): void;
}

export function SuggestedQuestions({ onAsk, disabled = false, collapsible = false, expanded = true, onToggle }: SuggestedQuestionsProps) {
  const titleId = useId();
  const listId = useId();
  const open = !collapsible || expanded;
  return (
    <div className="flex flex-col gap-1.5" data-suggested-questions>
      {collapsible ? (
        <button
          type="button"
          id={titleId}
          aria-expanded={open}
          aria-controls={listId}
          onClick={onToggle}
          className="inline-flex items-center gap-1 self-start rounded-control text-micro uppercase text-ink-2 hover:text-ink-1"
        >
          {open ? <ChevronDown aria-hidden="true" size={14} /> : <ChevronRight aria-hidden="true" size={14} />}
          {AGENT.suggestedTitle}
        </button>
      ) : (
        <p id={titleId} className="text-micro uppercase text-ink-3">
          {AGENT.suggestedTitle}
        </p>
      )}
      {open && (
        <ul id={listId} aria-labelledby={titleId} className="flex flex-col gap-1.5">
          {AGENT.suggested.map((question) => (
            <li key={question}>
              <button
                type="button"
                onClick={() => onAsk(question)}
                disabled={disabled}
                data-suggested
                className="w-full rounded-control border border-border bg-surface px-2.5 py-1.5 text-left text-dense text-ink-1 hover:border-accent hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50"
              >
                {question}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
