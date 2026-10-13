// What the operator's own actions changed on a case, kept until the server's card catches up
// (docs/ui-design.md 11.2 "never downgrade"): a refetch that still shows an older state of the
// case (a lower version, or a proposal already acted on) never replaces the answer the API gave
// to the action. Once the server's card is as new, it wins and the overlay is ignored.
import { useMemo } from 'react';
import type { AccountLink, CaseCard, DecisionView, DraftView, Metric, OutboxMessage, RecomputeResponse } from '@/api/models';
import { useCase } from '@/api/queries';
import { createStore, useStore } from '@/api/store';

/** models.ts does not alias it yet (open issue): derived from the card. */
type RevisionSummary = CaseCard['revisions'][number];

export interface CaseOverlay {
  /** The decision the API returned to an action (approve, override, reject, lift). */
  decision?: DecisionView;
  /** The message that action queued in the simulated outbox. */
  sentMessage?: OutboxMessage;
  /** A recompute after evidence or a dispute: the new version and its draft. */
  recompute?: RecomputeResponse;
  /** Links the recompute changed (explained by evidence, disputed), by link id. */
  links?: Record<string, Partial<AccountLink>>;
  /** A regenerated draft, valid for one decision id. */
  draft?: { decisionId: string; draft: DraftView };
}

export const overlayStore = createStore<Record<string, CaseOverlay>>({});

function patch(returnId: string, next: (current: CaseOverlay) => CaseOverlay): void {
  overlayStore.set((all) => ({ ...all, [returnId]: next(all[returnId] ?? {}) }));
}

export function recordDecision(decision: DecisionView, sentMessage?: OutboxMessage): void {
  patch(decision.return_id, (o) => ({ ...o, decision, sentMessage: sentMessage ?? o.sentMessage }));
}

export function recordRecompute(recompute: RecomputeResponse, links: Record<string, Partial<AccountLink>> = {}): void {
  patch(recompute.return_id, (o) => ({ ...o, recompute, links: { ...o.links, ...links }, decision: undefined, draft: undefined }));
}

export function recordDraft(returnId: string, decisionId: string, draft: DraftView): void {
  patch(returnId, (o) => ({ ...o, draft: { decisionId, draft } }));
}

export function resetOverlays(): void {
  overlayStore.set({});
}

/** Whether `next` is a later state of the same case than `current` (never downgrade). */
export function isNewer(next: DecisionView, current: DecisionView): boolean {
  if (next.return_id !== current.return_id) return false;
  if (next.version !== current.version) return next.version > current.version;
  if (next.decision_id !== current.decision_id) return false;
  return current.state === 'proposed' && next.state !== 'proposed';
}

function withRevision(revisions: CaseCard['revisions'], decision: DecisionView, risk: Metric | null): CaseCard['revisions'] {
  const existing = revisions.find((r) => r.decision_id === decision.decision_id);
  if (existing) {
    return revisions.map((r) =>
      r.decision_id === decision.decision_id ? { ...r, state: decision.state, lever_ids: decision.lever_ids } : r,
    ) as CaseCard['revisions'];
  }
  const summary: RevisionSummary = {
    decision_id: decision.decision_id,
    version: decision.version,
    trigger: decision.revision_trigger,
    created_at: decision.created_at,
    quadrant: decision.quadrant,
    risk,
    lever_ids: decision.lever_ids,
    state: decision.state,
  };
  return [...revisions, summary].sort((a, b) => a.version - b.version) as CaseCard['revisions'];
}

/** The card as the operator's last actions left it (pure; tested on the contract examples). */
export function applyOverlay(card: CaseCard, overlay: CaseOverlay | undefined): CaseCard {
  if (!overlay) return card;
  let out = card;
  const recompute = overlay.recompute;
  if (recompute && recompute.return_id === card.return_id && isNewer(recompute.decision, card.decision)) {
    const { after } = recompute;
    const risk =
      out.risk && after.risk
        ? {
            ...out.risk,
            score: after.risk,
            top_factors: after.top_factors.length === 3 ? (after.top_factors as typeof out.risk.top_factors) : out.risk.top_factors,
          }
        : out.risk;
    const links = overlay.links ?? {};
    const linked =
      out.linked_accounts && Object.keys(links).length > 0
        ? {
            ...out.linked_accounts,
            links: out.linked_accounts.links.map((l) =>
              links[l.link_id] ? { ...l, ...links[l.link_id] } : l,
            ) as typeof out.linked_accounts.links,
          }
        : out.linked_accounts;
    out = {
      ...out,
      decision: recompute.decision,
      draft: recompute.draft,
      draft_status: 'ready',
      evidence: { ...out.evidence, quality: after.evidence_quality },
      risk,
      linked_accounts: linked,
      revisions: withRevision(out.revisions, recompute.decision, after.risk),
    };
  }
  if (overlay.decision && isNewer(overlay.decision, out.decision)) {
    const newVersion = overlay.decision.version > out.decision.version;
    out = {
      ...out,
      decision: overlay.decision,
      // A new version from an action (a lift) has no draft of its own: its message is in the outbox.
      draft: newVersion ? null : out.draft,
      draft_status: 'ready',
      revisions: withRevision(out.revisions, overlay.decision, out.risk?.score ?? null),
    };
  }
  if (overlay.draft && overlay.draft.decisionId === out.decision.decision_id) {
    out = { ...out, draft: overlay.draft.draft, draft_status: 'ready' };
  }
  return out;
}

/** The case card with the operator's own actions applied (docs/ui-design.md 11.2). */
export function useCaseView(returnId: string | undefined) {
  const query = useCase(returnId);
  const overlays = useStore(overlayStore);
  const overlay = returnId ? overlays[returnId] : undefined;
  const card = useMemo(() => (query.data ? applyOverlay(query.data, overlay) : undefined), [query.data, overlay]);
  return { query, card, sentMessage: overlay?.sentMessage ?? null };
}
