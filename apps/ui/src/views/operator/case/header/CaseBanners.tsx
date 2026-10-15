// Banners under the case header (docs/ui-design.md 6.2.1). A refund past its deadline is always
// shown (rule 3: the refund is owed whatever happens to the proposal); after it, at most one more,
// most urgent first: the case changed while open, a restriction past its review date (US-607),
// read-only for the role or the public demo, and a draft still being prepared.
import { CalendarClock, Clock } from 'lucide-react';
import { useDemoClock } from '@/api/clock';
import { InlineNotice, useReadOnly } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { restrictionStatus } from '@/domain/decision';
import { usePersona } from '@/app/persona';
import { OPERATOR } from '../../copy';
import { roleWord } from '../../format';
import { refundOverdue } from '../../state/refund';
import { useCaseUi } from '../context';

interface CaseBannersProps {
  /** A decision id newer than the one the operator opened, reported by a refetch. */
  changedTo: string | null;
  onAcknowledge(): void;
  draftTimedOut: boolean;
}

/**
 * The refund of this case is past its deadline on the service's clock (ADR-15 sections 15, 19,
 * 20): a proposal can only be rejected or set to the standard terms, without a message; a case
 * decided late that way stays overdue. A guarantee case states no deadline and never shows this.
 */
export function RefundOverdueBanner() {
  const { card } = useCaseUi();
  const clock = useDemoClock();
  const d = card.decision;
  if (!refundOverdue(d, clock.today) || !d.refund_due_by || !clock.today) return null;
  const t = OPERATOR.refund;
  return (
    <div
      role="status"
      className="flex gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-body text-attn-fg"
      data-refund-overdue-banner={d.state === 'proposed' ? 'proposed' : 'acted-late'}
    >
      <Clock aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
      <div className="flex flex-col gap-1">
        <p>
          <strong className="font-semibold">{t.overdue}</strong> · {t.dueBy} <DateText value={d.refund_due_by} />; {t.serviceDay}{' '}
          <DateText value={clock.today} />. <span data-legal-ref>{t.owed}</span>
        </p>
        <p className="text-ink-1">{d.state === 'proposed' ? t.proposedRule : t.actedLate}</p>
      </div>
    </div>
  );
}

export function CaseBanners({ changedTo, onAcknowledge, draftTimedOut }: CaseBannersProps) {
  return (
    <>
      <RefundOverdueBanner />
      <OneBanner changedTo={changedTo} onAcknowledge={onAcknowledge} draftTimedOut={draftTimedOut} />
    </>
  );
}

function OneBanner({ changedTo, onAcknowledge, draftTimedOut }: CaseBannersProps) {
  const { card, gates, draftPending } = useCaseUi();
  const persona = usePersona();
  const readOnly = useReadOnly();
  const t = OPERATOR.banners;
  const status = restrictionStatus(card.decision);

  if (changedTo) {
    return (
      <InlineNotice tone="attention">
        <span className="flex flex-wrap items-center gap-2">
          <span>{t.newerVersion(changedTo)}</span>
          <Button onClick={onAcknowledge}>{t.loadNewer}</Button>
        </span>
      </InlineNotice>
    );
  }
  if (status.kind === 'lapsed') {
    return (
      <div
        role="status"
        className="flex flex-wrap items-center gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-body text-attn-fg"
        data-review-due-banner
      >
        <CalendarClock aria-hidden="true" size={16} />
        <span>
          {t.reviewDue(status.approver ?? '')}{' '}
          {status.approvedAt && (
            <>
              {t.on} <DateText value={status.approvedAt.slice(0, 10)} />
            </>
          )}{' '}
          · {t.reviewDate} {status.reviewAt && <DateText value={status.reviewAt} />} {t.reviewDuePassed}
        </span>
      </div>
    );
  }
  if (!gates.decide.allowed) {
    return (
      <InlineNotice tone="info">
        <span data-read-only-banner={readOnly ? 'public' : 'role'}>
          {readOnly ? OPERATOR.sheets.readOnly : t.readOnlyRole(roleWord(persona.role))}
        </span>
      </InlineNotice>
    );
  }
  if (draftPending) {
    return <InlineNotice tone={draftTimedOut ? 'attention' : 'info'}>{draftTimedOut ? t.draftTimedOut : t.draftWait}</InlineNotice>;
  }
  if (card.decision.superseded_by) {
    return (
      <InlineNotice tone="info">
        {t.superseded(card.decision.superseded_by)} <IdText id={card.decision.superseded_by} />
      </InlineNotice>
    );
  }
  return null;
}
