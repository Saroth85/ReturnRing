// System and benchmarks /system (US-901, US-902, US-905; docs/ui-design.md 9.4). Stub: readiness
// checks and versions, benchmark statuses, telemetry.
import { useBenchmarks, useReadyz, useTelemetry } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { PLATFORM } from '../copy';

export default function SystemPage() {
  const readyz = useReadyz();
  const benchmarks = useBenchmarks();
  const telemetry = useTelemetry();
  const t = PLATFORM.system;
  return (
    <ScreenPlaceholder title={PLATFORM.titles.system} area="platform" stories={['US-901', 'US-902', 'US-905']} spec="docs/ui-design.md 9.4">
      <section className="flex flex-col gap-2">
        <h2 className="text-h3">{t.readiness}</h2>
        <DataCheck label="GET /readyz" result={readyz} />
        {readyz.data && (
          <>
            <ul className="flex flex-wrap gap-2" data-readiness>
              {readyz.data.checks.map((check) => (
                <li key={check.component} className="rounded-chip border border-border px-2 py-0.5 text-caption" data-check={check.status}>
                  {check.component} · {t.checkStatus[check.status] ?? check.status}
                </li>
              ))}
            </ul>
            <p className="text-caption text-ink-3">
              {t.contract} <span data-id>{readyz.data.contract_version}</span> · {t.rules}{' '}
              <span data-id>{readyz.data.rules_version}</span> · {t.assumptions} <span data-id>{readyz.data.assumptions_version}</span>
            </p>
          </>
        )}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="text-h3">{t.benchmarks}</h2>
        <DataCheck label="GET /api/benchmarks" result={benchmarks} />
        {benchmarks.data && (
          <ul className="grid gap-2 sm:grid-cols-2">
            {benchmarks.data.items.map((b) => (
              <li
                key={b.id}
                className={b.status === 'illustrative' ? 'hatch-cached rounded-card p-3' : 'rounded-card border border-border bg-surface p-3'}
                data-benchmark={b.status}
              >
                <span className="font-mono" data-id>
                  {b.id}
                </span>{' '}
                · {t.status[b.status] ?? b.status}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="text-h3">{t.telemetry}</h2>
        <DataCheck label="GET /api/telemetry" result={telemetry} />
        {telemetry.data?.note && (
          <p className="text-body text-ink-2" data-verbatim>
            {telemetry.data.note}
          </p>
        )}
      </section>
    </ScreenPlaceholder>
  );
}
