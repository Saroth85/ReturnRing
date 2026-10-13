// Whether a restriction behind shown terms is still in force, from the API's fields only
// (docs/ui-design.md P9 and 11.4). Contract 1.1.0 has no lapse fields on checkout terms, snapshots
// or coupon applications; a pending change adds `in_force` or `review_due` at `review_at`. This
// reads them when present, so regenerating the types is all it takes; until then the answer is
// "unknown" and the screens show the review date only. The client clock never decides a lapse.

export type ServerLapse = { kind: 'in_force' } | { kind: 'lapsed' } | { kind: 'unknown' };

export function serverLapse(entity: object): ServerLapse {
  const fields = entity as Record<string, unknown>;
  const inForce = typeof fields.in_force === 'boolean' ? fields.in_force : null;
  const reviewDue = typeof fields.review_due === 'boolean' ? fields.review_due : null;
  if (reviewDue === true || inForce === false) return { kind: 'lapsed' };
  if (inForce === true) return { kind: 'in_force' };
  return { kind: 'unknown' };
}
