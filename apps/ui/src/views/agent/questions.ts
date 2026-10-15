// The suggested questions of a case (US-702, docs/ui-design.md 7): the link question names a link
// of the case's own linked accounts that contains the case customer (the strongest active one, so
// on the demo's Marco card it is the card, device and address link of the contract example); the
// Rossi and terms questions hold for every case. A case without linked accounts has no link
// question. The case card is read from the cache (or fetched) through useCase.
import type { AccountLink, CaseCard } from '@/api/models';
import { AGENT } from './copy';

/** Strongest first: the decayed weight, then the weight, then the link id (stable). */
function byStrength(a: AccountLink, b: AccountLink): number {
  return b.weight_decayed - a.weight_decayed || b.weight - a.weight || a.link_id.localeCompare(b.link_id);
}

/** The link the link question asks about: one that contains the case customer, active if possible. */
export function linkForQuestion(card: Pick<CaseCard, 'customer_id' | 'linked_accounts'>): AccountLink | null {
  const links = (card.linked_accounts?.links ?? []).filter((l) => l.customer_a === card.customer_id || l.customer_b === card.customer_id);
  const active = links.filter((l) => !l.disputed && !l.explained_by_evidence_id);
  return [...(active.length > 0 ? active : links)].sort(byStrength)[0] ?? null;
}

/** The suggested questions of a case, the link question first; without a card, the two general ones. */
export function suggestedQuestions(card: Pick<CaseCard, 'customer_id' | 'linked_accounts'> | null | undefined): string[] {
  const link = card ? linkForQuestion(card) : null;
  const other = link ? (link.customer_a === card?.customer_id ? link.customer_b : link.customer_a) : null;
  return [
    ...(link && card && other ? [AGENT.suggested.link(card.customer_id, other)] : []),
    AGENT.suggested.rossi,
    AGENT.suggested.terms,
  ];
}
