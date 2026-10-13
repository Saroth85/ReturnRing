// Lapse of a restriction from server fields only (docs/ui-design.md 11.3 toLapseVM, P9): the
// pending contract adds `in_force` or `review_due` at `review_at` on checkout terms, snapshots and
// coupon applications. Until a response carries them, only the review date is known and the client
// never derives a lapse from its own clock (T-SERVER-STATE).

export type LapseState = 'in_force' | 'lapsed' | 'unknown';

export interface LapseVM {
  state: LapseState;
  reviewAt: string | null;
}

/** Reads `review_due` / `in_force` when the server sends them; otherwise the state is unknown. */
export function toLapseVM(entity: { review_at?: string | null } & Record<string, unknown>): LapseVM {
  const reviewAt = typeof entity.review_at === 'string' ? entity.review_at : null;
  if (entity.review_due === true || entity.in_force === false) return { state: 'lapsed', reviewAt };
  if (entity.in_force === true || entity.review_due === false) return { state: 'in_force', reviewAt };
  return { state: 'unknown', reviewAt };
}
