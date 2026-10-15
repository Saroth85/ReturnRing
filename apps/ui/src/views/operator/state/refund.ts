// Refund deadlines on the service's clock (ADR-15 sections 15, 19 and 20; rules 3 and 4). The
// queue carries the server's `refund_overdue`; a case card does not, so the card reads the same
// rule from its decision and the service's day (useDemoClock().today, never the browser's date):
// - a guarantee case states no refund deadline (`refund_due_by` null): never overdue (rule 4);
// - a proposal past `refund_due_by` is overdue: a message would state a past date, so approve and
//   an override to other levers answer 409, while a rejection or the standard terms go on with no
//   message (outbox_message null);
// - a rejection or an override made after the deadline sent no message: it stays overdue.
// The functions mirror apps/api/schemas.py refund_overdue, acted_late and late_without_message.
import type { DecisionView, Lever } from '@/api/models';

export type RefundFields = Pick<DecisionView, 'state' | 'refund_due_by' | 'revision_trigger' | 'approved_at'>;

/** The UTC day of an ISO timestamp (the API's utc_day). */
const utcDayOf = (instant: string): string => new Date(instant).toISOString().slice(0, 10);

/** A person's rejection or override of a proposal after its refund deadline: no message stated the refund. */
export function actedLate(d: RefundFields): boolean {
  return (
    (d.state === 'rejected' || d.state === 'overridden') &&
    d.revision_trigger !== 'review' &&
    d.refund_due_by !== null &&
    d.approved_at !== null &&
    utcDayOf(d.approved_at) > d.refund_due_by
  );
}

/** refund_overdue of the queue for one decision at the service's day `today` (YYYY-MM-DD). */
export function refundOverdue(d: RefundFields, today: string | null): boolean {
  if (d.refund_due_by === null || today === null || d.refund_due_by >= today) return false;
  return d.state === 'proposed' || actedLate(d);
}

/** A proposal past its refund deadline: only a rejection or the standard terms, without a message. */
export function lateProposal(d: RefundFields, today: string | null): boolean {
  return d.state === 'proposed' && refundOverdue(d, today);
}

/**
 * Whether an act sent no message because it came after the refund deadline (late_without_message):
 * a rejection, or an override to the standard levers, of a case whose deadline had passed.
 */
export function lateWithoutMessage(d: RefundFields & { levers: readonly Lever[] }): boolean {
  return d.levers.length === 0 && actedLate(d);
}
