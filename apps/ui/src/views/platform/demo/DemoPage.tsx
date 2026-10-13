// Demo path /demo (spec 16, US-903; docs/ui-design.md 9.5 and 12): the preflight that must be green
// before the pitch, each red line with its fix, then the six beats in order with what to say, the
// persona of each beat, a new-tab link per beat, presenter mode and the cached URL.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { CircleCheck, CircleDashed, CircleX, ExternalLink, RefreshCw } from 'lucide-react';
import { Link, useSearchParams } from 'react-router';
import { API_MODE } from '@/api/adapters';
import { api } from '@/api/client';
import type { DemoUserId } from '@/api/models';
import { useAssumptions, useCase, useReadyz } from '@/api/queries';
import { setPersona, personaName } from '@/app/persona';
import { setPresent, usePresent } from '@/app/display';
import { Figure } from '@/components/provenance';
import { Button } from '@/components/ui';
import { CACHED_ORIGIN, GOLDEN_RETURN_IDS, MARCO_RETURN_ID, ROSSI_RETURN_ID } from '../constants';
import { PLATFORM } from '../copy';
import { PageHeader, Panel } from '../shared/layout';
import { loadMetric, preflightChecks, preflightState, type CheckState, type ExpectedMode, type PreflightCheck } from './preflight';

const t = PLATFORM.demo;
const LOAD_KEY = ['demoPreflightLoad'] as const;

/** The golden cards and the matrix fetched together, timed in this browser (never from the cache). */
function useGoldenLoad() {
  return useQuery({
    queryKey: LOAD_KEY,
    queryFn: async ({ signal }) => {
      const started = performance.now();
      await Promise.all([...GOLDEN_RETURN_IDS.map((id) => api.getCase(id, { signal })), api.getMatrix({ signal })]);
      return loadMetric(performance.now() - started, new Date().toISOString(), API_MODE);
    },
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

const ICON: Record<CheckState, typeof CircleCheck> = { pass: CircleCheck, fail: CircleX, pending: CircleDashed };
const TONE: Record<CheckState, string> = { pass: 'text-ok-fg', fail: 'text-danger-fg', pending: 'text-ink-3' };

function CheckRow({ check, load }: { check: PreflightCheck; load?: ReturnType<typeof useGoldenLoad>['data'] }) {
  const Icon = ICON[check.state];
  return (
    <li className="flex gap-2 border-t border-border py-2 first:border-t-0" data-preflight={check.id} data-state={check.state}>
      <Icon aria-hidden="true" size={18} className={clsx('mt-0.5 shrink-0', TONE[check.state])} />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="text-body">
          <span className="sr-only">{`${t.stateWords[check.state]}: `}</span>
          {/* Labels interpolate named constants (versions, the time target). */}
          <span data-constant>{check.label}</span>
          {check.id === 'load' && load && (
            <>
              {' '}
              · <Figure metric={load} label={check.label} />
            </>
          )}
        </p>
        {check.found && (
          <p className="text-caption text-ink-3" data-constant>
            {`${t.found}: ${check.found}`}
          </p>
        )}
        {check.fix && <p className="text-caption text-danger-fg">{check.fix}</p>}
        {check.state === 'pending' && <p className="text-caption text-ink-3">{t.pending}</p>}
      </div>
    </li>
  );
}

function Preflight() {
  const [params, setParams] = useSearchParams();
  const expected: ExpectedMode = params.get('expect') === 'cached' ? 'cached' : 'live';
  const client = useQueryClient();
  const readyz = useReadyz();
  const marco = useCase(MARCO_RETURN_ID);
  const rossi = useCase(ROSSI_RETURN_ID);
  const assumptions = useAssumptions();
  const load = useGoldenLoad();
  const answer = <T,>(q: { data: T | undefined; isError: boolean }) => ({ data: q.data, failed: q.isError });
  const checks = preflightChecks({
    expected,
    readyz: answer(readyz),
    marco: answer(marco),
    rossi: answer(rossi),
    assumptions: answer(assumptions),
    load: answer(load),
  });
  const state = preflightState(checks);
  const runAgain = () => {
    void readyz.refetch();
    void marco.refetch();
    void rossi.refetch();
    void assumptions.refetch();
    void client.refetchQueries({ queryKey: LOAD_KEY });
  };
  const setExpected = (mode: ExpectedMode) => {
    const next = new URLSearchParams(params);
    if (mode === 'live') next.delete('expect');
    else next.set('expect', mode);
    setParams(next, { replace: true });
  };

  return (
    <Panel
      title={t.preflight}
      aside={
        <Button onClick={runAgain}>
          <RefreshCw aria-hidden="true" size={14} className="mr-1 inline-block" />
          {t.runAgain}
        </Button>
      }
    >
      <p className="text-body text-ink-2">{t.preflightIntro}</p>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t.expectedMode}>
        <span className="text-caption text-ink-3">{t.expectedMode}</span>
        {(['live', 'cached'] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            aria-pressed={expected === mode}
            onClick={() => setExpected(mode)}
            className={clsx(
              'min-h-8 rounded-control border px-3 text-dense',
              expected === mode ? 'border-accent bg-accent-bg font-medium text-ink-1' : 'border-border text-ink-2 hover:bg-surface-2',
            )}
          >
            {mode === 'live' ? t.expectLive : t.expectCached}
          </button>
        ))}
      </div>
      <p
        role="status"
        className={clsx('text-body font-medium', state === 'pass' ? 'text-ok-fg' : state === 'fail' ? 'text-danger-fg' : 'text-ink-3')}
        data-preflight-state={state}
      >
        {state === 'pass' ? t.allGreen : state === 'fail' ? t.someRed : t.pending}
      </p>
      <ul className="flex flex-col">
        {checks.map((check) => (
          <CheckRow key={check.id} check={check} load={check.id === 'load' ? load.data : undefined} />
        ))}
      </ul>
    </Panel>
  );
}

function Beats() {
  return (
    <Panel title={t.beats}>
      <p className="text-body text-ink-2">{t.beatsIntro}</p>
      <ol className="flex flex-col">
        {t.beatList.map((beat, index) => (
          <li key={beat.to} className="flex flex-col gap-1 border-t border-border py-3 first:border-t-0 md:flex-row md:items-start md:gap-4" data-beat={index}>
            <span
              aria-hidden="true"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border-strong text-dense font-semibold"
              data-constant
            >
              {index + 1}
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <p className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-title">{beat.label}</span>
                <span className="text-caption text-ink-3">{t.asPersona(personaName(beat.persona))}</span>
                <code className="text-caption text-ink-3" data-id>
                  {beat.to}
                </code>
              </p>
              <p className="max-w-prose text-body text-ink-2" data-constant>
                <span className="font-medium text-ink-1">{t.say}:</span> {beat.say}
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-3">
              <Link
                to={beat.to}
                onClick={() => setPersona(beat.persona as DemoUserId)}
                className="inline-flex min-h-8 items-center rounded-control border border-border-strong bg-surface px-3 text-body font-medium text-ink-1 hover:bg-surface-2"
              >
                {t.open}
              </Link>
              <a
                href={beat.to}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => setPersona(beat.persona as DemoUserId)}
                className="inline-flex items-center gap-1 text-accent underline underline-offset-2"
              >
                {t.openTab}
                <ExternalLink aria-hidden="true" size={14} />
              </a>
            </div>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export default function DemoPage() {
  const present = usePresent();
  return (
    <div className="mx-auto flex max-w-content flex-col gap-4" data-page="demo">
      <PageHeader
        title={PLATFORM.titles.demo}
        intro={t.intro}
        stories={t.stories}
        aside={
          <div className="flex flex-wrap items-center gap-3">
            {CACHED_ORIGIN ? (
              <a href={CACHED_ORIGIN} className="text-accent underline underline-offset-2">
                {t.cachedUrl}
              </a>
            ) : (
              <span className="text-caption text-ink-3">{t.noCachedUrl}</span>
            )}
            <Button variant="primary" onClick={() => setPresent(!present)}>
              {present ? t.leavePresenter : t.presenter}
            </Button>
          </div>
        }
      />
      <div className="grid gap-4 xl:grid-cols-12">
        <div className="min-w-0 xl:col-span-5">
          <Preflight />
        </div>
        <div className="min-w-0 xl:col-span-7">
          <Beats />
        </div>
      </div>
    </div>
  );
}
