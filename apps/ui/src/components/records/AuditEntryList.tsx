import clsx from 'clsx';
import { UserCheck } from 'lucide-react';
import type { AuditEntry } from '@/api/models';
import { DateText } from '@/components/provenance';
import { ActorBadge } from './ActorBadge';
import { restrictiveApproval } from './audit';
import { RECORDS } from './copy';

/** "Human approval · review 27 Dec 2026" on a restrictive approval (rule 5). */
export function HumanApprovalPill({ entry }: { entry: AuditEntry }) {
  const approval = restrictiveApproval(entry);
  if (!approval) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border-strong px-2 py-0.5 text-caption whitespace-nowrap text-ink-1" data-human-approval>
      <UserCheck aria-hidden="true" size={12} />
      {RECORDS.humanApproval} · {RECORDS.review} <DateText value={approval.reviewAt} />
    </span>
  );
}

/**
 * A compact list of audit entries (docs/ui-design.md 5.7): time, who, what, and the reason as a
 * quote. For a case's history; the audit page has the full table.
 */
export function AuditEntryList({ entries, className }: { entries: AuditEntry[]; className?: string }) {
  return (
    <ol className={clsx('flex flex-col divide-y divide-border', className)} data-audit-list>
      {entries.map((entry) => (
        <li key={entry.entry_id} className="flex flex-col gap-1 py-2" data-audit-entry={entry.action}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <DateText value={entry.at} className="text-caption text-ink-3" />
            <ActorBadge actor={entry.actor} actorRole={entry.actor_role} action={entry.action} />
            <span className="text-dense font-medium">{RECORDS.action[entry.action]}</span>
            <HumanApprovalPill entry={entry} />
          </div>
          {entry.reason && (
            <blockquote className="border-l-2 border-border-strong pl-2 text-dense text-ink-2" data-verbatim>
              {entry.reason}
            </blockquote>
          )}
        </li>
      ))}
    </ol>
  );
}
