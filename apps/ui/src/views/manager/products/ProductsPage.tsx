// Product issues /products (US-208, US-606; docs/ui-design.md 8.5). Stub: the anomalies with their
// z-score, each linking to its detail.
import { Link } from 'react-router';
import { useAnomalies } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { Figure } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function ProductsPage() {
  const anomalies = useAnomalies();
  return (
    <ScreenPlaceholder title={MANAGER.titles.products} area="manager" stories={['US-208', 'US-606']} spec="docs/ui-design.md 8.5">
      <DataCheck label="GET /api/products/anomalies" result={anomalies} />
      <p>
        <Link to="/products/tasks" className="text-accent underline underline-offset-2">
          {MANAGER.titles.tasks}
        </Link>
      </p>
      {anomalies.data && (
        <ul className="divide-y divide-border rounded-card border border-border bg-surface">
          {anomalies.data.items.map((a) => (
            <li key={a.anomaly_id} className="flex flex-wrap items-center gap-3 px-3 py-2">
              <Link to={`/products/${encodeURIComponent(a.anomaly_id)}`} className="font-mono text-accent underline-offset-2 hover:underline" data-id>
                {a.anomaly_id}
              </Link>
              <Figure metric={a.z} label="z" />
            </li>
          ))}
        </ul>
      )}
    </ScreenPlaceholder>
  );
}
