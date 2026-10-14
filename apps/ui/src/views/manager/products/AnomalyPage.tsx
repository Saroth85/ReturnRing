// Product issue /products/:anomalyId (US-208, US-606, docs/ui-design.md 8.5): observed against
// expected, the weekly trend with the flagged week and, after a correction, the weeks before and
// after it; the linked returns stay regular (Elena: no restriction to the customer); a catalogue
// task opens from here.
import { ArrowLeft, ShieldCheck } from 'lucide-react';
import { Link, useParams } from 'react-router';
import type { ProductAnomaliesResponse } from '@/api/models';
import { useAnomalies, useCatalogTasks } from '@/api/queries';
import { WeeklyTrendChart } from '@/components/charts';
import { DateText, Figure, IdText } from '@/components/provenance';
import { MANAGER } from '../copy';
import type { ProductAnomaly } from '../types';
import { Card, Loadable, ManagerPage } from '../ui';
import { AnomalyStatus, CatalogTaskForm, ProductTabs, TaskCard } from './parts';

const D = MANAGER.products.detail;
const PR = MANAGER.products;

function Anomaly({ anomaly, data }: { anomaly: ProductAnomaly; data: ProductAnomaliesResponse }) {
  const tasks = useCatalogTasks();
  const task = tasks.data?.items.find((t) => t.task_id === anomaly.catalog_task_id || t.anomaly_id === anomaly.anomaly_id) ?? null;
  return (
    <ManagerPage
      screen="anomaly"
      title={`${MANAGER.titles.anomaly} · ${anomaly.variant_id}`}
      intro={
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span data-id>{anomaly.product_id}</span>
          <span>· {anomaly.category}</span>
          {anomaly.brand && <span>· {anomaly.brand}</span>}
          <span>· {PR.reason[anomaly.reason] ?? anomaly.reason}</span>
          <span>
            · {D.week} <DateText value={anomaly.week} />
          </span>
          <AnomalyStatus anomaly={anomaly} />
        </p>
      }
      actions={
        <Link to="/products" className="inline-flex items-center gap-1 text-body text-accent underline underline-offset-2">
          <ArrowLeft aria-hidden="true" size={14} />
          {D.back}
        </Link>
      }
    >
      <div className="grid gap-4 xl:grid-cols-12">
        <div className="flex min-w-0 flex-col gap-4 xl:col-span-8">
          <Card title={D.summary}>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4" data-anomaly-summary>
              <div>
                <dt className="text-caption text-ink-3">{D.observed}</dt>
                <dd className="text-h2">
                  <Figure metric={anomaly.observed} label={D.observed} />
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-3">{D.expected}</dt>
                <dd className="text-h2">
                  <Figure metric={anomaly.expected} label={D.expected} />
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-3">{D.z}</dt>
                <dd className="text-h2">
                  <Figure metric={anomaly.z} label={D.z} />
                </dd>
              </div>
              <div>
                <dt className="text-caption text-ink-3">{D.sales}</dt>
                <dd className="text-h2">
                  <Figure metric={anomaly.sales} label={D.sales} />
                </dd>
              </div>
            </dl>
          </Card>
          <Card>
            <WeeklyTrendChart
              title={D.trendTitle(anomaly.variant_id)}
              trend={anomaly.trend}
              provenance={data.provenance}
              flagged={{ week: anomaly.week, expected: anomaly.expected, z: anomaly.z }}
              correctionAt={task?.status === 'done' ? task.done_at : null}
            />
            {task?.status === 'done' && <p className="text-caption font-medium text-ink-1">{D.caveat}</p>}
          </Card>
        </div>
        <div className="flex min-w-0 flex-col gap-4 xl:col-span-4">
          <Card title={D.returnsTitle}>
            <p className="flex gap-2 text-body text-ink-2">
              <ShieldCheck aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
              {D.returnsNote}
            </p>
            <ul className="flex flex-col gap-1" data-linked-returns>
              {anomaly.return_ids.map((id) => (
                <li key={id} className="flex items-center justify-between gap-2">
                  <IdText id={id} />
                  <Link to={`/returns/${encodeURIComponent(id)}?focus=product`} className="text-dense text-accent underline underline-offset-2">
                    {D.openCase}
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
          <Card title={task ? D.task : MANAGER.tasks.form.title}>
            <Loadable query={tasks} label="GET /api/catalog-tasks" rows={3}>
              {() =>
                task ? (
                  <>
                    <TaskCard task={task} />
                    <Link to="/products/tasks" className="text-dense text-accent underline underline-offset-2">
                      {D.openTask}
                    </Link>
                  </>
                ) : (
                  <CatalogTaskForm anomaly={anomaly} />
                )
              }
            </Loadable>
          </Card>
        </div>
      </div>
    </ManagerPage>
  );
}

export default function AnomalyPage() {
  const { anomalyId = '' } = useParams();
  const anomalies = useAnomalies();
  const anomaly = anomalies.data?.items.find((a) => a.anomaly_id === anomalyId);
  if (!anomalies.data || !anomaly) {
    return (
      <ManagerPage screen="anomaly" title={MANAGER.titles.anomaly}>
        <ProductTabs />
        <Loadable query={anomalies} label="GET /api/products/anomalies" rows={6}>
          {() => (
            <p className="rounded-card border border-dashed border-border-strong p-6 text-center text-ink-2">
              <span data-id>{D.notFound(anomalyId)}</span>{' '}
              <Link to="/products" className="text-accent underline underline-offset-2">
                {D.back}
              </Link>
            </p>
          )}
        </Loadable>
      </ManagerPage>
    );
  }
  return <Anomaly anomaly={anomaly} data={anomalies.data} />;
}
