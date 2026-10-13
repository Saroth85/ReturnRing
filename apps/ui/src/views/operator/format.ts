// Small wording helpers of the operator area: plurals and labels built from data, so the copy
// file keeps no number literal (docs/ui-design.md 10.2).
import { DEMO_USERS, ENDPOINTS } from '@/api/contract.gen';
import type { EndpointKey } from '@/api/client';
import type { CaseCard, Role } from '@/api/models';
import { SHELL } from '@/copy/common';
import { daysBetween } from '@/format';
import { OPERATOR } from './copy';

const ONE = 1;

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === ONE ? one : many}`;
}

export const reasonLabel = (reason: string): string => OPERATOR.reasons[reason] ?? reason;

/** "today", "2 days ago": against the response's as_of, never the wall clock (docs/ui-design.md 4.7). */
export function ageLabel(fromIso: string, asOf: string): string {
  const days = daysBetween(fromIso, asOf);
  if (days <= 0) return 'today';
  return `${plural(days, 'day', 'days')} ago`;
}

export const changesLabel = (n: number): string => plural(n, 'change', 'changes');
export const otherAccountsLabel = (n: number): string => `${plural(n, 'other account', 'other accounts')}`;
export const accountsLabel = (n: number): string => plural(n, 'account', 'accounts');
export const linksLabel = (n: number): string => plural(n, 'link', 'links');

export function roleWord(role: string): string {
  return SHELL.roles[role] ?? role;
}

export function roleOfUser(id: string): Role | null {
  return DEMO_USERS.find((u) => u.id === id)?.role ?? null;
}

/**
 * The reason a write is disabled for this role, in the words of the area ("Decisions are for
 * operators and managers (warehouse can view).") rather than the generic RoleGate line, so each
 * area says once what the persona can do.
 */
export function roleNote(action: EndpointKey, subject: string, role: Role): string {
  const [method, path] = action.split(' ');
  const endpoint = ENDPOINTS.find((e) => e.method === method && e.path === path);
  const roles = (endpoint?.roles ?? []) as readonly string[];
  const allowed = roles.map((r) => `${roleWord(r)}s`).join(' and ');
  return OPERATOR.roleNote(subject, allowed, roleWord(role));
}

/** What changed between the card an operator acted on and the card loaded again (6.4). */
export function cardChanges(before: Pick<CaseCard, 'decision'>, after: Pick<CaseCard, 'decision'>): string[] {
  const r = OPERATOR.recompute.rows;
  const b = before.decision;
  const a = after.decision;
  const none = OPERATOR.queue.quadrantNone;
  const out: string[] = [];
  if (b.decision_id !== a.decision_id) out.push(`${r.decision}: ${b.decision_id} → ${a.decision_id}`);
  if (b.state !== a.state) out.push(`${OPERATOR.queue.state}: ${b.state} → ${a.state}`);
  if (b.quadrant !== a.quadrant) out.push(`${r.quadrant}: ${b.quadrant ?? none} → ${a.quadrant ?? none}`);
  const bl = [...b.lever_ids].sort().join(', ');
  const al = [...a.lever_ids].sort().join(', ');
  if (bl !== al) out.push(`${r.lever_ids}: ${bl || none} → ${al || none}`);
  return out;
}
