import clsx from 'clsx';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { LEGAL_MINIMUM_WINDOW_DAYS } from '@/api/contract.gen';
import type { Lever, Terms } from '@/api/models';
import { leverDirection, type Direction } from '@/domain/levers';
import { LEVER_COPY } from './copy';
import { leverLabel } from './labels';

/** Terms are policy values, not measurements: they carry no nature (`data-constant`). */
export function LeverChip({ lever, base }: { lever: Lever; base?: Terms }) {
  const direction: Direction | null = base ? leverDirection(lever, base) : null;
  const legalMinimum = lever.id === 'return_window_days' && lever.value === LEGAL_MINIMUM_WINDOW_DAYS;
  return (
    <span
      data-lever={`${lever.id}:${String(lever.value)}`}
      data-direction={direction ?? undefined}
      className={clsx(
        'inline-flex flex-wrap items-center gap-x-1 rounded-chip border px-1.5 py-0.5 text-dense',
        direction === 'less' ? 'border-attn-border' : 'border-border',
      )}
    >
      {direction === 'less' && <ArrowDown aria-hidden="true" size={12} className="text-attn-fg" />}
      {direction === 'more' && <ArrowUp aria-hidden="true" size={12} className="text-ink-2" />}
      <span data-constant className="text-ink-1">
        {leverLabel(lever)}
      </span>
      {legalMinimum && <span className="text-ink-3">({LEVER_COPY.legalMinimum})</span>}
      <span className="text-micro uppercase text-ink-3">· {LEVER_COPY.scope[lever.scope]}</span>
      {direction === 'less' && <span className="text-micro uppercase text-attn-fg">{LEVER_COPY.restrictive}</span>}
      {direction === 'more' && <span className="sr-only">{LEVER_COPY.moreGenerous}</span>}
    </span>
  );
}

/** The levers of a proposal or an alternative; "No change to terms" when there are none. */
export function LeverList({ levers, base, className }: { levers: readonly Lever[]; base?: Terms; className?: string }) {
  if (levers.length === 0) {
    return (
      <p className={clsx('text-body text-ink-2', className)} data-lever-list="empty">
        {LEVER_COPY.noLevers}
      </p>
    );
  }
  return (
    <ul aria-label={LEVER_COPY.listLabel} className={clsx('flex flex-wrap gap-1', className)} data-lever-list>
      {levers.map((lever) => (
        <li key={`${lever.id}:${String(lever.value)}`}>
          <LeverChip lever={lever} base={base} />
        </li>
      ))}
    </ul>
  );
}
