// Economic simulator /simulator (US-602, ADR-04; docs/ui-design.md 8.2). Stub: the server's net
// benefit of the current assumptions; the browser recompute (src/sim) comes next.
import { useAssumptions } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { Figure } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function SimulatorPage() {
  const assumptions = useAssumptions();
  const current = assumptions.data?.current;
  return (
    <ScreenPlaceholder title={MANAGER.titles.simulator} area="manager" stories={['US-602']} spec="docs/ui-design.md 8.2">
      <DataCheck label="GET /api/simulator/assumptions" result={assumptions} />
      {current && (
        <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4">
          <p className="text-caption text-ink-3">
            <span data-id>{current.version}</span> · {current.created_by}
          </p>
          <p>
            <span className="text-ink-3">{MANAGER.simulator.total}: </span>
            <Figure metric={current.net_benefit.total} label={MANAGER.simulator.total} signed size="hero" />
          </p>
          <p className="text-caption text-ink-2">{MANAGER.simulator.refundNote}</p>
        </section>
      )}
    </ScreenPlaceholder>
  );
}
