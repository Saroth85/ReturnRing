// Demo path /demo (spec 16, US-903; docs/ui-design.md 9.5 and 12): the preflight that must be green
// before the pitch, each red line with its fix, then the beats in order with what to say, the
// persona of each beat, a new-tab link per beat, presenter mode and the cached URL. Every case id
// comes from GET /api/demo/cases; before a reset the page says to run it, in the API's words.
import { useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { CircleCheck, CircleDashed, CircleX, ExternalLink, RefreshCw } from 'lucide-react';
import { Link, useSearchParams } from 'react-router';
import { API_MODE } from '@/api/adapters';
import { api } from '@/api/client';
import { useDemoClock } from '@/api/clock';
import { isApiError } from '@/api/errors';
import type { CaseCard, DemoCasesResponse } from '@/api/models';
import { useAssumptions, useCase, useDemoCases, useReadyz } from '@/api/queries';
import { setPersona, personaName } from '@/app/persona';
import { setPresent, usePresent } from '@/app/display';
import { ErrorState, LoadingBlock } from '@/components/feedback';
import { Figure, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { caseOf, goldenReturnIds } from '@/domain/demoCases';
import { CACHED_ORIGIN } from '../constants';
import { PLATFORM } from '../copy';
import { PageHeader, Panel } from '../shared/layout';
import { NoResetNotice } from '../shared/NoResetNotice';
import { buildBeats, type Beat } from './beats';
import { loadMetric, preflightChecks, preflightState, type CheckState, type ExpectedMode, type PreflightCheck } from './preflight';

const t = PLATFORM.demo;
const LOAD_KEY = 'demoPreflightLoad';

/**
 * The golden cards and the matrix fetched together, timed in this browser (never from the cache):
 * the milliseconds only, dated with the service's clock when shown.
 */
function useGoldenLoad(returnIds: readonly string[]) {
  return useQuery({
    queryKey: [LOAD_KEY, returnIds.join(',')],
    queryFn: async ({ signal }) => {
      const started = performance.now();
      await Promise.all([...returnIds.map((id) => api.getCase(id, { signal })), api.getMatrix({ signal })]);
      return performance.now() - started;
    },
    enabled: returnIds.length > 0,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

const ICON: Record<CheckState, typeof CircleCheck> = { pass: CircleCheck, fail: CircleX, pending: CircleDashed };
const TONE: Record<CheckState, string> = { pass: 'text-ok-fg', fail: 'text-danger-fg', pending: 'text-ink-3' };

function CheckRow({ check, load }: { check: PreflightCheck; load?: ReturnType<typeof loadMetric> }) {
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
          <p className="break-words text-caption text-ink-3" data-constant>
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
  const cases = useDemoCases();
  const clock = useDemoClock();
  const marcoId = caseOf(cases.data, 'marco')?.return_id;
  const rossiId = (caseOf(cases.data, 'rossi_card') ?? caseOf(cases.data, 'rossi'))?.return_id;
  const marco = useCase(marcoId);
  const rossi = useCase(rossiId);
  const assumptions = useAssumptions();
  const load = useGoldenLoad(goldenReturnIds(cases.data));
  const loadFigure = load.data !== undefined && clock.today ? loadMetric(load.data, clock.today, API_MODE) : undefined;
  const answer = <T,>(q: { data: T | undefined; isError: boolean; error: unknown }) => ({ data: q.data, failed: q.isError, error: q.error });
  const checks = preflightChecks({
    expected,
    adapter: API_MODE,
    readyz: answer(readyz),
    cases: answer(cases),
    marco: answer(marco),
    rossi: answer(rossi),
    assumptions: answer(assumptions),
    load: { data: loadFigure, failed: load.isError || cases.isError, error: load.error },
  });
  const state = preflightState(checks);
  const runAgain = () => {
    void readyz.refetch();
    void cases.refetch();
    if (marcoId) void marco.refetch();
    if (rossiId) void rossi.refetch();
    void assumptions.refetch();
    void client.refetchQueries({ queryKey: [LOAD_KEY] });
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
          <CheckRow key={check.id} check={check} load={check.id === 'load' ? loadFigure : undefined} />
        ))}
      </ul>
    </Panel>
  );
}

/** Whether Marco is approved, from his card: the ring checkout shows his restriction only then. */
function AfterMarco({ card }: { card: CaseCard | undefined }) {
  if (!card) return null;
  const state = card.decision.state;
  const text = state === 'approved' ? t.afterMarco.approved : state === 'proposed' ? t.afterMarco.proposed : t.afterMarco.other;
  return (
    <p className={clsx('text-caption', state === 'approved' ? 'text-ink-2' : 'font-medium text-attn-fg')} data-after-marco={state}>
      {text}
    </p>
  );
}

/** The dispute of the reset: the cluster before and after, and whether the decision changes. */
function DisputeDetail({ beat, provenance }: { beat: Beat; provenance: DemoCasesResponse['provenance'] }) {
  const dispute = beat.dispute;
  if (!dispute) return null;
  return (
    <ProvenanceScope provenance={provenance} covers="the cluster sizes of the demo dispute" className="flex flex-col gap-0.5 text-caption">
      <p className="text-ink-2">
        {t.disputeCluster} <IdText id={dispute.cluster_id} />
        {': '}
        <PlainNumber value={dispute.cluster_size_before} unit="count" /> → <PlainNumber value={dispute.cluster_size_after} unit="count" />{' '}
        {t.disputeAccounts}
      </p>
      <ProvenanceFooter compact />
      <p className={dispute.changes_decision ? 'text-ink-2' : 'font-medium text-ink-1'} data-dispute-changes={dispute.changes_decision ? 'yes' : 'no'}>
        {dispute.changes_decision ? t.disputeChanges : t.disputeUnchanged}
      </p>
    </ProvenanceScope>
  );
}

function BeatRow({ beat, index, beats, cases, marco }: { beat: Beat; index: number; beats: readonly Beat[]; cases: DemoCasesResponse | undefined; marco: CaseCard | undefined }) {
  const after = beat.after ? beats.find((b) => b.id === beat.after) : undefined;
  return (
    <li className="flex flex-col gap-1 border-t border-border py-3 first:border-t-0 md:flex-row md:items-start md:gap-4" data-beat={index} data-beat-id={beat.id}>
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
          <code className="break-all text-caption text-ink-3" data-id>
            {beat.to}
          </code>
        </p>
        {after && <p className="text-caption text-ink-3">{t.after(after.label)}</p>}
        <p className="max-w-prose text-body text-ink-2" data-constant>
          <span className="font-medium text-ink-1">{t.say}:</span> {beat.say}
        </p>
        {beat.after === 'marco' && <AfterMarco card={marco} />}
        {cases && <DisputeDetail beat={beat} provenance={cases.provenance} />}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-3">
        <Link
          to={beat.to}
          onClick={() => setPersona(beat.persona)}
          className="inline-flex min-h-8 items-center rounded-control border border-border-strong bg-surface px-3 text-body font-medium text-ink-1 hover:bg-surface-2"
        >
          {t.open}
        </Link>
        <a
          href={beat.to}
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => setPersona(beat.persona)}
          className="inline-flex items-center gap-1 text-accent underline underline-offset-2"
        >
          {t.openTab}
          <ExternalLink aria-hidden="true" size={14} />
        </a>
      </div>
    </li>
  );
}

function Beats() {
  const cases = useDemoCases();
  const marco = useCase(caseOf(cases.data, 'marco')?.return_id);
  const beats = buildBeats(cases.data);
  const main = beats.filter((b) => !b.offPath);
  const offPath = beats.filter((b) => b.offPath);
  const noReset = cases.isError && isApiError(cases.error) && cases.error.status === 404;
  const row = (beat: Beat) => (
    <BeatRow key={beat.id} beat={beat} index={beats.indexOf(beat)} beats={beats} cases={cases.data} marco={marco.data} />
  );
  return (
    <Panel title={t.beats}>
      <p className="text-body text-ink-2">{t.beatsIntro}</p>
      {cases.isError && (noReset ? <NoResetNotice error={cases.error} /> : <ErrorState error={cases.error} onRetry={() => void cases.refetch()} />)}
      {cases.isPending && <LoadingBlock rows={3} />}
      <ol className="flex flex-col">{main.map(row)}</ol>
      {offPath.length > 0 && (
        <section aria-labelledby="demo-off-path" className="flex flex-col gap-1 border-t border-border-strong pt-3" data-off-path>
          <h3 id="demo-off-path" className="text-title">
            {t.offPath}
          </h3>
          <p className="text-body text-ink-2">{t.offPathIntro}</p>
          <ol className="flex flex-col">{offPath.map(row)}</ol>
        </section>
      )}
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
