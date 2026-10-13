// Dev harness /dev/agent?return=RET-000102 (dev builds only, docs/ui-design.md 7): the agent
// area can be built before the case card exists. Stub: suggested questions and the raw stream.
import { useState } from 'react';
import { useSearchParams } from 'react-router';
import { usePersona } from '@/app/persona';
import { ErrorState, ScreenPlaceholder } from '@/components/feedback';
import { Button } from '@/components/ui';
import { AgentAnswer } from '../AgentAnswer';
import { AgentStepList } from '../AgentStepList';
import { CaseAssistant } from '../CaseAssistant';
import { AGENT } from '../copy';
import { SuggestedQuestions } from '../SuggestedQuestions';
import { useAgentStream } from '../useAgentStream';

export default function AgentHarness() {
  const [params] = useSearchParams();
  const returnId = params.get('return') ?? 'RET-000102';
  const persona = usePersona();
  const stream = useAgentStream(returnId, persona.id);
  const [open, setOpen] = useState(false);
  return (
    <ScreenPlaceholder title={AGENT.harnessTitle} area="agent" stories={['US-701', 'US-702']} spec="docs/ui-design.md 7">
      <p className="text-body text-ink-2">{AGENT.harnessIntro}</p>
      <div>
        <Button onClick={() => setOpen(true)}>{AGENT.openDrawer}</Button>
      </div>
      <SuggestedQuestions onAsk={(q) => void stream.ask(q)} disabled={stream.status === 'streaming'} />
      <section aria-busy={stream.status === 'streaming'} className="flex flex-col gap-2" data-agent-stream={stream.status}>
        <h2 className="text-h3">
          {AGENT.events} · {stream.status === 'streaming' ? AGENT.streaming : stream.status === 'done' ? AGENT.done : ''}
        </h2>
        <AgentStepList events={stream.events} />
        <AgentAnswer events={stream.events} />
        {stream.status === 'error' && <ErrorState error={stream.error} />}
      </section>
      <CaseAssistant returnId={returnId} open={open} onOpenChange={setOpen} />
    </ScreenPlaceholder>
  );
}
