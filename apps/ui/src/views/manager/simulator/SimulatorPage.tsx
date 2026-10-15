// Economic simulator /simulator (?scenario=; US-602, ADR-04, docs/ui-design.md 8.2): net benefit
// with disjoint components and editable, versioned assumptions, computed in the browser by the port
// of policy/simulator.py; baseline and period explicit; scenarios, not confidence intervals;
// realised kept apart from simulated. The refund owed is never a saving (rule 9).
import clsx from 'clsx';
import { Save } from 'lucide-react';
import { useEffect, useId, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { isApiError } from '@/api/errors';
import type { SimulatorAssumptionsResponse } from '@/api/models';
import { useUpdateAssumptions } from '@/api/mutations';
import { useAssumptions, useMatrix } from '@/api/queries';
import { usePersona } from '@/app/persona';
import { AssumptionLabel, ECONOMICS, Waterfall } from '@/components/economics';
import { ErrorState, InlineNotice } from '@/components/feedback';
import { DateText, Figure, NatureMark } from '@/components/provenance';
import { Button } from '@/components/ui';
import { SENSITIVITY_IDS, type InputKey, type SensitivityId } from '@/sim';
import { MIN_REASON_CHARS } from '@/components/records';
import { MANAGER } from '../copy';
import type { AssumptionVersion } from '../types';
import { Card, Loadable, ManagerPage, WriteError, WriteGate } from '../ui';
import { AssumptionForm } from './AssumptionForm';
import { draftOf, inputsFromDraft, isDirty, nextVersionLabel, scenarioViews, updateBody, type Draft } from './model';
import { ThresholdSandbox } from './ThresholdSandbox';

const S = MANAGER.simulator;
const isScenario = (id: string | null): id is SensitivityId => id !== null && (SENSITIVITY_IDS as readonly string[]).includes(id);

function SaveVersion({ draft, version, dirty, onSaved }: { draft: Draft; version: AssumptionVersion; dirty: boolean; onSaved(v: string): void }) {
  const persona = usePersona();
  const update = useUpdateAssumptions();
  const [note, setNote] = useState('');
  const noteId = useId();
  const hintId = useId();
  const short = note.trim().length < MIN_REASON_CHARS;
  const error = update.error;
  const conflict = isApiError(error) && error.code === 'conflict';
  const changed = useMemo(() => {
    const inputs = inputsFromDraft(draft, version);
    return Object.keys(S.fields).filter((key) => inputs[key as InputKey] !== version.net_benefit.assumptions[key as InputKey]);
  }, [draft, version]);

  // mutateAsync: the success hook replaces the current version, which remounts this form (keyed
  // by version) before a mutate() callback could run; the promise still reaches the parent.
  const save = () =>
    update
      .mutateAsync(updateBody(draft, version, note, persona.id))
      .then((result) => onSaved(result.current.version))
      .catch(() => undefined);

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3" data-save-version>
      <label htmlFor={noteId} className="text-dense font-medium">
        {S.form.note}
      </label>
      <textarea
        id={noteId}
        rows={2}
        value={note}
        aria-describedby={hintId}
        onChange={(event) => setNote(event.target.value)}
        className="rounded-control border border-border-strong bg-surface px-2 py-1 text-body"
      />
      <p id={hintId} className="text-caption text-ink-3">
        {S.form.noteHint}
      </p>
      <WriteGate action="PUT /api/simulator/assumptions">
        {(gate) => (
          <Button variant="primary" disabled={!gate.allowed || !dirty || short || update.isPending} title={gate.reason ?? undefined} onClick={save}>
            <Save aria-hidden="true" size={14} />
            {S.form.saveAs} <span data-version>{nextVersionLabel(version.version)}</span>
          </Button>
        )}
      </WriteGate>
      {error &&
        (isApiError(error) && error.status === 403 ? (
          <WriteError error={error} action={S.form.saveAction} />
        ) : conflict ? (
          <InlineNotice tone="attention">
            <p>
              <span data-version>{version.version}</span> {S.form.conflictTail}
            </p>
            <ul className="mt-1 list-disc pl-5 text-caption">
              {changed.map((key) => (
                <li key={key}>{S.fields[key]?.label}</li>
              ))}
            </ul>
          </InlineNotice>
        ) : (
          <ErrorState error={error} />
        ))}
    </div>
  );
}

function History({ history }: { history: SimulatorAssumptionsResponse['history'] }) {
  const c = S.historyCols;
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[44rem] border-collapse text-dense" data-assumption-history>
        <thead>
          <tr className="border-b border-border-strong text-left text-caption text-ink-2">
            {[c.version, c.author, c.at, c.note, c.supersedes, c.total].map((h) => (
              <th key={h} scope="col" className="px-2 py-1.5 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {history.map((v, i) => (
            <tr key={v.version} className="border-b border-border align-top" data-history-version={v.version}>
              <td className="px-2 py-1.5">
                <span className="font-mono" data-version>
                  {v.version}
                </span>
                {i === 0 && <span className="ml-1 rounded-chip bg-surface-2 px-1 text-micro uppercase">{S.current}</span>}
              </td>
              <td className="px-2 py-1.5">{v.created_by}</td>
              <td className="px-2 py-1.5">
                <DateText value={v.created_at} />
              </td>
              <td className="px-2 py-1.5" data-verbatim>
                {v.note}
              </td>
              <td className="px-2 py-1.5">
                {v.supersedes ? (
                  <span className="font-mono" data-version>
                    {v.supersedes}
                  </span>
                ) : (
                  <span className="text-ink-3">—</span>
                )}
              </td>
              <td className="px-2 py-1.5 text-right">
                <Figure metric={v.net_benefit.total} label={`${ECONOMICS.total} ${v.version}`} signed />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Simulator({ data, saved, onSaved }: { data: SimulatorAssumptionsResponse; saved: string | null; onSaved(version: string | null): void }) {
  const version = data.current;
  const [params, setParams] = useSearchParams();
  const requested = params.get('scenario');
  const selected: SensitivityId = isScenario(requested) ? requested : 'base';
  // The parent keys this component by version: a new current version starts a fresh draft.
  const [draft, setDraft] = useState<Draft>(() => draftOf(version));
  const matrix = useMatrix();

  const dirty = isDirty(draft, version);
  const views = useMemo(() => scenarioViews(draft, version), [draft, version]);
  const shown = views.find((v) => v.id === selected) ?? views[0];
  const differs = views.some((v) => v.differs);
  useEffect(() => {
    if (differs) console.warn('[simulator] browser and server net benefits differ for the stored assumptions; showing the server value');
  }, [differs]);

  const choose = (id: SensitivityId) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (id === 'base') next.delete('scenario');
        else next.set('scenario', id);
        return next;
      },
      { replace: true },
    );

  return (
    <ManagerPage
      screen="simulator"
      title={MANAGER.titles.simulator}
      intro={
        <div className="flex flex-col gap-1">
          <p className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-chip border border-dashed border-ink-2 px-1.5 py-0.5 text-micro uppercase text-ink-1">
              <NatureMark nature="simulated" decorative />
              {S.natureTag}
            </span>
            <span>{S.natureLine}</span>
          </p>
          <p className="text-caption text-ink-2">
            {S.baseline}: <span data-verbatim>{version.baseline}</span> · <span className="font-mono" data-version>{version.version}</span> {S.by}{' '}
            {version.created_by} · {S.periodLabel}: <span data-verbatim>{version.net_benefit.assumptions.period}</span>
          </p>
        </div>
      }
    >
      {saved && (
        <InlineNotice tone="info">
          <span data-saved-version={saved}>
            {S.form.savedAs} <span data-version>{saved}</span>
          </span>
        </InlineNotice>
      )}
      {differs && <InlineNotice tone="attention">{S.differs}</InlineNotice>}
      <div className="grid gap-4 xl:grid-cols-12">
        <Card title={S.form.title} className="xl:col-span-4">
          <AssumptionForm
            draft={draft}
            version={version}
            dirty={dirty}
            onChange={(key, value) => {
              onSaved(null);
              setDraft((d) => ({ ...d, [key]: value }));
            }}
            onReset={() => setDraft(draftOf(version))}
          />
          <SaveVersion draft={draft} version={version} dirty={dirty} onSaved={onSaved} />
        </Card>
        <Card
          title={S.waterfall}
          className="xl:col-span-5"
          aside={shown && <AssumptionLabel label={shown.view.assumptions.version} basedOn={shown.view.assumptions.based_on} />}
        >
          {shown && (
            <div className="flex flex-col gap-3" data-shown-scenario={shown.id} data-source={shown.source}>
              <p className="text-caption text-ink-2">
                {S.showing}: <span className="font-medium text-ink-1">{ECONOMICS.scenarioShort[shown.id]}</span> ·{' '}
                <span data-verbatim>{shown.view.assumptions.period}</span>
              </p>
              <Waterfall view={shown.view} />
              <p className="text-caption text-ink-3">
                {S.origin}: <span data-verbatim>{shown.view.total.provenance.origin}</span>
              </p>
            </div>
          )}
          <p className="border-t border-border pt-2 text-caption text-ink-2" data-formula>
            {S.formula}
          </p>
        </Card>
        <div className="flex min-w-0 flex-col gap-4 xl:col-span-3">
          <Card title={S.scenarios} level={2}>
            <ul className="flex flex-col gap-1" aria-label={S.scenarios} data-scenarios>
              {views.map((v) => {
                const active = v.id === shown?.id;
                return (
                  <li
                    key={v.id}
                    className={clsx('flex items-center justify-between gap-2 rounded-control px-1 py-0.5', active && 'bg-accent-bg')}
                    data-scenario={v.id}
                  >
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => choose(v.id)}
                      className={clsx(
                        'min-h-8 flex-1 rounded-control px-2 text-left text-dense',
                        active ? 'font-semibold text-ink-1' : 'text-accent underline-offset-2 hover:underline',
                      )}
                    >
                      {ECONOMICS.scenarioShort[v.id]}
                    </button>
                    <Figure metric={v.view.total} label={`${ECONOMICS.scenarioShort[v.id]} ${ECONOMICS.total}`} signed />
                  </li>
                );
              })}
            </ul>
            <p className="text-caption text-ink-2">{S.scenariosNote}</p>
            <div className="rounded-control border border-dashed border-border-strong px-2 py-1.5" data-realised>
              <p className="text-micro uppercase text-ink-3">{S.realised}</p>
              <p className="text-dense text-ink-2">{S.realisedNone}</p>
            </div>
          </Card>
          <Card title={S.sandbox.title}>
            <Loadable query={matrix} label="GET /api/portfolio/matrix" rows={3}>
              {(m) => <ThresholdSandbox matrix={m} />}
            </Loadable>
          </Card>
        </div>
      </div>
      <Card title={S.history}>
        <History history={data.history} />
      </Card>
    </ManagerPage>
  );
}

export default function SimulatorPage() {
  const assumptions = useAssumptions();
  const [saved, setSaved] = useState<string | null>(null);
  if (!assumptions.data) {
    return (
      <ManagerPage screen="simulator" title={MANAGER.titles.simulator}>
        <Loadable query={assumptions} label="GET /api/simulator/assumptions" rows={8}>
          {() => null}
        </Loadable>
      </ManagerPage>
    );
  }
  return <Simulator key={assumptions.data.current.version} data={assumptions.data} saved={saved} onSaved={setSaved} />;
}
