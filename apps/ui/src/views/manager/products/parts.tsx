// Pieces shared by the product issue screens (US-208, US-606, docs/ui-design.md 8.5): tabs, the
// catalogue task form, the task card with its status stepper, and the follow-up after a correction.
import clsx from 'clsx';
import { Check, CircleDot, Circle, XCircle } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { Link, NavLink } from 'react-router';
import type { CatalogTask } from '@/api/models';
import { useCreateCatalogTask, useUpdateCatalogTask } from '@/api/mutations';
import { usePersona } from '@/app/persona';
import { InlineNotice } from '@/components/feedback';
import { DateText, Figure, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { MIN_REASON_CHARS } from '@/components/records';
import { Button } from '@/components/ui';
import { MANAGER } from '../copy';
import type { ProductAnomaly } from '../types';
import { WriteError, WriteGate } from '../ui';

const T = MANAGER.tasks;
const PR = MANAGER.products;

export function ProductTabs() {
  const tab = (to: string, label: string, end: boolean) => (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        clsx(
          '-mb-px border-b-2 px-3 py-2 text-body',
          isActive ? 'border-accent font-medium text-ink-1' : 'border-transparent text-accent hover:border-border-strong',
        )
      }
    >
      {label}
    </NavLink>
  );
  return (
    <nav aria-label={PR.tabsLabel} className="flex gap-1 border-b border-border">
      {tab('/products', PR.tabs.anomalies, true)}
      {tab('/products/tasks', PR.tabs.tasks, false)}
    </nav>
  );
}

const KINDS: CatalogTask['kind'][] = ['description', 'size_guide', 'batch_check'];

/**
 * POST /api/catalog-tasks (201): kind, note, optional return; the customer is never restricted.
 * The returns offered are the ones a task can name: `queued_return_ids` (ADR-15 section 18, any
 * queued return of the variant), from the anomaly list or, opened from a case card, the card's
 * anomaly, which also holds the case's own return (Elena's, requested after the anomaly's week).
 */
export function CatalogTaskForm({
  anomaly,
  offered = anomaly.queued_return_ids,
  preselected = null,
  onCreated,
}: {
  anomaly: ProductAnomaly;
  offered?: readonly string[];
  preselected?: string | null;
  onCreated?(task: CatalogTask): void;
}) {
  const persona = usePersona();
  const create = useCreateCatalogTask();
  const [kind, setKind] = useState<CatalogTask['kind']>(anomaly.reason === 'defective' ? 'batch_check' : 'description');
  const [note, setNote] = useState('');
  const [returnId, setReturnId] = useState(preselected && offered.includes(preselected) ? preselected : '');
  const [created, setCreated] = useState<CatalogTask | null>(null);
  const noteId = useId();
  const returnSelectId = useId();
  const short = note.trim().length < MIN_REASON_CHARS;

  const submit = () =>
    create.mutate(
      { anomaly_id: anomaly.anomaly_id, kind, note: note.trim(), author: persona.id, return_id: returnId || null },
      {
        onSuccess: (task) => {
          setCreated(task);
          setNote('');
          onCreated?.(task);
        },
      },
    );

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!short) submit();
      }}
      data-catalog-task-form
    >
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-dense font-medium">{T.form.kind}</legend>
        <div className="flex flex-wrap gap-3">
          {KINDS.map((k) => (
            <label key={k} className="inline-flex items-center gap-1.5 text-body">
              <input type="radio" name="kind" value={k} checked={kind === k} onChange={() => setKind(k)} className="accent-[var(--accent)]" />
              {T.kind[k]}
            </label>
          ))}
        </div>
      </fieldset>
      <label htmlFor={noteId} className="text-dense font-medium">
        {T.form.note}
      </label>
      <textarea
        id={noteId}
        rows={3}
        value={note}
        onChange={(event) => setNote(event.target.value)}
        className="rounded-control border border-border-strong bg-surface px-2 py-1 text-body"
        aria-describedby={`${noteId}-hint`}
      />
      <p id={`${noteId}-hint`} className="text-caption text-ink-3">
        {T.form.noteHint}
      </p>
      {offered.length > 0 ? (
        <label htmlFor={returnSelectId} className="flex flex-col gap-1 text-dense">
          <span className="font-medium">{T.form.returnId}</span>
          <select
            id={returnSelectId}
            value={returnId}
            onChange={(event) => setReturnId(event.target.value)}
            className="min-h-8 max-w-xs rounded-control border border-border-strong bg-surface px-2"
            data-task-returns
          >
            <option value="">—</option>
            {offered.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
          <span className="text-caption text-ink-3">{T.form.returnHint}</span>
        </label>
      ) : (
        <p className="text-caption text-ink-3" data-no-queued-returns>
          {T.form.noQueued}
        </p>
      )}
      <p className="text-caption text-ink-2">{T.form.noRestriction}</p>
      <WriteGate action="POST /api/catalog-tasks">
        {(gate) => (
          <Button type="submit" variant="primary" disabled={!gate.allowed || short || create.isPending} title={gate.reason ?? undefined}>
            {T.form.submit}
          </Button>
        )}
      </WriteGate>
      {created && (
        <InlineNotice tone="info">
          <span data-task-created={created.task_id}>
            {T.form.created(created.task_id)}{' '}
            <Link to="/products/tasks" className="text-accent underline underline-offset-2">
              {PR.tabs.tasks}
            </Link>
          </span>
        </InlineNotice>
      )}
      {create.error && <WriteError error={create.error} action={T.form.submit.toLowerCase()} />}
    </form>
  );
}

const STEPS: CatalogTask['status'][] = ['open', 'in_progress', 'done'];
/** The moves a task can make (CatalogTaskUpdate: in_progress, done, cancelled). */
const NEXT: Record<CatalogTask['status'], ('in_progress' | 'done' | 'cancelled')[]> = {
  open: ['in_progress', 'cancelled'],
  in_progress: ['done', 'cancelled'],
  done: [],
  cancelled: [],
};

export function TaskStatusStepper({ status }: { status: CatalogTask['status'] }) {
  const reached = STEPS.indexOf(status);
  return (
    <ol className="flex flex-wrap items-center gap-1 text-caption" aria-label={T.steps} data-task-stepper={status}>
      {STEPS.map((step, i) => {
        const done = status !== 'cancelled' && i < reached;
        const current = step === status;
        return (
          <li key={step} className="flex items-center gap-1">
            {i > 0 && <span aria-hidden="true" className="h-px w-4 bg-border-strong" />}
            <span
              className={clsx(
                'inline-flex items-center gap-1 rounded-full border px-2 py-0.5',
                current ? 'border-ink-1 font-medium text-ink-1' : done ? 'border-border-strong text-ink-2' : 'border-border text-ink-3',
              )}
              aria-current={current ? 'step' : undefined}
            >
              {done ? <Check aria-hidden="true" size={12} /> : current ? <CircleDot aria-hidden="true" size={12} /> : <Circle aria-hidden="true" size={12} />}
              {T.status[step]}
            </span>
          </li>
        );
      })}
      {status === 'cancelled' && (
        <li className="flex items-center gap-1">
          <span aria-hidden="true" className="h-px w-4 bg-border-strong" />
          <span className="inline-flex items-center gap-1 rounded-full border border-ink-1 px-2 py-0.5 font-medium" aria-current="step">
            <XCircle aria-hidden="true" size={12} />
            {T.status.cancelled}
          </span>
        </li>
      )}
    </ol>
  );
}

/** The return rate before and after a correction; the caveat comes from the contract (verbatim). */
export function FollowUpPanel({ followUp }: { followUp: NonNullable<CatalogTask['follow_up']> }) {
  return (
    <ProvenanceScope
      provenance={followUp.after_rate?.provenance ?? followUp.baseline_rate.provenance}
      covers="the weeks observed after the correction"
      className="flex flex-col gap-2 rounded-control border border-border bg-surface-2 p-3"
    >
      <h4 className="text-dense font-medium">{T.followUp}</h4>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div>
          <dt className="text-caption text-ink-3">{T.baseline}</dt>
          <dd className="text-h3">
            <Figure metric={followUp.baseline_rate} label={T.baseline} />
          </dd>
        </div>
        <div>
          <dt className="text-caption text-ink-3">{T.after}</dt>
          <dd className="text-h3">{followUp.after_rate ? <Figure metric={followUp.after_rate} label={T.after} /> : <span className="text-ink-3">{T.notYet}</span>}</dd>
        </div>
        <div>
          <dt className="text-caption text-ink-3">{T.weeksAfter}</dt>
          <dd className="text-h3">
            <PlainNumber value={followUp.weeks_after} unit="count" />
          </dd>
        </div>
      </dl>
      <p className="text-caption font-medium text-ink-1" data-verbatim data-caveat>
        {followUp.caveat}
      </p>
      <ProvenanceFooter />
    </ProvenanceScope>
  );
}

/** One catalogue task: what, where from, its progress and the moves a person can make. */
export function TaskCard({ task, children }: { task: CatalogTask; children?: ReactNode }) {
  const persona = usePersona();
  const update = useUpdateCatalogTask(task.task_id);
  const [result, setResult] = useState<CatalogTask | null>(null);
  const [note, setNote] = useState('');
  const noteId = useId();
  // The server's answer wins over a list that has not been refetched yet.
  const shown = result && result.updated_at >= task.updated_at ? result : task;
  const moves = NEXT[shown.status];

  const move = (status: 'in_progress' | 'done' | 'cancelled') =>
    update.mutate(
      { status, note: note.trim() || null, author: persona.id },
      {
        onSuccess: (next) => {
          setResult(next);
          setNote('');
        },
      },
    );

  return (
    <article className="flex flex-col gap-3 rounded-card border border-border bg-surface p-4" data-task={task.task_id} data-status={shown.status}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h3 className="flex flex-wrap items-center gap-2 text-title">
            <IdText id={task.task_id} />
            <span>{T.kind[task.kind]}</span>
          </h3>
          <p className="flex flex-wrap items-center gap-x-2 text-caption text-ink-2">
            <span>{T.variant}</span>
            <Link to={`/products/${encodeURIComponent(task.anomaly_id)}`} className="font-mono text-accent underline underline-offset-2" data-id>
              {task.variant_id}
            </Link>
            <span>· {PR.reason[task.reason] ?? task.reason}</span>
            {task.return_id && (
              <>
                <span>· {T.fromReturn}</span>
                <Link to={`/returns/${encodeURIComponent(task.return_id)}?focus=product`} className="font-mono text-accent underline underline-offset-2" data-id>
                  {task.return_id}
                </Link>
              </>
            )}
          </p>
        </div>
        <TaskStatusStepper status={shown.status} />
      </header>
      <p className="text-body" data-verbatim>
        {shown.note}
      </p>
      <p className="flex flex-wrap gap-x-3 text-caption text-ink-3">
        <span>
          {T.openedBy} {task.created_by} · <DateText value={task.created_at} />
        </span>
        <span>
          {T.updatedAt} <DateText value={shown.updated_at} />
        </span>
        {shown.done_at && (
          <span>
            {T.doneAt} <DateText value={shown.done_at} />
          </span>
        )}
      </p>
      {shown.follow_up && <FollowUpPanel followUp={shown.follow_up} />}
      {children}
      {moves.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <label htmlFor={noteId} className="text-caption text-ink-2">
            {T.moveNote}
          </label>
          <input
            id={noteId}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="rounded-control border border-border-strong bg-surface px-2 py-1 text-body"
          />
          <WriteGate action="PATCH /api/catalog-tasks/{task_id}">
            {(gate) => (
              <div className="flex flex-wrap gap-2">
                {moves.map((m) => (
                  <Button
                    key={m}
                    variant={m === 'cancelled' ? 'secondary' : 'primary'}
                    disabled={!gate.allowed || update.isPending}
                    title={gate.reason ?? undefined}
                    onClick={() => move(m)}
                    data-move={m}
                  >
                    {T.move[m]}
                  </Button>
                ))}
              </div>
            )}
          </WriteGate>
        </div>
      )}
      {result && (
        <InlineNotice tone="info">
          <span data-task-updated>{T.updated(result.task_id, T.status[result.status])}</span>
        </InlineNotice>
      )}
      {update.error && <WriteError error={update.error} action={T.updateAction} />}
    </article>
  );
}

export function AnomalyStatus({ anomaly }: { anomaly: ProductAnomaly }) {
  return (
    <span className="flex flex-col gap-0.5">
      <span
        className={clsx(
          'inline-flex w-fit items-center rounded-chip border px-1.5 py-0.5 text-caption',
          anomaly.status === 'open' ? 'border-attn-border bg-attn-bg text-attn-fg' : 'border-border-strong text-ink-1',
        )}
        data-anomaly-status={anomaly.status}
      >
        {PR.status[anomaly.status]}
      </span>
      {anomaly.catalog_task_id && (
        <Link to="/products/tasks" className="font-mono text-caption text-accent underline underline-offset-2" data-id>
          {anomaly.catalog_task_id}
        </Link>
      )}
    </span>
  );
}
