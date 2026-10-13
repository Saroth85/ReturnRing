// CaseHeader (docs/ui-design.md 6.2.1). Stub: ids, customer's words and the badges.
import { Search } from 'lucide-react';
import type { CaseCard } from '@/api/models';
import { ApprovalFlag, EvidenceBadge, OutcomeBadge, PathBadge } from '@/components/decision';
import { RoleGate } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { Button, Kbd } from '@/components/ui';
import { OPERATOR } from '../../copy';

export function CaseHeader({ card, onAsk }: { card: CaseCard; onAsk(): void }) {
  const { decision } = card;
  return (
    <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4" aria-label={OPERATOR.titles.case}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <IdText id={card.return_id} />
        <IdText id={card.customer_id} />
        <span className="text-title">{card.display_name}</span>
        <span className="ml-auto">
          <RoleGate action="POST /api/agent/ask">
            {({ allowed }) => (
              <Button onClick={onAsk} disabled={!allowed}>
                <Search aria-hidden="true" size={16} />
                {OPERATOR.case.ask} <Kbd>I</Kbd>
              </Button>
            )}
          </RoleGate>
        </span>
      </div>
      <p className="text-caption text-ink-3">
        {card.item.category} · <IdText id={card.item.variant_id} /> · {card.request.reason} ·{' '}
        <DateText value={card.request.notice_on} />
      </p>
      {card.request.customer_text && (
        <p className="text-caption text-ink-2">
          {OPERATOR.case.customerWords}:{' '}
          <q lang="it" className="rounded-chip bg-surface-2 px-1" data-verbatim>
            {card.request.customer_text}
          </q>
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <PathBadge path={card.path} />
        <EvidenceBadge quality={card.evidence.quality} />
        <OutcomeBadge path={decision.path} evidenceQuality={decision.evidence_quality} quadrant={decision.quadrant} />
        <ApprovalFlag requiresApproval={decision.requires_approval} state={decision.state} />
      </div>
    </section>
  );
}
