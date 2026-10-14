// Entity references (EntityRef of the contract) and where they live on the case card: evidence
// refs, agent citations and deep links scroll to and pulse the same anchors (docs/ui-design.md 7).
import type { EntityRef } from '@/api/models';

export type RefKind = EntityRef['kind'];

/** The DOM id of the element that shows an entity on the case card, e.g. `ref-link-C-1~C-2`. */
export function refAnchorId(ref: Pick<EntityRef, 'kind' | 'id'>): string {
  return `ref-${ref.kind}-${ref.id}`;
}

/** The two accounts of a link id (`C-MARCO-1~C-MARCO-3`), as the contract writes it. */
export function linkEnds(linkId: string): [string, string] {
  const [a = '', b = ''] = linkId.split('~');
  return [a, b];
}
