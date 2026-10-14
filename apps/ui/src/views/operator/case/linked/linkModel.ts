// The linked-accounts view model (docs/ui-design.md 4.8, 5.5): identifier kinds, link strength,
// weak links, the dispute or explanation state, the graph layout and the coupon timeline. A link
// is a shared identifier, not proof of who uses the accounts.
import type { AccountLink, AccountNode, LinkedAccountsView } from '@/api/models';

export type IdentifierKind = AccountLink['kinds'][number];

/** Strongest first: the edge style of a link is the style of its strongest kind (4.8). */
export const KIND_ORDER: readonly IdentifierKind[] = ['card', 'email', 'device', 'address', 'phone', 'ip_prefix'];

/** An identifier shared by this many accounts or more is a weak link (docs/ui-design.md 5.5). */
export const WEAK_SHARERS = 10;
/** Links shown before "Show all" (docs/ui-design.md 5.5). */
export const STRONGEST_SHOWN = 5;

export type LinkState = 'active' | 'disputed' | 'explained';

export function linkState(link: Pick<AccountLink, 'disputed' | 'explained_by_evidence_id'>): LinkState {
  if (link.disputed) return 'disputed';
  if (link.explained_by_evidence_id) return 'explained';
  return 'active';
}

export const isWeak = (link: Pick<AccountLink, 'max_sharers'>): boolean => link.max_sharers >= WEAK_SHARERS;

/** The strongest kind of a link, or null when it has none (the API requires one; the edge then takes the base style). */
export function strongestKind(link: Pick<AccountLink, 'kinds'>): IdentifierKind | null {
  return KIND_ORDER.find((k) => link.kinds.includes(k)) ?? link.kinds[0] ?? null;
}

/** How many links share each kind (the identifier filter chips). */
export function kindCounts(links: readonly Pick<AccountLink, 'kinds'>[]): Array<{ kind: IdentifierKind; count: number }> {
  return KIND_ORDER.map((kind) => ({ kind, count: links.filter((l) => l.kinds.includes(kind)).length })).filter((k) => k.count > 0);
}

/** Strongest links first: weight after decay, then id (a stable order for the table). */
export function linksByStrength<T extends Pick<AccountLink, 'weight_decayed' | 'link_id'>>(links: readonly T[]): T[] {
  return [...links].sort((a, b) => b.weight_decayed - a.weight_decayed || a.link_id.localeCompare(b.link_id));
}

/** The identifier filter combines kinds with OR; no kind selected shows every link. */
export function matchesKinds(link: Pick<AccountLink, 'kinds'>, kinds: ReadonlySet<IdentifierKind>): boolean {
  return kinds.size === 0 || link.kinds.some((k) => kinds.has(k));
}

/** Links that still count in the cluster features (not disputed, not explained). */
export const activeLinks = (links: readonly AccountLink[]): AccountLink[] => links.filter((l) => linkState(l) === 'active');

export type GraphLayout = 'circle' | 'concentric' | 'cose';
const SMALL = 4;
const MEDIUM = 20;

/** ≤ 4 accounts on a circle, 5 to 20 concentric around the case customer, more with cose (4.8). */
export function graphLayout(accounts: number): GraphLayout {
  if (accounts <= SMALL) return 'circle';
  if (accounts <= MEDIUM) return 'concentric';
  return 'cose';
}

/** The case customer first, then the others clockwise by account creation (4.8). */
export function orderedAccounts(view: Pick<LinkedAccountsView, 'accounts'>): AccountNode[] {
  return [...view.accounts].sort(
    (a, b) =>
      Number(b.is_case_customer) - Number(a.is_case_customer) ||
      a.created_at.localeCompare(b.created_at) ||
      a.customer_id.localeCompare(b.customer_id),
  );
}

export interface TimelineRange {
  start: string;
  end: string;
}

/** From the first account creation to the last welcome coupon redeemed (the coupon sequence). */
export function timelineRange(accounts: readonly AccountNode[]): TimelineRange {
  const created = accounts.map((a) => a.created_at).sort();
  const redeemed = accounts
    .map((a) => a.welcome_coupon_redeemed_at)
    .filter((d): d is string => d !== null)
    .sort();
  const start = created[0] ?? '';
  const end = [created[created.length - 1] ?? start, redeemed[redeemed.length - 1] ?? start].sort().pop() ?? start;
  return { start, end };
}

/** Where a timestamp falls on the range, 0 to 1. */
export function positionOn(iso: string, range: TimelineRange): number {
  const start = Date.parse(range.start);
  const end = Date.parse(range.end);
  if (!(end > start)) return 0;
  return Math.min(1, Math.max(0, (Date.parse(iso) - start) / (end - start)));
}
