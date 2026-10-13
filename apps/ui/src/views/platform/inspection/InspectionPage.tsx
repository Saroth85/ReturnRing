// Warehouse inspection /inspection (US-501..503, Could; docs/ui-design.md 9.2). Stub: the
// withdrawal-path returns, each opening its inspection.
import { Link } from 'react-router';
import { useQueue } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { PLATFORM } from '../copy';

export default function InspectionPage() {
  const returns = useQueue({ path: ['withdrawal'] });
  return (
    <ScreenPlaceholder title={PLATFORM.titles.inspection} area="platform" stories={['US-501', 'US-502', 'US-503']} spec="docs/ui-design.md 9.2">
      <p className="max-w-prose text-body text-ink-2">{PLATFORM.inspection.note}</p>
      <DataCheck label="GET /api/returns?path=withdrawal" result={returns} />
      {returns.data && (
        <ul className="divide-y divide-border rounded-card border border-border bg-surface">
          {returns.data.items.map((item) => (
            <li key={item.return_id} className="flex flex-wrap items-center gap-3 px-3 py-2">
              <Link to={`/inspection/${item.return_id}`} className="font-mono text-accent underline-offset-2 hover:underline" data-id>
                {item.return_id}
              </Link>
              <span>{item.display_name}</span>
            </li>
          ))}
        </ul>
      )}
    </ScreenPlaceholder>
  );
}
