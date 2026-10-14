// The linked-accounts model on the contract examples (docs/ui-design.md 4.8, 5.5).
import { describe, expect, it } from 'vitest';
import type { CaseCard } from '@/api/models';
import { readExample } from '@/test/examples';
import {
  activeLinks,
  graphLayout,
  isWeak,
  kindCounts,
  linksByStrength,
  linkState,
  matchesKinds,
  orderedAccounts,
  positionOn,
  strongestKind,
  timelineRange,
} from './linkModel';

const marco = readExample<CaseCard>('case_card.marco.json').linked_accounts!;
const chiara = readExample<CaseCard>('case_card.chiara.json').linked_accounts!;

describe('linkModel', () => {
  it("counts Marco's links per identifier kind", () => {
    expect(kindCounts(marco.links)).toEqual([
      { kind: 'card', count: 9 },
      { kind: 'email', count: 1 },
      { kind: 'device', count: 21 },
      { kind: 'address', count: 10 },
      { kind: 'phone', count: 1 },
      { kind: 'ip_prefix', count: 1 },
    ]);
    expect(marco.links).toHaveLength(21);
  });

  it('orders links by weight after decay and names the strongest kind', () => {
    const [strongest] = linksByStrength(marco.links);
    expect(strongest?.link_id).toBe('C-MARCO-2~C-MARCO-7');
    expect(strongestKind(strongest!)).toBe('card');
    expect(strongestKind({ kinds: ['ip_prefix', 'device'] })).toBe('device');
  });

  it("marks Chiara's card link as weak: shared by 41 accounts", () => {
    expect(chiara.links.every(isWeak)).toBe(true);
    expect(marco.links.some(isWeak)).toBe(false);
  });

  it('filters with OR and keeps every link with no kind selected', () => {
    expect(marco.links.filter((l) => matchesKinds(l, new Set(['card']))).length).toBe(9);
    expect(marco.links.filter((l) => matchesKinds(l, new Set(['phone', 'email']))).length).toBe(2);
    expect(marco.links.filter((l) => matchesKinds(l, new Set())).length).toBe(21);
  });

  it('reads the dispute and explanation state', () => {
    const link = marco.links[0];
    expect(linkState(link)).toBe('active');
    expect(linkState({ ...link, disputed: true })).toBe('disputed');
    expect(linkState({ ...link, explained_by_evidence_id: 'EV-1' })).toBe('explained');
    expect(activeLinks([link, { ...link, disputed: true }])).toHaveLength(1);
  });

  it('lays out by size, case customer first', () => {
    expect(graphLayout(3)).toBe('circle');
    expect(graphLayout(7)).toBe('concentric');
    expect(graphLayout(21)).toBe('cose');
    expect(orderedAccounts(marco).map((a) => a.customer_id)).toEqual([
      'C-MARCO-1',
      'C-MARCO-2',
      'C-MARCO-3',
      'C-MARCO-4',
      'C-MARCO-5',
      'C-MARCO-6',
      'C-MARCO-7',
    ]);
  });

  it('spans the coupon sequence from the first creation to the last welcome coupon', () => {
    const range = timelineRange(marco.accounts);
    expect(range).toEqual({ start: '2026-09-01T10:00:00Z', end: '2026-09-11T12:00:00Z' });
    expect(positionOn(range.start, range)).toBe(0);
    expect(positionOn(range.end, range)).toBe(1);
  });
});
