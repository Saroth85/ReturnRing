// Labels of levers and terms (docs/ui-design.md 5.3), shared by the decision components and the
// views that list levers in prose.
import type { Lever } from '@/api/models';
import type { LeverId } from '@/domain/levers';
import { LEVER_COPY, TERMS_COPY } from './copy';

/** "Return window 14 days", "Coupons paused for the linked accounts". */
export function leverLabel(lever: Pick<Lever, 'id' | 'value'>): string {
  if (lever.id === 'return_window_days') return LEVER_COPY.window(Number(lever.value));
  return LEVER_COPY.label[lever.id][lever.value ? 'true' : 'false'];
}

/** A term value in a TermsDiff cell: "14 days", "paid by the customer", "paused" (the count is data: TermsDiff). */
export function termValue(lever: LeverId, value: number | boolean): string {
  if (lever === 'return_window_days') return TERMS_COPY.days(Number(value));
  return TERMS_COPY.value[lever][value ? 'true' : 'false'];
}
