// The evidence column (docs/ui-design.md 6.2.3): the section navigation, then the sections in
// reading order. Each section is collapsible and remembered per browser; the hero sits above.
import type { CaseCard } from '@/api/models';
import { ProductAnomalyPanel } from '../hero/heroes';
import { EvidenceBars } from '../hero/EvidenceBars';
import { OPERATOR } from '../../copy';
import { Section } from '../../ui/Section';
import { AlternativesSection } from './AlternativesSection';
import { ExplanationSection } from './ExplanationSection';
import { HistorySection } from './HistorySection';
import { RiskSection } from './RiskSection';
import { OrdersSection, ValueSection } from './ValueOrders';

const NAV = ['evidence', 'risk', 'value', 'orders', 'alternatives', 'message', 'product', 'history'] as const;

function SectionNav({ card }: { card: CaseCard }) {
  const shown = NAV.filter((id) =>
    id === 'product'
      ? card.product_anomaly && card.path !== 'guarantee'
      : id === 'value'
        ? card.value
        : id === 'evidence'
          ? card.evidence.quality === 'sufficient'
          : true,
  );
  return (
    <nav aria-label={OPERATOR.case.sections} className="flex flex-wrap gap-1 text-caption" data-section-nav>
      {shown.map((id) => (
        <a
          key={id}
          href={`#section-${id}`}
          className="rounded-control border border-border bg-surface px-2 py-1 text-ink-2 hover:border-accent hover:text-accent"
        >
          {OPERATOR.case.sectionNames[id]}
        </a>
      ))}
    </nav>
  );
}

export function EvidenceColumn({ card, draftTimedOut, onReload }: { card: CaseCard; draftTimedOut: boolean; onReload(): void }) {
  const e = OPERATOR.evidence;
  return (
    <div className="flex flex-col gap-4">
      <SectionNav card={card} />
      {/* With not enough evidence the hero already shows the three bars and what is missing. */}
      {card.evidence.quality === 'sufficient' && (
        <Section id="evidence" title={e.title}>
          {/* Rule 4: a guarantee-path case is never scored, whatever its evidence (UI14). */}
          <p className="mb-3 text-body text-ink-2" data-evidence-line>
            {card.path === 'guarantee' ? e.sufficientGuarantee : e.sufficient}
          </p>
          <EvidenceBars components={card.evidence.components} compact />
        </Section>
      )}
      <RiskSection card={card} />
      <ValueSection card={card} />
      <OrdersSection card={card} />
      <AlternativesSection card={card} />
      <ExplanationSection card={card} draftTimedOut={draftTimedOut} onReload={onReload} />
      {card.product_anomaly && card.path !== 'guarantee' && (
        <div id="section-product" data-section="product">
          <ProductAnomalyPanel card={card} anomaly={card.product_anomaly} />
        </div>
      )}
      <HistorySection card={card} />
    </div>
  );
}
