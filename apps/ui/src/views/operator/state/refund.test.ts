// Refund deadlines on the service's clock (ADR-15 sections 15, 19, 20) and the confirmation of an
// act (DecisionResult: message, superseded version, coupon application), on the contract examples.
import { describe, expect, it } from 'vitest';
import type { DecisionResult, DecisionView } from '@/api/models';
import { readExample } from '@/test/examples';
import { OPERATOR } from '../copy';
import { resultNotice, resultWord } from './notices';
import { actedLate, lateProposal, lateWithoutMessage, refundOverdue } from './refund';

const result = (name: string) => readExample<DecisionResult>(`decision_result.${name}.json`);
const marco = () => readExample<{ decision: DecisionView }>('case_card.marco.json').decision;
const elena = () => readExample<{ decision: DecisionView }>('case_card.elena.json').decision;

describe('refundOverdue (apps/api/schemas.py refund_overdue)', () => {
  it('a proposal is overdue only after its refund day, on the service day', () => {
    const d = marco();
    expect(d.refund_due_by).toBe('2026-10-12');
    expect(refundOverdue(d, '2026-10-12')).toBe(false);
    expect(refundOverdue(d, '2026-10-13')).toBe(true);
    expect(lateProposal(d, '2026-10-13')).toBe(true);
    // No clock yet: nothing is claimed.
    expect(refundOverdue(d, null)).toBe(false);
  });

  it('a guarantee case states no refund deadline and is never overdue (rule 4)', () => {
    const d = elena();
    expect(d.path).toBe('guarantee');
    expect(d.refund_due_by).toBeNull();
    expect(refundOverdue(d, '2027-12-31')).toBe(false);
    expect(lateProposal(d, '2027-12-31')).toBe(false);
  });

  it('a rejection or standard terms after the deadline sent no message: it stays overdue', () => {
    const rejected = result('reject').decision;
    const late = { ...rejected, refund_due_by: '2026-09-29', approved_at: '2026-09-30T18:20:00Z' };
    expect(actedLate(late)).toBe(true);
    expect(lateWithoutMessage(late)).toBe(true);
    expect(refundOverdue(late, '2026-10-01')).toBe(true);
    // In time: a message stated the refund.
    const inTime = { ...rejected, refund_due_by: '2026-10-11', approved_at: '2026-09-30T18:20:00Z' };
    expect(actedLate(inTime)).toBe(false);
    expect(refundOverdue(inTime, '2026-10-20')).toBe(false);
    // An approval stated the refund in its message; a lift or a renewal reviews a restriction.
    const approved = { ...result('marco_approved').decision, refund_due_by: '2026-09-29' };
    expect(refundOverdue(approved, '2026-10-20')).toBe(false);
    const lifted = { ...result('lift').decision, refund_due_by: '2026-01-01' };
    expect(actedLate(lifted)).toBe(false);
  });

  it('an override with levers after the deadline is not a late act without a message', () => {
    const standard = { ...result('override').decision, refund_due_by: '2026-09-29' };
    expect(standard.levers).toEqual([]);
    expect(lateWithoutMessage(standard)).toBe(true);
    const withLever = { ...standard, levers: [{ id: 'size_advice' as const, value: true, scope: 'current_order' as const }] };
    expect(actedLate(withLever)).toBe(true);
    expect(lateWithoutMessage(withLever)).toBe(false);
  });
});

describe('resultNotice', () => {
  it('names the act, the message and the coupon pause an approval confirmed', () => {
    const notice = resultNotice(result('marco_approved'));
    expect(notice.text).toBe('Approved by sara');
    expect(notice.messageId).toBe('MSG-000102-1');
    expect(notice.noMessage).toBeNull();
    expect(notice.supersededId).toBeNull();
    expect(notice.coupon).toMatchObject({ applicationId: 'CRA-000031', state: 'confirmed', accounts: 7, reviewAt: '2026-12-27' });
  });

  it('a renewal and a lift send no message and name the version they supersede', () => {
    const renew = resultNotice(result('renew'));
    expect(resultWord(result('renew').decision)).toBe(OPERATOR.result.renewed);
    expect(renew.text).toMatch(/^Renewed by /);
    expect(renew).toMatchObject({ messageId: null, noMessage: OPERATOR.result.noMessageReview, supersededId: 'DEC-000088-1' });
    const lift = resultNotice(result('lift'));
    expect(lift.text).toMatch(/^Lifted by /);
    expect(lift).toMatchObject({ messageId: null, noMessage: OPERATOR.result.noMessageReview, supersededId: 'DEC-000088-1' });
  });

  it('a late rejection says no message was sent and the case stays refund overdue', () => {
    const reject = result('reject');
    const late: DecisionResult = {
      ...reject,
      decision: { ...reject.decision, refund_due_by: '2026-09-29', approved_at: '2026-09-30T18:20:00Z' },
      outbox_message: null,
    };
    const notice = resultNotice(late);
    expect(notice.text).toMatch(/^Rejected by /);
    expect(notice.messageId).toBeNull();
    expect(notice.noMessage).toBe(OPERATOR.result.noMessageLate);
    expect(notice.noMessage).toContain('stays refund overdue');
  });
});
