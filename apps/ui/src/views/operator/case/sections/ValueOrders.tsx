// Value (docs/ui-design.md 6.2.3 item 3) with its legend that never collapses, and the orders of
// the last twelve months with this order's terms (item 4). Customer value orients benefits and
// service; it never feeds the risk score (rule 6).
import clsx from 'clsx';
import type { CaseCard } from '@/api/models';
import { leverLabel } from '@/components/decision';
import { DateText, Figure, IdText, Money, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { refAnchorId } from '@/domain/refs';
import { OPERATOR } from '../../copy';
import { reasonLabel } from '../../format';
import { Section } from '../../ui/Section';

export function ValueSection({ card }: { card: CaseCard }) {
  const t = OPERATOR.value;
  const value = card.value;
  if (!value) return null;
  const body = (
    <div className="flex flex-col gap-3" data-value-panel>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-caption text-ink-3">{t.value}</span>
        <span className="text-h2">
          <Figure metric={value.value_eur} label={t.value} compareTo={value.threshold} />
        </span>
        <span className="text-body text-ink-1" data-band={value.band}>
          {value.band === 'high' ? t.above : t.below}
        </span>
      </div>
      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-body" aria-label={t.equation}>
        <span>=</span>
        <Figure metric={value.kept_margin_eur} label={t.keptMargin} />
        <span className="text-ink-3">{t.keptMargin}</span>
        <span>−</span>
        <Figure metric={value.return_logistics_eur} label={t.logistics} />
        <span className="text-ink-3">{t.logistics}</span>
        <span>−</span>
        <Figure metric={value.coupon_discounts_eur} label={t.coupons} />
        <span className="text-ink-3">{t.coupons}</span>
      </p>
      <p
        className="max-w-prose rounded-control border border-border-strong bg-surface-2 px-3 py-2 text-body text-ink-1"
        data-verbatim
        data-value-legend
      >
        {value.legend}
      </p>
    </div>
  );
  if (card.path === 'guarantee') {
    return (
      <Section id="value" title={t.title}>
        <p className="mb-2 text-body text-ink-2">{t.guarantee}</p>
        <details>
          <summary className="cursor-pointer text-caption text-accent">{t.title}</summary>
          <div className="mt-2">{body}</div>
        </details>
      </Section>
    );
  }
  return (
    <Section id="value" title={t.title}>
      {body}
    </Section>
  );
}

export function OrdersSection({ card }: { card: CaseCard }) {
  const t = OPERATOR.orders;
  const { orders } = card;
  const terms = card.order_terms;
  return (
    <Section id="orders" title={<span data-constant>{t.title}</span>}>
      <ProvenanceScope provenance={orders.provenance} covers="order totals, line prices and the terms of this order">
        <div className="flex flex-col gap-3">
          <dl className="flex flex-wrap gap-x-6 gap-y-1 text-body">
            <div>
              <dt className="text-caption text-ink-3">{t.lines}</dt>
              <dd>
                <Figure metric={orders.lines} label={t.lines} />
              </dd>
            </div>
            <div>
              <dt className="text-caption text-ink-3">{t.returnedLines}</dt>
              <dd>
                <Figure metric={orders.returned_lines} label={t.returnedLines} />
              </dd>
            </div>
            <div>
              <dt className="text-caption text-ink-3">{t.returnRate}</dt>
              <dd>
                <Figure metric={orders.return_rate} label={t.returnRate} />
              </dd>
            </div>
          </dl>
          <div className="relative overflow-x-auto rounded-control border border-border">
            <table className="w-full min-w-[34rem] text-dense" data-orders-table>
              <caption className="sr-only">{t.title}</caption>
              <thead className="bg-surface-2 text-left text-caption text-ink-2">
                <tr>
                  <th scope="col" className="px-2 py-1 font-medium">
                    {t.line}
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    {t.category}
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    {t.size}
                  </th>
                  <th scope="col" className="px-2 py-1 text-right font-medium">
                    {t.price}
                  </th>
                  <th scope="col" className="px-2 py-1 font-medium">
                    {t.outcome}
                  </th>
                </tr>
              </thead>
              {orders.orders.map((order) => (
                <tbody key={order.order_id} id={refAnchorId({ kind: 'order', id: order.order_id })} data-order={order.order_id}>
                  <tr className="border-t-2 border-border bg-surface">
                    <th scope="colgroup" colSpan={5} className="px-2 py-1 text-left font-normal">
                      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <IdText id={order.order_id} />
                        <span className="text-ink-3">
                          {t.placed} <DateText value={order.placed_at} />
                        </span>
                        <span className="text-ink-3">
                          {t.delivered} {order.delivered_on ? <DateText value={order.delivered_on} /> : t.notDelivered}
                        </span>
                        <span>
                          {t.total} <Money value={order.total_eur} />
                        </span>
                        <span className="text-ink-3">
                          {t.coupon}:{' '}
                          {order.coupon_code ? (
                            <>
                              <span data-id className="font-mono">
                                {order.coupon_code}
                              </span>{' '}
                              ({t.couponKinds[order.coupon_kind ?? ''] ?? order.coupon_kind})
                            </>
                          ) : (
                            t.noCoupon
                          )}
                        </span>
                        <span className="text-ink-3">
                          {t.terms} <IdText id={order.terms_snapshot_id} />
                        </span>
                      </span>
                    </th>
                  </tr>
                  {order.lines.map((line) => {
                    const thisReturn = line.return_id === card.return_id;
                    return (
                      <tr
                        key={line.line_id}
                        className={clsx('border-t border-border', thisReturn && 'bg-accent-bg font-medium')}
                        data-line={line.line_id}
                        data-this-return={thisReturn || undefined}
                      >
                        <td className="px-2 py-1">
                          <IdText id={line.variant_id} className="whitespace-nowrap" />
                        </td>
                        <td className="px-2 py-1">{line.category}</td>
                        <td className="px-2 py-1">{line.size ?? '—'}</td>
                        <td className="px-2 py-1 text-right">
                          <Money value={line.price_eur} />
                        </td>
                        <td className="px-2 py-1">
                          {line.returned ? (
                            <span className="flex flex-wrap items-center gap-1">
                              {t.returned}
                              {line.return_reason && <span className="text-ink-3">· {reasonLabel(line.return_reason)}</span>}
                              {line.return_id && <IdText id={line.return_id} />}
                              {thisReturn && <span className="text-caption text-ink-2">({t.thisReturn})</span>}
                            </span>
                          ) : (
                            <span className="text-ink-2">{t.kept}</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              ))}
            </table>
          </div>
          <div
            className="flex flex-col gap-2 rounded-control border border-border p-3"
            id={refAnchorId({ kind: 'terms_snapshot', id: terms.terms_snapshot_id })}
          >
            <p className="text-body">
              {t.termsOf} <IdText id={card.order_id} /> ({t.snapshotWord} <IdText id={terms.terms_snapshot_id} />,{' '}
              {terms.source === 'base' ? t.sourceBase : `${t.source} ${terms.source}`}) {t.shownAt} <DateText value={terms.shown_at} />.{' '}
              {t.stayValid}
              {terms.review_at && (
                <>
                  {' '}
                  {t.snapshotReview} <DateText value={terms.review_at} />.
                </>
              )}
            </p>
            <ul className="flex flex-wrap gap-1" data-order-terms>
              {(Object.keys(terms.terms) as Array<keyof typeof terms.terms>).map((key) => (
                <li key={key} className="rounded-chip border border-border px-1.5 py-0.5 text-dense" data-constant>
                  {leverLabel({ id: key, value: terms.terms[key] })}
                </li>
              ))}
            </ul>
          </div>
        </div>
        <ProvenanceFooter />
      </ProvenanceScope>
    </Section>
  );
}
