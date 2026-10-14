// AgentAnswer (docs/ui-design.md 7): tokens append without animation into a region with
// aria-busy; the final answer replaces them as generated text with its badge (the panel makes the
// one polite announcement); then "Evidence consulted" and "Evidence missing" side by side. Model
// text is plain text, never HTML or Markdown.
import { useId } from 'react';
import type { GenerationInfo } from '@/api/models';
import { GeneratedText } from '@/components/provenance';
import { AGENT } from './copy';
import { RefList, type RefActivate } from './RefChip';
import type { FinalView } from './stream';

type Answer = Extract<FinalView, { kind: 'answer' }>;

interface AgentAnswerProps {
  /** The final answer, once it arrived. */
  answer: Answer | null;
  /** The tokens received so far. */
  streamedText: string;
  streaming: boolean;
  generation: GenerationInfo | null;
  returnId: string;
  onActivateRef?: RefActivate;
}

export function AgentAnswer({ answer, streamedText, streaming, generation, returnId, onActivateRef }: AgentAnswerProps) {
  const headingId = useId();
  const text = answer?.text ?? streamedText;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2" data-agent-answer>
      <h3 id={headingId} className="text-micro uppercase text-ink-3">
        {AGENT.turn.answerTitle} · {AGENT.turn.answerNote}
      </h3>
      <div aria-busy={streaming} className="text-body text-ink-1">
        {answer && generation ? (
          <GeneratedText generation={generation}>{answer.text}</GeneratedText>
        ) : text ? (
          <p className="whitespace-pre-wrap" data-generated data-verbatim>
            {text}
          </p>
        ) : streaming ? (
          <p className="text-ink-3">{AGENT.turn.writing}</p>
        ) : null}
      </div>
      {answer && <EvidenceColumns answer={answer} returnId={returnId} onActivateRef={onActivateRef} />}
    </section>
  );
}

function EvidenceColumns({ answer, returnId, onActivateRef }: { answer: Answer; returnId: string; onActivateRef?: RefActivate }) {
  const consultedId = useId();
  const missingId = useId();
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-1 gap-3 @min-[25rem]:grid-cols-2" data-evidence-columns>
        <section aria-labelledby={consultedId} className="flex min-w-0 flex-col gap-1.5 rounded-control border border-border p-2" data-evidence="consulted">
          <h4 id={consultedId} className="text-micro uppercase text-ink-2">
            {AGENT.turn.consulted}
          </h4>
          {answer.evidenceConsulted.length > 0 ? (
            <RefList refs={answer.evidenceConsulted} returnId={returnId} onActivate={onActivateRef} />
          ) : (
            <p className="text-caption text-ink-3">{AGENT.turn.noneConsulted}</p>
          )}
        </section>
        <section aria-labelledby={missingId} className="flex min-w-0 flex-col gap-1.5 rounded-control border border-dashed border-border-strong p-2" data-evidence="missing">
          <h4 id={missingId} className="text-micro uppercase text-ink-2">
            {AGENT.turn.missing}
          </h4>
          {answer.evidenceMissing.length > 0 ? (
            <ul className="list-disc space-y-1 pl-4 text-caption text-ink-1" data-verbatim>
              {answer.evidenceMissing.map((item, index) => (
                <li key={`${index}:${item}`}>{item}</li>
              ))}
            </ul>
          ) : (
            <p className="text-caption text-ink-3">{AGENT.turn.noneMissing}</p>
          )}
        </section>
      </div>
      {answer.otherRefs.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-micro uppercase text-ink-3">{AGENT.turn.alsoCited}</p>
          <RefList refs={answer.otherRefs} returnId={returnId} onActivate={onActivateRef} />
        </div>
      )}
    </div>
  );
}
