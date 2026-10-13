// Evidence column sections (docs/ui-design.md 6.2.3). Stub: risk and value as Figures, and the
// customer messages in their three layers.
import type { CaseCard } from '@/api/models';
import { InlineNotice } from '@/components/feedback';
import { MessagePreview } from '@/components/message';
import { Figure } from '@/components/provenance';
import { OPERATOR } from '../../copy';

export function EvidenceColumn({ card }: { card: CaseCard }) {
  const t = OPERATOR.case;
  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-wrap gap-x-8 gap-y-2 rounded-card border border-border bg-surface p-4" data-section="risk-value">
        <div>
          <h3 className="text-caption text-ink-3">{t.riskScore}</h3>
          {card.risk ? (
            <Figure metric={card.risk.score} compareTo={card.risk.threshold} label={t.riskScore} />
          ) : (
            <p className="text-ink-3">{card.path === 'guarantee' ? t.notScoredGuarantee : t.notScoredInsufficient}</p>
          )}
        </div>
        {card.value && (
          <div>
            <h3 className="text-caption text-ink-3">{t.customerValue}</h3>
            <Figure metric={card.value.value_eur} compareTo={card.value.threshold} label={t.customerValue} />
            <p className="mt-1 max-w-prose border-l-2 border-border pl-2 text-caption text-ink-2" data-verbatim>
              {card.value.legend}
            </p>
          </div>
        )}
      </section>
      <section className="flex flex-col gap-3" data-section="message">
        <h2 className="text-h3">{t.messageTitle}</h2>
        {card.draft === null ? (
          <InlineNotice tone="info">{t.draftPending}</InlineNotice>
        ) : (
          card.draft.messages.map((message) => (
            <MessagePreview key={message.lang} message={message} stored={message.rendered_text} />
          ))
        )}
      </section>
    </div>
  );
}
