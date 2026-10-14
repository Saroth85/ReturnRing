// Whether a restriction is in force, from the API's fields only (docs/ui-design.md P9, 11.4): the
// pending contract adds `in_force` or `review_due` at `review_at` on checkout terms, snapshots and
// coupon applications. One reading for every screen (UI9, UI42): `in_force` decides when present;
// otherwise `review_due: true` means lapsed; `review_due: false` alone says nothing (a proposed or
// unconfirmed restriction is not in force either). The client clock never decides a lapse.

export type LapseState = 'in_force' | 'lapsed' | 'unknown';

export function readLapse(entity: object): LapseState {
  const fields = entity as Record<string, unknown>;
  if (typeof fields.in_force === 'boolean') return fields.in_force ? 'in_force' : 'lapsed';
  if (fields.review_due === true) return 'lapsed';
  return 'unknown';
}
