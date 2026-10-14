// History (docs/ui-design.md 6.2.3 item 8, US-604): the versions of the decision, each opening its
// decision record, and the audit entries of this return. A person or the system, never mixed.
import { ChevronRight, Cpu, UserRound } from 'lucide-react';
import { Link } from 'react-router';
import type { CaseCard } from '@/api/models';
import { useAudit } from '@/api/queries';
import { QuadrantBadge } from '@/components/decision';
import { ErrorState, LoadingBlock } from '@/components/feedback';
import { DateText, Figure, IdText } from '@/components/provenance';
import { OPERATOR } from '../../copy';
import { roleWord } from '../../format';
import { Section } from '../../ui/Section';

function VersionChain({ card }: { card: CaseCard }) {
  const h = OPERATOR.history;
  return (
    <ol className="flex flex-wrap items-stretch gap-2" data-version-chain aria-label={h.versions}>
      {card.revisions.map((r, i) => (
        <li key={r.decision_id} className="flex items-center gap-2">
          {i > 0 && <ChevronRight aria-hidden="true" size={16} className="text-ink-3" />}
          <div className="flex flex-col gap-1 rounded-control border border-border px-3 py-2 text-dense" data-version-node={r.decision_id}>
            <Link
              to={`/returns/${card.return_id}/versions/${r.decision_id}`}
              className="flex items-center gap-2 text-accent underline-offset-2 hover:underline"
              aria-label={`${h.openRecord}: ${r.decision_id}`}
            >
              <span className="font-mono font-medium" data-version>
                v{r.version}
              </span>
              <span>{h.triggers[r.trigger] ?? r.trigger}</span>
            </Link>
            <span className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
              <span>{OPERATOR.queue.states[r.state] ?? r.state}</span>
              {/* A risk shows only for a scored version (US-307: none without a quadrant). */}
              {r.quadrant && <QuadrantBadge quadrant={r.quadrant} />}
              {r.quadrant && r.risk && <Figure metric={r.risk} label={OPERATOR.risk.score} />}
              <DateText value={r.created_at} />
            </span>
          </div>
        </li>
      ))}
    </ol>
  );
}

function AuditList({ returnId }: { returnId: string }) {
  const h = OPERATOR.history;
  const audit = useAudit({ return_id: returnId });
  if (audit.isPending) return <LoadingBlock rows={3} />;
  if (audit.isError) return <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />;
  const items = [...audit.data.items].sort((a, b) => a.at.localeCompare(b.at) || a.entry_id - b.entry_id);
  if (items.length === 0) return <p className="text-body text-ink-3">{h.noAudit}</p>;
  return (
    <ol className="flex flex-col divide-y divide-border rounded-control border border-border" data-audit-list>
      {items.map((entry) => (
        <li key={entry.entry_id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2 text-dense">
          <span className="font-mono text-caption text-ink-3" data-id>
            #{entry.entry_id}
          </span>
          <DateText value={entry.at} className="text-caption text-ink-3" />
          {entry.actor === 'system' ? (
            <span className="inline-flex items-center gap-1 italic text-ink-3">
              <Cpu aria-hidden="true" size={14} />
              {h.system}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-ink-1">
              <UserRound aria-hidden="true" size={14} />
              {entry.actor} <span className="text-ink-3">· {roleWord(entry.actor_role)}</span>
            </span>
          )}
          <span className="font-medium">{h.actions[entry.action] ?? entry.action}</span>
          <IdText id={entry.entity_id} />
          {entry.reason && (
            <span className="basis-full text-ink-2" data-verbatim>
              “{entry.reason}”
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

export function HistorySection({ card }: { card: CaseCard }) {
  const h = OPERATOR.history;
  return (
    <Section id="history" title={h.title}>
      <div className="flex flex-col gap-3">
        <VersionChain card={card} />
        <h3 className="text-title">{h.audit}</h3>
        <AuditList returnId={card.return_id} />
        <Link to={`/audit?return_id=${card.return_id}`} className="self-start text-accent underline underline-offset-2">
          {h.allAudit}
        </Link>
      </div>
    </Section>
  );
}
