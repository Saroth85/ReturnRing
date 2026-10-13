// A benchmark B1..B4 (US-902, docs/ui-design.md 9.4): its status (Measured with the time,
// Pending, or Illustrative, not measured with a hatch and a watermark), CPU and MI300X paired per
// metric in one small chart per phase and unit, the qualifiers that make a number comparable
// (concurrency, batch, protocol), a speedup only between measured results, and the dataset,
// versions and configuration in a disclosure.
import clsx from 'clsx';
import { CircleDashed, Gauge } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Benchmark } from '@/api/models';
import { DateText, Figure, NatureMark, PlainNumber, ProvenanceScope } from '@/components/provenance';
import { UNIT_WORDS } from '@/format';
import { barFraction, resultCharts, speedups, type BenchmarkResult, type MetricBars } from './benchmarks';
import { SYSTEM } from './copy';

const s = SYSTEM;

export function BenchmarkStatus({ benchmark }: { benchmark: Benchmark }) {
  const word = s.benchmarkStatus[benchmark.status] ?? benchmark.status;
  if (benchmark.status === 'illustrative') {
    return (
      <span className="hatch-cached inline-flex items-center gap-1 rounded-chip px-2 py-0.5 text-micro uppercase" data-benchmark-status="illustrative">
        <NatureMark nature="illustrative" decorative />
        {word}
      </span>
    );
  }
  if (benchmark.status === 'pending') {
    return (
      <span className="inline-flex items-center gap-1 rounded-chip border border-dashed border-ink-3 px-2 py-0.5 text-micro uppercase text-ink-2" data-benchmark-status="pending">
        <CircleDashed aria-hidden="true" size={12} />
        {word}
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1 rounded-chip border border-border-strong px-2 py-0.5 text-micro uppercase text-ink-1" data-benchmark-status="measured">
      <Gauge aria-hidden="true" size={12} />
      {word}
      {benchmark.measured_at && (
        <span className="normal-case tracking-normal text-ink-3">
          · {s.measuredOn} <DateText value={benchmark.measured_at} />
        </span>
      )}
    </span>
  );
}

function sameQualifiers(rows: readonly BenchmarkResult[]): boolean {
  const [first, ...rest] = rows;
  return !first || rest.every((r) => r.concurrency === first.concurrency && r.batch_size === first.batch_size && r.protocol === first.protocol);
}

/** "concurrency 1 · batch 32 · decision-time protocol", the numbers under the result's provenance. */
function Qualifiers({ result }: { result: BenchmarkResult }) {
  const parts: ReactNode[] = [];
  if (result.concurrency !== null) {
    parts.push(
      <span key="c">
        {s.concurrency} <PlainNumber value={result.concurrency} unit="count" />
      </span>,
    );
  }
  if (result.batch_size !== null) {
    parts.push(
      <span key="b">
        {s.batch} <PlainNumber value={result.batch_size} unit="count" />
      </span>,
    );
  }
  if (result.protocol) parts.push(<span key="p">{s.protocol[result.protocol] ?? result.protocol}</span>);
  if (parts.length === 0) return null;
  return (
    <ProvenanceScope provenance={result.value.provenance} covers="the load of this measurement" className="inline">
      <span className="inline-flex flex-wrap gap-x-1 text-caption text-ink-3" data-qualifiers>
        {parts.map((part, i) => (
          <span key={i}>
            {i > 0 && '· '}
            {part}
          </span>
        ))}
      </span>
    </ProvenanceScope>
  );
}

function MetricRows({ bars, max, illustrative, status }: { bars: MetricBars; max: number; illustrative: boolean; status: string }) {
  const shared = sameQualifiers(bars.rows);
  return (
    <div className="flex flex-col gap-1.5" data-metric={bars.metric}>
      <div className="flex flex-wrap items-baseline gap-x-2 text-body">
        <span className="font-medium" data-verbatim>
          {bars.label}
        </span>
        {shared && bars.rows[0] && <Qualifiers result={bars.rows[0]} />}
      </div>
      <ul className="flex flex-col gap-1.5">
        {bars.rows.map((row) => (
          <li key={`${row.device}-${row.phase}`} className="grid grid-cols-[4.5rem_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5" data-device={row.device}>
            <span className="text-dense text-ink-2" data-constant>
              {s.deviceName[row.device] ?? row.device}
            </span>
            <span className="flex min-w-0 items-center gap-2">
              <span className="relative h-3 min-w-0 flex-1" aria-hidden="true">
                <span
                  className={clsx(
                    'absolute inset-y-0 left-0 rounded-r-[4px]',
                    illustrative ? 'bg-ink-3' : row.device === 'mi300x' ? 'bg-chart-pos' : 'bg-mark',
                  )}
                  style={{ width: `${Math.max(1, barFraction(row.value.value, max) * 100)}%` }}
                />
              </span>
              <span className="shrink-0 text-dense">
                <Figure metric={row.value} label={`${bars.label} · ${s.deviceName[row.device] ?? row.device}`} status={status} />
              </span>
            </span>
            {!shared && (
              <div className="col-start-2">
                <Qualifiers result={row} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Details({ benchmark }: { benchmark: Benchmark }) {
  const versions = Object.entries(benchmark.versions);
  const config = Object.entries(benchmark.config);
  return (
    <details className="text-body" data-benchmark-details>
      <summary className="cursor-pointer text-accent">{s.details}</summary>
      <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
        <dt className="text-caption text-ink-3">{s.dataset}</dt>
        <dd data-verbatim>{benchmark.dataset}</dd>
        <dt className="text-caption text-ink-3">{s.versions}</dt>
        <dd>
          {versions.length === 0 ? (
            <span className="text-ink-3">{s.none}</span>
          ) : (
            <ul className="flex flex-wrap gap-1">
              {versions.map(([name, version]) => (
                <li key={name} className="rounded-chip border border-border px-1.5 font-mono text-caption" data-version>
                  {name} {version}
                </li>
              ))}
            </ul>
          )}
        </dd>
        <dt className="text-caption text-ink-3">{s.config}</dt>
        <dd>
          {config.length === 0 ? (
            <span className="text-ink-3">{s.none}</span>
          ) : (
            <ul className="flex flex-wrap gap-1" data-verbatim>
              {config.map(([key, value]) => (
                <li key={key} className="rounded-chip border border-border px-1.5 font-mono text-caption">
                  {key}={String(value)}
                </li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
    </details>
  );
}

export function BenchmarkCard({ benchmark }: { benchmark: Benchmark }) {
  const charts = resultCharts(benchmark.results);
  const illustrative = benchmark.status === 'illustrative';
  const gains = speedups(benchmark);
  const caveat = s.caveat[benchmark.id];
  return (
    <article className="flex min-w-0 flex-col gap-3 rounded-card border border-border bg-surface p-4" data-benchmark={benchmark.id} data-benchmark-state={benchmark.status}>
      <header className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-baseline gap-2 text-title">
            <span className="font-mono" data-id>
              {benchmark.id}
            </span>
          </h3>
          <BenchmarkStatus benchmark={benchmark} />
        </div>
        <p className="text-body text-ink-1" data-verbatim>
          {benchmark.title}
        </p>
      </header>
      {charts.length === 0 ? (
        <div className="flex items-start gap-2 text-body text-ink-2" data-not-run>
          <CircleDashed aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
          {benchmark.notes.length > 0 ? (
            <ul className="flex flex-col gap-0.5" data-verbatim>
              {benchmark.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : (
            <span>{s.notRun}</span>
          )}
        </div>
      ) : (
        <div className={clsx('relative flex flex-col gap-4 overflow-hidden rounded-control p-3', illustrative ? 'border border-dashed border-cached-stripe' : 'bg-surface-2')}>
          {illustrative && (
            <>
              <span aria-hidden="true" className="hatch-cached pointer-events-none absolute inset-0 opacity-40" />
              <span
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 flex items-center justify-center text-[2.5rem] font-semibold tracking-[0.3em] text-cached-fg uppercase opacity-15 select-none [transform:rotate(-14deg)]"
                data-watermark
              >
                {s.watermark}
              </span>
            </>
          )}
          {charts.map((chart) => (
            <figure key={chart.key} className="relative flex flex-col gap-2" data-chart={chart.key}>
              <figcaption className="text-micro uppercase text-ink-3">
                {s.phase[chart.phase]} · {UNIT_WORDS[chart.unit]}
              </figcaption>
              {chart.metrics.map((bars) => (
                <MetricRows key={bars.metric} bars={bars} max={chart.max} illustrative={illustrative} status={benchmark.status} />
              ))}
            </figure>
          ))}
        </div>
      )}
      {gains.length > 0 && (
        <ul className="flex flex-wrap gap-2" data-speedups>
          {gains.map((g) => (
            <li key={g.metric} className="inline-flex items-baseline gap-1 rounded-chip border border-border-strong px-2 py-1 text-body">
              ×<Figure metric={g.factor} label={`${g.label}: ${s.speedup}`} /> {s.speedup}
            </li>
          ))}
        </ul>
      )}
      {caveat && <p className="text-body text-ink-2">{caveat}</p>}
      {charts.length > 0 && benchmark.notes.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-caption text-ink-3" data-verbatim>
          {benchmark.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      <Details benchmark={benchmark} />
    </article>
  );
}
