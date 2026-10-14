// Inspection logic (US-501..503, docs/ui-design.md 9.2), on the contract examples.
import { describe, expect, it } from 'vitest';
import type { InspectionView } from '@/api/models';
import type { ResponseOf } from '@/api/client';
import { readExample } from '@/test/examples';
import {
  answersComplete,
  CHECKLIST_ITEMS,
  checkPhoto,
  confirmProblem,
  confirmRequest,
  createProblem,
  emptyAnswers,
  proposalStatus,
  stripDataUrl,
  type Answers,
} from './inspection';

const awaiting = readExample<InspectionView>('inspection.awaiting.json');
const manual = readExample<InspectionView>('inspection.manual_review.json');
const confirmed = readExample<ResponseOf<'POST /api/inspections/{inspection_id}/confirm'>>('inspection_confirm_response.json');

const valentinaAnswers: Answers = { tag_attached: 'no', visible_damage: 'yes', photo_quality: 'yes' };

describe('checklist answers (US-501)', () => {
  it('starts with nothing preselected', () => {
    expect(emptyAnswers()).toEqual({ tag_attached: null, visible_damage: null, photo_quality: null });
    expect(answersComplete(emptyAnswers())).toBe(false);
  });

  it('needs all three items, "not observable" included', () => {
    expect(CHECKLIST_ITEMS).toEqual(['tag_attached', 'visible_damage', 'photo_quality']);
    expect(answersComplete({ ...valentinaAnswers, photo_quality: null })).toBe(false);
    expect(answersComplete({ tag_attached: 'not_observable', visible_damage: 'not_observable', photo_quality: 'no' })).toBe(true);
  });
});

describe('proposalStatus (US-502: only from confirmed observations)', () => {
  it('has no proposal on manual review (US-503)', () => {
    expect(proposalStatus(manual.proposal, valentinaAnswers)).toBe('none');
  });

  it('waits until every item of the basis is answered', () => {
    expect(proposalStatus(awaiting.proposal, emptyAnswers())).toBe('pending');
    expect(proposalStatus(awaiting.proposal, { ...emptyAnswers(), tag_attached: 'no' })).toBe('pending');
  });

  it('holds only when Ahmed answers yes to every item of its basis', () => {
    expect(proposalStatus(awaiting.proposal, valentinaAnswers)).toBe('holds');
  });

  it('is contradicted by a no or a not observable on its basis', () => {
    expect(proposalStatus(awaiting.proposal, { ...valentinaAnswers, visible_damage: 'no' })).toBe('contradicted');
    expect(proposalStatus(awaiting.proposal, { ...valentinaAnswers, visible_damage: 'not_observable' })).toBe('contradicted');
  });
});

describe('confirmRequest', () => {
  it('is the contract example when Ahmed confirms the damage and applies the compensation', () => {
    expect(confirmRequest('ahmed', valentinaAnswers, { proposal: awaiting.proposal, apply: true, note: '' })).toEqual(
      readExample('inspection_confirm_request.json'),
    );
  });

  it('never applies a compensation the observations do not support', () => {
    const contradicted = { ...valentinaAnswers, visible_damage: 'no' as const };
    expect(confirmRequest('ahmed', contradicted, { proposal: awaiting.proposal, apply: true, note: '' }).apply_compensation).toBe(false);
    expect(confirmRequest('ahmed', valentinaAnswers, { proposal: null, apply: true, note: '' }).apply_compensation).toBe(false);
  });

  it('keeps a typed note, trimmed', () => {
    expect(confirmRequest('ahmed', valentinaAnswers, { proposal: null, apply: false, note: '  Checked by hand  ' }).note).toBe(
      'Checked by hand',
    );
  });

  it('refuses incomplete answers', () => {
    expect(() => confirmRequest('ahmed', emptyAnswers(), { proposal: null, apply: false, note: '' })).toThrow(/every item/);
  });
});

describe('checkPhoto', () => {
  it('accepts JPEG, PNG and WebP up to the limit', () => {
    expect(checkPhoto({ type: 'image/jpeg', size: 1000 })).toEqual({ ok: true, mediaType: 'image/jpeg' });
    expect(checkPhoto({ type: 'image/webp', size: 8 * 1024 * 1024 })).toEqual({ ok: true, mediaType: 'image/webp' });
  });

  it('refuses other types and files above the limit, with a reason', () => {
    expect(checkPhoto({ type: 'image/gif', size: 1000 })).toEqual({ ok: false, reason: 'type' });
    expect(checkPhoto({ type: 'image/png', size: 8 * 1024 * 1024 + 1 })).toEqual({ ok: false, reason: 'size' });
  });

  it('strips the data URL prefix to the base64 the API takes', () => {
    expect(stripDataUrl('data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2Q==')).toBe('/9j/4AAQSkZJRgABAQAAAQABAAD/2Q==');
    expect(stripDataUrl('/9j/4AAQ')).toBe('/9j/4AAQ');
  });
});

describe('answers that do not belong to the request are shown, never used (P10)', () => {
  it('accepts an inspection of the return inspected', () => {
    expect(createProblem('RET-000110', awaiting)).toBeNull();
    expect(createProblem('RET-000111', manual)).toBeNull();
  });

  it('flags an inspection of another return', () => {
    expect(createProblem('RET-000111', awaiting)).toEqual({ kind: 'other_return', got: 'RET-000110' });
  });

  it('accepts the confirmation of the inspection sent, with the claim it asked for', () => {
    expect(confirmProblem('INS-000014', true, confirmed)).toBeNull();
  });

  it('flags a confirmation of another inspection', () => {
    expect(confirmProblem('INS-000015', false, confirmed)).toEqual({ kind: 'other_inspection', got: 'INS-000014' });
  });

  it('flags a claim nobody applied', () => {
    expect(confirmProblem('INS-000014', false, confirmed)).toEqual({ kind: 'unexpected_claim', got: 'INS-000014' });
  });
});
