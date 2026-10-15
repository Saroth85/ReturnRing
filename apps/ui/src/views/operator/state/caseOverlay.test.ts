// Never downgrade (docs/ui-design.md 11.2): the answer to the operator's action stays on screen
// until the server's card is at least as new, then the server wins.
import { afterEach, describe, expect, it } from 'vitest';
import type { CaseCard, DecisionResult, LinkDisputeResponse, RecomputeResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { applyOverlay, isNewer, overlayStore, recordDecision, recordDraft, recordRecompute, resetOverlays } from './caseOverlay';

const card = (name: string) => readExample<CaseCard>(`case_card.${name}.json`);
const result = (name: string) => readExample<DecisionResult>(`decision_result.${name}.json`);

afterEach(() => resetOverlays());

describe('applyOverlay', () => {
  it('keeps an approval the server has not shown yet', () => {
    const marco = card('marco');
    const approved = result('marco_approved');
    const out = applyOverlay(marco, { decision: approved.decision });
    expect(out.decision.state).toBe('approved');
    expect(out.decision.approver).toBe('sara');
    expect(out.revisions).toHaveLength(1);
    expect(out.revisions[0]?.state).toBe('approved');
    expect(out.draft).toBe(marco.draft);
  });

  it('lets the server win once it is as new', () => {
    const approved = result('marco_approved').decision;
    const serverCaughtUp = { ...card('marco'), decision: approved };
    const olderOverlay = card('marco').decision;
    expect(applyOverlay(serverCaughtUp, { decision: olderOverlay }).decision.state).toBe('approved');
    expect(isNewer(olderOverlay, approved)).toBe(false);
  });

  it('adds the version a lift creates, without the earlier draft', () => {
    const out = applyOverlay(card('lorenzo'), { decision: result('lift').decision });
    expect(out.decision.decision_id).toBe('DEC-000088-2');
    expect(out.revisions.map((r) => [r.decision_id, r.trigger, r.state])).toEqual([
      ['DEC-000088-1', 'initial', 'approved'],
      ['DEC-000088-2', 'review', 'overridden'],
    ]);
    expect(out.draft).toBeNull();
  });

  it('ignores an answer about another return', () => {
    const giulia = card('giulia');
    expect(applyOverlay(giulia, { decision: result('marco_approved').decision })).toBe(giulia);
  });

  it('shows the recomputed version after evidence: risk, factors, draft and the explained link', () => {
    const recompute = readExample<RecomputeResponse>('recompute_response.rossi_card.json');
    const linkId = 'C-ROSSI-CARD-1~C-ROSSI-CARD-2';
    const out = applyOverlay(card('rossi_card'), { recompute, links: { [linkId]: { explained_by_evidence_id: 'EV-000004' } } });
    expect(out.decision.decision_id).toBe('DEC-000104-2');
    expect(out.risk?.score.value).toBe(0.21);
    expect(out.risk?.top_factors[2]?.phrase).toBe('No identifier shared beyond address and device');
    expect(out.draft).toBe(recompute.draft);
    expect(out.linked_accounts?.links.find((l) => l.link_id === linkId)?.explained_by_evidence_id).toBe('EV-000004');
    expect(out.revisions.map((r) => r.version)).toEqual([1, 2]);
  });

  it("shows Chiara's dispute: the link disputed and the new proposal", () => {
    const dispute = readExample<LinkDisputeResponse>('link_dispute_response.json');
    const out = applyOverlay(card('chiara'), { recompute: dispute.recompute!, links: { [dispute.link.link_id]: dispute.link } });
    expect(out.decision.quadrant).toBe('Q1');
    expect(out.decision.requires_approval).toBe(false);
    expect(out.linked_accounts?.links.find((l) => l.link_id === dispute.link.link_id)?.disputed).toBe(true);
  });

  it('applies a regenerated draft only to the decision it was written for', () => {
    const giulia = card('giulia');
    const original = giulia.draft!.generation.generation_id;
    const draft = { ...giulia.draft!, generation: { ...giulia.draft!.generation, generation_id: 'GEN-000101-2' } };
    expect(applyOverlay(giulia, { draft: { decisionId: 'DEC-000101-1', draft } }).draft?.generation.generation_id).toBe('GEN-000101-2');
    expect(applyOverlay(giulia, { draft: { decisionId: 'DEC-000101-0', draft } }).draft?.generation.generation_id).toBe(original);
  });
});

describe('the overlay store', () => {
  it('records by return id and resets', () => {
    const approved = result('marco_approved');
    recordDecision(approved.decision, approved.outbox_message);
    recordDraft('RET-000101', 'DEC-000101-1', card('giulia').draft!);
    expect(overlayStore.get()['RET-000102']?.sentMessage?.message_id).toBe('MSG-000102-1');
    expect(overlayStore.get()['RET-000101']?.draft?.decisionId).toBe('DEC-000101-1');
    recordRecompute(readExample<RecomputeResponse>('recompute_response.rossi_card.json'));
    expect(overlayStore.get()['RET-000104']?.recompute?.decision.version).toBe(2);
    resetOverlays();
    expect(overlayStore.get()).toEqual({});
  });
});
