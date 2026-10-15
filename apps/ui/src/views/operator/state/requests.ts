// Request bodies of the operator actions (ADR-15 section 3, docs/ui-design.md 6.3). Every action
// sends the decision_id it was shown and the acting person. The customer message is never free
// text: the operator picks a language and, at most, reviewed phrasebook sentences
// (`message.sentence_ids`, null for the draft's own selection); the API writes the body, the
// template fields and the legal clause (rule 10). DecisionRequest (contract 1.8.0):
// - approve, override, reject: a message; override and reject a reason and a category;
// - lift: a reason and a category, no message (the customer sees the terms at checkout);
// - renew: no reason, no category, no message;
// - levers only for override (approve repeats the proposal, as the contract example does).
import type { BodyOf } from '@/api/client';
import type { CaseCard, DecisionView, Lang, Lever, SentenceId } from '@/api/models';
import type { OverrideCategory } from '../ui/fields';

export type DecisionBody = BodyOf<'POST /api/returns/{return_id}/decision'>;
export type EvidenceBody = BodyOf<'POST /api/returns/{return_id}/evidence'>;
export type DisputeBody = BodyOf<'POST /api/links/{link_id}/dispute'>;
export type RegenerateBody = BodyOf<'POST /api/returns/{return_id}/regenerate'>;
export type EvidenceKind = EvidenceBody['kind'];

/** Kinds of evidence that explain links: the request names them (EvidenceRequest). */
export const LINK_EXPLAINING_KINDS: readonly EvidenceKind[] = ['household_members', 'shared_payment_explained', 'collective_address'];

/** The message of an act: its language and, optionally, other reviewed sentences than the draft's. */
function messageChoice(lang: Lang, sentenceIds: SentenceId[] | null): NonNullable<DecisionBody['message']> {
  return { lang, sentence_ids: sentenceIds };
}

/** Approve: `levers` equal to the proposal, exactly as decision_request.marco_approve.json. */
export function approveBody(
  decision: Pick<DecisionView, 'decision_id' | 'proposed_levers'>,
  lang: Lang,
  approver: string,
  sentenceIds: SentenceId[] | null = null,
): DecisionBody {
  return {
    decision_id: decision.decision_id,
    action: 'approve',
    levers: decision.proposed_levers,
    message: messageChoice(lang, sentenceIds),
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
  sentenceIds: SentenceId[] | null = null,
): DecisionBody {
  return {
    decision_id: decision.decision_id,
    action: 'override',
    levers,
    message: messageChoice(lang, sentenceIds),
    override_reason: reason.trim(),
    override_category: category,
    approver,
  };
}

/** Reject: this order keeps its terms; the customer gets a message (unless the refund is overdue). */
export function rejectBody(
  decision: Pick<DecisionView, 'decision_id'>,
  reason: string,
  category: OverrideCategory,
  lang: Lang,
  approver: string,
  sentenceIds: SentenceId[] | null = null,
): DecisionBody {
  return {
    decision_id: decision.decision_id,
    action: 'reject',
    levers: null,
    message: messageChoice(lang, sentenceIds),
    override_reason: reason.trim(),
    override_category: category,
    approver,
  };
}

/** Lift a restriction back to the standard terms: a reason and a category, no message. */
export function liftBody(decision: Pick<DecisionView, 'decision_id'>, reason: string, category: OverrideCategory, approver: string): DecisionBody {
  return {
    decision_id: decision.decision_id,
    action: 'lift',
    levers: null,
    message: null,
    override_reason: reason.trim(),
    override_category: category,
    approver,
  };
}

/** Renew a restriction at or after its review date: the fresh proposal, a new review date, nothing else. */
export function renewBody(decision: Pick<DecisionView, 'decision_id'>, approver: string): DecisionBody {
  return {
    decision_id: decision.decision_id,
    action: 'renew',
    levers: null,
    message: null,
    override_reason: null,
    override_category: null,
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

/** A new draft in one or both languages (no tone since contract 1.2.0: llm/ has none to apply). */
export function regenerateBody(decision: Pick<DecisionView, 'decision_id'>, languages: Lang[], requestedBy: string): RegenerateBody {
  const unique = [...new Set(languages)];
  return { decision_id: decision.decision_id, languages: unique.length > 0 ? unique : ['it'], requested_by: requestedBy };
}
