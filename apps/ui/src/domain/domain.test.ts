// Domain logic of the operator area against the contract examples (docs/ui-design.md 5.3, 6.2.4,
// 11.3): lever directions, the restriction status from server fields only (T-SERVER-STATE), the
// actions each state allows, the decision echo and the terms diff.
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CaseCard, DecisionResult, DecisionView, RecomputeResponse, Terms } from '@/api/models';
import { readExample } from '@/test/examples';
import {
  actionPlan,
  changedRows,
  changedSnapshotRows,
  echoMatches,
  overrideLock,
  proposalNeedsApproval,
  queueRestrictionKind,
  restrictionStatus,
  termsRows,
} from './decision';
import { hasRestrictiveLever, leverDirection, leverKey, leversFromValues, leverValues, parseLeverKey, sameLevers } from './levers';
import { linkEnds, refAnchorId } from './refs';

const card = (name: string) => readExample<CaseCard>(`case_card.${name}.json`);
const BASE: Terms = {
  return_window_days: 30,
  free_return_shipping: true,
  instant_refund: true,
  free_size_exchange: true,
  coupons_enabled: true,
};

describe('levers', () => {
  it('round-trips the "id:value" form of contracts/levers.py', () => {
    for (const id of card('marco').decision.lever_ids) expect(leverKey(parseLeverKey(id))).toBe(id);
    expect(parseLeverKey('cluster_coupons_disabled:true')).toEqual({
      id: 'cluster_coupons_disabled',
      value: true,
      scope: 'future_orders_cluster',
    });
    expect(() => parseLeverKey('return_window_days:-3')).toThrow();
    expect(() => parseLeverKey('instant_refund:no')).toThrow();
    expect(() => parseLeverKey('unknown:true')).toThrow();
  });

  it('gives each lever a direction against the base terms', () => {
    expect(leverDirection({ id: 'return_window_days', value: 14 }, BASE)).toBe('less');
    expect(leverDirection({ id: 'return_window_days', value: 60 }, BASE)).toBe('more');
    expect(leverDirection({ id: 'return_window_days', value: 30 }, BASE)).toBe('same');
    expect(leverDirection({ id: 'free_return_shipping', value: false }, BASE)).toBe('less');
    expect(leverDirection({ id: 'instant_refund', value: true }, BASE)).toBe('same');
    expect(leverDirection({ id: 'cluster_coupons_disabled', value: true }, BASE)).toBe('less');
    expect(leverDirection({ id: 'transparency_notice', value: true }, BASE)).toBe('less');
    expect(leverDirection({ id: 'priority_size_exchange', value: true }, BASE)).toBe('more');
    expect(leverDirection({ id: 'size_advice', value: true }, BASE)).toBe('more');
  });

  it('finds the restrictive proposals of the examples, and only those', () => {
    const restrictive = ['marco', 'martina', 'chiara', 'lorenzo'];
    for (const name of ['marco', 'martina', 'chiara', 'lorenzo', 'giulia', 'andrea', 'rossi', 'elena', 'insufficient']) {
      const c = card(name);
      expect(hasRestrictiveLever(c.decision.proposed_levers, c.order_terms.base_terms), name).toBe(restrictive.includes(name));
      // The contract agrees: a restriction is exactly what needs a person's approval (rule 5).
      expect(c.decision.requires_approval, name).toBe(restrictive.includes(name));
    }
  });

  it('turns editor values back into levers that differ from the base', () => {
    const marco = card('marco').decision;
    const values = leverValues(marco.levers, BASE);
    expect(values.return_window_days).toBe(14);
    expect(values.free_size_exchange).toBe(true);
    expect(sameLevers(leversFromValues(values, BASE), marco.levers)).toBe(true);
    expect(leversFromValues(leverValues([], BASE), BASE)).toEqual([]);
    expect(sameLevers([{ id: 'instant_refund', value: false }], [{ id: 'instant_refund', value: true }])).toBe(false);
  });
});

describe('restrictionStatus: server fields only (T-SERVER-STATE)', () => {
  afterEach(() => vi.useRealTimers());

  it('reads each example decision state', () => {
    expect(restrictionStatus(card('marco').decision).kind).toBe('needs_approval');
    expect(restrictionStatus(card('lorenzo').decision).kind).toBe('lapsed');
    expect(restrictionStatus(card('giulia').decision).kind).toBe('applies_itself');
    expect(restrictionStatus(card('rossi').decision).kind).toBe('no_change');
    expect(restrictionStatus(card('elena').decision).kind).toBe('no_change');
    const approved = readExample<DecisionResult>('decision_result.marco_approved.json').decision;
    expect(restrictionStatus(approved)).toMatchObject({ kind: 'in_force', reviewAt: '2026-12-27', approver: 'sara' });
    expect(restrictionStatus(readExample<DecisionResult>('decision_result.lift.json').decision).kind).toBe('lifted');
    expect(restrictionStatus(readExample<DecisionResult>('decision_result.reject.json').decision).kind).toBe('rejected');
    expect(restrictionStatus(readExample<DecisionResult>('decision_result.override.json').decision).kind).toBe('no_change');
    const superseded = { ...approved, superseded_by: 'DEC-000102-2' };
    expect(restrictionStatus(superseded).kind).toBe('superseded');
  });

  it('never derives a lapse from the client clock', () => {
    const approved = readExample<DecisionResult>('decision_result.marco_approved.json').decision;
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2027-06-01T00:00:00Z')); // well past review_at 2026-12-27
    expect(restrictionStatus(approved).kind).toBe('in_force');
    expect(restrictionStatus({ ...approved, in_force: false }).kind).toBe('lapsed');
  });

  it('reads queue rows the same way, with review_due from the server', () => {
    const queue = readExample<{ items: Parameters<typeof queueRestrictionKind>[0][] }>('queue_response.json');
    const kinds = Object.fromEntries(queue.items.map((i) => [(i as unknown as { return_id: string }).return_id, queueRestrictionKind(i)]));
    expect(kinds['RET-000102']).toBe('needs_approval');
    expect(kinds['RET-000088']).toBe('lapsed');
    expect(kinds['RET-000101']).toBe('applies_itself');
    expect(kinds['RET-000105']).toBe('no_change');
  });
});

describe('actionPlan (docs/ui-design.md 6.2.4)', () => {
  it('asks for an approval sheet on a proposed restriction', () => {
    expect(actionPlan(card('marco').decision)).toEqual({
      primary: 'approve_restriction',
      secondary: ['override', 'reject'],
      readOnly: false,
    });
  });

  it('confirms a non-restrictive proposal and the guarantee path', () => {
    expect(actionPlan(card('giulia').decision).primary).toBe('confirm');
    expect(actionPlan(card('insufficient').decision).primary).toBe('confirm');
    expect(actionPlan(card('elena').decision).primary).toBe('confirm_guarantee');
  });

  it('reviews a lapsed restriction and lifts one in force; renew only when the contract has it', () => {
    expect(actionPlan(card('lorenzo').decision)).toEqual({ primary: 'review', secondary: [], readOnly: false });
    const approved = readExample<DecisionResult>('decision_result.marco_approved.json').decision;
    expect(actionPlan(approved, false)).toEqual({ primary: null, secondary: ['lift'], readOnly: false });
    expect(actionPlan(approved, true).secondary).toEqual(['lift', 'renew']);
  });

  it('is read-only once rejected, lifted or superseded', () => {
    for (const file of ['decision_result.reject.json', 'decision_result.lift.json', 'decision_result.override.json']) {
      expect(actionPlan(readExample<DecisionResult>(file).decision).readOnly, file).toBe(true);
    }
    expect(actionPlan({ ...card('marco').decision, superseded_by: 'DEC-000102-2' }).readOnly).toBe(true);
  });
});

describe('echoMatches (T-ECHO)', () => {
  it('accepts every example draft and refuses a forged echo', () => {
    for (const name of ['marco', 'giulia', 'elena', 'insufficient', 'lorenzo', 'rossi_card']) {
      const c = card(name);
      if (c.draft) expect(echoMatches(c.draft.decision_echo, c.decision), name).toBe(true);
    }
    const marco = card('marco');
    const echo = marco.draft!.decision_echo;
    expect(echoMatches({ ...echo, quadrant: 'Q1' }, marco.decision)).toBe(false);
    expect(echoMatches({ ...echo, lever_ids: echo.lever_ids.slice(1) }, marco.decision)).toBe(false);
    expect(echoMatches({ ...echo, lever_ids: [...echo.lever_ids].reverse() }, marco.decision)).toBe(true);
  });
});

describe('termsRows', () => {
  it("lists Marco's six changes, all less generous, and keeps the size exchange", () => {
    const d: DecisionView = card('marco').decision;
    const rows = termsRows(d.current_return_terms, d.future_terms, d.levers);
    expect(changedRows(rows).map((r) => [r.lever, r.change])).toEqual([
      ['return_window_days', 'less'],
      ['free_return_shipping', 'less'],
      ['instant_refund', 'less'],
      ['coupons_enabled', 'less'],
      ['cluster_coupons_disabled', 'less'],
      ['transparency_notice', 'less'],
    ]);
    expect(rows.find((r) => r.lever === 'free_size_exchange')?.change).toBe('same');
  });

  it("marks Giulia's upgrade as more generous", () => {
    const d = card('giulia').decision;
    expect(changedRows(termsRows(d.current_return_terms, d.future_terms, d.levers)).map((r) => [r.lever, r.change])).toEqual([
      ['return_window_days', 'more'],
      ['priority_size_exchange', 'more'],
    ]);
  });
});

describe('changedSnapshotRows (US-310)', () => {
  it('marks what the Rossi evidence changed', () => {
    const r = readExample<RecomputeResponse>('recompute_response.rossi_card.json');
    expect([...changedSnapshotRows(r.before, r.after)].sort()).toEqual(['active_links', 'decision', 'risk', 'top_factors']);
  });
});

describe('refs', () => {
  it('names anchors and link ends', () => {
    expect(refAnchorId({ kind: 'link', id: 'C-MARCO-1~C-MARCO-3' })).toBe('ref-link-C-MARCO-1~C-MARCO-3');
    expect(linkEnds('C-MARCO-1~C-MARCO-3')).toEqual(['C-MARCO-1', 'C-MARCO-3']);
  });
});

describe('proposalNeedsApproval (UI3, rule 5)', () => {
  it("reads the engine's proposal, not the current state: Martina's overridden Q3 proposal needed a person", () => {
    const overridden = readExample<DecisionResult>('decision_result.override.json').decision;
    expect(overridden.requires_approval).toBe(false);
    expect(proposalNeedsApproval(overridden, overridden.inputs.base_terms)).toBe(true);
  });

  it('is true for Q3 and Q4 and for restrictive levers, false for a standard Q1 proposal', () => {
    const marco = card('marco').decision;
    expect(proposalNeedsApproval(marco, BASE)).toBe(true);
    expect(proposalNeedsApproval({ quadrant: 'Q1', proposed_levers: [] }, BASE)).toBe(false);
    expect(proposalNeedsApproval({ quadrant: null, proposed_levers: [] }, BASE)).toBe(false);
    expect(proposalNeedsApproval({ quadrant: 'Q2', proposed_levers: [{ id: 'free_return_shipping', value: false, scope: 'future_orders' }] }, BASE)).toBe(true);
  });
});

describe('overrideLock (UI13, rule 4, US-307)', () => {
  it('locks the guarantee path and the not-enough-evidence case, nothing else', () => {
    expect(overrideLock(card('elena'))).toBe('guarantee');
    expect(overrideLock(card('insufficient'))).toBe('insufficient');
    expect(overrideLock(card('marco'))).toBeNull();
  });
});
