// Product issues /products (?status=; US-208, US-606, docs/ui-design.md 8.5): anomalies by variant,
// reason and week, observed against expected, separate from any hypothesis about customers.
import clsx from 'clsx';
import { Link, useSearchParams } from 'react-router';
import type { ProductAnomaliesResponse } from '@/api/models';
import { useAnomalies } from '@/api/queries';
import { Sparkline } from '@/components/charts';
import { DateText, Figure, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { MANAGER } from '../copy';
import type { ProductAnomaly } from '../types';
import { Loadable, ManagerPage } from '../ui';
import { AnomalyStatus, ProductTabs } from './parts';

const PR = MANAGER.products;
const STATUSES: ProductAnomaly['status'][] = ['open', 'task_opened', 'resolved'];
const isStatus = (s: string | null): s is ProductAnomaly['status'] => s !== null && (STATUSES as string[]).includes(s);

function Thresholds({ data }: { data: ProductAnomaliesResponse }) {
  return (
    <p className="text-caption text-ink-2" data-anomaly-thresholds>
      {PR.flaggedLead} <PlainNumber value={data.thresholds.z} unit="score" /> {PR.flaggedMid}{' '}
      <PlainNumber value={data.thresholds.min_sales} unit="count" /> {PR.flaggedTail}
    </p>
  );
}

function Products({ data }: { data: ProductAnomaliesResponse }) {
  const [params, setParams] = useSearchParams();
  const status = params.get('status');
  const filter = isStatus(status) ? status : null;
  const items = filter ? data.items.filter((a) => a.status === filter) : data.items;
  const c = PR.cols;
  const chip = (value: ProductAnomaly['status'] | null, label: string) => (
    <button
      key={value ?? 'all'}
      type="button"
      aria-pressed={filter === value}
      onClick={() => setParams(value ? { status: value } : {}, { replace: true })}
      className={clsx(
        'min-h-8 rounded-full border px-3 text-dense',
        filter === value ? 'border-accent bg-accent-bg font-medium text-ink-1' : 'border-border-strong bg-surface text-ink-1 hover:bg-surface-2',
      )}
    >
      {label}
    </button>
  );
  return (
    <ManagerPage screen="products" title={MANAGER.titles.products} intro={<p>{PR.intro}</p>}>
      <ProductTabs />
      <ProvenanceScope provenance={data.provenance} covers="the anomaly thresholds and the weekly trends of the variants" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Thresholds data={data} />
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label={PR.filter}>
            {chip(null, PR.all)}
            {STATUSES.map((s) => chip(s, PR.status[s]))}
          </div>
        </div>
        {items.length === 0 ? (
          <p className="rounded-card border border-dashed border-border-strong p-6 text-center text-ink-2">{PR.empty}</p>
        ) : (
          <div className="relative overflow-x-auto rounded-card border border-border bg-surface">
            <table className="w-full min-w-[60rem] border-collapse text-dense" data-anomalies>
              <caption className="sr-only">{PR.caption}</caption>
              <thead className="bg-surface-2 text-left text-caption text-ink-2">
                <tr>
                  {[c.variant, c.reason, c.week, c.observed, c.z, c.sales, c.status, c.returns, c.trend].map((h) => (
                    <th key={h} scope="col" className="px-3 py-2 font-medium whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((a) => (
                  <tr key={a.anomaly_id} className="border-t border-border align-top" data-anomaly={a.anomaly_id}>
                    <td className="px-3 py-2">
                      <Link to={`/products/${encodeURIComponent(a.anomaly_id)}`} className="font-mono text-accent underline-offset-2 hover:underline" data-id>
                        {a.variant_id}
                      </Link>
                      <p className="text-caption text-ink-3">
                        <span data-id>{a.product_id}</span> · {a.category}
                        {a.brand ? ` · ${a.brand}` : ''}
                      </p>
                    </td>
                    <td className="px-3 py-2">{PR.reason[a.reason] ?? a.reason}</td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <DateText value={a.week} />
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <Figure metric={a.observed} label={PR.detail.observed} /> <span className="text-ink-3">/</span>{' '}
                      <Figure metric={a.expected} label={PR.detail.expected} />
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <Figure metric={a.z} label={PR.detail.z} />
                    </td>
                    <td className="px-3 py-2">
                      <Figure metric={a.sales} label={PR.detail.sales} />
                    </td>
                    <td className="px-3 py-2">
                      <AnomalyStatus anomaly={a} />
                    </td>
                    <td className="px-3 py-2">
                      <ul className="flex flex-col gap-0.5">
                        {a.return_ids.map((id) => (
                          <li key={id}>
                            <Link to={`/returns/${encodeURIComponent(id)}?focus=product`} className="font-mono text-caption text-accent underline-offset-2 hover:underline" data-id>
                              {id}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    </td>
                    <td className="px-3 py-2">
                      {a.trend.length > 0 ? (
                        <Sparkline
                          values={a.trend.map((w) => w.returns)}
                          label={PR.trendLabel(a.variant_id)}
                          highlight={Math.max(0, a.trend.findIndex((w) => w.week === a.week))}
                        />
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <ProvenanceFooter />
      </ProvenanceScope>
    </ManagerPage>
  );
}

export default function ProductsPage() {
  const anomalies = useAnomalies();
  if (!anomalies.data) {
    return (
      <ManagerPage screen="products" title={MANAGER.titles.products}>
        <ProductTabs />
        <Loadable query={anomalies} label="GET /api/products/anomalies" rows={5}>
          {() => null}
        </Loadable>
      </ManagerPage>
    );
  }
  return <Products data={anomalies.data} />;
}
