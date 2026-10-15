// Weekly KPIs /kpi?week= (US-603, docs/ui-design.md 8.3): simulated net benefit of the week (the
// realised one stays null until outcomes are observed, never 0), decisions, decision time,
// abstentions for not enough evidence, false positives by cohort from the offline evaluation, and
// override reasons. Every tile carries its provenance footer.
import { ChevronLeft, ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { KpiResponse, Metric, Provenance } from '@/api/models';
import { useKpi } from '@/api/queries';
import { DotPlot } from '@/components/charts';
import { AssumptionLabel, NetBenefitBreakdown } from '@/components/economics';
import { DateRange, Figure, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { RECORDS } from '@/components/records';
import { Button } from '@/components/ui';
import { P50_LABEL, P95_LABEL } from '../constants';
import { MANAGER } from '../copy';
import { Loadable, ManagerPage, ReportUnavailable } from '../ui';
import { shiftWeek } from './week';

const K = MANAGER.kpi;
function Tile({ title, provenance, covers, children, className }: { title: string; provenance: Provenance; covers: string; children: ReactNode; className?: string }) {
  return (
    <ProvenanceScope provenance={provenance} covers={covers} className={`flex min-w-0 flex-col gap-3 rounded-card border border-border bg-surface p-4 ${className ?? ''}`}>
      <h2 className="text-h3">{title}</h2>
      <div className="flex flex-1 flex-col gap-3">{children}</div>
      <ProvenanceFooter />
    </ProvenanceScope>
  );
}

const SEGMENTS: { key: keyof Omit<KpiResponse['decisions'], 'total'>; className: string }[] = [
  { key: 'approved', className: 'bg-ink-2' },
  { key: 'overridden', className: 'bg-ink-3' },
  { key: 'rejected', className: 'hatch-sim border border-ink-2' },
  { key: 'pending', className: 'bg-surface-2 border border-border-strong' },
];

function DecisionsBar({ decisions }: { decisions: KpiResponse['decisions'] }) {
  const total = decisions.total.value || 1;
  return (
    <div className="flex flex-col gap-2">
      <p className="flex items-baseline gap-2">
        <Figure metric={decisions.total} label={`${K.decisions} ${K.total}`} size="hero" />
        <span className="text-ink-3">{K.total}</span>
      </p>
      <div className="flex h-4 w-full gap-0.5 overflow-hidden rounded-chip" aria-hidden="true" data-decision-bar>
        {SEGMENTS.map((s) => {
          const value = (decisions[s.key] as Metric).value;
          return value > 0 ? <span key={s.key} className={s.className} style={{ width: `${(value / total) * 100}%` }} data-segment={s.key} /> : null;
        })}
      </div>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-dense">
        {SEGMENTS.map((s) => (
          <li key={s.key} className="flex items-center gap-2">
            <span aria-hidden="true" className={`inline-block h-3 w-3 rounded-[2px] ${s.className}`} />
            <span className="text-ink-2">{K.states[s.key]}</span>
            <Figure metric={decisions[s.key] as Metric} label={K.states[s.key]} className="ml-auto" />
          </li>
        ))}
      </ul>
    </div>
  );
}

function Kpi({ data, week }: { data: KpiResponse; week: string }) {
  const [, setParams] = useSearchParams();
  const go = (weeks: number) => setParams({ week: shiftWeek(week, weeks) });
  const nb = data.net_benefit_simulated;
  const fpProvenance = data.false_positives[0]?.false_positive_rate.provenance ?? data.decisions.total.provenance;
  return (
    <ManagerPage
      screen="kpi"
      title={MANAGER.titles.kpi}
      intro={
        <p>
          {K.week} <DateRange start={data.period_start} end={data.period_end} className="font-medium text-ink-1" /> · {K.intro}
        </p>
      }
      actions={
        <>
          <Button onClick={() => go(-1)}>
            <ChevronLeft aria-hidden="true" size={14} />
            {K.previous}
          </Button>
          <Button onClick={() => go(1)}>
            {K.next}
            <ChevronRight aria-hidden="true" size={14} />
          </Button>
        </>
      }
    >
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Tile title={K.netBenefit} provenance={nb.total.provenance} covers="the simulated net benefit of the week" className="xl:row-span-2">
          <p data-kpi-net-benefit>
            <Figure metric={nb.total} label={K.netBenefit} signed size="hero" />
          </p>
          <AssumptionLabel label={nb.assumptions.version} basedOn={nb.assumptions.based_on} />
          <p className="text-caption text-ink-2" data-verbatim>
            {nb.assumptions.period}
          </p>
          <details className="rounded-control border border-border px-2 py-1">
            <summary className="cursor-pointer text-dense text-accent">{K.components}</summary>
            <NetBenefitBreakdown view={nb} className="mt-2" />
          </details>
          <div className="rounded-control border border-dashed border-border-strong px-2 py-1.5" data-realised>
            <p className="text-micro uppercase text-ink-3">{K.realisedTitle}</p>
            {data.net_benefit_realized === null ? (
              <p className="text-dense text-ink-2">{K.realised}</p>
            ) : (
              <Figure metric={data.net_benefit_realized.total} label={K.realisedTitle} signed />
            )}
          </div>
        </Tile>
        <Tile title={K.decisions} provenance={data.decisions.total.provenance} covers="the decisions of the week">
          <DecisionsBar decisions={data.decisions} />
        </Tile>
        <Tile title={K.time} provenance={data.decision_time_p50.provenance} covers="the decision times of the week">
          <dl className="grid grid-cols-2 gap-3">
            <div>
              <dt className="text-caption text-ink-3">
                <span data-constant>{P50_LABEL}</span> · {K.p50}
              </dt>
              <dd className="text-h2">
                <Figure metric={data.decision_time_p50} label={`${K.time} ${K.p50}`} />
              </dd>
            </div>
            <div>
              <dt className="text-caption text-ink-3">
                <span data-constant>{P95_LABEL}</span> · {K.p95}
              </dt>
              <dd className="text-h2">
                <Figure metric={data.decision_time_p95} label={`${K.time} ${K.p95}`} />
              </dd>
            </div>
          </dl>
        </Tile>
        <Tile title={K.abstentions} provenance={data.abstentions_insufficient_evidence.provenance} covers="the abstentions of the week">
          <dl className="grid grid-cols-2 gap-3">
            <div>
              <dt className="text-caption text-ink-3">{K.abstentionCount}</dt>
              <dd className="text-h2">
                <Figure metric={data.abstentions_insufficient_evidence} label={K.abstentionCount} />
              </dd>
            </div>
            <div>
              <dt className="text-caption text-ink-3">{K.abstentionRate}</dt>
              <dd className="text-h2">
                <Figure metric={data.abstention_rate} label={K.abstentionRate} />
              </dd>
            </div>
          </dl>
          <Link to="/queue?evidence_quality=insufficient" className="text-dense text-accent underline underline-offset-2">
            {K.abstentionLink}
          </Link>
        </Tile>
        <Tile title={K.tasks} provenance={data.open_catalog_tasks.provenance} covers="the catalogue tasks open at the end of the week">
          <p className="text-h2">
            <Figure metric={data.open_catalog_tasks} label={K.tasks} />
          </p>
          <Link to="/products/tasks" className="text-dense text-accent underline underline-offset-2">
            {K.tasksLink}
          </Link>
        </Tile>
        <Tile title={K.falsePositives} provenance={fpProvenance} covers="the false positives by cohort of the offline evaluation" className="md:col-span-2">
          <p className="text-caption text-ink-2">
            {K.fromReport}{' '}
            <span className="font-mono" data-id>
              {data.evaluation_report_id}
            </span>{' '}
            · {K.syntheticLabels} · {MANAGER.period} <DateRange start={fpProvenance.period_start} end={fpProvenance.period_end} />
          </p>
          <DotPlot
            rows={data.false_positives.map((c) => ({
              key: c.cohort,
              label: K.cohorts[c.cohort] ?? c.cohort,
              value: c.false_positive_rate,
              count: c.customers,
            }))}
            valueLabel={K.fpRate}
            countLabel={K.customers}
          />
          <p className="text-caption text-ink-2">{K.labelsNote}</p>
        </Tile>
        <Tile title={K.overrides} provenance={data.decisions.overridden.provenance} covers="the override reasons of the week">
          {data.override_reasons.length === 0 ? (
            <p className="text-ink-2">{K.overridesEmpty}</p>
          ) : (
            <ul className="flex flex-col gap-1 text-dense" data-override-reasons>
              {data.override_reasons.map((r) => (
                <li key={r.category} className="flex items-baseline justify-between gap-2">
                  <span className="text-ink-2">{RECORDS.category[r.category] ?? r.category}</span>
                  <Figure metric={r.count} label={RECORDS.category[r.category]} />
                </li>
              ))}
            </ul>
          )}
        </Tile>
      </div>
    </ManagerPage>
  );
}

export default function KpiPage() {
  const [params] = useSearchParams();
  const requested = params.get('week');
  const kpi = useKpi(requested);
  if (!kpi.data) {
    return (
      <ManagerPage screen="kpi" title={MANAGER.titles.kpi}>
        <Loadable query={kpi} label="GET /api/kpi" rows={8} notFound={(message) => <ReportUnavailable message={message} />}>
          {() => null}
        </Loadable>
      </ManagerPage>
    );
  }
  return <Kpi data={kpi.data} week={kpi.data.period_start} />;
}
