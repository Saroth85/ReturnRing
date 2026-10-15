// The suggested questions of a case (US-702): the link question names a link of the case's own
// linked accounts that contains the case customer, the strongest active one; a case without
// linked accounts gets the two general questions only.
import { describe, expect, it } from 'vitest';
import type { AccountLink, CaseCard } from '@/api/models';
import { readExample } from '@/test/examples';
import { AGENT } from './copy';
import { linkForQuestion, suggestedQuestions } from './questions';

const marco = () => readExample<CaseCard>('case_card.marco.json');

function withLinks(card: CaseCard, change: (links: AccountLink[]) => AccountLink[]): CaseCard {
  const la = card.linked_accounts!;
  return { ...card, linked_accounts: { ...la, links: change([...la.links]) as typeof la.links } };
}

describe('suggestedQuestions', () => {
  it("names on Marco's card the strongest link of C-MARCO-1, the one of the contract example", () => {
    expect(linkForQuestion(marco())?.link_id).toBe('C-MARCO-1~C-MARCO-3');
    expect(suggestedQuestions(marco())[0]).toBe('Which evidence supports the link between C-MARCO-1 and C-MARCO-3?');
  });

  it('always names a link that contains the case customer, the customer first', () => {
    const card = { ...marco(), customer_id: 'C-MARCO-4' };
    const link = linkForQuestion(card);
    expect([link?.customer_a, link?.customer_b]).toContain('C-MARCO-4');
    expect(suggestedQuestions(card)[0]).toMatch(/^Which evidence supports the link between C-MARCO-4 and C-MARCO-\d\?$/);
  });

  it('prefers an active link: a disputed or explained one is used only when no other is left', () => {
    const card = withLinks(marco(), (links) =>
      links.map((l) => (l.link_id === 'C-MARCO-1~C-MARCO-3' ? { ...l, disputed: true } : l)),
    );
    expect(linkForQuestion(card)?.link_id).toBe('C-MARCO-1~C-MARCO-6');
    const onlyDisputed = withLinks(marco(), (links) =>
      links.filter((l) => l.link_id === 'C-MARCO-1~C-MARCO-3').map((l) => ({ ...l, disputed: true })),
    );
    expect(linkForQuestion(onlyDisputed)?.link_id).toBe('C-MARCO-1~C-MARCO-3');
  });

  it('a case without linked accounts, or before its card loads, gets the two general questions', () => {
    const giulia = readExample<CaseCard>('case_card.giulia.json');
    expect(giulia.linked_accounts).toBeNull();
    expect(suggestedQuestions(giulia)).toEqual([AGENT.suggested.rossi, AGENT.suggested.terms]);
    expect(suggestedQuestions(undefined)).toEqual([AGENT.suggested.rossi, AGENT.suggested.terms]);
  });
});
