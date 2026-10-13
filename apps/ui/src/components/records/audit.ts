// Audit log helpers (US-604, ADR-15 section 4 "Audit", docs/ui-design.md 8.7). The action sets
// mirror contracts/vocab.py (HUMAN_ONLY_ACTIONS, REASON_REQUIRED_ACTIONS); the test keeps them in
// step with the generated AUDIT_ACTIONS.
import type { AuditEntry, AuditQuery } from '@/api/models';

export type AuditAction = AuditEntry['action'];
export type DecisionAuditState = Extract<NonNullable<AuditEntry['after']>, { decision_id: string; lever_ids: string[] }>;

/** Actions a person takes: never logged with the system role (contracts/vocab.py). */
export const HUMAN_ONLY_ACTIONS: ReadonlySet<AuditAction> = new Set<AuditAction>([
  'decision_approved',
  'decision_overridden',
  'decision_rejected',
  'restriction_lifted',
  'evidence_added',
  'link_disputed',
  'inspection_confirmed',
  'catalog_task_opened',
  'catalog_task_updated',
  'coupon_rule_approved',
  'coupon_rule_application_reversed',
  'assumptions_updated',
]);

/** Actions whose entry carries a reason (contracts/vocab.py). */
export const REASON_REQUIRED_ACTIONS: ReadonlySet<AuditAction> = new Set<AuditAction>([
  'decision_overridden',
  'decision_rejected',
  'restriction_lifted',
  'evidence_added',
  'link_disputed',
  'coupon_rule_application_reversed',
]);

/** The entries about a decision version carry a typed DecisionAuditState (ADR-15). */
export function isDecisionState(value: AuditEntry['before']): value is DecisionAuditState {
  return (
    value !== null &&
    typeof value === 'object' &&
    typeof (value as Record<string, unknown>).decision_id === 'string' &&
    Array.isArray((value as Record<string, unknown>).lever_ids)
  );
}

/** A human action recorded with the system role: the integrity check of ck_audit_log_human_action. */
export function systemRecordedHumanAction(entry: Pick<AuditEntry, 'action' | 'actor_role'>): boolean {
  return entry.actor_role === 'system' && HUMAN_ONLY_ACTIONS.has(entry.action);
}

/** An approval of a restriction: a person and a review date (rule 5). */
export function restrictiveApproval(entry: AuditEntry): { reviewAt: string } | null {
  if (entry.action !== 'decision_approved' || !isDecisionState(entry.after)) return null;
  const { requires_approval: restrictive, review_at: reviewAt } = entry.after;
  return restrictive && reviewAt ? { reviewAt } : null;
}

/** Where an entry's entity lives in the UI (cross-area navigation by URL, design 13.3). */
export function entityHref(entry: AuditEntry): string | null {
  const returnPath = entry.return_id ? `/returns/${encodeURIComponent(entry.return_id)}` : null;
  switch (entry.entity_type) {
    case 'decision':
      return returnPath && entry.decision_id ? `${returnPath}/versions/${encodeURIComponent(entry.decision_id)}` : returnPath;
    case 'catalog_task':
      return '/products/tasks';
    case 'simulator_assumptions':
      return '/simulator';
    case 'inspection':
      return entry.return_id ? `/inspection/${encodeURIComponent(entry.return_id)}` : '/inspection';
    case 'outbox_message':
      return entry.return_id ? `/outbox?return_id=${encodeURIComponent(entry.return_id)}` : '/outbox';
    case 'link_dispute': {
      const after = entry.after as Record<string, unknown> | null;
      const link = typeof after?.link_id === 'string' ? after.link_id : null;
      return returnPath ? `${returnPath}${link ? `?link=${encodeURIComponent(link)}` : '?focus=linked'}` : null;
    }
    case 'evidence_addition':
      return returnPath ? `${returnPath}?focus=linked` : null;
    case 'coupon_rule':
      return '/coupons';
    case 'coupon_rule_application':
      return '/reviews';
    case 'llm_generation':
      return returnPath ? `${returnPath}?focus=message` : null;
    case 'terms_snapshot':
      return returnPath;
    default:
      return returnPath;
  }
}

/** A day of a date field as the date-time bounds of AuditQuery (UTC, inclusive). */
export const sinceOf = (date: string) => `${date}T00:00:00Z`;
export const untilOf = (date: string) => `${date}T23:59:59Z`;

/** The filters of the audit page (AuditQuery without format and paging), plus "human actions only". */
export interface AuditFilters {
  return_id?: string | null;
  decision_id?: string | null;
  actor?: string | null;
  action?: AuditAction[] | null;
  entity_type?: AuditEntry['entity_type'] | null;
  since?: string | null;
  until?: string | null;
  humanOnly?: boolean;
}

/** The query sent to the API: AuditQuery fields that are set. */
export function toAuditQuery(f: AuditFilters): Partial<AuditQuery> {
  const q: Partial<AuditQuery> = {};
  if (f.return_id) q.return_id = f.return_id;
  if (f.decision_id) q.decision_id = f.decision_id;
  if (f.actor) q.actor = f.actor;
  if (f.action && f.action.length > 0) q.action = f.action;
  if (f.entity_type) q.entity_type = f.entity_type;
  if (f.since) q.since = f.since;
  if (f.until) q.until = f.until;
  return q;
}

const day = (iso: string) => iso.slice(0, 10);

/**
 * The same filters applied to a page in the browser. A correct server returns entries that pass
 * them unchanged; the mock server filters only by return id, so this keeps the screen honest.
 */
export function matchesAuditFilters(entry: AuditEntry, f: AuditFilters): boolean {
  if (f.return_id && entry.return_id !== f.return_id) return false;
  if (f.decision_id && entry.decision_id !== f.decision_id) return false;
  if (f.actor && entry.actor !== f.actor) return false;
  if (f.action && f.action.length > 0 && !f.action.includes(entry.action)) return false;
  if (f.entity_type && entry.entity_type !== f.entity_type) return false;
  // Timestamps are ISO UTC with a Z: text order is time order.
  if (f.since && entry.at < f.since) return false;
  if (f.until && entry.at > f.until) return false;
  if (f.humanOnly && entry.actor_role === 'system') return false;
  return true;
}

/** `returnring-audit-<filters>.csv`, from the filters that are set. */
export function auditCsvFilename(f: AuditFilters): string {
  const parts = [
    f.return_id,
    f.decision_id,
    f.actor,
    ...(f.action ?? []),
    f.entity_type,
    f.since ? `since-${day(f.since)}` : null,
    f.until ? `until-${day(f.until)}` : null,
    f.humanOnly ? 'human' : null,
  ]
    .filter((p): p is string => Boolean(p))
    .map((p) => p.replace(/[^A-Za-z0-9._-]+/g, '_'));
  return `returnring-audit-${parts.length > 0 ? parts.join('-') : 'all'}.csv`;
}

/** Lines of a CSV text whose rows pass the human-only filter (for the export of that toggle). */
export function keepHumanRows(csv: string): string {
  const lines = csv.split('\n');
  const header = lines[0] ?? '';
  const roleIndex = header.split(',').indexOf('actor_role');
  if (roleIndex < 0) return csv;
  // actor_role is never quoted (an enum) and comes before the free-text columns.
  const kept = lines.slice(1).filter((line) => line !== '' && line.split(',')[roleIndex] !== 'system');
  return `${[header, ...kept].join('\n')}\n`;
}
