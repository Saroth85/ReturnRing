// Decision rail (docs/ui-design.md 6.2.4): the only place where the outcome and the actions
// live. Stub: authorship, the customer's floor, the approval flag, the levers and the review date.
import type { CaseCard } from '@/api/models';
import { ApprovalFlag, RefundFloorNotice, ReviewDateChip } from '@/components/decision';
import { OPERATOR } from '../../copy';

export function DecisionRail({ card }: { card: CaseCard }) {
  const { decision } = card;
  const t = OPERATOR.case;
  return (
    <aside
      aria-label={t.engineDecision}
      className="sticky top-12 flex flex-col gap-3 rounded-card border border-border bg-surface p-4"
      data-decision-rail
    >
      <header>
        <h2 className="text-h3">{t.engineDecision}</h2>
        <p className="text-caption text-ink-3">
          {t.rules} <span data-id>{decision.rules_version}</span> · {t.rulesDecided}
        </p>
      </header>
      <RefundFloorNotice refundDueBy={decision.refund_due_by} path={decision.path} />
      <ApprovalFlag requiresApproval={decision.requires_approval} state={decision.state} />
      <div>
        <h3 className="text-caption text-ink-3">{t.leverIds}</h3>
        {decision.lever_ids.length === 0 ? (
          <p>{t.noLevers}</p>
        ) : (
          <ul className="flex flex-wrap gap-1">
            {decision.lever_ids.map((id) => (
              <li key={id} className="rounded-chip border border-border px-1 font-mono text-caption" data-id>
                {id}
              </li>
            ))}
          </ul>
        )}
      </div>
      <ReviewDateChip reviewAt={decision.review_at} />
    </aside>
  );
}
