import clsx from 'clsx';
import type { ReactNode } from 'react';
import { Link, useInRouterContext } from 'react-router';
import type { AuditEntry, Metric } from '@/api/models';
import { QuadrantBadge } from '@/components/decision';
import { DateText, Figure } from '@/components/provenance';
import { MESSAGE } from '@/copy/common';
import { isDecisionState, isGenerationState, type DecisionAuditState, type GenerationAuditState } from './audit';
import { RECORDS } from './copy';
import { leverText } from './levers';

const D = RECORDS.diff;

interface AuditDiffProps {
  before: AuditEntry['before'];
  after: AuditEntry['after'];
  /** The decision record, where the scored values carry their provenance. */
  recordHref?: string | null;
}

function None() {
  return (
    <span className="text-ink-3">
      <span aria-hidden="true">{D.none}</span>
      <span className="sr-only">{D.noneSr}</span>
    </span>
  );
}

function Levers({ ids, other, mark }: { ids: string[]; other: string[]; mark: 'added' | 'removed' }) {
  if (ids.length === 0) return <span className="text-ink-3">{D.noLevers}</span>;
  return (
    <ul className="flex flex-col gap-0.5">
      {ids.map((id) => {
        const lever = leverText(id);
        const differs = !other.includes(id);
        return (
          <li key={id} className="flex flex-wrap items-baseline gap-1">
            <span data-constant className={clsx(mark === 'removed' && differs && 'line-through decoration-ink-3')}>
              {lever.label}
            </span>
            {differs && <span className="text-micro uppercase text-ink-3">{mark === 'added' ? D.changed : ''}</span>}
          </li>
        );
      })}
    </ul>
  );
}

function riskWord(before: number | null, after: number | null): string | null {
  if (before === null || after === null) return null;
  if (after > before) return D.riskUp;
  if (after < before) return D.riskDown;
  return D.riskSame;
}

interface Row {
  key: string;
  label: string;
  before: ReactNode;
  after: ReactNode;
  changed: boolean;
}

function decisionRows(b: DecisionAuditState | null, a: DecisionAuditState): Row[] {
  const text = (v: string | null | undefined) => (v ? v : <None />);
  const flag = (v: boolean | undefined) => (v === undefined ? <None /> : v ? D.yes : D.no);
  const date = (v: string | null | undefined) => (v ? <DateText value={v} /> : <None />);
  const quadrant = (q: DecisionAuditState['quadrant'] | undefined) => (q ? <QuadrantBadge quadrant={q} /> : <None />);
  const id = (v: string | null | undefined) =>
    v ? (
      <span className="font-mono" data-id>
        {v}
      </span>
    ) : (
      <None />
    );
  // The risk is a Metric since contract 1.2.0: a Figure with its own provenance, and its direction.
  const word = riskWord(b?.risk?.value ?? null, a.risk?.value ?? null);
  const risk = (m: DecisionAuditState['risk'] | undefined) => (m ? <Figure metric={m} label={D.risk} /> : D.riskNotScored);
  const sameLevers = b !== null && b.lever_ids.length === a.lever_ids.length && b.lever_ids.every((x) => a.lever_ids.includes(x));
  return [
    { key: 'version', label: D.version, before: id(b?.decision_id), after: id(a.decision_id), changed: b?.decision_id !== a.decision_id },
    { key: 'state', label: D.state, before: text(b ? RECORDS.state[b.state] : null), after: text(RECORDS.state[a.state]), changed: b?.state !== a.state },
    { key: 'quadrant', label: D.quadrant, before: quadrant(b?.quadrant), after: quadrant(a.quadrant), changed: b?.quadrant !== a.quadrant },
    {
      key: 'risk',
      label: D.risk,
      before: b ? risk(b.risk) : <None />,
      after: (
        <span className="inline-flex flex-wrap items-baseline gap-1">
          {risk(a.risk)}
          {a.risk && word && <span className="text-caption text-ink-3">{word}</span>}
        </span>
      ),
      changed: (b?.risk?.value ?? null) !== (a.risk?.value ?? null),
    },
    {
      key: 'levers',
      label: D.levers,
      before: b ? <Levers ids={b.lever_ids} other={a.lever_ids} mark="removed" /> : <None />,
      after: <Levers ids={a.lever_ids} other={b?.lever_ids ?? []} mark="added" />,
      changed: !sameLevers,
    },
    { key: 'approval', label: D.approval, before: flag(b?.requires_approval), after: flag(a.requires_approval), changed: b?.requires_approval !== a.requires_approval },
    { key: 'review', label: D.reviewAt, before: date(b?.review_at), after: date(a.review_at), changed: (b?.review_at ?? null) !== a.review_at },
    { key: 'approver', label: D.approver, before: text(b?.approver), after: text(a.approver), changed: (b?.approver ?? null) !== a.approver },
    {
      key: 'category',
      label: D.category,
      before: text(b?.override_category ? RECORDS.category[b.override_category] : null),
      after: text(a.override_category ? RECORDS.category[a.override_category] : null),
      changed: (b?.override_category ?? null) !== a.override_category,
    },
    {
      key: 'coupon',
      label: D.coupon,
      before: id(b?.coupon_application_id),
      after: id(a.coupon_application_id),
      changed: (b?.coupon_application_id ?? null) !== a.coupon_application_id,
    },
  ];
}

/**
 * How a generated text was produced (GenerationAuditState): its path, why it fell back to the
 * template, the model calls and the validator codes, where it was served from. The count of model
 * calls is shown as the generation badges show it (a named count of the record, not a figure).
 */
function generationRows(b: GenerationAuditState | null, a: GenerationAuditState): Row[] {
  const g = MESSAGE.generation;
  const id = (v: string | undefined) =>
    v ? (
      <span className="font-mono" data-id>
        {v}
      </span>
    ) : (
      <None />
    );
  const codes = (s: GenerationAuditState | null) =>
    !s ? (
      <None />
    ) : s.validator_codes.length === 0 ? (
      <span className="text-ink-3">{D.noCodes}</span>
    ) : (
      <span className="font-mono text-caption" data-id>
        {s.validator_codes.join(', ')}
      </span>
    );
  const text = (v: string | null | undefined) => (v ? v : <None />);
  const calls = (s: GenerationAuditState | null) => (s ? <span data-constant>{g.modelCalls(s.model_calls)}</span> : <None />);
  return [
    { key: 'generation', label: D.generation, before: id(b?.generation_id), after: id(a.generation_id), changed: b?.generation_id !== a.generation_id },
    { key: 'kind', label: D.kind, before: text(b ? D.kinds[b.kind] : null), after: text(D.kinds[a.kind]), changed: b?.kind !== a.kind },
    { key: 'decision', label: D.decision, before: id(b?.decision_id), after: id(a.decision_id), changed: b?.decision_id !== a.decision_id },
    {
      key: 'path',
      label: D.path,
      before: text(b ? g[b.generation_path] : null),
      after: text(g[a.generation_path]),
      changed: b?.generation_path !== a.generation_path,
    },
    {
      key: 'fallback',
      label: D.fallback,
      before: text(b?.fallback_reason ? g.fallback[b.fallback_reason] : null),
      after: text(a.fallback_reason ? g.fallback[a.fallback_reason] : null),
      changed: (b?.fallback_reason ?? null) !== a.fallback_reason,
    },
    { key: 'model_calls', label: D.modelCalls, before: calls(b), after: calls(a), changed: b?.model_calls !== a.model_calls },
    {
      key: 'validator_codes',
      label: D.validatorCodes,
      before: codes(b),
      after: codes(a),
      changed: (b?.validator_codes ?? []).join() !== a.validator_codes.join(),
    },
    { key: 'llm_mode', label: D.llmMode, before: text(b ? D.llmModes[b.llm_mode] : null), after: text(D.llmModes[a.llm_mode]), changed: b?.llm_mode !== a.llm_mode },
    {
      key: 'served_from',
      label: D.servedFrom,
      before: text(b ? D.served[b.served_from] : null),
      after: text(D.served[a.served_from]),
      changed: b?.served_from !== a.served_from,
    },
    {
      key: 'model',
      label: D.model,
      before: b ? <span data-id>{b.model}</span> : <None />,
      after: <span data-id>{a.model}</span>,
      changed: b?.model !== a.model,
    },
  ];
}

function DiffTable({ rows }: { rows: Row[] }) {
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full border-collapse text-dense">
        <thead>
          <tr className="border-b border-border-strong text-left text-caption text-ink-2">
            <th scope="col" className="px-2 py-1 font-medium">
              {D.field}
            </th>
            <th scope="col" className="px-2 py-1 font-medium">
              {D.before}
            </th>
            <th scope="col" className="px-2 py-1 font-medium">
              {D.after}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-b border-border align-top" data-diff-row={row.key} data-changed={row.changed || undefined}>
              <th scope="row" className="px-2 py-1 text-left font-normal text-ink-2">
                {row.label}
                {row.changed && <span className="ml-1 rounded-chip bg-surface-2 px-1 text-micro uppercase text-ink-1">{D.changed}</span>}
              </th>
              <td className="px-2 py-1">{row.before}</td>
              <td className="px-2 py-1">{row.after}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A Metric of a payload (an inspection's compensation): a value with its unit and provenance. */
function isMetric(value: unknown): value is Metric {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return typeof v.value === 'number' && typeof v.unit === 'string' && Boolean(v.provenance) && typeof v.provenance === 'object';
}

/**
 * A generic payload as key and value: a Metric as a Figure with its provenance; a plain number
 * stays in its record, where it has one.
 */
function Payload({ value }: { value: AuditEntry['before'] }) {
  if (value === null || typeof value !== 'object') return <span className="text-ink-3">{D.empty}</span>;
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="text-ink-3">{D.empty}</span>;
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
      {entries.map(([key, v]) => (
        <div key={key} className="contents">
          <dt className="font-mono text-caption text-ink-3">{key.replace(/_/g, ' ')}</dt>
          <dd className="min-w-0 break-words">
            {isMetric(v) ? (
              <Figure metric={v} label={key.replace(/_/g, ' ')} />
            ) : typeof v === 'number' ? (
              <span className="text-ink-3">{D.numberHidden}</span>
            ) : typeof v === 'boolean' ? (
              v ? D.yes : D.no
            ) : v === null ? (
              <None />
            ) : (
              <span data-verbatim>{Array.isArray(v) ? v.join(', ') : String(v)}</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * What an audit entry changed (docs/ui-design.md 5.7): for a decision version, the
 * DecisionAuditState before and after with "changed" markers (state, quadrant, risk, levers,
 * approval flag, review date, approver, category, coupon application); for a generated text, its
 * GenerationAuditState; otherwise the payloads. The risk is a Figure with its own provenance and
 * its direction (US-906).
 */
export function AuditDiff({ before, after, recordHref }: AuditDiffProps) {
  const inRouter = useInRouterContext();
  if (isGenerationState(after)) {
    return (
      <div className="flex flex-col gap-2" data-audit-diff="generation">
        <DiffTable rows={generationRows(isGenerationState(before) ? before : null, after)} />
        <p className="text-caption text-ink-3">{D.generationNote}</p>
      </div>
    );
  }
  if (isDecisionState(after)) {
    const rows = decisionRows(isDecisionState(before) ? before : null, after);
    return (
      <div className="flex flex-col gap-2" data-audit-diff="decision">
        <DiffTable rows={rows} />
        <p className="text-caption text-ink-3">
          {D.riskNote}{' '}
          {recordHref && inRouter && (
            <Link to={recordHref} className="text-accent underline underline-offset-2">
              {RECORDS.entity.decision}
            </Link>
          )}
        </p>
      </div>
    );
  }
  return (
    <div className="grid gap-3 sm:grid-cols-2" data-audit-diff="payload">
      <div>
        <p className="text-micro uppercase text-ink-3">{D.before}</p>
        <Payload value={before} />
      </div>
      <div>
        <p className="text-micro uppercase text-ink-3">{D.after}</p>
        <Payload value={after} />
      </div>
    </div>
  );
}
