// Weekly KPIs /kpi?week= (US-603, docs/ui-design.md 8.3). Stub: the simulated net benefit of the
// week and the realised one (null until outcomes are observed, never 0).
import { useSearchParams } from 'react-router';
import { useKpi } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { DateRange, Figure } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function KpiPage() {
  const [params] = useSearchParams();
  const kpi = useKpi(params.get('week'));
  const data = kpi.data;
  return (
    <ScreenPlaceholder title={MANAGER.titles.kpi} area="manager" stories={['US-603']} spec="docs/ui-design.md 8.3">
      <DataCheck label="GET /api/kpi" result={kpi} />
      {data && (
        <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4">
          <p className="text-caption text-ink-3">
            <DateRange start={data.period_start} end={data.period_end} />
          </p>
          <p>
            <span className="text-ink-3">{MANAGER.kpi.netBenefit}: </span>
            <Figure metric={data.net_benefit_simulated.total} label={MANAGER.kpi.netBenefit} signed size="hero" />
          </p>
          {data.net_benefit_realized === null && <p className="text-ink-3">{MANAGER.kpi.realised}</p>}
        </section>
      )}
    </ScreenPlaceholder>
  );
}
