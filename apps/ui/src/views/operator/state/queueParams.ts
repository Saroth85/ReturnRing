// Queue tabs and filters (docs/ui-design.md 6.1). Each tab is a QueueQuery; the filters refine it
// and live in the URL, so a queue view can be shared and `?from=<tab>` keeps the case loop.
import { DECISION_STATES, EVIDENCE_QUALITIES, PATHS, QUADRANTS, QUEUE_SORTS } from '@/api/contract.gen';
import type { DecisionState, EvidenceQualityId, PathId, QuadrantId, QueueQuery } from '@/api/models';
import type { QueueQueryParams } from '@/api/queries';

export const QUEUE_TABS = ['approval', 'review', 'open', 'guarantee', 'insufficient', 'all'] as const;
export type QueueTab = (typeof QUEUE_TABS)[number];
/** Sara's default tab (docs/ui-design.md 6.1). */
export const DEFAULT_TAB: QueueTab = 'approval';

export const TAB_QUERIES: Record<QueueTab, QueueQueryParams> = {
  approval: { state: ['proposed'], quadrant: ['Q3', 'Q4'], requires_approval: true },
  review: { review_due: true },
  open: { state: ['proposed'] },
  guarantee: { path: ['guarantee'] },
  insufficient: { evidence_quality: 'insufficient' },
  all: {},
};

export type QueueSort = NonNullable<QueueQuery['sort']>;
/** The sort orders of the contract (QueueQuery.sort), in its order. */
export const SORTS: readonly QueueSort[] = QUEUE_SORTS;
export const DEFAULT_SORT: QueueSort = '-expected_net_benefit';
export type QuadrantFilter = QuadrantId | 'none';
export const QUADRANT_FILTERS: readonly QuadrantFilter[] = [...QUADRANTS, 'none'];
/** The contract's page size for the queue (QueueQuery default). */
export const PAGE_SIZE = 25;

export interface QueueFilters {
  path: PathId[];
  quadrant: QuadrantFilter[];
  state: DecisionState[];
  evidence: EvidenceQualityId | null;
  q: string;
  sort: QueueSort;
  page: number;
}

export const EMPTY_FILTERS: QueueFilters = {
  path: [],
  quadrant: [],
  state: [],
  evidence: null,
  q: '',
  sort: DEFAULT_SORT,
  page: 1,
};

const isTab = (v: string | null): v is QueueTab => v !== null && (QUEUE_TABS as readonly string[]).includes(v);

function listOf<T extends string>(search: URLSearchParams, name: string, allowed: readonly T[]): T[] {
  return search.getAll(name).filter((v): v is T => (allowed as readonly string[]).includes(v));
}

export function parseTab(value: string | null): QueueTab | null {
  return isTab(value) ? value : null;
}

export function parseQueueSearch(search: URLSearchParams): { tab: QueueTab; filters: QueueFilters } {
  const tab = parseTab(search.get('tab')) ?? DEFAULT_TAB;
  const evidence = search.get('evidence');
  const sort = search.get('sort');
  const page = Number(search.get('page'));
  return {
    tab,
    filters: {
      path: listOf(search, 'path', PATHS),
      quadrant: listOf(search, 'quadrant', QUADRANT_FILTERS),
      state: listOf(search, 'state', DECISION_STATES),
      evidence: evidence && (EVIDENCE_QUALITIES as readonly string[]).includes(evidence) ? (evidence as EvidenceQualityId) : null,
      q: search.get('q') ?? '',
      sort: sort && (SORTS as readonly string[]).includes(sort) ? (sort as QueueSort) : DEFAULT_SORT,
      page: Number.isInteger(page) && page > 1 ? page : 1,
    },
  };
}

/** The URL of a queue view; defaults are left out (`/queue` is Sara's default view). */
export function toQueueSearch(tab: QueueTab, filters: QueueFilters): URLSearchParams {
  const search = new URLSearchParams();
  if (tab !== DEFAULT_TAB) search.set('tab', tab);
  filters.path.forEach((v) => search.append('path', v));
  filters.quadrant.forEach((v) => search.append('quadrant', v));
  filters.state.forEach((v) => search.append('state', v));
  if (filters.evidence) search.set('evidence', filters.evidence);
  if (filters.q.trim()) search.set('q', filters.q.trim());
  if (filters.sort !== DEFAULT_SORT) search.set('sort', filters.sort);
  if (filters.page > 1) search.set('page', String(filters.page));
  return search;
}

function refine<T extends string>(
  tabList: readonly T[] | null | undefined,
  filterList: readonly T[],
): { list: T[] | undefined; empty: boolean } {
  if (!tabList || tabList.length === 0) return { list: filterList.length > 0 ? [...filterList] : undefined, empty: false };
  if (filterList.length === 0) return { list: [...tabList], empty: false };
  const list = tabList.filter((v) => filterList.includes(v));
  return { list, empty: list.length === 0 };
}

/**
 * The QueueQuery of a tab refined by the filters. A filter that contradicts the tab (Q1 in
 * "Needs approval") makes the view empty instead of silently widening it: `impossible`.
 */
export function buildQueueQuery(tab: QueueTab, filters: QueueFilters): { query: QueueQueryParams; impossible: boolean } {
  const base = TAB_QUERIES[tab];
  const path = refine(base.path, filters.path);
  const quadrant = refine(base.quadrant, filters.quadrant);
  const state = refine(base.state, filters.state);
  let evidence = base.evidence_quality ?? filters.evidence ?? undefined;
  let evidenceConflict = false;
  if (base.evidence_quality && filters.evidence && base.evidence_quality !== filters.evidence) {
    evidenceConflict = true;
    evidence = base.evidence_quality;
  }
  const query: QueueQueryParams = {
    ...base,
    ...(path.list ? { path: path.list } : {}),
    ...(quadrant.list ? { quadrant: quadrant.list } : {}),
    ...(state.list ? { state: state.list } : {}),
    ...(evidence ? { evidence_quality: evidence } : {}),
    ...(filters.q.trim() ? { q: filters.q.trim() } : {}),
    sort: filters.sort,
    page: filters.page,
    page_size: PAGE_SIZE,
  };
  return { query, impossible: path.empty || quadrant.empty || state.empty || evidenceConflict };
}

/** The query of a tab with no filter: what `?from=<tab>` walks through on the case card. */
export const tabQuery = (tab: QueueTab): QueueQueryParams => buildQueueQuery(tab, EMPTY_FILTERS).query;

export function activeFilterCount(filters: QueueFilters): number {
  return filters.path.length + filters.quadrant.length + filters.state.length + (filters.evidence ? 1 : 0) + (filters.q.trim() ? 1 : 0);
}
