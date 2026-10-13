// Telemetry (US-905, docs/ui-design.md 9.4): GPU utilisation, memory and power from amd-smi, the
// language model's requests, latency and tokens per second, and the API latency. Small multiples,
// one unit each (no dual axis); polled every interval_s while the tab is visible. In cached mode
// the GPU is off: the note says so and no number is shown, never a zero.
import { Lock } from 'lucide-react';
import type { ReactNode } from 'react';
import { isApiError } from '@/api/errors';
import type { Metric, TelemetryResponse } from '@/api/models';
import { useTelemetry } from '@/api/queries';
import { ErrorState, LoadingBlock } from '@/components/feedback';
import { DateText, Figure, NatureMark, PlainNumber, ProvenanceScope } from '@/components/provenance';
import { formatValue } from '@/format';
import { Panel } from '../shared/layout';
import { SYSTEM } from './copy';
import { Sparkline } from './Sparkline';
import { telemetryNatures } from './telemetry';

const s = SYSTEM;

type Sample = NonNullable<TelemetryResponse['gpu']>;

function Tile({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2 rounded-control border border-border p-3" data-telemetry-tile={title}>
      <h3 className="text-caption font-medium text-ink-2" data-constant>
        {title}
      </h3>
      {children}
    </div>
  );
}

function MemoryMeter({ used, total }: { used: Metric; total: Metric }) {
  const share = total.value > 0 ? Math.min(1, used.value / total.value) : 0;
  return (
    <span aria-hidden="true" className="block h-2 w-full overflow-hidden rounded-full bg-surface-2 ring-1 ring-border">
      <span className="block h-full rounded-full bg-chart-pos" style={{ width: `${share * 100}%` }} />
    </span>
  );
}

function HistoryTable({ samples }: { samples: Sample[] }) {
  return (
    <details className="text-body" data-telemetry-history>
      <summary className="cursor-pointer text-accent">{s.history}</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse text-dense">
          <thead>
            <tr className="border-b border-border text-left text-caption text-ink-3">
              <th scope="col" className="px-2 py-1 font-medium">
                {s.time}
              </th>
              <th scope="col" className="px-2 py-1 font-medium">
                {s.utilisation}
              </th>
              <th scope="col" className="px-2 py-1 font-medium">
                {s.memoryUsed}
              </th>
              <th scope="col" className="px-2 py-1 font-medium">
                {s.power}
              </th>
            </tr>
          </thead>
          <tbody>
            {samples.map((sample) => (
              <tr key={sample.sampled_at} className="border-b border-border last:border-b-0">
                <td className="px-2 py-1">
                  <DateText value={sample.sampled_at} />
                </td>
                <td className="px-2 py-1">
                  <Figure metric={sample.utilization} label={s.gpuUtil} />
                </td>
                <td className="px-2 py-1">
                  <Figure metric={sample.memory_used} label={s.memoryUsed} />
                </td>
                <td className="px-2 py-1">{sample.power ? <Figure metric={sample.power} label={s.power} /> : s.noPower}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function LiveTelemetry({ data }: { data: TelemetryResponse }) {
  const gpu = data.gpu;
  const history = data.gpu_history.length > 0 ? data.gpu_history : gpu ? [gpu] : [];
  const powers = history.map((h) => h.power?.value).filter((v): v is number => typeof v === 'number');
  const powerMax = powers.length > 0 ? Math.max(...powers) : 0;
  const illustrative = telemetryNatures(data).includes('illustrative');
  return (
    <div className="flex flex-col gap-3">
      {illustrative && (
        <p className="hatch-cached flex items-center gap-2 rounded-control px-3 py-1.5 text-caption font-medium" data-telemetry-illustrative>
          <NatureMark nature="illustrative" decorative />
          {s.illustrative}
        </p>
      )}
      {data.device && (
        <p className="text-body text-ink-2" data-constant>
          {data.device}
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {gpu ? (
          <>
            <Tile title={s.gpuUtil}>
              <span className="text-h2">
                <Figure metric={gpu.utilization} label={s.gpuUtil} />
              </span>
              <Sparkline values={history.map((h) => h.utilization.value)} min={0} max={100} />
            </Tile>
            <Tile title={s.memory}>
              <span className="flex flex-wrap items-baseline gap-x-1 text-title">
                <Figure metric={gpu.memory_used} label={s.memoryUsed} />
                <span className="text-body font-normal text-ink-3">{s.memoryOf}</span>
                <Figure metric={gpu.memory_total} label={s.memory} />
              </span>
              <MemoryMeter used={gpu.memory_used} total={gpu.memory_total} />
            </Tile>
            <Tile title={s.power}>
              {gpu.power ? (
                <>
                  <span className="text-h2">
                    <Figure metric={gpu.power} label={s.power} />
                  </span>
                  {powerMax > 0 && <Sparkline values={powers} min={0} max={powerMax} />}
                </>
              ) : (
                <span className="text-ink-3">{s.noPower}</span>
              )}
            </Tile>
          </>
        ) : (
          <p className="text-body text-ink-3">{s.noGpu}</p>
        )}
        {data.llm && (
          <Tile title={s.llm}>
            <ProvenanceScope provenance={data.llm.requests.provenance} covers="the window of the language-model statistics">
              <p className="text-caption text-ink-3">
                {s.llmWindow} <PlainNumber value={data.llm.window_s} unit="seconds" />
              </p>
            </ProvenanceScope>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-body">
              <dt className="text-ink-3">{s.requests}</dt>
              <dd>
                <Figure metric={data.llm.requests} label={s.requests} />
              </dd>
              <dt className="text-ink-3" data-constant>
                {s.p50}
              </dt>
              <dd>
                <Figure metric={data.llm.latency_p50} label={s.p50} />
              </dd>
              <dt className="text-ink-3" data-constant>
                {s.p95}
              </dt>
              <dd>
                <Figure metric={data.llm.latency_p95} label={s.p95} />
              </dd>
              <dt className="text-ink-3">{s.tokens}</dt>
              <dd>
                <Figure metric={data.llm.tokens_per_second} label={s.tokens} />
              </dd>
            </dl>
          </Tile>
        )}
        {data.api_latency_p95 && (
          <Tile title={s.api}>
            <span className="text-h2">
              <Figure metric={data.api_latency_p95} label={s.api} />
            </span>
          </Tile>
        )}
      </div>
      {history.length > 0 && <HistoryTable samples={history} />}
    </div>
  );
}

export function TelemetryPanel() {
  const telemetry = useTelemetry();
  const data = telemetry.data;
  const protectedError = isApiError(telemetry.error) && telemetry.error.code === 'protected';
  return (
    <Panel
      id="telemetry"
      title={s.sections.telemetry}
      aside={
        data && (
          <span className="flex flex-wrap items-center gap-x-1 text-caption text-ink-3">
            {s.every} <span data-constant>{formatValue(data.interval_s, 'seconds')}</span> · {s.sampledAt}{' '}
            <DateText value={data.sampled_at} />
          </span>
        )
      }
    >
      <p className="text-body text-ink-2">{s.telemetryIntro}</p>
      {telemetry.isPending && <LoadingBlock rows={4} />}
      {protectedError && (
        <p className="flex items-center gap-2 rounded-control border border-border bg-surface-2 px-3 py-2 text-body" data-telemetry-protected>
          <Lock aria-hidden="true" size={16} />
          {s.protected}
        </p>
      )}
      {telemetry.isError && !protectedError && !data && <ErrorState error={telemetry.error} onRetry={() => void telemetry.refetch()} />}
      {data && !data.gpu && !data.llm && data.note ? (
        <div className="hatch-cached flex flex-col gap-1 rounded-control px-3 py-2" data-telemetry-cached>
          <p className="font-medium">{s.cachedTitle}</p>
          <p className="text-body" data-verbatim>
            {data.note}
          </p>
        </div>
      ) : (
        data && <LiveTelemetry data={data} />
      )}
    </Panel>
  );
}
