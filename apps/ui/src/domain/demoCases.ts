// The golden cases of the demo by key (GET /api/demo/cases, ADR-15 section 15): the reset finds
// them in the live store, so the UI never hard-codes their ids. A key the store does not hold (a
// small --scale) is missing from the response, and before a reset there is no response at all
// (404): every function here takes that in its stride and returns null or an empty list.
import type { DemoCart, DemoCartKey, DemoCase, DemoCaseKey, DemoCasesResponse, DemoDispute } from '@/api/models';

type Cases = DemoCasesResponse | null | undefined;

/** The cases of the pitch (spec 16, docs/ui-design.md 12): Giulia, Marco, Rossi twice, Elena. */
export const PITCH_CASE_KEYS = ['giulia', 'marco', 'rossi', 'rossi_card', 'elena'] as const satisfies readonly DemoCaseKey[];

/** The customers the matrix annotates (docs/ui-design.md 8.1): Giulia and Elena. */
export const HIGHLIGHT_CUSTOMER_KEYS = ['giulia', 'elena'] as const satisfies readonly DemoCaseKey[];

/** The cases whose clusters the matrix annotates: the Marco linked accounts and both Rossi families. */
export const HIGHLIGHT_CLUSTER_KEYS = ['marco', 'rossi', 'rossi_card'] as const satisfies readonly DemoCaseKey[];

export function caseOf(cases: Cases, key: DemoCaseKey): DemoCase | null {
  return cases?.cases.find((c) => c.key === key) ?? null;
}

export function cartOf(cases: Cases, key: DemoCartKey): DemoCart | null {
  return cases?.carts.find((c) => c.key === key) ?? null;
}

export function disputeOf(cases: Cases): DemoDispute | null {
  return cases?.dispute ?? null;
}

/** The return ids of the cases named, in that order, leaving out the ones the store does not hold. */
export function goldenReturnIds(cases: Cases, keys: readonly DemoCaseKey[] = PITCH_CASE_KEYS): string[] {
  return keys.map((key) => caseOf(cases, key)?.return_id).filter((id): id is string => typeof id === 'string');
}

export interface DemoHighlights {
  customers: { key: DemoCaseKey; customerId: string }[];
  clusters: { key: DemoCaseKey; clusterId: string }[];
}

/**
 * What the portfolio matrix annotates: the customers of Giulia and Elena, and the clusters of the
 * Marco and Rossi cases (a case without a cluster, or missing, is left out; a cluster named twice
 * once).
 */
export function highlights(cases: Cases): DemoHighlights {
  const customers = HIGHLIGHT_CUSTOMER_KEYS.flatMap((key) => {
    const found = caseOf(cases, key);
    return found ? [{ key, customerId: found.customer_id }] : [];
  });
  const seen = new Set<string>();
  const clusters = HIGHLIGHT_CLUSTER_KEYS.flatMap((key) => {
    const clusterId = caseOf(cases, key)?.cluster_id ?? null;
    if (clusterId === null || seen.has(clusterId)) return [];
    seen.add(clusterId);
    return [{ key, clusterId }];
  });
  return { customers, clusters };
}

/** The case page of the demo dispute with the link to dispute selected, or null without one. */
export function disputePath(cases: Cases): string | null {
  const dispute = disputeOf(cases);
  if (!dispute) return null;
  return `/returns/${encodeURIComponent(dispute.return_id)}?link=${encodeURIComponent(dispute.link_id)}`;
}
