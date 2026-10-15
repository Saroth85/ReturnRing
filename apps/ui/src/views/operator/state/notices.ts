// The confirmation of a decision act (docs/ui-design.md 6.3.3) from the API's DecisionResult: who
// did what, the message queued or why none was (a lift or a renewal: the customer sees the terms
// at checkout; an act after the refund deadline: no message may state a past date), the version a
// review superseded and the cluster coupon pause the act confirmed or reversed.
import type { DecisionResult, DecisionView } from '@/api/models';
import { OPERATOR } from '../copy';
import type { ActionNotice } from './prefs';
import { lateWithoutMessage } from './refund';

/** "Approved by", "Renewed by", "Lifted by"…: the act that left the decision in its state. */
export function resultWord(d: Pick<DecisionView, 'state' | 'revision_trigger'>): string {
  const t = OPERATOR.result;
  if (d.revision_trigger === 'review') return d.state === 'approved' ? t.renewed : t.lifted;
  return d.state === 'approved' ? t.approved : d.state === 'rejected' ? t.rejected : t.overridden;
}

export function resultNotice(result: DecisionResult): ActionNotice {
  const d = result.decision;
  const t = OPERATOR.result;
  const noMessage = result.outbox_message
    ? null
    : d.revision_trigger === 'review'
      ? t.noMessageReview
      : lateWithoutMessage(d)
        ? t.noMessageLate
        : null;
  const application = result.coupon_application;
  return {
    returnId: d.return_id,
    text: `${resultWord(d)} ${d.approver ?? ''}`.trim(),
    messageId: result.outbox_message?.message_id ?? null,
    auditEntryIds: [result.audit_entry.entry_id],
    noMessage,
    supersededId: result.superseded?.decision_id ?? null,
    coupon: application
      ? {
          applicationId: application.application_id,
          state: application.state,
          accounts: application.affected_customer_ids.length,
          accountsProvenance: d.inputs.provenance,
          reviewAt: application.review_at,
          inForce: application.in_force,
        }
      : null,
  };
}
