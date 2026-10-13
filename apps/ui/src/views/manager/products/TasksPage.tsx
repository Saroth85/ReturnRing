// Catalogue tasks /products/tasks (US-606, docs/ui-design.md 8.5). Stub: the task ids and states.
import { useCatalogTasks } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { IdText } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function TasksPage() {
  const tasks = useCatalogTasks();
  return (
    <ScreenPlaceholder title={MANAGER.titles.tasks} area="manager" stories={['US-606']} spec="docs/ui-design.md 8.5">
      <DataCheck label="GET /api/catalog-tasks" result={tasks} />
      {tasks.data && (
        <ul className="flex flex-col gap-1">
          {tasks.data.items.map((task) => (
            <li key={task.task_id} className="flex items-center gap-2">
              <IdText id={task.task_id} />
              <span className="text-ink-2">{task.status}</span>
            </li>
          ))}
        </ul>
      )}
    </ScreenPlaceholder>
  );
}
