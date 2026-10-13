import { useQueryClient } from '@tanstack/react-query';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { api } from '@/api/client';
import type { PortfolioMatrix } from '@/api/models';
import { queryKeys } from '@/api/queries';
import { IdText, Money, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import type { CustomerSort } from '../constants';
import { MANAGER } from '../copy';

type MatrixPoint = PortfolioMatrix['points'][number];

const L = MANAGER.portfolio.list;

/** "Open case": look up an open return of the customer, then go to its card (open question O-12). */
function OpenCaseButton({ customerId }: { customerId: string }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [state, setState] = useState<'idle' | 'looking' | 'none'>('idle');
  const open = async () => {
    setState('looking');
    try {
      const query = { q: customerId };
      const page = await client.fetchQuery({ queryKey: queryKeys.queue(query), queryFn: ({ signal }) => api.listReturns(query, { signal }) });
      const hit = page.items.find((item) => item.customer_id === customerId);
      if (hit) navigate(`/returns/${encodeURIComponent(hit.return_id)}?focus=risk`);
      else setState('none');
    } catch {
      setState('none');
    }
  };
  if (state === 'none') return <span className="text-caption text-ink-3">{L.noOpenReturn}</span>;
  return (
    <Button variant="quiet" className="min-h-7 px-2 text-caption" onClick={() => void open()} disabled={state === 'looking'}>
      {state === 'looking' ? L.looking : L.openCase}
    </Button>
  );
}

const SORTERS: Record<CustomerSort, (a: MatrixPoint, b: MatrixPoint) => number> = {
  name: (a, b) => (a.display_name ?? '').localeCompare(b.display_name ?? '') || a.customer_id.localeCompare(b.customer_id),
  risk: (a, b) => (a.risk ?? -1) - (b.risk ?? -1),
  value: (a, b) => a.value_eur - b.value_eur,
  returns: (a, b) => a.returns_12m - b.returns_12m,
  coupons: (a, b) => a.welcome_coupons - b.welcome_coupons,
};

interface CustomerListProps {
  points: MatrixPoint[];
  provenance: PortfolioMatrix['points_provenance'];
  /** Without risk: the not-placed list (not enough evidence). */
  showRisk?: boolean;
  defaultSort?: CustomerSort;
}

/**
 * Customers of a quadrant (or not placed), sortable (docs/ui-design.md 8.1 QuadrantCustomerList):
 * name, id, risk, value, cluster, returns, coupons, and "Open case". Numbers are attributes of the
 * matrix points, covered by the points' provenance.
 */
export function CustomerList({ points, provenance, showRisk = true, defaultSort = 'risk' }: CustomerListProps) {
  const [sort, setSort] = useState<{ key: CustomerSort; desc: boolean }>({ key: defaultSort, desc: defaultSort !== 'name' });
  const sorted = useMemo(() => {
    const out = [...points].sort(SORTERS[sort.key]);
    return sort.desc ? out.reverse() : out;
  }, [points, sort]);

  const header = (key: CustomerSort, label: string, align: 'left' | 'right' = 'right') => {
    const active = sort.key === key;
    return (
      <th scope="col" aria-sort={active ? (sort.desc ? 'descending' : 'ascending') : 'none'} className={align === 'left' ? 'px-2 py-1.5 text-left font-medium' : 'px-2 py-1.5 text-right font-medium'}>
        <button
          type="button"
          className="inline-flex items-center gap-1 hover:text-ink-1"
          aria-label={L.sortBy(label)}
          onClick={() => setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== 'name' }))}
        >
          {label}
          {active && (sort.desc ? <ArrowDown aria-hidden="true" size={12} /> : <ArrowUp aria-hidden="true" size={12} />)}
        </button>
      </th>
    );
  };

  return (
    <ProvenanceScope provenance={provenance} covers="the matrix points: risk, value, cluster, returns and coupons">
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-dense tabular-nums" data-customer-list>
          <thead>
            <tr className="border-b border-border-strong text-caption text-ink-2">
              {header('name', L.name, 'left')}
              {showRisk && header('risk', L.risk)}
              {header('value', L.value)}
              <th scope="col" className="px-2 py-1.5 text-left font-medium">
                {L.cluster}
              </th>
              {header('returns', L.returns)}
              {header('coupons', L.coupons)}
              <th scope="col" className="px-2 py-1.5">
                <span className="sr-only">{L.openCase}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => (
              <tr key={p.customer_id} className="border-b border-border align-top" data-customer={p.customer_id}>
                <td className="px-2 py-1.5">
                  <span className="flex flex-col items-start">
                    <span>{p.display_name ?? L.unnamed}</span>
                    <IdText id={p.customer_id} className="text-caption text-ink-3" />
                  </span>
                </td>
                {showRisk && <td className="px-2 py-1.5 text-right">{p.risk !== null && <PlainNumber value={p.risk} unit="probability" />}</td>}
                <td className="px-2 py-1.5 text-right">
                  <Money value={p.value_eur} />
                </td>
                <td className="px-2 py-1.5 whitespace-nowrap">{p.cluster_id ? <IdText id={p.cluster_id} /> : <span className="text-ink-3">—</span>}</td>
                <td className="px-2 py-1.5 text-right">
                  <PlainNumber value={p.returns_12m} unit="count" />
                </td>
                <td className="px-2 py-1.5 text-right">
                  <PlainNumber value={p.welcome_coupons} unit="count" />
                </td>
                <td className="px-2 py-1.5 text-right whitespace-nowrap">
                  <OpenCaseButton customerId={p.customer_id} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ProvenanceFooter />
    </ProvenanceScope>
  );
}
