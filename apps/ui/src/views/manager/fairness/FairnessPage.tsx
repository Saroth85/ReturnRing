// Fairness /fairness (US-605, docs/ui-design.md 8.4): false positives of the risk model by segment
// for each dimension, the gap against the maximum gap threshold (NFR-04), and the warning when the
// gap is above it. The warning flags the model's errors, never a group.
import clsx from 'clsx';
import { TriangleAlert } from 'lucide-react';
import type { FairnessResponse } from '@/api/models';
import { useFairness } from '@/api/queries';
import { DotPlot } from '@/components/charts';
import { Figure, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { MANAGER } from '../copy';
import type { FairnessDimension } from '../types';
import { Loadable, ManagerPage } from '../ui';

const F = MANAGER.fairness;

function DimensionCard({
  dimension,
  threshold,
  domainMax,
}: {
  dimension: FairnessDimension;
  threshold: FairnessResponse['max_gap_threshold'];
  /** One axis for every dimension, so dots compare across cards. */
  domainMax: number;
}) {
  const title = F.dimensions[dimension.dimension] ?? dimension.dimension;
  return (
    <ProvenanceScope
      provenance={dimension.max_gap.provenance}
      covers="the false-positive rates of the segments"
      className={clsx(
        'flex min-w-0 flex-col gap-3 rounded-card border bg-surface p-4',
        dimension.warning ? 'border-2 border-attn-border' : 'border-border',
      )}
    >
      <section className="flex flex-col gap-3" data-fairness-dimension={dimension.dimension} data-warning={dimension.warning || undefined}>
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-h3">{title}</h2>
          <p className="flex items-baseline gap-1 text-dense">
            <span className="text-ink-3">{F.gap}</span>
            <Figure metric={dimension.max_gap} label={`${title} ${F.gap}`} compareTo={threshold} />
          </p>
        </header>
        {dimension.warning ? (
          <div role="status" className="flex gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-attn-fg" data-fairness-warning>
            <TriangleAlert aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
            <div className="text-body">
              <p className="font-medium">
                {F.warningLead} <Figure metric={threshold} label={F.threshold} /> {F.warningTail}
              </p>
              <p>{F.warningNote}</p>
            </div>
          </div>
        ) : (
          <p className="text-caption text-ink-3">{F.withinThreshold}</p>
        )}
        <DotPlot
          rows={dimension.segments.map((s) => ({
            key: s.segment,
            label: <span data-verbatim>{s.segment}</span>,
            value: s.false_positive_rate,
            count: s.customers,
          }))}
          valueLabel={F.fpRate}
          countLabel={F.customers}
          domainMax={domainMax}
          bracket={{ text: F.gap, label: <Figure metric={dimension.max_gap} label={`${title} ${F.gap}`} />, attention: dimension.warning }}
        />
      </section>
      <ProvenanceFooter />
    </ProvenanceScope>
  );
}

function Fairness({ data }: { data: FairnessResponse }) {
  const warnings = data.dimensions.filter((d) => d.warning);
  const top = Math.max(...data.dimensions.flatMap((d) => d.segments.map((s) => s.false_positive_rate.value)));
  const domainMax = Math.max(top * 1.15, 0.01);
  return (
    <ManagerPage
      screen="fairness"
      title={MANAGER.titles.fairness}
      intro={
        <div className="flex flex-col gap-1">
          <p>{F.intro}</p>
          <dl className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-caption">
            <div className="flex gap-1">
              <dt className="text-ink-3">{F.report}</dt>
              <dd className="font-mono" data-id>
                {data.report_id}
              </dd>
            </div>
            <div className="flex gap-1">
              <dt className="text-ink-3">{F.scenario}</dt>
              <dd data-verbatim>{data.scenario}</dd>
            </div>
            <div className="flex gap-1">
              <dt className="text-ink-3">{F.model}</dt>
              <dd className="font-mono" data-version>
                {data.model_version}
              </dd>
            </div>
            <div className="flex items-baseline gap-1">
              <dt className="text-ink-3">{F.threshold}</dt>
              <dd>
                <Figure metric={data.max_gap_threshold} label={F.threshold} />
              </dd>
            </div>
          </dl>
        </div>
      }
    >
      {warnings.length > 0 && (
        <div role="status" className="hatch-attn flex items-start gap-2 rounded-control border border-attn-border px-3 py-2" data-fairness-banner>
          <TriangleAlert aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
          <p className="text-body font-medium">
            {warnings.map((d) => F.dimensions[d.dimension] ?? d.dimension).join(', ')}:{' '}
            {F.warningLead} <Figure metric={data.max_gap_threshold} label={F.threshold} /> {F.warningTail} {F.warningNote}
          </p>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {[...data.dimensions]
          .sort((a, b) => Number(b.warning) - Number(a.warning))
          .map((d) => (
            <DimensionCard key={d.dimension} dimension={d} threshold={data.max_gap_threshold} domainMax={domainMax} />
          ))}
      </div>
      <p className="max-w-prose text-caption text-ink-2" data-fairness-footer>
        {F.footer}
      </p>
    </ManagerPage>
  );
}

export default function FairnessPage() {
  const fairness = useFairness();
  if (!fairness.data) {
    return (
      <ManagerPage screen="fairness" title={MANAGER.titles.fairness}>
        <Loadable query={fairness} label="GET /api/fairness" rows={6}>
          {() => null}
        </Loadable>
      </ManagerPage>
    );
  }
  return <Fairness data={fairness.data} />;
}
