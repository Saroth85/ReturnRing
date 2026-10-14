// Decision logic of the UI (docs/ui-design.md 5.3, 6.2.4, 11.3): the restriction status, the
// actions a decision state allows, the decision-echo check and the terms diff. Server fields
// only (P9): the client clock never decides whether a restriction is in force or lapsed.
import { HAS_RENEW } from '@/api/models';
import type { CaseCard, CaseSnapshot, DecisionView, DraftView, Lever, QueueItem, Terms } from '@/api/models';
import { hasRestrictiveLever, leverDirection, leverKey, type Direction, type LeverId, type TermKey } from './levers';

// ---------------------------------------------------------------------------------------------
// Restriction status (RestrictionBadge)

export type RestrictionKind =
  | 'superseded'
  | 'rejected'
  | 'lifted'
  | 'no_change'
  | 'needs_approval'
  | 'in_force'
  | 'lapsed'
  | 'applies_itself'
  | 'benefit_in_force'
  | 'not_in_force';

export interface RestrictionStatus {
  kind: RestrictionKind;
  reviewAt: string | null;
  approver: string | null;
  approvedAt: string | null;
  supersededBy: string | null;
}

export type RestrictionFields = Pick<
  DecisionView,
  'state' | 'requires_approval' | 'in_force' | 'review_at' | 'superseded_by' | 'revision_trigger' | 'lever_ids' | 'approver' | 'approved_at'
>;

/**
 * The status a RestrictionBadge shows, from the API fields of a decision version only. A
 * restriction is a version that needs a person's approval (requires_approval refers to the
 * levers of the current state, ADR-15); `in_force` says whether it applies at `as_of`.
 */
export function restrictionStatus(view: RestrictionFields): RestrictionStatus {
  const status = (kind: RestrictionKind): RestrictionStatus => ({
    kind,
    reviewAt: view.review_at,
    approver: view.approver,
    approvedAt: view.approved_at,
    supersededBy: view.superseded_by,
  });
  if (view.superseded_by) return status('superseded');
  if (view.state === 'rejected') return status('rejected');
  if (view.lever_ids.length === 0) {
    return status(view.revision_trigger === 'review' && view.state === 'overridden' ? 'lifted' : 'no_change');
  }
  if (view.requires_approval) {
    if (view.state === 'proposed') return status('needs_approval');
    return status(view.in_force ? 'in_force' : 'lapsed');
  }
  if (view.state === 'proposed') return status(view.in_force ? 'applies_itself' : 'not_in_force');
  return status(view.in_force ? 'benefit_in_force' : 'not_in_force');
}

/** The same status for a queue row, which carries `review_due` instead of `in_force`. */
export function queueRestrictionKind(item: Pick<QueueItem, 'state' | 'requires_approval' | 'lever_ids' | 'review_due'>): RestrictionKind {
  if (item.state === 'rejected') return 'rejected';
  if (item.lever_ids.length === 0) return 'no_change';
  if (item.requires_approval) {
    if (item.state === 'proposed') return 'needs_approval';
    return item.review_due ? 'lapsed' : 'in_force';
  }
  return item.state === 'proposed' ? 'applies_itself' : 'benefit_in_force';
}

// ---------------------------------------------------------------------------------------------
// Actions by decision state (docs/ui-design.md 6.2.4)

export type PrimaryAction = 'approve_restriction' | 'confirm' | 'confirm_guarantee' | 'review';
export type SecondaryAction = 'override' | 'reject' | 'lift' | 'renew';

export interface ActionPlan {
  primary: PrimaryAction | null;
  secondary: SecondaryAction[];
  /** Nothing can be acted on: a read-only summary of who, when and why. */
  readOnly: boolean;
}

export function actionPlan(
  view: Pick<DecisionView, 'state' | 'requires_approval' | 'in_force' | 'superseded_by' | 'path'>,
  hasRenew: boolean = HAS_RENEW,
): ActionPlan {
  if (view.superseded_by) return { primary: null, secondary: [], readOnly: true };
  if (view.state === 'proposed') {
    const primary: PrimaryAction = view.requires_approval
      ? 'approve_restriction'
      : view.path === 'guarantee'
        ? 'confirm_guarantee'
        : 'confirm';
    return { primary, secondary: ['override', 'reject'], readOnly: false };
  }
  if (view.state !== 'rejected' && view.requires_approval) {
    if (view.in_force) return { primary: null, secondary: hasRenew ? ['lift', 'renew'] : ['lift'], readOnly: false };
    return { primary: 'review', secondary: [], readOnly: false };
  }
  return { primary: null, secondary: [], readOnly: true };
}

/**
 * Whether the engine's proposal itself needed a person (rule 5): a Q3 or Q4 quadrant, or any
 * restrictive lever against the base terms. `requires_approval` and `review_at` describe the levers
 * of the current state (ADR-15 section 4), so an overridden proposal reads them as false and null;
 * the decision record's "Engine output" uses this instead (UI3).
 */
export function proposalNeedsApproval(proposal: Pick<DecisionView, 'quadrant' | 'proposed_levers'>, base: Terms): boolean {
  return proposal.quadrant === 'Q3' || proposal.quadrant === 'Q4' || hasRestrictiveLever(proposal.proposed_levers, base);
}

/**
 * Why an override may not move any lever away from the base terms: the guarantee path (rule 4) and
 * a case without enough evidence, which is never treated as safe or suspicious (US-307; the legal
 * guard refuses INSUFFICIENT_EVIDENCE_RESTRICTED). Null when the levers are free (UI13).
 */
export type OverrideLock = 'guarantee' | 'insufficient';
export function overrideLock(card: Pick<CaseCard, 'path'> & { decision: Pick<DecisionView, 'evidence_quality'> }): OverrideLock | null {
  if (card.path === 'guarantee') return 'guarantee';
  if (card.decision.evidence_quality === 'insufficient') return 'insufficient';
  return null;
}

/** Whether an action sends the customer message, and so waits for a ready draft (6.2.4). */
export const SENDS_MESSAGE: Record<PrimaryAction | SecondaryAction, boolean> = {
  approve_restriction: true,
  confirm: true,
  confirm_guarantee: true,
  review: true,
  override: true,
  reject: true,
  lift: true,
  renew: true,
};

// ---------------------------------------------------------------------------------------------
// Decision echo (T-ECHO): a draft that does not repeat the engine decision is withheld.

export function echoMatches(echo: DraftView['decision_echo'], decision: Pick<DecisionView, 'path' | 'quadrant' | 'lever_ids'>): boolean {
  if (echo.path !== decision.path || echo.quadrant !== decision.quadrant) return false;
  const a = [...echo.lever_ids].sort();
  const b = [...decision.lever_ids].sort();
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

/**
 * What the engine decided, as the draft echoes it: the path, the quadrant and the proposed
 * levers. After an override the levers of the current state differ, but the draft still has to
 * repeat the engine's proposal it was written for.
 */
export function engineEcho(decision: Pick<DecisionView, 'path' | 'quadrant' | 'proposed_levers'>): DraftView['decision_echo'] {
  return { path: decision.path, quadrant: decision.quadrant, lever_ids: decision.proposed_levers.map(leverKey) };
}

// ---------------------------------------------------------------------------------------------
// Terms diff (TermsDiff, ApproveRestrictionSheet)

export interface TermRow {
  lever: LeverId;
  /** This order's value (the snapshot stored at checkout). */
  current: number | boolean;
  /** The value for future orders. */
  future: number | boolean;
  change: Direction;
}

const TERM_KEYS: readonly TermKey[] = [
  'return_window_days',
  'free_return_shipping',
  'instant_refund',
  'free_size_exchange',
  'coupons_enabled',
];
const EXTRA_LEVERS: readonly LeverId[] = ['cluster_coupons_disabled', 'transparency_notice', 'size_advice', 'priority_size_exchange'];

/**
 * The rows of "This order" against "Future orders": the five terms always, then the levers
 * outside the terms that the decision sets (cluster coupons, return-rate notice, size advice,
 * priority size exchange).
 */
export function termsRows(current: Terms, future: Terms, levers: readonly Pick<Lever, 'id' | 'value'>[]): TermRow[] {
  const rows: TermRow[] = TERM_KEYS.map((key) => ({
    lever: key,
    current: current[key],
    future: future[key],
    change: leverDirection({ id: key, value: future[key] }, current),
  }));
  for (const id of EXTRA_LEVERS) {
    const lever = levers.find((l) => l.id === id);
    if (!lever) continue;
    rows.push({ lever: id, current: false, future: lever.value, change: leverDirection(lever, current) });
  }
  return rows;
}

export const changedRows = (rows: readonly TermRow[]): TermRow[] => rows.filter((r) => r.change !== 'same');

// ---------------------------------------------------------------------------------------------
// Recompute before and after (RecomputeComparison, US-310)

export type SnapshotRowKey =
  | 'decision'
  | 'quadrant'
  | 'evidence_quality'
  | 'risk'
  | 'top_factors'
  | 'lever_ids'
  | 'requires_approval'
  | 'cluster_size'
  | 'active_links';

export const SNAPSHOT_ROWS: readonly SnapshotRowKey[] = [
  'decision',
  'quadrant',
  'evidence_quality',
  'risk',
  'top_factors',
  'lever_ids',
  'requires_approval',
  'cluster_size',
  'active_links',
];

/** Which rows differ between the two versions (marked "changed", never by colour alone). */
export function changedSnapshotRows(before: CaseSnapshot, after: CaseSnapshot): Set<SnapshotRowKey> {
  const changed = new Set<SnapshotRowKey>();
  if (before.decision_id !== after.decision_id || before.version !== after.version) changed.add('decision');
  if (before.quadrant !== after.quadrant) changed.add('quadrant');
  if (before.evidence_quality !== after.evidence_quality) changed.add('evidence_quality');
  if ((before.risk?.value ?? null) !== (after.risk?.value ?? null)) changed.add('risk');
  const phrases = (s: CaseSnapshot) => s.top_factors.map((f) => f.phrase).join('\n');
  if (phrases(before) !== phrases(after)) changed.add('top_factors');
  if ([...before.lever_ids].sort().join() !== [...after.lever_ids].sort().join()) changed.add('lever_ids');
  if (before.requires_approval !== after.requires_approval) changed.add('requires_approval');
  if (before.cluster_size !== after.cluster_size) changed.add('cluster_size');
  if (before.active_links !== after.active_links) changed.add('active_links');
  return changed;
}
