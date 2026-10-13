// Simulated outbox /outbox (US-304, docs/ui-design.md 6.7): every message a decision queued, in its
// three layers (connective text, template fields filled by code, the fixed legal clause), with the
// decision and the generation it came from. No email leaves the system.
import clsx from 'clsx';
import { Mail } from 'lucide-react';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { useOutbox } from '@/api/queries';
import { EmptyState, ErrorState, InlineNotice, LoadingBlock } from '@/components/feedback';
import { MessagePreview } from '@/components/message';
import { DateText, IdText } from '@/components/provenance';
import { OPERATOR } from '../copy';

export default function OutboxPage() {
  const [params] = useSearchParams();
  const returnId = params.get('return_id');
  const outbox = useOutbox(returnId ? { return_id: returnId } : {});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const t = OPERATOR.outbox;
  const items = outbox.data?.items ?? [];
  const selected = items.find((m) => m.message_id === selectedId) ?? items[0];
  const c = t.columns;
  return (
    <div className="mx-auto flex max-w-content flex-col gap-4" data-page="outbox">
      <h1 className="text-page">{OPERATOR.titles.outbox}</h1>
      <InlineNotice tone="info">{t.banner}</InlineNotice>
      {returnId && (
        <p className="flex flex-wrap items-center gap-2 text-body">
          {t.filtered} <IdText id={returnId} />
          <Link to="/outbox" className="text-accent underline underline-offset-2">
            {t.showAll}
          </Link>
        </p>
      )}
      {outbox.isPending && <LoadingBlock rows={5} />}
      {outbox.isError && <ErrorState error={outbox.error} onRetry={() => void outbox.refetch()} />}
      {outbox.data && items.length === 0 && <EmptyState icon={Mail} message={t.empty} />}
      {items.length > 0 && (
        <div className="grid gap-4 xl:grid-cols-12">
          <div className="relative min-w-0 overflow-x-auto rounded-card border border-border bg-surface xl:col-span-6">
            <table className="w-full text-dense" data-outbox-table>
              <thead className="bg-surface-2 text-left text-caption text-ink-2">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {c.subject}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {c.customer}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {c.lang}
                  </th>
                  <th scope="col" className="hidden px-3 py-2 font-medium md:table-cell">
                    {c.status}
                  </th>
                  <th scope="col" className="hidden px-3 py-2 font-medium md:table-cell">
                    {c.clause}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {c.by}
                  </th>
                  <th scope="col" className="hidden px-3 py-2 font-medium lg:table-cell">
                    {c.at}
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((m) => {
                  const active = m.message_id === selected?.message_id;
                  return (
                    <tr
                      key={m.message_id}
                      aria-selected={active}
                      className={clsx('border-t border-border align-top', active && 'bg-accent-bg')}
                      data-outbox-row={m.message_id}
                    >
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => setSelectedId(m.message_id)}
                          aria-pressed={active}
                          className="text-left text-accent underline-offset-2 hover:underline"
                          data-id
                        >
                          {m.subject}
                        </button>
                        <span className="block font-mono text-caption text-ink-3" data-id>
                          {m.message_id}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <IdText id={m.customer_id} />
                      </td>
                      <td className="px-3 py-2">{OPERATOR.sheets.langs[m.lang] ?? m.lang}</td>
                      <td className="hidden px-3 py-2 md:table-cell">{t.status[m.status] ?? m.status}</td>
                      <td className="hidden px-3 py-2 md:table-cell">
                        {m.legal_clause_kind ? t.clauseKinds[m.legal_clause_kind] : t.noClause}
                      </td>
                      <td className="px-3 py-2">{m.created_by}</td>
                      <td className="hidden px-3 py-2 lg:table-cell">
                        <DateText value={m.created_at} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {selected && (
            <section className="flex min-w-0 flex-col gap-3 xl:col-span-6" aria-label={t.selected} data-outbox-detail={selected.message_id}>
              <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-3">
                <span>
                  {t.decision}{' '}
                  <Link
                    to={`/returns/${selected.return_id}/versions/${selected.decision_id}`}
                    className="font-mono text-accent underline underline-offset-2"
                    data-id
                  >
                    {selected.decision_id}
                  </Link>
                </span>
                <span>
                  {t.generation} {selected.generation_id ? <IdText id={selected.generation_id} /> : t.template}
                </span>
                <Link to={`/returns/${selected.return_id}`} className="text-accent underline underline-offset-2">
                  {t.openCase}
                </Link>
              </p>
              <MessagePreview message={selected} stored={selected.body} title={selected.subject} />
            </section>
          )}
        </div>
      )}
    </div>
  );
}
