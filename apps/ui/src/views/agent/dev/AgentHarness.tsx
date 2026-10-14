// Dev harness /dev/agent?return=RET-000102 (dev builds only, docs/ui-design.md 3.2 and 7): the
// case assistant as a full page, without the case card. The panel is the one the drawer shows;
// the side column lists the refs it asked the case card to focus (onRefSelect) and the raw
// events of the latest stream. "Open the drawer" mounts the exported CaseAssistant itself.
import { PanelRight, Search } from 'lucide-react';
import { useCallback, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { EntityRef } from '@/api/models';
import { usePersona } from '@/app/persona';
import { IdText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { AssistantPanel } from '../AssistantPanel';
import { CaseAssistant } from '../CaseAssistant';
import { citationHref, citationLabel } from '../citations';
import { AGENT } from '../copy';
import { useAgentStream } from '../useAgentStream';

const DEFAULT_RETURN = 'RET-000102';
const STORIES = ['US-701', 'US-702'];
/** The focus log keeps the latest few requests. */
const FOCUS_LOG_SIZE = 6;

export default function AgentHarness() {
  const [params] = useSearchParams();
  const returnId = params.get('return') || DEFAULT_RETURN;
  const persona = usePersona();
  const conversation = useAgentStream(returnId, persona.id);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [focusLog, setFocusLog] = useState<EntityRef[]>([]);
  const onRefSelect = useCallback((entity: EntityRef) => {
    setFocusLog((log) => [entity, ...log.filter((e) => e.kind !== entity.kind || e.id !== entity.id)].slice(0, FOCUS_LOG_SIZE));
  }, []);
  const latest = conversation.turns[conversation.turns.length - 1];

  return (
    <div className="mx-auto flex max-w-content flex-col gap-6" data-agent-harness>
      <header className="flex flex-col gap-2">
        <h1 className="text-page">{AGENT.harnessTitle}</h1>
        <p className="text-body text-ink-2">{AGENT.harnessIntro}</p>
        <p className="flex flex-wrap items-center gap-2 text-caption text-ink-3">
          <span>{AGENT.harness.returnLabel}</span>
          <IdText id={returnId} />
          <span aria-hidden="true">·</span>
          <Link to={`/returns/${encodeURIComponent(returnId)}`} className="text-accent underline underline-offset-2">
            {AGENT.harness.openCase}
          </Link>
          {STORIES.map((story) => (
            <span key={story} className="rounded-chip border border-border px-1 font-mono" data-id>
              {story}
            </span>
          ))}
        </p>
      </header>

      <div className="grid gap-6 xl:grid-cols-12">
        <section
          aria-labelledby="agent-harness-panel-title"
          className="flex h-[44rem] max-h-[calc(100vh-8rem)] min-h-[32rem] flex-col overflow-hidden rounded-card border border-border bg-surface xl:col-span-7"
        >
          <div className="flex flex-col gap-0.5 border-b border-border px-4 py-3">
            <h2 id="agent-harness-panel-title" className="text-title text-ink-1">
              <Search aria-hidden="true" size={16} className="mr-2 inline-block align-[-2px] text-ink-2" />
              {AGENT.title(returnId)}{' '}
              <span className="font-normal text-ink-3" data-constant>
                {AGENT.modelName}
              </span>
            </h2>
            <p className="text-body text-ink-2">{AGENT.readOnly}</p>
          </div>
          <AssistantPanel conversation={conversation} returnId={returnId} onRefSelect={onRefSelect} variant="page" />
        </section>

        <aside className="flex min-w-0 flex-col gap-4 xl:col-span-5">
          <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4" aria-labelledby="agent-harness-drawer">
            <h2 id="agent-harness-drawer" className="text-title">
              {AGENT.harness.drawerTitle}
            </h2>
            <p className="text-body text-ink-2">{AGENT.harness.drawerBody}</p>
            <div>
              <Button onClick={() => setDrawerOpen(true)}>
                <PanelRight aria-hidden="true" size={16} />
                {AGENT.harness.openDrawer}
              </Button>
            </div>
          </section>

          <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4" aria-labelledby="agent-harness-focus">
            <h2 id="agent-harness-focus" className="text-title">
              {AGENT.harness.focusTitle}
            </h2>
            {focusLog.length === 0 ? (
              <p className="text-body text-ink-3">{AGENT.harness.focusEmpty}</p>
            ) : (
              <ul className="flex flex-col gap-1" data-focus-log>
                {focusLog.map((entity) => (
                  <li key={`${entity.kind}:${entity.id}`} className="flex flex-wrap items-baseline gap-x-2 text-body">
                    <span className="text-micro uppercase text-ink-3">{citationLabel(entity.kind)}</span>
                    <span className="font-mono text-dense" data-id>
                      {entity.id}
                    </span>
                    <Link to={citationHref(entity, returnId)} className="text-caption text-accent underline underline-offset-2">
                      {AGENT.harness.focusOpen}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4" aria-labelledby="agent-harness-events">
            <h2 id="agent-harness-events" className="text-title">
              {AGENT.harness.eventsTitle}
            </h2>
            {latest && latest.events.length > 0 ? (
              <ol className="flex flex-wrap gap-1 text-caption" data-agent-events>
                {latest.events.map((event) => (
                  <li key={event.seq} className="rounded-chip border border-border px-1 font-mono text-ink-2" data-id>
                    {event.seq}·{event.type}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-body text-ink-3">{AGENT.harness.eventsEmpty}</p>
            )}
          </section>
        </aside>
      </div>

      <CaseAssistant returnId={returnId} open={drawerOpen} onOpenChange={setDrawerOpen} onRefSelect={onRefSelect} />
    </div>
  );
}
