// CaseHero (docs/ui-design.md 6.2.2): one hero per case type. Stub: names the hero to build.
import type { CaseCard } from '@/api/models';
import { OPERATOR } from '../../copy';

function heroKind(card: CaseCard): 'cluster' | 'guarantee' | 'insufficient' | 'other' {
  if (card.path === 'guarantee') return 'guarantee';
  if (card.evidence.quality === 'insufficient') return 'insufficient';
  if (card.linked_accounts !== null) return 'cluster';
  return 'other';
}

const LABEL = {
  cluster: OPERATOR.case.heroCluster,
  guarantee: OPERATOR.case.heroGuarantee,
  insufficient: OPERATOR.case.heroInsufficient,
  other: OPERATOR.case.heroOther,
};

export function CaseHero({ card }: { card: CaseCard }) {
  const kind = heroKind(card);
  return (
    <section data-case-hero={kind} className="rounded-card border border-dashed border-border-strong bg-surface p-4 text-ink-2">
      <h2 className="text-h3 text-ink-1">{LABEL[kind]}</h2>
    </section>
  );
}
