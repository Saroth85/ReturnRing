// Lapse of a restriction from server fields only (docs/ui-design.md 11.3 toLapseVM, P9), with the
// review date. The reading itself is shared with the platform screens (src/domain/lapse.ts).
import { readLapse, type LapseState } from '@/domain/lapse';

export type { LapseState };

export interface LapseVM {
  state: LapseState;
  reviewAt: string | null;
}

/** Reads `in_force` / `review_due` when the server sends them; otherwise the state is unknown. */
export function toLapseVM(entity: { review_at?: string | null } & Record<string, unknown>): LapseVM {
  const reviewAt = typeof entity.review_at === 'string' ? entity.review_at : null;
  return { state: readLapse(entity), reviewAt };
}
