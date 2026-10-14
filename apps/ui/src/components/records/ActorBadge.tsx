import clsx from 'clsx';
import { Cpu, OctagonAlert } from 'lucide-react';
import type { AuditEntry } from '@/api/models';
import { systemRecordedHumanAction } from './audit';
import { RECORDS } from './copy';

interface ActorBadgeProps {
  actor: string;
  actorRole: AuditEntry['actor_role'] | string;
  /** The action recorded: a human action recorded as the system shows an integrity warning. */
  action?: AuditEntry['action'];
  className?: string;
}

const initials = (name: string) => name.slice(0, 2).toUpperCase();

/**
 * Who acted (docs/ui-design.md 5.7): a person ("sara · operator", initials) or the system (Cpu,
 * "System", italic). A human action recorded with the system actor breaks ck_audit_log_human_action
 * and is shown as an integrity failure, never silently (P10).
 */
export function ActorBadge({ actor, actorRole: role, action, className }: ActorBadgeProps) {
  const broken = action !== undefined && systemRecordedHumanAction({ action, actor_role: role as AuditEntry['actor_role'] });
  if (role === 'system') {
    return (
      <span className={clsx('inline-flex flex-col gap-0.5', className)} data-actor="system">
        <span className="inline-flex items-center gap-1 text-dense italic text-ink-3">
          <Cpu aria-hidden="true" size={14} />
          {RECORDS.system}
        </span>
        {broken && (
          <span role="alert" className="inline-flex items-center gap-1 text-caption text-danger-fg" data-integrity="system-human-action">
            <OctagonAlert aria-hidden="true" size={12} />
            {RECORDS.systemIntegrity}
          </span>
        )}
      </span>
    );
  }
  return (
    <span className={clsx('inline-flex items-center gap-1.5 text-dense', className)} data-actor={actor}>
      <span aria-hidden="true" className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-surface-2 text-micro font-medium text-ink-2">
        {initials(actor)}
      </span>
      <span>
        <span className="font-medium text-ink-1">{actor}</span>
        <span className="text-ink-3"> · {RECORDS.role[role] ?? role}</span>
      </span>
    </span>
  );
}
