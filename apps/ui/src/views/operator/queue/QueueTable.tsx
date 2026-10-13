// The queue table (docs/ui-design.md 6.1): one row per return, with the consequence rail, the
// outcome, the proposed terms, the status, the three figures and the flags. Risk is never "—"
// alone and never 0 for an unscored case: it reads "not scored".
import clsx from 'clsx';
import { Link2, MessageSquareText, Shirt } from 'lucide-react';
import { useRef } from 'react';
import { Link, useNavigate } from 'react-router';
import type { Metric, QueueItem, QueueResponse } from '@/api/models';
import { ApprovalFlag, leverLabel, OutcomeBadge } from '@/components/decision';
import { DateText, Figure, IdText, NatureMark, displayNature } from '@/components/provenance';
import { Tooltip } from '@/components/ui';
import { queueRestrictionKind } from '@/domain/decision';
import { parseLeverKey } from '@/domain/levers';
import { OPERATOR } from '../copy';
import { ageLabel, changesLabel, otherAccountsLabel, reasonLabel } from '../format';
import type { QueueTab } from '../state/queueParams';

const HOVER_PREFETCH_MS = 150;

type RailKind = 'approval' | 'reviewDue' | 'guarantee' | 'insufficient' | null;

function railKind(item: QueueItem): RailKind {
  if (item.state === 'proposed' && item.requires_approval) return 'approval';
  if (item.review_due) return 'reviewDue';
  if (item.path === 'guarantee') return 'guarantee';
  if (item.evidence_quality === 'insufficient') return 'insufficient';
  return null;
}

const RAIL_STYLE: Record<Exclude<RailKind, null>, string> = {
  approval: 'hatch-attn',
  reviewDue: 'bg-attn-border',
  guarantee: 'bg-ink-2',
  insufficient: 'border-l-2 border-dashed border-ink-3',
};

/** The 6 px left edge: why a row needs attention, in shape and words (never colour alone). */
function ConsequenceRail({ item }: { item: QueueItem }) {
  const kind = railKind(item);
  return (
    <td className="w-2 p-0" data-rail={kind ?? 'none'}>
      <span aria-hidden="true" className={clsx('block h-full min-h-14 w-1.5', kind && RAIL_STYLE[kind])} />
      {kind && <span className="sr-only">{OPERATOR.rail[kind]}</span>}
    </td>
  );
}

function LeverSummary({ item }: { item: QueueItem }) {
  const t = OPERATOR.queue;
  if (item.lever_ids.length === 0) {
    return <span className="text-ink-2">{item.evidence_quality === 'insufficient' ? t.standardTerms : t.noChange}</span>;
  }
  const levers = item.lever_ids.map(parseLeverKey);
  const pausesCoupons = item.lever_ids.includes('cluster_coupons_disabled:true');
  const summary = (
    <span className="text-left">
      <span className="font-medium">{changesLabel(levers.length)}</span>
      {' · '}
      {item.requires_approval ? t.restrictsFuture : t.moreGenerous}
      {pausesCoupons && <span className="block text-ink-2">{t.pausesCoupons}</span>}
    </span>
  );
  return (
    <Tooltip
      content={
        <ul className="list-disc pl-4" data-constant>
          {levers.map((l) => (
            <li key={`${l.id}:${String(l.value)}`}>{leverLabel(l)}</li>
          ))}
        </ul>
      }
    >
      <button type="button" className="rounded-chip text-left hover:bg-surface-2" data-lever-summary>
        {summary}
      </button>
    </Tooltip>
  );
}

function Status({ item }: { item: QueueItem }) {
  const kind = queueRestrictionKind(item);
  const t = OPERATOR.queue;
  if (kind === 'needs_approval') {
    return (
      <span className="flex flex-col items-start gap-1">
        <ApprovalFlag requiresApproval state={item.state} />
        {item.review_at && (
          <span className="text-caption whitespace-nowrap text-ink-2">
            {t.review} <DateText value={item.review_at} />
          </span>
        )}
      </span>
    );
  }
  if (kind === 'lapsed') {
    return (
      <span className="flex flex-col items-start gap-1">
        <span
          className="inline-flex items-center rounded-chip border border-attn-border bg-attn-bg px-1.5 text-dense font-medium text-attn-fg"
          data-review-due
        >
          {OPERATOR.rail.reviewDue}
        </span>
        {item.review_at && (
          <span className="text-caption text-ink-2">
            {t.review} <DateText value={item.review_at} />
          </span>
        )}
      </span>
    );
  }
  return (
    <span className="flex flex-col items-start gap-1">
      <span className="text-ink-1">{t.states[item.state] ?? item.state}</span>
      {kind === 'in_force' && item.review_at && (
        <span className="text-caption text-ink-2">
          {t.review} <DateText value={item.review_at} />
        </span>
      )}
    </span>
  );
}

function Flags({ item }: { item: QueueItem }) {
  const t = OPERATOR.queue;
  return (
    <span className="flex flex-col items-start gap-1 text-caption text-ink-2">
      {item.linked_account_count > 0 && (
        <span className="inline-flex items-center gap-1" data-flag="linked">
          <Link2 aria-hidden="true" size={14} />
          {otherAccountsLabel(item.linked_account_count)}
        </span>
      )}
      {item.variant_anomaly && (
        <span className="inline-flex items-center gap-1" data-flag="product">
          <Shirt aria-hidden="true" size={14} />
          {t.productIssue}
        </span>
      )}
      {item.draft_status === 'pending' && (
        <span className="inline-flex items-center gap-1" data-flag="draft">
          <MessageSquareText aria-hidden="true" size={14} />
          {t.draftPending}
        </span>
      )}
    </span>
  );
}

/** A metric column header with the nature glyph of its figures (MetricColumnHeader). */
function MetricHeader({ label, metric }: { label: string; metric: Metric | null | undefined }) {
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      {metric && <NatureMark nature={displayNature(metric.provenance)} />}
    </span>
  );
}

interface QueueTableProps {
  page: QueueResponse;
  tab: QueueTab;
  selected: number;
  onSelect(index: number): void;
  onPrefetch(returnId: string): void;
  newIds: ReadonlySet<string>;
}

export function QueueTable({ page, tab, selected, onSelect, onPrefetch, newIds }: QueueTableProps) {
  const navigate = useNavigate();
  const hover = useRef<ReturnType<typeof setTimeout> | null>(null);
  const c = OPERATOR.queue.columns;
  const first = page.items[0];
  const href = (item: QueueItem) => `/returns/${item.return_id}?from=${tab}`;
  const riskSample = page.items.find((i) => i.risk)?.risk;
  const valueSample = page.items.find((i) => i.value)?.value;

  return (
    <div className="relative overflow-x-auto rounded-card border border-border bg-surface" data-queue-table>
      <table className="w-full border-collapse text-dense">
        <caption className="sr-only">{OPERATOR.queue.caption}</caption>
        <thead className="bg-surface-2 text-left text-caption text-ink-2">
          <tr>
            <th scope="col" className="w-2 p-0">
              <span className="sr-only">{OPERATOR.queue.railLegend}</span>
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              {c.ret}
            </th>
            <th scope="col" className="hidden px-3 py-2 font-medium lg:table-cell">
              {c.reason}
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              {c.outcome}
            </th>
            <th scope="col" className="hidden px-3 py-2 font-medium md:table-cell">
              {c.terms}
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              {c.status}
            </th>
            <th scope="col" className="hidden px-3 py-2 text-right font-medium sm:table-cell">
              <MetricHeader label={c.risk} metric={riskSample} />
            </th>
            <th scope="col" className="hidden px-3 py-2 text-right font-medium md:table-cell">
              <MetricHeader label={c.value} metric={valueSample} />
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              <MetricHeader label={c.netBenefit} metric={first?.expected_net_benefit} />
            </th>
            <th scope="col" className="hidden px-3 py-2 font-medium xl:table-cell">
              {c.flags}
            </th>
          </tr>
        </thead>
        <tbody>
          {page.items.map((item, index) => (
            // The row link is the keyboard path; a click anywhere on the row is a mouse shortcut.
            <tr
              key={item.return_id}
              data-return-row={item.return_id}
              aria-selected={index === selected}
              onClick={(event) => {
                if ((event.target as HTMLElement).closest('a,button,input,select')) return;
                onSelect(index);
                void navigate(href(item));
              }}
              onPointerEnter={() => {
                if (hover.current) clearTimeout(hover.current);
                hover.current = setTimeout(() => onPrefetch(item.return_id), HOVER_PREFETCH_MS);
              }}
              onPointerLeave={() => {
                if (hover.current) clearTimeout(hover.current);
              }}
              className={clsx('cursor-pointer border-t border-border align-top hover:bg-surface-2', index === selected && 'bg-accent-bg')}
            >
              <ConsequenceRail item={item} />
              <td className="px-3 py-2">
                <div className="flex flex-wrap items-center gap-x-2">
                  <Link
                    to={href(item)}
                    data-id
                    data-row-link={index}
                    onFocus={() => onSelect(index)}
                    className="font-mono text-dense font-medium whitespace-nowrap text-accent underline-offset-2 hover:underline"
                  >
                    {item.return_id}
                  </Link>
                  <span className="text-caption text-ink-3">
                    <DateText value={item.requested_at.slice(0, 10)} />
                  </span>
                  {newIds.has(item.return_id) && (
                    <span className="rounded-full bg-accent-bg px-1.5 text-micro uppercase text-accent">{OPERATOR.queue.newRow}</span>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-x-2 text-ink-2">
                  <span>{item.display_name}</span>
                  <IdText id={item.customer_id} className="text-caption whitespace-nowrap text-ink-3" />
                </div>
              </td>
              <td className="hidden px-3 py-2 lg:table-cell">
                <div>{reasonLabel(item.reason)}</div>
                <div className="text-caption text-ink-3" data-date>
                  {ageLabel(item.requested_at, page.as_of)}
                </div>
              </td>
              <td className="px-3 py-2">
                <OutcomeBadge path={item.path} evidenceQuality={item.evidence_quality} quadrant={item.quadrant} />
              </td>
              <td className="hidden max-w-56 px-3 py-2 md:table-cell">
                <LeverSummary item={item} />
              </td>
              <td className="px-3 py-2">
                <Status item={item} />
              </td>
              <td className="hidden px-3 py-2 text-right whitespace-nowrap sm:table-cell">
                {item.risk ? (
                  <Figure metric={item.risk} label={c.risk} />
                ) : (
                  <span className="text-ink-3" title={OPERATOR.queue.notScoredHint}>
                    {OPERATOR.queue.notScored}
                  </span>
                )}
              </td>
              <td className="hidden px-3 py-2 text-right whitespace-nowrap md:table-cell">
                {item.value && <Figure metric={item.value} label={c.value} />}
              </td>
              <td className="px-3 py-2 text-right whitespace-nowrap">
                <Figure metric={item.expected_net_benefit} label={c.netBenefit} signed />
              </td>
              <td className="hidden px-3 py-2 xl:table-cell">
                <Flags item={item} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
