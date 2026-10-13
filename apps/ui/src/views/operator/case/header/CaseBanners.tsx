// Banners under the case header (docs/ui-design.md 6.2.1), at most one, most urgent first: the
// case changed while open, a restriction past its review date (US-607), read-only for the role,
// and a draft still being prepared.
import { CalendarClock } from 'lucide-react';
import { InlineNotice, READ_ONLY } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { restrictionStatus } from '@/domain/decision';
import { usePersona } from '@/app/persona';
import { OPERATOR } from '../../copy';
import { roleWord } from '../../format';
import { useCaseUi } from '../context';

interface CaseBannersProps {
  /** A decision id newer than the one the operator opened, reported by a refetch. */
  changedTo: string | null;
  onAcknowledge(): void;
  draftTimedOut: boolean;
}

export function CaseBanners({ changedTo, onAcknowledge, draftTimedOut }: CaseBannersProps) {
  const { card, gates, draftPending } = useCaseUi();
  const persona = usePersona();
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
    return <InlineNotice tone="info">{READ_ONLY ? gates.decide.reason : t.readOnlyRole(roleWord(persona.role))}</InlineNotice>;
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
