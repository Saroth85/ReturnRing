// AgentAnswer (docs/ui-design.md 7): the answer as generated text with its badge. Stub: the final
// answer or refusal text, verbatim.
import type { AgentEvent } from '@/api/models';
import { GeneratedText } from '@/components/provenance';

export function AgentAnswer({ events }: { events: AgentEvent[] }) {
  const final = events.find((e) => e.type === 'answer' || e.type === 'refusal' || e.type === 'error');
  const done = events.find((e) => e.type === 'done');
  if (!final || final.type === 'error') return null;
  if (!done || done.type !== 'done') {
    return (
      <p className="whitespace-pre-wrap" data-generated data-verbatim>
        {final.text}
      </p>
    );
  }
  return <GeneratedText generation={done.generation}>{final.text}</GeneratedText>;
}
