// What the parts of the case card share: the card (with the operator's own actions applied), the
// sheet opener, the selected link, the send language and the role gates of the writes.
import { createContext, useContext } from 'react';
import type { CaseCard, EntityRef, Lang, Lever, OutboxMessage } from '@/api/models';
import type { Gate } from '@/components/feedback';

export type SheetKind = 'confirm' | 'approve' | 'override' | 'reject' | 'review' | 'evidence' | 'dispute' | 'regenerate';

export interface SheetRequest {
  kind: SheetKind;
  /** The link a dispute or an explanation starts from. */
  linkId?: string;
  /** Levers an override starts from (an alternative's "Use these terms instead…"). */
  prefill?: Lever[];
}

export interface CaseGates {
  decide: Gate;
  evidence: Gate;
  dispute: Gate;
  regenerate: Gate;
  /** One line per area saying what the persona can do (RoleGate wording would repeat). */
  decideNote: string | null;
  evidenceNote: string | null;
  regenerateNote: string | null;
}

export interface CaseUi {
  card: CaseCard;
  openSheet(request: SheetRequest): void;
  selectedLinkId: string | null;
  selectLink(linkId: string | null): void;
  /** Scroll to and pulse an entity shown on the card (evidence refs, agent citations). */
  selectRef(ref: Pick<EntityRef, 'kind' | 'id'>): void;
  sendLang: Lang;
  setSendLang(lang: Lang): void;
  gates: CaseGates;
  /** The message an action of this session queued for this case, if any. */
  sentMessage: OutboxMessage | null;
  /** Message-sending actions wait for the draft (docs/ui-design.md 6.2.4). */
  draftPending: boolean;
}

export const CaseUiContext = createContext<CaseUi | null>(null);

export function useCaseUi(): CaseUi {
  const value = useContext(CaseUiContext);
  if (!value) throw new Error('useCaseUi outside the case card');
  return value;
}
