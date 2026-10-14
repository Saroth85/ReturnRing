// Queue tabs and filters (docs/ui-design.md 6.1). Tabs are links (each a URL); the filters write
// to the URL too, so `/queue?tab=open&quadrant=Q4` can be shared and reloaded.
import clsx from 'clsx';
import { Search, X } from 'lucide-react';
import { useId } from 'react';
import { Link } from 'react-router';
import { DECISION_STATES, EVIDENCE_QUALITIES, PATHS } from '@/api/contract.gen';
import { useQueueCount } from '@/api/queries';
import { QuadrantGlyph } from '@/components/decision';
import { Button, Kbd } from '@/components/ui';
import { OPERATOR } from '../copy';
import {
  activeFilterCount,
  EMPTY_FILTERS,
  QUADRANT_FILTERS,
  QUEUE_TABS,
  SORTS,
  TAB_QUERIES,
  toQueueSearch,
  type QueueFilters,
  type QueueSort,
  type QueueTab,
} from '../state/queueParams';
import { CountChip } from '../ui/CountChip';

function TabLink({ tab, current }: { tab: QueueTab; current: boolean }) {
  const count = useQueueCount(tab, TAB_QUERIES[tab]);
  const search = toQueueSearch(tab, EMPTY_FILTERS).toString();
  return (
    <Link
      to={search ? `/queue?${search}` : '/queue'}
      aria-current={current ? 'page' : undefined}
      data-queue-tab={tab}
      className={clsx(
        'inline-flex min-h-8 items-center gap-2 rounded-control border px-3 text-body',
        current ? 'border-accent bg-accent-bg font-medium text-ink-1' : 'border-border bg-surface text-ink-2 hover:bg-surface-2',
      )}
    >
      {OPERATOR.queue.tabs[tab]}
      {count.data && <CountChip value={count.data.total} provenance={count.data.provenance} />}
    </Link>
  );
}

export function QueueTabs({ tab }: { tab: QueueTab }) {
  return (
    <nav aria-label={OPERATOR.queue.tabsLabel} className="flex flex-wrap gap-2" data-queue-tabs>
      {QUEUE_TABS.map((t) => (
        <TabLink key={t} tab={t} current={t === tab} />
      ))}
    </nav>
  );
}

const SELECT = 'rounded-control border border-border-strong bg-surface px-2 py-1 text-body text-ink-1';

export function QueueFiltersBar({ filters, onChange }: { filters: QueueFilters; onChange(next: QueueFilters): void }) {
  const t = OPERATOR.queue;
  const ids = { path: useId(), state: useId(), evidence: useId(), q: useId(), sort: useId() };
  const set = (patch: Partial<QueueFilters>) => onChange({ ...filters, ...patch, page: 1 });
  const toggleQuadrant = (q: (typeof QUADRANT_FILTERS)[number]) =>
    set({ quadrant: filters.quadrant.includes(q) ? filters.quadrant.filter((x) => x !== q) : [...filters.quadrant, q] });

  return (
    <form
      role="search"
      aria-label={t.filters}
      onSubmit={(e) => e.preventDefault()}
      className="flex flex-wrap items-end gap-x-4 gap-y-2 rounded-card border border-border bg-surface px-3 py-2"
      data-queue-filters
    >
      <label htmlFor={ids.path} className="flex flex-col text-caption text-ink-3">
        {t.path}
        <select
          id={ids.path}
          className={SELECT}
          value={filters.path[0] ?? ''}
          onChange={(e) => set({ path: e.target.value ? [e.target.value as (typeof PATHS)[number]] : [] })}
        >
          <option value="">{t.any}</option>
          {PATHS.map((p) => (
            <option key={p} value={p}>
              {t.paths[p]}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="flex flex-col text-caption text-ink-3">
        <legend>{t.quadrant}</legend>
        <span className="flex gap-1">
          {QUADRANT_FILTERS.map((q) => (
            <button
              key={q}
              type="button"
              aria-pressed={filters.quadrant.includes(q)}
              title={q === 'none' ? t.quadrantNoneLong : undefined}
              onClick={() => toggleQuadrant(q)}
              className={clsx(
                'inline-flex min-h-8 items-center gap-1 rounded-control border px-2 text-body',
                filters.quadrant.includes(q) ? 'border-accent bg-accent-bg text-ink-1' : 'border-border-strong bg-surface text-ink-2',
              )}
              data-id
            >
              {q !== 'none' && <QuadrantGlyph quadrant={q} />}
              {q === 'none' ? t.quadrantNone : q}
            </button>
          ))}
        </span>
      </fieldset>
      <label htmlFor={ids.state} className="flex flex-col text-caption text-ink-3">
        {t.state}
        <select
          id={ids.state}
          className={SELECT}
          value={filters.state[0] ?? ''}
          onChange={(e) => set({ state: e.target.value ? [e.target.value as (typeof DECISION_STATES)[number]] : [] })}
        >
          <option value="">{t.any}</option>
          {DECISION_STATES.map((s) => (
            <option key={s} value={s}>
              {t.states[s]}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={ids.evidence} className="flex flex-col text-caption text-ink-3">
        {t.evidence}
        <select
          id={ids.evidence}
          className={SELECT}
          value={filters.evidence ?? ''}
          onChange={(e) => set({ evidence: (e.target.value || null) as QueueFilters['evidence'] })}
        >
          <option value="">{t.any}</option>
          {EVIDENCE_QUALITIES.map((q) => (
            <option key={q} value={q}>
              {t.evidenceValues[q]}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={ids.q} className="flex min-w-48 flex-1 flex-col text-caption text-ink-3">
        <span className="inline-flex items-center gap-1">
          {t.search} <Kbd>/</Kbd>
        </span>
        <span className="relative">
          <Search aria-hidden="true" size={14} className="absolute top-2 left-2 text-ink-3" />
          <input
            id={ids.q}
            type="search"
            data-search
            value={filters.q}
            placeholder={t.searchPlaceholder}
            onChange={(e) => set({ q: e.target.value })}
            className={clsx(SELECT, 'w-full pl-7')}
          />
        </span>
      </label>
      <label htmlFor={ids.sort} className="flex flex-col text-caption text-ink-3">
        {t.sort}
        <select id={ids.sort} className={SELECT} value={filters.sort} onChange={(e) => set({ sort: e.target.value as QueueSort })}>
          {SORTS.map((s) => (
            <option key={s} value={s}>
              {t.sorts[s]}
            </option>
          ))}
        </select>
      </label>
      {activeFilterCount(filters) > 0 && (
        <Button variant="quiet" onClick={() => onChange({ ...EMPTY_FILTERS, sort: filters.sort })}>
          <X aria-hidden="true" size={14} />
          {t.clearFilters}
        </Button>
      )}
    </form>
  );
}
