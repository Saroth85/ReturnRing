// The operator sends exactly the request examples of the contract (ADR-15, docs/ui-design.md 6.3),
// apart from free text, which never has to equal the example.
import { describe, expect, it } from 'vitest';
import type { CaseCard, SentenceId } from '@/api/models';
import { readExample } from '@/test/examples';
import { approveBody, disputeBody, evidenceBody, liftBody, overrideBody, regenerateBody, rejectBody, renewBody } from './requests';

const card = (name: string) => readExample<CaseCard>(`case_card.${name}.json`);

describe('request bodies', () => {
  it("approves Marco's restriction exactly as decision_request.marco_approve.json", () => {
    expect(approveBody(card('marco').decision, 'it', 'sara')).toEqual(readExample('decision_request.marco_approve.json'));
  });

  it("rejects Andrea's proposal as decision_request.reject.json", () => {
    const example = readExample<Record<string, unknown>>('decision_request.reject.json');
    const body = rejectBody(card('andrea').decision, String(example.override_reason), 'customer_context', 'en', 'sara');
    expect(body).toEqual(example);
  });

  it("lifts Lorenzo's restriction as decision_request.lift.json: a reason, no message", () => {
    const example = readExample<Record<string, unknown>>('decision_request.lift.json');
    expect(liftBody(card('lorenzo').decision, `  ${String(example.override_reason)} `, 'periodic_review', 'luca')).toEqual(example);
  });

  it("renews Lorenzo's restriction as decision_request.renew.json: no reason, no category, no message", () => {
    expect(renewBody(card('lorenzo').decision, 'luca')).toEqual(readExample('decision_request.renew.json'));
  });

  it('overrides with the chosen levers, a reason, a category and reviewed sentences, never free text', () => {
    const example = readExample<{ override_reason: string; message: { sentence_ids: SentenceId[] } }>('decision_request.override.json');
    const body = overrideBody(card('martina').decision, [], example.override_reason, 'customer_context', 'it', 'sara', example.message.sentence_ids);
    expect(body).toEqual(example);
    expect(overrideBody(card('martina').decision, [], example.override_reason, 'customer_context', 'it', 'sara').message).toEqual({
      lang: 'it',
      sentence_ids: null,
    });
  });

  it('explains the Rossi card link as evidence_request.rossi_card.json', () => {
    const example = readExample<Record<string, unknown>>('evidence_request.rossi_card.json');
    const body = evidenceBody(
      card('rossi_card').decision,
      'shared_payment_explained',
      String(example.note),
      ['C-ROSSI-CARD-1~C-ROSSI-CARD-2'],
      'sara',
    );
    expect(body).toEqual(example);
    expect(evidenceBody(card('rossi_card').decision, 'customer_contact', 'Phone call', ['x'], 'sara').target_link_ids).toEqual([]);
  });

  it("disputes Chiara's card link as link_dispute_request.json", () => {
    const example = readExample<Record<string, unknown>>('link_dispute_request.json');
    expect(disputeBody(card('chiara'), String(example.reason), 'sara')).toEqual(example);
  });

  it('regenerates as regenerate_request.json, with no tone', () => {
    expect(regenerateBody(card('giulia').decision, ['en'], 'sara')).toEqual(readExample('regenerate_request.json'));
    expect(regenerateBody(card('giulia').decision, ['it', 'en', 'it'], 'sara').languages).toEqual(['it', 'en']);
  });
});
