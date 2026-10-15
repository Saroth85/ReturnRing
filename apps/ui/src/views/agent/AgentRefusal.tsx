// A refusal (docs/ui-design.md 7): a neutral card, never an error colour. A request to change
// something (write_request) points to the decision panel, where a person decides (rule 1, rule 5);
// code refuses it before any model call (fallback_reason write_request, no model call: ADR-15
// section 15), and the card says so. The assistant's own words follow verbatim as generated text.
import { Eye } from 'lucide-react';
import { useId, type MouseEvent } from 'react';
import { Link } from 'react-router';
import type { EntityRef, GenerationInfo } from '@/api/models';
import { useCase } from '@/api/queries';
import { GeneratedText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { AGENT } from './copy';
import type { FinalView } from './stream';

type Refusal = Extract<FinalView, { kind: 'refusal' }>;

interface AgentRefusalProps {
  refusal: Refusal;
  generation: GenerationInfo | null;
  returnId: string;
  /** The case card's onRefSelect: "Go to the decision panel" sends the decision ref. */
  onRefSelect?(entity: EntityRef): void;
  /** Closes the drawer, which covers the decision rail. */
  onDismiss?(): void;
}

export function AgentRefusal({ refusal, generation, returnId, onRefSelect, onDismiss }: AgentRefusalProps) {
  const titleId = useId();
  const copy = AGENT.refusal[refusal.reason];
  return (
    <section
      aria-labelledby={titleId}
      className="flex flex-col gap-2 rounded-card border border-border bg-surface-2 p-3"
      data-agent-refusal={refusal.reason}
    >
      <p id={titleId} className="flex items-center gap-2 font-medium text-ink-1">
        <Eye aria-hidden="true" size={16} className="shrink-0 text-ink-2" />
        {copy.title}
      </p>
      <p className="text-body text-ink-2" data-constant>
        {copy.body}
      </p>
      {generation?.fallback_reason === 'write_request' && (
        <p className="inline-flex self-start rounded-chip border border-border px-1.5 text-caption text-ink-2" data-refused-by-code>
          {AGENT.refusal.byCode}
        </p>
      )}
      <div className="flex flex-col gap-1">
        <p className="text-micro uppercase text-ink-3">{AGENT.refusal.replied}</p>
        <div className="text-body text-ink-1">
          {generation ? (
            <GeneratedText generation={generation}>{refusal.text}</GeneratedText>
          ) : (
            <p className="whitespace-pre-wrap" data-generated data-verbatim>
              {refusal.text}
            </p>
          )}
        </div>
      </div>
      {refusal.reason === 'write_request' && <GoToDecision returnId={returnId} onRefSelect={onRefSelect} onDismiss={onDismiss} />}
    </section>
  );
}

function GoToDecision({ returnId, onRefSelect, onDismiss }: Pick<AgentRefusalProps, 'returnId' | 'onRefSelect' | 'onDismiss'>) {
  // The card is cached on the case page; the decision id makes the ref precise.
  const card = useCase(returnId);
  const decisionId = card.data?.decision.decision_id;
  if (onRefSelect) {
    const target: EntityRef = decisionId ? { kind: 'decision', id: decisionId } : { kind: 'return', id: returnId };
    return (
      <div>
        <Button
          variant="secondary"
          onClick={() => {
            onDismiss?.();
            onRefSelect(target);
          }}
        >
          {AGENT.refusal.goToDecision}
        </Button>
      </div>
    );
  }
  return (
    <div>
      <Link
        to={`/returns/${encodeURIComponent(returnId)}`}
        onClick={(event: MouseEvent) => {
          if (event.button === 0) onDismiss?.();
        }}
        className="inline-flex min-h-8 items-center rounded-control border border-border-strong bg-surface px-3 text-body font-medium text-ink-1 hover:bg-surface-2"
      >
        {AGENT.refusal.goToDecision}
      </Link>
    </div>
  );
}
