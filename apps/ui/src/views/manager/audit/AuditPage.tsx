// Audit log /audit with CSV export (US-604, docs/ui-design.md 8.7). Stub: the entries' actions.
import { useAudit } from '@/api/queries';
import { DataCheck, InlineNotice, ScreenPlaceholder } from '@/components/feedback';
import { DateText } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function AuditPage() {
  const audit = useAudit();
  return (
    <ScreenPlaceholder title={MANAGER.titles.audit} area="manager" stories={['US-604']} spec="docs/ui-design.md 8.7">
      <InlineNotice tone="info">{MANAGER.audit.banner}</InlineNotice>
      <DataCheck label="GET /api/audit" result={audit} />
      {audit.data && (
        <ul className="divide-y divide-border rounded-card border border-border bg-surface text-dense">
          {audit.data.items.map((entry) => (
            <li key={entry.entry_id} className="flex flex-wrap gap-3 px-3 py-1.5">
              <DateText value={entry.at} className="text-ink-3" />
              <span className="font-mono" data-id>
                {entry.action}
              </span>
              <span className="text-ink-2">{entry.actor}</span>
            </li>
          ))}
        </ul>
      )}
    </ScreenPlaceholder>
  );
}
