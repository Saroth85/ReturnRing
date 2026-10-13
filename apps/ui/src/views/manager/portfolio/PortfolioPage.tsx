// Portfolio matrix /portfolio (?quadrant=, ?cluster=, ?cover=1; US-601, docs/ui-design.md 8.1).
// Stub: the quadrant totals and the legend; the canvas matrix comes next.
import { useMatrix } from '@/api/queries';
import { QuadrantBadge } from '@/components/decision';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { Figure } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function PortfolioPage() {
  const matrix = useMatrix();
  const data = matrix.data;
  const thresholds = data ? { risk: data.thresholds.risk, value: data.thresholds.value_eur } : undefined;
  return (
    <ScreenPlaceholder title={MANAGER.titles.portfolio} area="manager" stories={['US-601']} spec="docs/ui-design.md 8.1">
      <DataCheck label="GET /api/portfolio/matrix" result={matrix} />
      {data && (
        <>
          <ul className="grid gap-3 sm:grid-cols-2" data-quadrant-tiles>
            {data.quadrants.map((q) => (
              <li key={q.quadrant} className="flex items-center gap-3 rounded-card border border-border bg-surface p-3">
                <QuadrantBadge quadrant={q.quadrant} thresholds={thresholds} />
                <Figure metric={q.customers} label={`${q.quadrant} customers`} />
              </li>
            ))}
          </ul>
          <p className="flex flex-wrap items-center gap-2 text-body">
            {MANAGER.portfolio.insufficient}: <Figure metric={data.insufficient_evidence} />
          </p>
          <p className="max-w-prose border-l-2 border-border pl-2 text-caption text-ink-2" data-verbatim>
            {data.legend}
          </p>
        </>
      )}
    </ScreenPlaceholder>
  );
}
