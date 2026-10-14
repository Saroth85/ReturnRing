import clsx from 'clsx';
import type { ReactNode } from 'react';
import type { Metric } from '@/api/models';
import { Figure } from '@/components/provenance';

export interface DotPlotRow {
  key: string;
  label: ReactNode;
  value: Metric;
  /** How many customers the value rests on. */
  count?: Metric;
}

interface DotPlotProps {
  rows: DotPlotRow[];
  /** What the dots are ("False-positive rate"), for the figures and the column header. */
  valueLabel: string;
  countLabel?: string;
  /** Upper end of the shared axis (default: the largest value plus a quarter). */
  domainMax?: number;
  /** A bracket from the lowest to the highest dot, labelled (the gap of a fairness dimension). */
  bracket?: { label: ReactNode; text: string; attention?: boolean };
  className?: string;
}

/**
 * Dots on one shared axis, one row per segment (docs/ui-design.md 5.6, 8.3, 8.4): neutral ink for
 * every dot, so no segment reads as worse by colour; the value and the count are Figures next to
 * the dot. An optional bracket spans the lowest and highest dot.
 */
export function DotPlot({ rows, valueLabel, countLabel, domainMax, bracket, className }: DotPlotProps) {
  const values = rows.map((r) => r.value.value);
  const top = domainMax ?? Math.max(...values, 0) * 1.25;
  const max = top > 0 ? top : 1;
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / max) * 100))}%`;
  const low = Math.min(...values);
  const high = Math.max(...values);
  const grid = countLabel
    ? 'grid-cols-[minmax(0,10rem)_minmax(4rem,1fr)_auto_auto] sm:grid-cols-[minmax(0,13rem)_minmax(6rem,1fr)_auto_auto]'
    : 'grid-cols-[minmax(0,10rem)_minmax(4rem,1fr)_auto] sm:grid-cols-[minmax(0,13rem)_minmax(6rem,1fr)_auto]';
  return (
    <div className={clsx('grid items-center gap-x-3 gap-y-1.5 text-dense', grid, className)} data-dot-plot>
      {rows.map((row) => (
        <div key={row.key} className="contents" data-dot-row={row.key}>
          <span className="min-w-0 text-ink-2">{row.label}</span>
          <span className="relative h-4" aria-hidden="true">
            <span className="absolute inset-x-0 top-1/2 h-px bg-grid" />
            <span
              className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink-2 ring-2 ring-surface"
              style={{ left: pct(row.value.value) }}
              data-dot
            />
          </span>
          <span className="justify-self-end">
            <Figure metric={row.value} label={valueLabel} />
          </span>
          {countLabel && (
            <span className="justify-self-end text-ink-3">
              {row.count && <Figure metric={row.count} label={countLabel} />}
            </span>
          )}
        </div>
      ))}
      {bracket && rows.length > 1 && (
        <div className="contents" data-bracket={bracket.attention ? 'attention' : 'neutral'}>
          <span className="text-caption text-ink-3">{bracket.text}</span>
          <span className="relative h-4" aria-hidden="true">
            <span
              className={clsx(
                'absolute top-1 h-2 border-x-2 border-b-2',
                bracket.attention ? 'border-attn-border' : 'border-ink-3',
              )}
              style={{ left: pct(low), width: `calc(${pct(high)} - ${pct(low)})` }}
            />
          </span>
          <span className={clsx('justify-self-end', countLabel && 'col-span-2')}>{bracket.label}</span>
        </div>
      )}
    </div>
  );
}
