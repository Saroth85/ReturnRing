import clsx from 'clsx';
import type { NetBenefitView } from '@/api/models';
import { Figure } from '@/components/provenance';
import { ECONOMICS } from './copy';

interface WaterfallProps {
  view: NetBenefitView;
  /** Print the rule-9 footnote under the total (default true). */
  footnote?: boolean;
  className?: string;
}

interface Step {
  id: string;
  label: string;
  amount: NetBenefitView['total'];
  from: number;
  to: number;
  kind: 'increase' | 'decrease' | 'zero' | 'total';
}

function steps(view: NetBenefitView): Step[] {
  let running = 0;
  const out: Step[] = view.components.map((c) => {
    const from = running;
    running += c.amount.value;
    const kind = c.amount.value > 0 ? 'increase' : c.amount.value < 0 ? 'decrease' : 'zero';
    return { id: c.id, label: ECONOMICS.component[c.id], amount: c.amount, from, to: running, kind };
  });
  out.push({ id: 'total', label: ECONOMICS.total, amount: view.total, from: 0, to: view.total.value, kind: 'total' });
  return out;
}

/**
 * The net benefit as a horizontal waterfall (docs/ui-design.md 4.8, 8.2): increases solid
 * `--chart-pos`, decreases hatched and outlined in `--ink-2`, the total in `--ink-1` (hatched when
 * negative, with the words), the zero line always visible, a signed direct label on every row.
 * Polarity reads from direction and fill, never from red and green. Bars move in 300 ms when the
 * view changes (a scenario chip); reduced motion turns that off.
 */
export function Waterfall({ view, footnote = true, className }: WaterfallProps) {
  const rows = steps(view);
  const ends = rows.flatMap((r) => [r.from, r.to]);
  const min = Math.min(0, ...ends);
  const max = Math.max(0, ...ends);
  const span = max - min || 1;
  const pct = (x: number) => `${((x - min) / span) * 100}%`;
  const negative = view.total.value < 0;

  return (
    <figure className={clsx('flex flex-col gap-1', className)} aria-label={ECONOMICS.waterfallLabel} data-waterfall>
      <ol className="flex flex-col">
        {rows.map((row) => {
          const low = Math.min(row.from, row.to);
          const width = Math.abs(row.to - row.from);
          const isTotal = row.kind === 'total';
          return (
            <li
              key={row.id}
              data-component={isTotal ? undefined : row.id}
              data-net-total={isTotal ? '' : undefined}
              className={clsx(
                // The bar keeps a minimum width: in provenance mode the figure column grows with the
                // nature and period and would squeeze the bars to slivers (UI21).
                'grid grid-cols-[minmax(0,9rem)_minmax(4rem,1fr)_auto] items-center gap-x-3 py-1 sm:grid-cols-[minmax(0,12rem)_minmax(6rem,1fr)_auto]',
                isTotal && 'mt-1 border-t border-border-strong pt-2 font-medium',
              )}
            >
              <span className={clsx('text-dense', isTotal ? 'text-ink-1' : 'text-ink-2')}>{row.label}</span>
              <span className="relative h-5" aria-hidden="true">
                <span data-zero-line className="absolute inset-y-[-4px] w-px bg-ink-3" style={{ left: pct(0) }} />
                {row.kind === 'zero' ? (
                  <span data-bar="zero" className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-ink-3" style={{ left: pct(row.from) }} />
                ) : (
                  <span
                    data-bar={row.kind}
                    className={clsx(
                      'absolute top-0.5 h-4 rounded-[2px] transition-[left,width] duration-300 ease-out',
                      row.kind === 'increase' && 'bg-chart-pos',
                      row.kind === 'decrease' && 'hatch-sim border border-ink-2',
                      isTotal && (negative ? 'hatch-sim border-2 border-ink-1' : 'bg-ink-1'),
                    )}
                    style={{ left: pct(low), width: `max(2px, ${(width / span) * 100}%)` }}
                  />
                )}
              </span>
              <span className={clsx('justify-self-end text-dense', isTotal && 'text-body')}>
                <Figure metric={row.amount} label={row.label} signed />
              </span>
              {isTotal && negative && (
                <span className="col-span-3 mt-1 inline-flex items-center gap-2 text-body font-medium text-ink-1" data-negative-total>
                  <span aria-hidden="true" className="hatch-sim inline-block h-3 w-5 border border-ink-1" />
                  {ECONOMICS.negative}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      {footnote && <figcaption className="text-caption text-ink-2">{ECONOMICS.refundNote}</figcaption>}
    </figure>
  );
}
