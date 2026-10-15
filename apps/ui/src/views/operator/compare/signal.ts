// The shared signal of the compare band (pitch beat 1, spec 16): how soon after delivery each
// return was notified. The band calls it the same signal only when both returns are within
// SOON_AFTER_DELIVERY_DAYS of delivery; otherwise it names the difference. The 12-month return
// rate is one of the rows that differ (on the live store 25.0% against 85.7%), never the band.
import type { CaseCard } from '@/api/models';
import { SOON_AFTER_DELIVERY_DAYS } from '@/copy/constants';

export interface NoticeSignal {
  /** Days from delivery to the return notice, left and right (request.days_since_delivery). */
  days: [number, number];
  /** Both returns were notified within SOON_AFTER_DELIVERY_DAYS of delivery. */
  same: boolean;
}

export function noticeSignal(a: Pick<CaseCard, 'request'>, b: Pick<CaseCard, 'request'>): NoticeSignal {
  const days: [number, number] = [a.request.days_since_delivery, b.request.days_since_delivery];
  return { days, same: days.every((d) => d <= SOON_AFTER_DELIVERY_DAYS) };
}
