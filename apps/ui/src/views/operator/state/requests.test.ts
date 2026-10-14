// The operator sends exactly the request examples of the contract (ADR-15, docs/ui-design.md 6.3),
// apart from free text, which never has to equal the example.
import { describe, expect, it } from 'vitest';
import type { CaseCard } from '@/api/models';
import { readExample } from '@/test/examples';
import { approveBody, disputeBody, evidenceBody, overrideBody, regenerateBody, withoutLeversBody } from './requests';

const card = (name: string) => readExample<CaseCard>(`case_card.${name}.json`);

describe('request bodies', () => {
  it("approves Marco's restriction exactly as decision_request.marco_approve.json", () => {
    expect(approveBody(card('marco').decision, 'it', 'sara')).toEqual(readExample('decision_request.marco_approve.json'));
  });

  it("rejects Andrea's proposal as decision_request.reject.json", () => {
    const example = readExample<Record<string, unknown>>('decision_request.reject.json');
    const body = withoutLeversBody('reject', card('andrea').decision, String(example.override_reason), 'customer_context', 'en', 'sara');
    expect(body).toEqual(example);
  });

  it("lifts Lorenzo's restriction as decision_request.lift.json", () => {
    const example = readExample<Record<string, unknown>>('decision_request.lift.json');
    expect(
      withoutLeversBody('lift', card('lorenzo').decision, `  ${String(example.override_reason)} `, 'periodic_review', 'it', 'luca'),
    ).toEqual(example);
  });

  it('overrides with the chosen levers, a reason and a category, never with free message text', () => {
    const example = readExample<Record<string, unknown>>('decision_request.override.json');
    const body = overrideBody(card('martina').decision, [], String(example.override_reason), 'customer_context', 'it', 'sara');
    expect(body).toEqual({ ...example, message: { lang: 'it', text: null } });
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

  it('regenerates as regenerate_request.json', () => {
    expect(regenerateBody(card('giulia').decision, ['en'], 'formal', 'sara')).toEqual(readExample('regenerate_request.json'));
  });
});
