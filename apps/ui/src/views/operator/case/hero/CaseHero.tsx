// CaseHero (docs/ui-design.md 6.2.2): one hero per case type, at the top of the evidence column.
// Cluster: the linked accounts. Guarantee: the guarantee banner and the product issue. Not enough
// evidence: the panel with what is missing. Otherwise the risk panel moves up.
import type { CaseCard } from '@/api/models';
import { OPERATOR } from '../../copy';
import { LinkedAccountsPanel } from '../linked/LinkedAccountsPanel';
import { GuaranteeBanner, InsufficientEvidencePanel, ProductAnomalyPanel } from './heroes';

type HeroKind = 'cluster' | 'guarantee' | 'insufficient' | 'other';

function heroKindOf(card: Pick<CaseCard, 'path' | 'evidence' | 'linked_accounts'>): HeroKind {
  if (card.path === 'guarantee') return 'guarantee';
  if (card.evidence.quality === 'insufficient') return 'insufficient';
  if (card.linked_accounts !== null) return 'cluster';
  return 'other';
}

export function CaseHero({ card }: { card: CaseCard }) {
  const kind = heroKindOf(card);
  return (
    <div data-case-hero={kind} className="flex flex-col gap-3">
      {kind === 'guarantee' && (
        <>
          <GuaranteeBanner />
          {card.product_anomaly && <ProductAnomalyPanel card={card} anomaly={card.product_anomaly} />}
        </>
      )}
      {kind === 'insufficient' && <InsufficientEvidencePanel card={card} />}
      {kind === 'cluster' && card.linked_accounts && (
        <section className="rounded-card border border-border bg-surface p-4" data-section="linked">
          <LinkedAccountsPanel view={card.linked_accounts} />
        </section>
      )}
      {kind === 'other' && (
        <p className="rounded-card border border-border bg-surface px-4 py-3 text-body text-ink-2" data-no-linked>
          {OPERATOR.hero.noLinked}
        </p>
      )}
    </div>
  );
}
