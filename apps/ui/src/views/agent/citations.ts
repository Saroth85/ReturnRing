// Refs cited by the agent (US-701: "a direct link to the order or link cited"). On the case card
// a ref calls onRefSelect, which scrolls to and pulses the link, account or order; everywhere it
// is also a real link built from the deep-link parameters of docs/ui-design.md 3.2, so it works
// in a new tab and in the dev harness. Cross-area navigation is by URL only (13.3).
import type { EntityRef } from '@/api/models';
import { AGENT } from './copy';

type Kind = EntityRef['kind'];

/** The kind as a word on a chip: "Link", "Terms snapshot". */
export function citationLabel(kind: Kind): string {
  return AGENT.refs.kinds[kind];
}

const e = encodeURIComponent;

/** Where a ref lives: the case card with ?link= or ?focus=, a decision record, a product page. */
export function citationHref(ref: EntityRef, returnId: string): string {
  const card = `/returns/${e(returnId)}`;
  switch (ref.kind) {
    case 'link':
      return `${card}?link=${e(ref.id)}`;
    case 'cluster':
    case 'customer':
      return `${card}?focus=linked`;
    case 'order':
    case 'order_line':
    case 'terms_snapshot':
      return `${card}?focus=orders`;
    case 'variant':
      return `${card}?focus=product`;
    case 'return':
      return `/returns/${e(ref.id)}`;
    case 'decision':
      return `${card}/versions/${e(ref.id)}`;
    case 'anomaly':
      return `/products/${e(ref.id)}`;
    case 'catalog_task':
      return '/products/tasks';
  }
}

/** Whether the case card of `returnId` shows the ref itself (so it can scroll to it). */
export function focusesCaseCard(ref: EntityRef, returnId: string): boolean {
  switch (ref.kind) {
    case 'link':
    case 'cluster':
    case 'customer':
    case 'order':
    case 'order_line':
    case 'terms_snapshot':
    case 'variant':
      return true;
    case 'return':
      return ref.id === returnId;
    default:
      return false;
  }
}
