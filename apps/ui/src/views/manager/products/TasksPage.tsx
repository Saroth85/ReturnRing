// Catalogue tasks /products/tasks (US-606, docs/ui-design.md 8.5): each task with its status stepper
// (Open -> In progress -> Done or Cancelled, PATCH with status, note, author) and, once done, the
// follow-up and the variant's weekly returns before and after the correction.
import type { CatalogTask } from '@/api/models';
import { useAnomalies, useCatalogTasks } from '@/api/queries';
import { WeeklyTrendChart } from '@/components/charts';
import { EmptyState } from '@/components/feedback';
import { MANAGER } from '../copy';
import { Loadable, ManagerPage } from '../ui';
import { ProductTabs, TaskCard } from './parts';

const T = MANAGER.tasks;
const ORDER: Record<CatalogTask['status'], number> = { open: 0, in_progress: 1, done: 2, cancelled: 3 };

export default function TasksPage() {
  const tasks = useCatalogTasks();
  const anomalies = useAnomalies();
  return (
    <ManagerPage screen="tasks" title={MANAGER.titles.tasks} intro={<p>{T.intro}</p>}>
      <ProductTabs />
      <Loadable query={tasks} label="GET /api/catalog-tasks" rows={6}>
        {(data) =>
          data.items.length === 0 ? (
            <EmptyState message={T.empty} />
          ) : (
            <div className="grid items-start gap-4 xl:grid-cols-2">
              {[...data.items]
                .sort((a, b) => ORDER[a.status] - ORDER[b.status] || b.updated_at.localeCompare(a.updated_at))
                .map((task) => {
                  const anomaly = anomalies.data?.items.find((a) => a.anomaly_id === task.anomaly_id);
                  return (
                    <TaskCard key={task.task_id} task={task}>
                      {task.status === 'done' && anomaly && anomalies.data && (
                        <WeeklyTrendChart
                          title={T.beforeAfter}
                          trend={anomaly.trend}
                          provenance={anomalies.data.provenance}
                          correctionAt={task.done_at}
                          height={160}
                        />
                      )}
                    </TaskCard>
                  );
                })}
            </div>
          )
        }
      </Loadable>
    </ManagerPage>
  );
}
