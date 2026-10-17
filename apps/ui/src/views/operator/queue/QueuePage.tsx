// Returns queue /queue (US-301, docs/ui-design.md 6.1): ordered by expected net benefit, tabs that
// are queries, filters in the URL, j/k and Enter, the top cards prefetched. No bulk actions: each
// decision has its own audit entry and message, and each restriction its own person.
import { CircleCheck, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useQueue } from '@/api/queries';
import { useHotkeys } from '@/app/hotkeys';
import { EmptyState, ErrorState, LoadingBlock } from '@/components/feedback';
import { DateText, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { OPERATOR } from '../copy';
import { ActionNoticeBar } from '../case/ActionNoticeBar';
import { usePrefetchCase } from '../state/prefetch';
import { setAutoAdvance, useAutoAdvance } from '../state/prefs';
import { buildQueueQuery, EMPTY_FILTERS, parseQueueSearch, toQueueSearch, type QueueFilters } from '../state/queueParams';
import { QueueFiltersBar, QueueTabs } from './QueueControls';
import { QueueTable } from './QueueTable';

const PREFETCH_TOP = 3;

/** Rows that appeared after the first load of this session carry "New" until opened. */
const seen = new Set<string>();
let baselineTaken = false;

export default function QueuePage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const { tab, filters } = parseQueueSearch(params);
  const { query, impossible } = useMemo(() => buildQueueQuery(tab, filters), [tab, filters]);
  const queue = useQueue(query);
  const prefetch = usePrefetchCase();
  const autoAdvance = useAutoAdvance();
  const [selected, setSelected] = useState(0);
  const tableRef = useRef<HTMLDivElement>(null);
  const t = OPERATOR.queue;
  const items = useMemo(() => (impossible ? [] : (queue.data?.items ?? [])), [impossible, queue.data]);

  const setFilters = (next: QueueFilters) => setParams(toQueueSearch(tab, next), { replace: true });

  // Prefetch the top cards on load (US-301 budget); remember the baseline for the "New" dots.
  useEffect(() => {
    if (!queue.data || impossible) return;
    queue.data.items.slice(0, PREFETCH_TOP).forEach((item) => prefetch(item.return_id));
    if (!baselineTaken) {
      queue.data.items.forEach((item) => seen.add(item.return_id));
      baselineTaken = true;
    }
  }, [queue.data, impossible, prefetch]);

  const newIds = useMemo(() => new Set(items.filter((i) => baselineTaken && !seen.has(i.return_id)).map((i) => i.return_id)), [items]);

  const clamp = (i: number) => Math.max(0, Math.min(items.length - 1, i));
  const focusRow = (i: number) => {
    const index = clamp(i);
    setSelected(index);
    tableRef.current?.querySelector<HTMLElement>(`[data-row-link="${index}"]`)?.focus();
  };
  const markSeen = (returnId: string) => {
    seen.add(returnId);
  };
  const openSelected = () => {
    const item = items[clamp(selected)];
    if (!item) return;
    markSeen(item.return_id);
    void navigate(`/returns/${item.return_id}?from=${tab}`);
  };
  useHotkeys({
    j: () => focusRow(selected + 1),
    k: () => focusRow(selected - 1),
    ArrowDown: () => focusRow(selected + 1),
    ArrowUp: () => focusRow(selected - 1),
  });
  const openRef = useRef(openSelected);
  useEffect(() => {
    openRef.current = openSelected;
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' || event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return;
      const active = document.activeElement;
      if (active && active !== document.body && active.id !== 'main') return;
      event.preventDefault();
      openRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const refresh = () => {
    void queue.refetch();
    void client.invalidateQueries({ queryKey: ['queueCount'] });
  };
  const page = queue.data;
  const totalPages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1;

  return (
    <div className="mx-auto flex max-w-content flex-col gap-4" data-page="queue">
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="text-page">{OPERATOR.titles.queue}</h1>
        {page && (
          <span className="text-caption text-ink-3">
            {t.asOf} <DateText value={page.as_of} />
          </span>
        )}
        <Button variant="quiet" className="ml-auto" onClick={refresh}>
          <RefreshCw aria-hidden="true" size={14} />
          {t.refresh}
        </Button>
      </header>
      <ActionNoticeBar />
      <QueueTabs tab={tab} />
      <QueueFiltersBar filters={filters} onChange={setFilters} />
      <div className="flex flex-wrap items-center justify-between gap-2 text-caption text-ink-3">
        <span>{t.keyboard}</span>
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" checked={autoAdvance} onChange={(e) => setAutoAdvance(e.target.checked)} />
          {t.autoAdvance}
        </label>
      </div>
      {queue.isError && <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />}
      {queue.isPending && !impossible && <LoadingBlock rows={8} />}
      {impossible && (
        <EmptyState
          message={t.impossible}
          action={
            <Button variant="quiet" onClick={() => setFilters({ ...EMPTY_FILTERS, sort: filters.sort })}>
              {t.clearFilters}
            </Button>
          }
        />
      )}
      {!impossible && page && page.items.length === 0 && (
        <EmptyState
          icon={CircleCheck}
          message={tab === 'approval' ? t.empty.approval : tab === 'review' ? t.empty.review : t.empty.other}
          action={
            tab === 'approval' ? (
              <Link to="/queue?tab=open" className="text-accent underline underline-offset-2">
                {t.seeAllOpen}
              </Link>
            ) : (
              <Button variant="quiet" onClick={() => setFilters({ ...EMPTY_FILTERS, sort: filters.sort })}>
                {t.clearFilters}
              </Button>
            )
          }
        />
      )}
      {!impossible && page && page.items.length > 0 && (
        <ProvenanceScope provenance={page.provenance} covers="linked-account counts and lever counts of the queue rows" className="min-w-0">
          <div ref={tableRef}>
            <QueueTable page={page} tab={tab} selected={selected} onSelect={setSelected} onOpen={markSeen} onPrefetch={prefetch} newIds={newIds} />
          </div>
          {totalPages > 1 && (
            <nav aria-label={t.pageLabel} className="mt-2 flex items-center gap-2 text-caption">
              <Button disabled={filters.page <= 1} onClick={() => setFilters({ ...filters, page: filters.page - 1 })}>
                {t.previous}
              </Button>
              <span data-constant>
                {filters.page} / {totalPages}
              </span>
              <Button disabled={filters.page >= totalPages} onClick={() => setFilters({ ...filters, page: filters.page + 1 })}>
                {t.next}
              </Button>
            </nav>
          )}
          <p className="mt-2 text-caption text-ink-3">
            {t.provenanceNote} {t.noBulk}
          </p>
          <ProvenanceFooter />
        </ProvenanceScope>
      )}
    </div>
  );
}
