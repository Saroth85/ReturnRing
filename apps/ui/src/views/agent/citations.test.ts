// Cited refs (US-701: "a direct link to the order or link cited"): each kind gets a word and a
// deep link of docs/ui-design.md 3.2, so a ref works as a link even without the case card's
// onRefSelect (dev harness, new tab).
import { describe, expect, it } from 'vitest';
import { ENTITY_REF_KINDS } from '@/api/contract.gen';
import type { EntityRef } from '@/api/models';
import { citationHref, citationLabel, focusesCaseCard } from './citations';

const RET = 'RET-000102';

describe('citationHref', () => {
  it.each<[EntityRef, string]>([
    [{ kind: 'link', id: 'C-MARCO-1~C-MARCO-3' }, '/returns/RET-000102?link=C-MARCO-1~C-MARCO-3'],
    [{ kind: 'cluster', id: 'CL-000417' }, '/returns/RET-000102?focus=linked'],
    [{ kind: 'customer', id: 'C-MARCO-3' }, '/returns/RET-000102?focus=linked'],
    [{ kind: 'order', id: 'ORD-005230' }, '/returns/RET-000102?focus=orders'],
    [{ kind: 'order_line', id: 'OL-1' }, '/returns/RET-000102?focus=orders'],
    [{ kind: 'terms_snapshot', id: 'TS-005230' }, '/returns/RET-000102?focus=orders'],
    [{ kind: 'return', id: 'RET-000102' }, '/returns/RET-000102'],
    [{ kind: 'return', id: 'RET-000103' }, '/returns/RET-000103'],
    [{ kind: 'decision', id: 'DEC-000102-1' }, '/returns/RET-000102/versions/DEC-000102-1'],
    [{ kind: 'variant', id: 'V-COA-1180' }, '/returns/RET-000102?focus=product'],
    [{ kind: 'anomaly', id: 'V-JKT-DEF~defective~2026-09-21' }, '/products/V-JKT-DEF~defective~2026-09-21'],
    [{ kind: 'catalog_task', id: 'CT-000012' }, '/products/tasks'],
  ])('%j -> %s', (ref, href) => {
    expect(citationHref(ref, RET)).toBe(href);
  });

  it('encodes ids that are not URL-safe', () => {
    expect(citationHref({ kind: 'link', id: 'A B&C' }, RET)).toBe('/returns/RET-000102?link=A%20B%26C');
    expect(citationHref({ kind: 'decision', id: 'D/1' }, 'R 1')).toBe('/returns/R%201/versions/D%2F1');
  });

  it('covers every ref kind of the contract', () => {
    for (const kind of ENTITY_REF_KINDS) {
      expect(citationHref({ kind, id: 'X-1' }, RET)).toMatch(/^\//);
      expect(citationLabel(kind)).not.toBe('');
    }
  });
});

describe('focusesCaseCard', () => {
  it('is true for what the case card can scroll to, false for other pages', () => {
    expect(focusesCaseCard({ kind: 'link', id: 'x' }, RET)).toBe(true);
    expect(focusesCaseCard({ kind: 'order', id: 'x' }, RET)).toBe(true);
    expect(focusesCaseCard({ kind: 'return', id: RET }, RET)).toBe(true);
    expect(focusesCaseCard({ kind: 'return', id: 'RET-000103' }, RET)).toBe(false);
    expect(focusesCaseCard({ kind: 'decision', id: 'DEC-000102-1' }, RET)).toBe(false);
    expect(focusesCaseCard({ kind: 'anomaly', id: 'x' }, RET)).toBe(false);
  });
});
