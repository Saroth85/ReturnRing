// Warehouse inspection /inspection (US-501..503, Could; docs/ui-design.md 9.2), step 1 "Find the
// return": the withdrawal-path returns (GET /api/returns?path=withdrawal) with a search. Tablet
// first: rows are 48 px targets. Every role can look; only the warehouse creates and confirms.
import { ChevronRight, ScanSearch, Search } from 'lucide-react';
import { useId } from 'react';
import { Link, useSearchParams } from 'react-router';
import { useQueue } from '@/api/queries';
import { usePersona } from '@/app/persona';
import { EmptyState, ErrorState, InlineNotice, LoadingBlock } from '@/components/feedback';
import { DateText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { PLATFORM } from '../copy';
import { PageHeader } from '../shared/layout';
import { INSPECTION } from './copy';

export default function InspectionPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const requestedPage = Number(params.get('page'));
  // QueueQuery bounds pages to 100,000; an invalid shared URL starts at the first page.
  const currentPage = Number.isSafeInteger(requestedPage) && requestedPage > 0 && requestedPage <= 100_000 ? requestedPage : 1;
  const searchId = useId();
  const persona = usePersona();
  const returns = useQueue({ path: ['withdrawal'], q: q.trim() ? q.trim() : null, sort: '-requested_at', page: currentPage });
  const t = INSPECTION;

  const setQuery = (value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set('q', value);
    else next.delete('q');
    next.delete('page');
    setParams(next, { replace: true });
  };
  const goPage = (page: number) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (page <= 1) next.delete('page');
      else next.set('page', String(page));
      return next;
    });

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6" data-page="inspection">
      <PageHeader title={PLATFORM.titles.inspection} intro={t.listIntro} stories={t.stories} />
      <p className="max-w-3xl rounded-control border border-border bg-surface-2 px-3 py-2 text-body text-ink-1" data-inspection-note>
        {t.note}
      </p>
      {persona.role !== 'warehouse' && (
        <InlineNotice>
          <p>{t.readOnly(`${persona.id} (${persona.role})`)}</p>
        </InlineNotice>
      )}
      <div className="flex flex-col gap-1">
        <label htmlFor={searchId} className="text-caption text-ink-2">
          {t.search}
        </label>
        <div className="relative max-w-md">
          <Search aria-hidden="true" size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-3" />
          <input
            id={searchId}
            type="search"
            data-search
            value={q}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t.searchHint}
            className="min-h-12 w-full rounded-control border border-border-strong bg-surface pr-3 pl-9 text-body"
          />
        </div>
      </div>
      {returns.isPending && <LoadingBlock rows={6} />}
      {returns.isError && <ErrorState error={returns.error} onRetry={() => void returns.refetch()} />}
      {returns.data && returns.data.items.length === 0 && (
        <EmptyState
          icon={ScanSearch}
          message={t.empty}
          action={q ? <Button onClick={() => setQuery('')}>{t.clearSearch}</Button> : undefined}
        />
      )}
      {returns.data && returns.data.items.length > 0 && (
        <ul className="flex flex-col divide-y divide-border overflow-hidden rounded-card border border-border bg-surface" data-inspection-list>
          {returns.data.items.map((item) => (
            <li key={item.return_id}>
              <Link
                to={`/inspection/${encodeURIComponent(item.return_id)}`}
                className="flex min-h-12 flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-surface-2 focus-visible:bg-surface-2"
                data-inspection-row={item.return_id}
              >
                <span className="font-mono text-dense font-medium text-accent" data-id>
                  {item.return_id}
                </span>
                <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-ink-1">{item.display_name ?? item.customer_id}</span>
                  <span className="font-mono text-caption text-ink-3" data-id>
                    {item.customer_id}
                  </span>
                </span>
                <span className="text-body text-ink-2">{t.reasons[item.reason] ?? item.reason}</span>
                <span className="text-caption text-ink-3">
                  <DateText value={item.requested_at} />
                </span>
                <span className="inline-flex items-center gap-1 text-body font-medium text-accent">
                  {t.inspect}
                  <ChevronRight aria-hidden="true" size={16} />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {returns.data && (currentPage > 1 || returns.data.total > returns.data.page_size) && (
        <nav aria-label={t.pages} className="flex items-center gap-2 text-caption">
          <Button disabled={currentPage <= 1 || returns.isFetching} onClick={() => goPage(currentPage - 1)}>
            {t.previous}
          </Button>
          <span data-constant>
            {returns.data.page} / {Math.max(1, Math.ceil(returns.data.total / returns.data.page_size))}
          </span>
          <Button disabled={returns.data.page * returns.data.page_size >= returns.data.total || returns.isFetching} onClick={() => goPage(currentPage + 1)}>
            {t.next}
          </Button>
        </nav>
      )}
      <p className="text-caption text-ink-3">{t.listScope}</p>
    </div>
  );
}
