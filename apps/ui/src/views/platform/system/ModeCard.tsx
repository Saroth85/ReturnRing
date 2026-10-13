// Service mode and readiness (US-901, docs/ui-design.md 5.2 and 9.4): liveness from GET /healthz,
// the mode, language model, device, label, component checks and versions from GET /readyz, and the
// X-ReturnRing-Mode header of the latest response, which must agree with /readyz. The ok colour
// appears only here (system health).
import clsx from 'clsx';
import { CircleCheck, CircleMinus, OctagonAlert } from 'lucide-react';
import { API_MODE } from '@/api/adapters';
import { useApiMeta } from '@/api/meta';
import type { ReadyzResponse } from '@/api/models';
import { useHealthz, useReadyz } from '@/api/queries';
import { ErrorState, LoadingBlock } from '@/components/feedback';
import { IdText } from '@/components/provenance';
import { Fact, Panel } from '../shared/layout';
import { SYSTEM } from './copy';
import { headerAgreement, readinessTone, type ReadinessTone } from './readiness';

const s = SYSTEM;

const TONE_ICON = { ok: CircleCheck, off: CircleMinus, fail: OctagonAlert } as const;
const TONE_CLASS: Record<ReadinessTone, string> = { ok: 'text-ok-fg', off: 'text-ink-3', fail: 'text-danger-fg' };

function ToneWord({ tone, children }: { tone: ReadinessTone; children: string }) {
  const Icon = TONE_ICON[tone];
  return (
    <span className={clsx('inline-flex items-center gap-1 font-medium', TONE_CLASS[tone])} data-tone={tone}>
      <Icon aria-hidden="true" size={14} />
      {children}
    </span>
  );
}

/** "● Live · ready" in the page header (ok, attention or danger with words). */
export function ReadinessPill({ readyz }: { readyz: ReadyzResponse | undefined }) {
  if (!readyz) return null;
  const tone: ReadinessTone = readyz.status === 'ready' ? 'ok' : readyz.status === 'degraded' ? 'off' : 'fail';
  const Icon = TONE_ICON[tone];
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-body font-medium',
        tone === 'ok' && 'border-ok-fg bg-ok-bg text-ok-fg',
        tone === 'off' && 'border-attn-border bg-attn-bg text-attn-fg',
        tone === 'fail' && 'border-danger-fg bg-danger-bg text-danger-fg',
      )}
      data-readiness-pill={readyz.status}
    >
      <Icon aria-hidden="true" size={16} />
      {s.modeWord[readyz.mode] ?? readyz.mode} · {s.status[readyz.status] ?? readyz.status}
    </span>
  );
}

export function ModeCard() {
  const readyz = useReadyz();
  const healthz = useHealthz();
  const meta = useApiMeta();
  const r = readyz.data;
  const agreement = r ? headerAgreement(r.mode, meta.lastMode) : 'unknown';
  return (
    <Panel id="readiness" title={s.sections.readiness}>
      {readyz.isPending && <LoadingBlock rows={4} />}
      {readyz.isError && <ErrorState error={readyz.error} onRetry={() => void readyz.refetch()} title="GET /readyz" />}
      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2" data-mode-facts>
        <Fact label={s.liveness}>
          <span className="flex flex-wrap items-center gap-x-2">
            <span className="font-mono text-dense">GET /healthz</span>
            {healthz.data ? (
              <ToneWord tone="ok">{`${healthz.data.status} · ${s.livenessOk}`}</ToneWord>
            ) : healthz.isError ? (
              <ToneWord tone="fail">{s.livenessFailed}</ToneWord>
            ) : null}
          </span>
        </Fact>
        {r && (
          <>
            <Fact label={s.readiness}>
              <ToneWord tone={r.status === 'ready' ? 'ok' : r.status === 'degraded' ? 'off' : 'fail'}>{s.status[r.status] ?? r.status}</ToneWord>
            </Fact>
            <Fact label={s.mode}>
              <span data-mode={r.mode}>{s.modeWord[r.mode] ?? r.mode}</span>
            </Fact>
            <Fact label={s.llmMode}>
              <span data-constant>{s.llmModes[r.llm_mode] ?? r.llm_mode}</span>
            </Fact>
            <Fact label={s.device}>{s.devices[r.device] ?? r.device}</Fact>
            <Fact label={s.label}>{r.label ? <span data-constant>{r.label}</span> : <span className="text-ink-3">{s.noLabel}</span>}</Fact>
            <Fact label={s.header}>
              <span className="flex flex-wrap items-center gap-x-1" data-header-agreement={agreement}>
                <span className="font-mono text-dense">X-ReturnRing-Mode: {meta.lastMode ?? '—'}</span>
                <span className={agreement === 'differs' ? 'font-medium text-attn-fg' : 'text-ink-2'}>· {s.headerAgreement[agreement]}</span>
                {meta.lastRequestId && (
                  <span className="text-caption text-ink-3">
                    · {s.lastRequest} <IdText id={meta.lastRequestId} className="text-caption" />
                  </span>
                )}
              </span>
            </Fact>
            <Fact label={s.dataSource}>{s.dataSources[API_MODE]}</Fact>
          </>
        )}
      </dl>
      {r && (
        <>
          <div className="flex flex-col gap-2">
            <h3 className="text-title">{s.checks}</h3>
            <ul className="grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3" data-readiness>
              {r.checks.map((check) => (
                <li key={check.component} className="flex flex-wrap items-center gap-x-2 text-body" data-check={check.status} data-component={check.component}>
                  <span className="min-w-24 text-ink-1">{s.components[check.component] ?? check.component}</span>
                  <ToneWord tone={readinessTone(check.status)}>{s.tone[readinessTone(check.status)]}</ToneWord>
                  {check.detail && (
                    <span className="text-caption text-ink-3" data-verbatim>
                      {check.detail}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="text-title">{s.versions}</h3>
            <ul className="flex flex-wrap gap-2 text-caption" data-versions>
              {(
                [
                  [s.contract, r.contract_version],
                  [s.rules, r.rules_version],
                  [s.assumptions, r.assumptions_version],
                  ...Object.entries(r.model_versions).map(([name, version]) => [name, version ?? '']),
                ] as Array<[string, string]>
              ).map(([name, version]) => (
                <li key={name} className="rounded-chip border border-border px-2 py-0.5">
                  <span className="text-ink-3">{name}</span>{' '}
                  <span className="font-mono text-ink-1" data-version>
                    {version}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </Panel>
  );
}
