// Inspection logic (US-501..503, docs/ui-design.md 9.2, spec 8.8). The vision model suggests a
// checklist; Ahmed answers every item himself (nothing preselected, "not observable" allowed); a
// compensation proposal holds only while his answers confirm its basis, and is applied only when
// he ticks it. Pure functions, tested on the contract examples.
import type { BodyOf, ResponseOf } from '@/api/client';
import type { InspectionView } from '@/api/models';
import { PHOTO_MAX_BYTES, PHOTO_TYPES } from '../constants';

export type ChecklistItem = InspectionView['checklist'][number]['item'];
export type Observation = InspectionView['checklist'][number]['suggestion'];
export type Proposal = InspectionView['proposal'];
export type ConfirmBody = BodyOf<'POST /api/inspections/{inspection_id}/confirm'>;
export type ConfirmResult = ResponseOf<'POST /api/inspections/{inspection_id}/confirm'>;
export type PhotoType = (typeof PHOTO_TYPES)[number];

/** The checklist of spec 8.8, in its order. */
export const CHECKLIST_ITEMS: readonly ChecklistItem[] = ['tag_attached', 'visible_damage', 'photo_quality'];
export const OBSERVATIONS: readonly Observation[] = ['yes', 'no', 'not_observable'];

export type Answers = Record<ChecklistItem, Observation | null>;

export const emptyAnswers = (): Answers => ({ tag_attached: null, visible_damage: null, photo_quality: null });

export const answersComplete = (answers: Answers) => CHECKLIST_ITEMS.every((item) => answers[item] !== null);

/**
 * none: no proposal (manual review, or nothing to claim). pending: an item of its basis is not
 * answered yet. holds: Ahmed answered yes to every item of the basis. contradicted: he did not.
 */
export type ProposalStatus = 'none' | 'pending' | 'holds' | 'contradicted';

export function proposalStatus(proposal: Proposal, answers: Answers): ProposalStatus {
  if (!proposal) return 'none';
  const basis = proposal.basis.map((item) => answers[item]);
  if (basis.some((a) => a === null)) return 'pending';
  return basis.every((a) => a === 'yes') ? 'holds' : 'contradicted';
}

export interface ConfirmChoice {
  proposal: Proposal;
  /** "Apply the compensation", off by default. */
  apply: boolean;
  note: string;
}

/** POST /api/inspections/{id}/confirm: the compensation only when it holds and Ahmed applies it. */
export function confirmRequest(inspector: string, answers: Answers, choice: ConfirmChoice): ConfirmBody {
  if (!answersComplete(answers)) throw new Error('confirm needs an answer for every item');
  const note = choice.note.trim();
  return {
    inspector,
    observations: CHECKLIST_ITEMS.map((item) => ({ item, observation: answers[item] as Observation })),
    apply_compensation: choice.apply && proposalStatus(choice.proposal, answers) === 'holds',
    note: note ? note : null,
  };
}

export type PhotoCheck = { ok: true; mediaType: PhotoType } | { ok: false; reason: 'type' | 'size' };

/** JPEG, PNG or WebP up to the client limit; refused with a reason before any upload. */
export function checkPhoto(file: { type: string; size: number }): PhotoCheck {
  const mediaType = PHOTO_TYPES.find((t) => t === file.type);
  if (!mediaType) return { ok: false, reason: 'type' };
  if (file.size > PHOTO_MAX_BYTES) return { ok: false, reason: 'size' };
  return { ok: true, mediaType };
}

/** `data:image/jpeg;base64,AAAA` -> `AAAA` (PhotoUpload.data_base64). */
export function stripDataUrl(dataUrl: string): string {
  const comma = dataUrl.indexOf(',');
  return dataUrl.startsWith('data:') && comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
}

export type AnswerProblem =
  | { kind: 'other_return'; got: string }
  | { kind: 'other_inspection'; got: string }
  | { kind: 'unexpected_claim'; got: string };

/** An inspection created for another return is shown as such and never confirmed (P10). */
export function createProblem(returnId: string, inspection: InspectionView): AnswerProblem | null {
  return inspection.return_id === returnId ? null : { kind: 'other_return', got: inspection.return_id };
}

/** A confirmation of another inspection, or a claim nobody applied, is shown as such (P10). */
export function confirmProblem(inspectionId: string, applied: boolean, result: ConfirmResult): AnswerProblem | null {
  if (result.inspection.inspection_id !== inspectionId) return { kind: 'other_inspection', got: result.inspection.inspection_id };
  if (!applied && result.claim !== null) return { kind: 'unexpected_claim', got: result.inspection.inspection_id };
  return null;
}
