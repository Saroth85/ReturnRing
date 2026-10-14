import clsx from 'clsx';
import type { NetBenefitView } from '@/api/models';
import { Figure } from '@/components/provenance';
import { ECONOMICS } from './copy';
import { Waterfall } from './Waterfall';

interface NetBenefitBreakdownProps {
  view: NetBenefitView;
  variant?: 'list' | 'waterfall';
  className?: string;
}

/**
 * The eight disjoint components of a net benefit in contract order, each a signed Figure, then the
 * total and the fixed footnote of rule 9 (docs/ui-design.md 5.6).
 */
export function NetBenefitBreakdown({ view, variant = 'list', className }: NetBenefitBreakdownProps) {
  if (variant === 'waterfall') return <Waterfall view={view} className={className} />;
  return (
    <div className={clsx('flex flex-col gap-1', className)} data-net-benefit-breakdown>
      <ul className="flex flex-col divide-y divide-border">
        {view.components.map((c) => (
          <li key={c.id} data-component={c.id} className="flex items-baseline justify-between gap-3 py-1 text-dense">
            <span className="text-ink-2">{ECONOMICS.component[c.id]}</span>
            <Figure metric={c.amount} label={ECONOMICS.component[c.id]} signed />
          </li>
        ))}
        <li data-net-total className="flex items-baseline justify-between gap-3 border-t border-border-strong py-1.5 font-medium">
          <span>{ECONOMICS.total}</span>
          <Figure metric={view.total} label={ECONOMICS.total} signed />
        </li>
      </ul>
      {view.total.value < 0 && <p className="text-body font-medium">{ECONOMICS.negative}</p>}
      <p className="text-caption text-ink-2">{ECONOMICS.refundNote}</p>
    </div>
  );
}
