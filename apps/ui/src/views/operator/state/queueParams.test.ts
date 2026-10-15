// Queue tabs and filters in the URL (docs/ui-design.md 6.1): every tab is a QueueQuery, filters
// refine it, and the URL round-trips.
import { describe, expect, it } from 'vitest';
import { normaliseQuery, queueItemMatches } from '@/api/adapters/mockRouter';
import type { QueueItem, QueueResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { buildQueueQuery, EMPTY_FILTERS, parseQueueSearch, TAB_QUERIES, toQueueSearch, type QueueFilters } from './queueParams';

const queue = readExample<QueueResponse>('queue_response.json');
const matching = (query: Record<string, unknown>) =>
  queue.items.filter((item: QueueItem) => queueItemMatches(item, query)).map((i) => i.return_id);

describe('tabs', () => {
  it('"Needs approval" is exactly the contract query of queue_query.json', () => {
    const { query } = buildQueueQuery('approval', EMPTY_FILTERS);
    const search = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (Array.isArray(v)) v.forEach((x) => search.append(k, String(x)));
      else if (v !== undefined && v !== null) search.append(k, String(v));
    }
    expect(normaliseQuery('queue_query', search)).toEqual(readExample('queue_query.json'));
  });

  it('"Review due" is queue_query.review_due.json', () => {
    const { query } = buildQueueQuery('review', EMPTY_FILTERS);
    const search = new URLSearchParams(Object.entries(query).map(([k, v]) => [k, String(v)]));
    expect(normaliseQuery('queue_query', search)).toEqual(readExample('queue_query.review_due.json'));
  });

  it('each tab selects the returns of the design table', () => {
    expect(matching(TAB_QUERIES.approval as Record<string, unknown>)).toEqual(['RET-000102', 'RET-000108', 'RET-000106']);
    expect(matching(TAB_QUERIES.review as Record<string, unknown>)).toEqual(['RET-000088']);
    expect(matching(TAB_QUERIES.guarantee as Record<string, unknown>)).toEqual(['RET-000105', 'RET-000112']);
    expect(matching(TAB_QUERIES.insufficient as Record<string, unknown>)).toEqual(['RET-000109']);
    expect(matching(TAB_QUERIES.open as Record<string, unknown>)).toHaveLength(12);
    expect(matching(TAB_QUERIES.all as Record<string, unknown>)).toHaveLength(14);
  });
});

describe('filters refine the tab', () => {
  it('intersects list filters with the tab', () => {
    const filters: QueueFilters = { ...EMPTY_FILTERS, quadrant: ['Q4', 'Q1'] };
    const { query, impossible } = buildQueueQuery('approval', filters);
    expect(impossible).toBe(false);
    expect(query.quadrant).toEqual(['Q4']);
  });

  it('reports a filter that contradicts the tab instead of widening it', () => {
    expect(buildQueueQuery('approval', { ...EMPTY_FILTERS, quadrant: ['Q1'] }).impossible).toBe(true);
    expect(buildQueueQuery('insufficient', { ...EMPTY_FILTERS, evidence: 'sufficient' }).impossible).toBe(true);
    expect(buildQueueQuery('guarantee', { ...EMPTY_FILTERS, path: ['withdrawal'] }).impossible).toBe(true);
  });

  it('adds search, sort and page', () => {
    const { query } = buildQueueQuery('all', { ...EMPTY_FILTERS, q: ' rossi ', sort: '-risk', page: 2 });
    expect(query).toMatchObject({ q: 'rossi', sort: '-risk', page: 2, page_size: 25 });
  });
});

describe('URL round trip', () => {
  it('keeps tab and filters, and leaves defaults out', () => {
    const filters: QueueFilters = { ...EMPTY_FILTERS, path: ['withdrawal'], quadrant: ['Q3', 'none'], q: 'marco', sort: '-requested_at' };
    const search = toQueueSearch('open', filters);
    expect(search.toString()).toBe('tab=open&path=withdrawal&quadrant=Q3&quadrant=none&q=marco&sort=-requested_at');
    expect(parseQueueSearch(search)).toEqual({ tab: 'open', filters });
    expect(toQueueSearch('approval', EMPTY_FILTERS).toString()).toBe('');
  });

  it('ignores unknown values', () => {
    const parsed = parseQueueSearch(new URLSearchParams('tab=nope&quadrant=Q9&state=done&sort=x&page=-1&evidence=maybe'));
    expect(parsed).toEqual({ tab: 'approval', filters: EMPTY_FILTERS });
  });
});
