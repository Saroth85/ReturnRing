// AgentStepList (docs/ui-design.md 7): one row per tool_call/tool_result pair. Stub: the raw
// event types in order.
import type { AgentEvent } from '@/api/models';

export function AgentStepList({ events }: { events: AgentEvent[] }) {
  return (
    <ol className="flex flex-wrap gap-1 text-caption" data-agent-steps>
      {events.map((event) => (
        <li key={event.seq} className="rounded-chip border border-border px-1 font-mono" data-id>
          {event.type}
        </li>
      ))}
    </ol>
  );
}
