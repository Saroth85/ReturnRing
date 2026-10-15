// Per-browser conveniences of the operator area (docs/ui-design.md 6.2.3, 6.3.3): collapsed
// sections, auto-advance after an action, and the last action's confirmation. Storage can fail
// (private windows): safeStorage never throws and the page works without it.
import { createStore, safeStorage, useStore } from '@/api/store';
import type { Lang } from '@/api/models';

const AUTO_ADVANCE_KEY = 'returnring.operator.autoAdvance';
const SECTIONS_KEY = 'returnring.operator.collapsed';

export const autoAdvanceStore = createStore<boolean>(safeStorage.get('local', AUTO_ADVANCE_KEY) !== 'off');
export function setAutoAdvance(on: boolean): void {
  safeStorage.set('local', AUTO_ADVANCE_KEY, on ? null : 'off');
  autoAdvanceStore.set(on);
}
export const useAutoAdvance = () => useStore(autoAdvanceStore);

function readCollapsed(): string[] {
  try {
    const parsed: unknown = JSON.parse(safeStorage.get('local', SECTIONS_KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

export const collapsedStore = createStore<string[]>(readCollapsed());
export function toggleSection(id: string, collapsed: boolean): void {
  collapsedStore.set((list) => {
    const next = collapsed ? [...new Set([...list, id])] : list.filter((x) => x !== id);
    safeStorage.set('local', SECTIONS_KEY, JSON.stringify(next));
    return next;
  });
}
export const useCollapsed = () => useStore(collapsedStore);

/** The language the customer message is sent in (O-4: Italian by default). */
export const sendLangStore = createStore<Lang>('it');
export const useSendLang = () => useStore(sendLangStore);

/** The confirmation of the last action, shown on the next card or the queue (no toast region yet). */
export interface ActionNotice {
  returnId: string;
  text: string;
  messageId: string | null;
  auditEntryIds: number[];
  /** Why the act sent no message: a lift or a renewal, or an act after the refund deadline. */
  noMessage?: string | null;
  /** The version a lift or a renewal superseded (DecisionResult.superseded). */
  supersededId?: string | null;
  /** The cluster coupon pause the act confirmed or reversed (DecisionResult.coupon_application). */
  coupon?: { applicationId: string; state: string; accounts: number; reviewAt: string; inForce: boolean } | null;
}
export const noticeStore = createStore<ActionNotice | null>(null);
export const useActionNotice = () => useStore(noticeStore);
