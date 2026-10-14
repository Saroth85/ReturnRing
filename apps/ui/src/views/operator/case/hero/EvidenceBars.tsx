// Evidence quality as three threshold bars (docs/ui-design.md 6.2.3 item 1, US-209, US-307):
// observations, identifier completeness and reason consistency, each with "meets threshold" or
// "below threshold" in words. Neutral ink; a dashed bar when below.
import clsx from 'clsx';
import type { CaseCard } from '@/api/models';
import { Figure } from '@/components/provenance';
import { OPERATOR } from '../../copy';

type Component = CaseCard['evidence']['components'][number];
const HEADROOM = 1.25;

function scaleMax(c: Component): number {
  if (c.value.unit === 'ratio' || c.value.unit === 'probability') return 1;
  return Math.max(c.value.value, c.threshold.value) * HEADROOM || 1;
}

export function EvidenceBars({ components, compact = false }: { components: Component[]; compact?: boolean }) {
  const t = OPERATOR.evidence;
  const h = OPERATOR.hero;
  return (
    <ul className={clsx('grid gap-3', compact ? 'sm:grid-cols-3' : 'grid-cols-1')} data-evidence-bars>
      {components.map((c) => {
        const max = scaleMax(c);
        const value = Math.min(1, c.value.value / max);
        const threshold = Math.min(1, c.threshold.value / max);
        const label = t.components[c.id] ?? c.id;
        return (
          <li key={c.id} className="flex flex-col gap-1" data-evidence-component={c.id} data-passed={c.passed}>
            <span className="flex flex-wrap items-baseline justify-between gap-x-2 text-dense">
              <span className="text-ink-2">{label}</span>
              <Figure metric={c.value} compareTo={c.threshold} label={label} />
            </span>
            <span className="relative block h-2 rounded-full bg-surface-2" aria-hidden="true">
              <span
                className={clsx(
                  'absolute inset-y-0 left-0 rounded-full',
                  c.passed ? 'bg-ink-2' : 'border border-dashed border-ink-2 bg-transparent',
                )}
                style={{ width: `${value * 100}%` }}
              />
              <span className="absolute -top-1 h-4 w-0.5 bg-ink-1" style={{ left: `${threshold * 100}%` }} />
            </span>
            <span className={clsx('text-caption', c.passed ? 'text-ink-3' : 'font-medium text-ink-1')}>
              {c.passed ? h.meetsThreshold : h.belowThreshold}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
