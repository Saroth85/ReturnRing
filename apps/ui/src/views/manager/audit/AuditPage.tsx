// Audit log /audit (US-604, docs/ui-design.md 8.7): append-only entries with who, what, the entity,
// the reason and the request id; restrictive approvals name a person and a review date; a row opens
// into what changed and the versions of rules, models and assumptions. CSV export with the filters.
import { ChevronDown, ChevronRight, Download, Filter } from 'lucide-react';
import { Fragment, useId, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router';
import { api } from '@/api/client';
import { AUDIT_ACTIONS, DEMO_USERS } from '@/api/contract.gen';
import type { AuditEntry } from '@/api/models';
import { useAudit } from '@/api/queries';
import { EmptyState, ErrorState, InlineNotice } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import {
  ActorBadge,
  auditCsvFilename,
  AuditDiff,
  entityHref,
  HumanApprovalPill,
  keepHumanRows,
  matchesAuditFilters,
  RECORDS,
  sinceOf,
  toAuditQuery,
  untilOf,
  type AuditAction,
  type AuditFilters,
} from '@/components/records';
import { Button } from '@/components/ui';
import { MANAGER } from '../copy';
import { Loadable, ManagerPage } from '../ui';

const A = MANAGER.audit;
const ENTITY_TYPES = Object.keys(RECORDS.entity) as AuditEntry['entity_type'][];

function filtersFrom(params: URLSearchParams): AuditFilters {
  const day = (key: string) => {
    const v = params.get(key);
    return v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : null;
  };
  const since = day('since');
  const until = day('until');
  const actions = params.getAll('action').filter((a): a is AuditAction => (AUDIT_ACTIONS as readonly string[]).includes(a));
  const entity = params.get('entity_type');
  return {
    return_id: params.get('return_id'),
    decision_id: params.get('decision_id'),
    actor: params.get('actor'),
    action: actions.length > 0 ? actions : null,
    entity_type: entity && (ENTITY_TYPES as string[]).includes(entity) ? (entity as AuditEntry['entity_type']) : null,
    since: since ? sinceOf(since) : null,
    until: until ? untilOf(until) : null,
    humanOnly: params.get('human') === '1',
  };
}

function FilterForm({ params, onApply }: { params: URLSearchParams; onApply(next: URLSearchParams): void }) {
  const id = useId();
  const selected = new Set(params.getAll('action'));
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const key of ['return_id', 'decision_id', 'actor', 'entity_type', 'since', 'until']) {
      const value = String(form.get(key) ?? '').trim();
      if (value) next.set(key, value);
    }
    for (const action of form.getAll('action')) next.append('action', String(action));
    if (form.get('human')) next.set('human', '1');
    onApply(next);
  };
  const input = 'min-h-8 w-full rounded-control border border-border-strong bg-surface px-2 text-body';
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 rounded-card border border-border bg-surface p-4" aria-label={A.filters} data-audit-filters>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="flex flex-col gap-1 text-caption text-ink-2" htmlFor={`${id}-ret`}>
          {A.returnId}
          <input id={`${id}-ret`} name="return_id" defaultValue={params.get('return_id') ?? ''} className={`${input} font-mono`} />
        </label>
        <label className="flex flex-col gap-1 text-caption text-ink-2" htmlFor={`${id}-dec`}>
          {A.decisionId}
          <input id={`${id}-dec`} name="decision_id" defaultValue={params.get('decision_id') ?? ''} className={`${input} font-mono`} />
        </label>
        <label className="flex flex-col gap-1 text-caption text-ink-2" htmlFor={`${id}-actor`}>
          {A.actor}
          <select id={`${id}-actor`} name="actor" defaultValue={params.get('actor') ?? ''} className={input}>
            <option value="">{A.anyone}</option>
            {DEMO_USERS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.id} · {RECORDS.role[u.role] ?? u.role}
              </option>
            ))}
            <option value="system">{RECORDS.system}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-caption text-ink-2" htmlFor={`${id}-entity`}>
          {A.entity}
          <select id={`${id}-entity`} name="entity_type" defaultValue={params.get('entity_type') ?? ''} className={input}>
            <option value="">{A.anyEntity}</option>
            {ENTITY_TYPES.map((t) => (
              <option key={t} value={t}>
                {RECORDS.entity[t]}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-caption text-ink-2" htmlFor={`${id}-since`}>
          {A.since}
          <input id={`${id}-since`} type="date" name="since" defaultValue={params.get('since') ?? ''} className={input} />
        </label>
        <label className="flex flex-col gap-1 text-caption text-ink-2" htmlFor={`${id}-until`}>
          {A.until}
          <input id={`${id}-until`} type="date" name="until" defaultValue={params.get('until') ?? ''} className={input} />
        </label>
        <details className="sm:col-span-2" open={selected.size > 0}>
          <summary className="cursor-pointer text-caption text-accent">
            {A.action}
            {selected.size > 0 && ` · ${[...selected].map((a) => RECORDS.action[a as AuditAction] ?? a).join(', ')}`}
          </summary>
          <fieldset className="mt-2 grid gap-1 sm:grid-cols-2">
            <legend className="sr-only">{A.action}</legend>
            {AUDIT_ACTIONS.map((action) => (
              <label key={action} className="inline-flex items-center gap-2 text-dense">
                <input type="checkbox" name="action" value={action} defaultChecked={selected.has(action)} className="accent-[var(--accent)]" />
                {RECORDS.action[action]}
              </label>
            ))}
          </fieldset>
        </details>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label className="inline-flex items-center gap-2 text-body">
          <input type="checkbox" name="human" defaultChecked={params.get('human') === '1'} className="accent-[var(--accent)]" />
          {A.humanOnly}
        </label>
        <Button type="submit" variant="primary">
          <Filter aria-hidden="true" size={14} />
          {A.apply}
        </Button>
        <Button onClick={() => onApply(new URLSearchParams())}>{A.clear}</Button>
      </div>
    </form>
  );
}

function EntityCell({ entry }: { entry: AuditEntry }) {
  const href = entityHref(entry);
  const label = RECORDS.entity[entry.entity_type];
  return (
    <span className="flex flex-col">
      <span className="text-caption text-ink-3">{label}</span>
      {href ? (
        <Link to={href} className="font-mono text-dense whitespace-nowrap text-accent underline-offset-2 hover:underline" data-id>
          {entry.entity_id}
        </Link>
      ) : (
        <span className="font-mono" data-id>
          {entry.entity_id}
        </span>
      )}
    </span>
  );
}

function Versions({ entry }: { entry: AuditEntry }) {
  if (!entry.rules_version && !entry.model_versions && !entry.assumptions_version) return null;
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-caption" data-audit-versions>
      {entry.rules_version && (
        <>
          <dt className="text-ink-3">{A.rules}</dt>
          <dd className="font-mono" data-version>
            {entry.rules_version}
          </dd>
        </>
      )}
      {entry.model_versions && (
        <>
          <dt className="text-ink-3">{A.models}</dt>
          <dd className="flex flex-wrap gap-x-2 font-mono" data-version>
            {Object.entries(entry.model_versions).map(([k, v]) => (
              <span key={k}>{v}</span>
            ))}
          </dd>
        </>
      )}
      {entry.assumptions_version && (
        <>
          <dt className="text-ink-3">{A.assumptions}</dt>
          <dd className="font-mono" data-version>
            {entry.assumptions_version}
          </dd>
        </>
      )}
    </dl>
  );
}

function EntryRow({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const c = A.cols;
  return (
    <Fragment>
      <tr className="border-b border-border align-top" data-audit-row={entry.entry_id} data-action={entry.action}>
        <td className="px-2 py-2">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={() => setOpen((o) => !o)}
            className="rounded-control p-1 text-ink-2 hover:bg-surface-2"
            aria-label={`${open ? A.hideDetails : A.details}: ${RECORDS.action[entry.action]}`}
          >
            {open ? <ChevronDown aria-hidden="true" size={14} /> : <ChevronRight aria-hidden="true" size={14} />}
          </button>
        </td>
        <td className="px-2 py-2 whitespace-nowrap" data-label={c.at}>
          <DateText value={entry.at} />
        </td>
        <td className="px-2 py-2">
          <ActorBadge actor={entry.actor} actorRole={entry.actor_role} action={entry.action} />
        </td>
        <td className="px-2 py-2">
          <span className="flex flex-col items-start gap-1">
            <span className="font-medium">{RECORDS.action[entry.action]}</span>
            <HumanApprovalPill entry={entry} />
          </span>
        </td>
        <td className="px-2 py-2">
          <EntityCell entry={entry} />
        </td>
        <td className="max-w-xs px-2 py-2">
          {entry.reason ? (
            <q className="text-ink-2" data-verbatim>
              {entry.reason}
            </q>
          ) : (
            <span className="text-ink-3">—</span>
          )}
        </td>
        <td className="px-2 py-2 whitespace-nowrap">{entry.request_id ? <IdText id={entry.request_id} /> : <span className="text-ink-3">—</span>}</td>
      </tr>
      {open && (
        <tr id={detailsId} className="border-b border-border bg-surface-2" data-audit-details={entry.entry_id}>
          <td />
          <td colSpan={6} className="px-2 py-3">
            <div className="flex flex-col gap-3">
              <AuditDiff before={entry.before} after={entry.after} recordHref={entry.entity_type === 'decision' ? entityHref(entry) : null} />
              <Versions entry={entry} />
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function ExportButton({ filters }: { filters: AuditFilters }) {
  const [state, setState] = useState<{ kind: 'idle' } | { kind: 'busy' } | { kind: 'done'; name: string } | { kind: 'error'; error: unknown }>({ kind: 'idle' });
  const run = async () => {
    setState({ kind: 'busy' });
    try {
      const csv = await api.getAuditCsv(toAuditQuery(filters));
      const text = filters.humanOnly ? keepHumanRows(csv) : csv;
      const name = auditCsvFilename(filters);
      const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setState({ kind: 'done', name });
    } catch (error) {
      setState({ kind: 'error', error });
    }
  };
  return (
    <div className="flex flex-col items-end gap-1">
      <Button onClick={() => void run()} disabled={state.kind === 'busy'} data-export-csv>
        <Download aria-hidden="true" size={14} />
        {state.kind === 'busy' ? A.exporting : A.export}
      </Button>
      {state.kind === 'done' && (
        <p role="status" className="text-caption text-ink-2" data-exported={state.name}>
          {A.exported(state.name)}
        </p>
      )}
      {state.kind === 'error' && <ErrorState error={state.error} />}
    </div>
  );
}

export default function AuditPage() {
  const [params, setParams] = useSearchParams();
  const filters = filtersFrom(params);
  const page = Math.max(1, Number(params.get('page') ?? '1') || 1);
  const audit = useAudit({ ...toAuditQuery(filters), page });
  const c = A.cols;
  const goPage = (next: number) =>
    setParams((prev) => {
      const out = new URLSearchParams(prev);
      if (next <= 1) out.delete('page');
      else out.set('page', String(next));
      return out;
    });

  return (
    <ManagerPage screen="audit" title={MANAGER.titles.audit} actions={<ExportButton filters={filters} />}>
      <InlineNotice tone="info">{A.banner}</InlineNotice>
      <FilterForm key={params.toString()} params={params} onApply={(next) => setParams(next)} />
      <Loadable query={audit} label="GET /api/audit" rows={8}>
        {(data) => {
          const items = data.items.filter((e) => matchesAuditFilters(e, filters));
          const more = data.page * data.page_size < data.total;
          return (
            <div className="flex flex-col gap-3">
              {items.length === 0 ? (
                <EmptyState message={A.empty} />
              ) : (
                <div className="relative overflow-x-auto rounded-card border border-border bg-surface">
                  <table className="w-full min-w-[60rem] border-collapse text-dense" data-audit-table>
                    <caption className="sr-only">{A.caption}</caption>
                    <thead className="bg-surface-2 text-left text-caption text-ink-2">
                      <tr>
                        <th scope="col" className="w-8 px-2 py-2">
                          <span className="sr-only">{A.details}</span>
                        </th>
                        {[c.at, c.actor, c.action, c.entity, c.reason, c.request].map((h) => (
                          <th key={h} scope="col" className="px-2 py-2 font-medium">
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((entry) => (
                        <EntryRow key={entry.entry_id} entry={entry} />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {(page > 1 || more) && (
                <nav className="flex items-center justify-between gap-2" aria-label={A.caption}>
                  <Button disabled={page <= 1} onClick={() => goPage(page - 1)}>
                    {A.newer}
                  </Button>
                  <Button disabled={!more} onClick={() => goPage(page + 1)}>
                    {A.older}
                  </Button>
                </nav>
              )}
            </div>
          );
        }}
      </Loadable>
    </ManagerPage>
  );
}
