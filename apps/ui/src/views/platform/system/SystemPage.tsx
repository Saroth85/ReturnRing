// System and benchmarks /system (US-901, US-902, US-905; docs/ui-design.md 9.4; pitch beat 6):
// what runs where on the MI300X, the service mode and readiness (GET /healthz, GET /readyz, the
// mode header), the AMD benchmarks B1..B4 with the cost per decision, and live telemetry.
import { Cpu, Server } from 'lucide-react';
import type { BenchmarksResponse } from '@/api/models';
import { useBenchmarks, useReadyz } from '@/api/queries';
import { ErrorState, LoadingBlock } from '@/components/feedback';
import { Figure, IdText } from '@/components/provenance';
import { ARCHITECTURE } from '../constants';
import { PLATFORM } from '../copy';
import { PageHeader, Panel } from '../shared/layout';
import { BenchmarkCard, BenchmarkStatus } from './BenchmarkCard';
import { costPerDecision } from './benchmarks';
import { SYSTEM } from './copy';
import { ModeCard, ReadinessPill } from './ModeCard';
import { TelemetryPanel } from './TelemetryPanel';

const s = SYSTEM;

function ArchitectureStrip() {
  const chip = 'rounded-chip border border-border bg-surface px-2 py-1 text-dense';
  return (
    <div className="flex flex-col gap-3 rounded-card border border-border bg-surface-2 p-4 md:flex-row md:items-center md:gap-6" data-architecture>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 text-caption font-medium text-ink-2">
          <Cpu aria-hidden="true" size={14} />
          {s.arch.gpu}
        </span>
        {ARCHITECTURE.gpu.map((name) => (
          <span key={name} className={chip} data-constant>
            {name}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1 text-caption font-medium text-ink-2">
          <Server aria-hidden="true" size={14} />
          {s.arch.cpu}
        </span>
        {ARCHITECTURE.cpu.map((name) => (
          <span key={name} className={chip}>
            {name}
          </span>
        ))}
      </div>
    </div>
  );
}

function CostTile({ data }: { data: BenchmarksResponse }) {
  const cost = costPerDecision(data.items);
  const illustrative = cost?.benchmark.status === 'illustrative';
  return (
    <div
      className={
        illustrative
          ? 'hatch-cached flex flex-col gap-1 rounded-card p-4'
          : 'flex flex-col gap-1 rounded-card border border-border-strong bg-surface p-4'
      }
      data-cost-per-decision={cost?.benchmark.status ?? 'none'}
    >
      <p className="text-caption font-medium uppercase tracking-wide">{s.costTitle}</p>
      {cost ? (
        <>
          <p className="flex flex-wrap items-baseline gap-x-3">
            <Figure metric={cost.result.value} label={cost.result.label} size="hero" status={cost.benchmark.status} />
            <BenchmarkStatus benchmark={cost.benchmark} />
          </p>
          <p className="text-body">
            <span data-verbatim>{cost.result.label}</span> · {s.costIdle} · {s.costFrom} <IdText id={cost.benchmark.id} />
          </p>
        </>
      ) : (
        <p className="text-body">{s.costNone}</p>
      )}
    </div>
  );
}

function Benchmarks() {
  const benchmarks = useBenchmarks();
  return (
    <section id="benchmarks" aria-labelledby="benchmarks-title" className="flex flex-col gap-3">
      <h2 id="benchmarks-title" className="text-h2">
        {s.sections.benchmarks}
      </h2>
      <p className="max-w-3xl text-body text-ink-2">{s.benchmarksIntro}</p>
      {benchmarks.isPending && <LoadingBlock rows={6} />}
      {benchmarks.isError && <ErrorState error={benchmarks.error} onRetry={() => void benchmarks.refetch()} />}
      {benchmarks.data && (
        <>
          <CostTile data={benchmarks.data} />
          <div className="grid items-start gap-4 xl:grid-cols-2" data-benchmarks>
            {benchmarks.data.items.map((b) => (
              <BenchmarkCard key={b.id} benchmark={b} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

export default function SystemPage() {
  const readyz = useReadyz();
  return (
    <div className="mx-auto flex max-w-content flex-col gap-6" data-page="system">
      <PageHeader title={PLATFORM.titles.system} stories={s.stories} aside={<ReadinessPill readyz={readyz.data} />} />
      <nav aria-label={s.onPage} className="flex flex-wrap gap-x-4 gap-y-1 text-body lg:hidden">
        <span className="text-caption text-ink-3">{s.onPage}</span>
        <a href="#readiness" className="text-accent underline underline-offset-2">
          {s.sections.readiness}
        </a>
        <a href="#benchmarks" className="text-accent underline underline-offset-2">
          {s.sections.benchmarks}
        </a>
        <a href="#telemetry" className="text-accent underline underline-offset-2">
          {s.sections.telemetry}
        </a>
      </nav>
      <Panel>
        <p className="text-h3" data-constant>
          {s.hero}
        </p>
        <ArchitectureStrip />
      </Panel>
      <ModeCard />
      <Benchmarks />
      <TelemetryPanel />
    </div>
  );
}
