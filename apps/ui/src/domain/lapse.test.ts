// One reading of the pending lapse fields for every screen (UI9, UI42; docs/ui-design.md P9, 11.4):
// `in_force` decides when present; otherwise `review_due: true` means lapsed; `review_due: false`
// alone says nothing about force (a proposed restriction is not in force either).
import { describe, expect, it } from 'vitest';
import { readLapse } from './lapse';

const VALUES = [true, false, undefined] as const;

describe('readLapse', () => {
  const expected = (inForce: boolean | undefined, reviewDue: boolean | undefined) =>
    inForce === true ? 'in_force' : inForce === false ? 'lapsed' : reviewDue === true ? 'lapsed' : 'unknown';
  for (const inForce of VALUES) {
    for (const reviewDue of VALUES) {
      it(`in_force ${String(inForce)}, review_due ${String(reviewDue)}`, () => {
        const entity: Record<string, unknown> = {};
        if (inForce !== undefined) entity.in_force = inForce;
        if (reviewDue !== undefined) entity.review_due = reviewDue;
        expect(readLapse(entity)).toBe(expected(inForce, reviewDue));
      });
    }
  }

  it('ignores fields of the wrong type', () => {
    expect(readLapse({ in_force: 'yes', review_due: 1 })).toBe('unknown');
  });
});
