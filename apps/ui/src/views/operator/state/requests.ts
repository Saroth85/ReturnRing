// Request bodies of the operator actions (ADR-15 section 3, docs/ui-design.md 6.3). Every action
// sends the decision_id it was shown and the acting person; the customer message is never free
// text (`message.text` null): the API fills the template fields and appends the legal clause.
import type { BodyOf } from '@/api/client';
import type { CaseCard, DecisionView, Lang, Lever } from '@/api/models';
import type { OverrideCategory } from '../ui/fields';

export type DecisionBody = BodyOf<'POST /api/returns/{return_id}/decision'>;
export type EvidenceBody = BodyOf<'POST /api/returns/{return_id}/evidence'>;
export type DisputeBody = BodyOf<'POST /api/links/{link_id}/dispute'>;
export type RegenerateBody = BodyOf<'POST /api/returns/{return_id}/regenerate'>;
export type EvidenceKind = EvidenceBody['kind'];
export type Tone = NonNullable<RegenerateBody['tone']>;

/** Kinds of evidence that explain links: the request names them (EvidenceRequest). */
export const LINK_EXPLAINING_KINDS: readonly EvidenceKind[] = ['household_members', 'shared_payment_explained', 'collective_address'];

/** Approve: `levers` equal to the proposal, exactly as decision_request.marco_approve.json. */
export function approveBody(decision: Pick<DecisionView, 'decision_id' | 'proposed_levers'>, lang: Lang, approver: string): DecisionBody {
  return {
    decision_id: decision.decision_id,
    action: 'approve',
    levers: decision.proposed_levers,
    message: { lang, text: null },
    override_reason: null,
    override_category: null,
    approver,
  };
}

export function overrideBody(
  decision: Pick<DecisionView, 'decision_id'>,
  levers: Lever[],
  reason: string,
  category: OverrideCategory,
  lang: Lang,
  approver: string,
): DecisionBody {
  return {
    decision_id: decision.decision_id,
    action: 'override',
    levers,
    message: { lang, text: null },
    override_reason: reason.trim(),
    override_category: category,
    approver,
  };
}

/**
 * Reject, lift and (once the contract has it) renew carry no levers: reject keeps this order's
 * terms, lift goes back to the standard terms, renew keeps the restriction with a new review date.
 */
export function withoutLeversBody(
  action: DecisionBody['action'],
  decision: Pick<DecisionView, 'decision_id'>,
  reason: string,
  category: OverrideCategory,
  lang: Lang,
  approver: string,
): DecisionBody {
  return {
    decision_id: decision.decision_id,
    action,
    levers: null,
    message: { lang, text: null },
    override_reason: reason.trim(),
    override_category: category,
    approver,
  };
}

export function evidenceBody(
  decision: Pick<DecisionView, 'decision_id'>,
  kind: EvidenceKind,
  note: string,
  linkIds: string[],
  author: string,
): EvidenceBody {
  return {
    decision_id: decision.decision_id,
    kind,
    note: note.trim(),
    target_link_ids: LINK_EXPLAINING_KINDS.includes(kind) ? linkIds : [],
    author,
  };
}

export function disputeBody(card: Pick<CaseCard, 'return_id' | 'decision'>, reason: string, author: string): DisputeBody {
  return { reason: reason.trim(), author, return_id: card.return_id, decision_id: card.decision.decision_id };
}

export function regenerateBody(
  decision: Pick<DecisionView, 'decision_id'>,
  languages: Lang[],
  tone: Tone,
  requestedBy: string,
): RegenerateBody {
  const langs = languages.length === 2 ? ([languages[0], languages[1]] as [Lang, Lang]) : ([languages[0] ?? 'it'] as [Lang]);
  return { decision_id: decision.decision_id, languages: langs, tone, requested_by: requestedBy };
}
