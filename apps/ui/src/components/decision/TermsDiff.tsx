import clsx from 'clsx';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { LEGAL_MINIMUM_WINDOW_DAYS } from '@/api/contract.gen';
import type { Lever, Terms } from '@/api/models';
import { changedRows, termsRows, type TermRow } from '@/domain/decision';
import { LEVER_COPY, TERMS_COPY } from './copy';
import { termValue } from './labels';

export interface TermsDiffProps {
  /** The terms stored with this order (decision.current_return_terms). */
  current: Terms;
  future: Terms;
  levers: readonly Lever[];
  /** The order's terms snapshot id, shown in the column header. */
  snapshotId?: string;
  /** How many linked accounts a cluster coupon pause covers. */
  clusterSize?: number;
  /** Only the rows that change (the approval sheet). */
  onlyChanges?: boolean;
  className?: string;
}

function Change({ row }: { row: TermRow }) {
  if (row.change === 'less') {
    return (
      <span className="inline-flex items-center gap-1 font-medium text-attn-fg" data-change="less">
        <ArrowDown aria-hidden="true" size={14} />
        {TERMS_COPY.change.less}
      </span>
    );
  }
  if (row.change === 'more') {
    return (
      <span className="inline-flex items-center gap-1 text-ink-1" data-change="more">
        <ArrowUp aria-hidden="true" size={14} />
        {TERMS_COPY.change.more}
      </span>
    );
  }
  return (
    <span className="text-ink-3" data-change="same">
      {TERMS_COPY.change.same}
    </span>
  );
}

/**
 * This order against future orders (docs/ui-design.md 5.3): the order keeps the terms shown at
 * checkout; a restriction applies from the next order. A 14-day window carries "legal minimum".
 * Terms are policy values, not measurements (`data-constant`).
 */
export function TermsDiff({ current, future, levers, snapshotId, clusterSize, onlyChanges = false, className }: TermsDiffProps) {
  const all = termsRows(current, future, levers);
  const rows = onlyChanges ? changedRows(all) : all;
  return (
    <div className={clsx('relative overflow-x-auto', className)}>
      <table className="w-full text-dense" data-terms-diff>
        <caption className="sr-only">{TERMS_COPY.caption}</caption>
        <thead className="text-left text-caption text-ink-3">
          <tr>
            <th scope="col" className="py-1 pr-2 font-normal">
              {TERMS_COPY.term}
            </th>
            <th scope="col" className="py-1 pr-2 font-normal">
              {TERMS_COPY.thisOrder}
              {snapshotId && (
                <span className="block text-micro normal-case" data-id>
                  {TERMS_COPY.snapshot} {snapshotId}
                </span>
              )}
            </th>
            <th scope="col" className="py-1 pr-2 font-normal">
              {TERMS_COPY.futureOrders}
            </th>
            <th scope="col" className="py-1 font-normal">
              {TERMS_COPY.changeHeader}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.lever} className="border-t border-border align-top" data-term-row={row.lever} data-change={row.change}>
              <th scope="row" className="py-1 pr-2 text-left font-normal text-ink-2">
                {TERMS_COPY.row[row.lever]}
              </th>
              <td className="py-1 pr-2" data-constant>
                {termValue(row.lever, row.current)}
              </td>
              <td className={clsx('py-1 pr-2', row.change === 'less' && 'font-medium')} data-constant>
                {termValue(row.lever, row.future, clusterSize)}
                {row.lever === 'return_window_days' && row.future === LEGAL_MINIMUM_WINDOW_DAYS && (
                  <span className="ml-1 text-caption text-ink-3">({LEVER_COPY.legalMinimum})</span>
                )}
              </td>
              <td className="py-1">
                <Change row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
