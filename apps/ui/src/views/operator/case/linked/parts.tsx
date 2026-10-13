// Parts of the linked-accounts panel (docs/ui-design.md 5.5): identifier kinds, the link table
// (the accessible primary view, in sync with the graph), the selected link or account, the coupon
// sequence and the cluster features. Numbers are attributes of the graph records: the cluster's
// ProvenanceScope covers them.
import clsx from 'clsx';
import { AtSign, CreditCard, Home, Network, Phone, Smartphone, Ticket, type LucideIcon } from 'lucide-react';
import type { AccountLink, AccountNode, LinkedAccountsView } from '@/api/models';
import { DateText, Figure, IdText, PlainNumber } from '@/components/provenance';
import { Button } from '@/components/ui';
import { refAnchorId } from '@/domain/refs';
import { OPERATOR } from '../../copy';
import { useCaseUi } from '../context';
import { isWeak, linkState, positionOn, timelineRange, type IdentifierKind } from './linkModel';

const KIND_ICON: Record<IdentifierKind, LucideIcon> = {
  card: CreditCard,
  email: AtSign,
  device: Smartphone,
  address: Home,
  phone: Phone,
  ip_prefix: Network,
};

export function KindTag({ kind }: { kind: IdentifierKind }) {
  const Icon = KIND_ICON[kind];
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap" data-kind={kind}>
      <Icon aria-hidden="true" size={13} />
      {OPERATOR.linked.kinds[kind]}
    </span>
  );
}

function SharersNote({ link }: { link: AccountLink }) {
  const t = OPERATOR.linked;
  return (
    <span className={clsx(isWeak(link) && 'font-medium text-ink-1')}>
      {t.sharers(String(link.max_sharers))}
      {isWeak(link) ? `: ${t.weak}` : ` (${t.commonWeighLess})`}
    </span>
  );
}

/**
 * Dispute and explain, disabled with the reason for a role that may not write. In the table the
 * labels are short ("Dispute…"); the accessible name keeps the full action and the link.
 */
export function LinkActions({ link, compact = false }: { link: AccountLink; compact?: boolean }) {
  const { openSheet, gates } = useCaseUi();
  const t = OPERATOR.actions;
  const state = linkState(link);
  const ends = `${link.customer_a} ~ ${link.customer_b}`;
  return (
    <span className={clsx('flex gap-1', compact ? 'flex-col items-start' : 'flex-wrap')}>
      <Button
        variant={compact ? 'quiet' : 'secondary'}
        className={clsx('min-h-6 whitespace-nowrap', compact && 'px-1 text-caption')}
        disabled={!gates.dispute.allowed || state !== 'active'}
        title={gates.dispute.reason ?? undefined}
        aria-label={compact ? `${t.disputeLink} ${ends}` : undefined}
        onClick={() => openSheet({ kind: 'dispute', linkId: link.link_id })}
        data-dispute-link={link.link_id}
      >
        {compact ? t.disputeShort : t.disputeLink}
      </Button>
      <Button
        variant={compact ? 'quiet' : 'secondary'}
        className={clsx('min-h-6 whitespace-nowrap', compact && 'px-1 text-caption')}
        disabled={!gates.evidence.allowed || state !== 'active'}
        title={gates.evidence.reason ?? undefined}
        aria-label={compact ? `${t.explainLink} ${ends}` : undefined}
        onClick={() => openSheet({ kind: 'evidence', linkId: link.link_id })}
        data-explain-link={link.link_id}
      >
        {compact ? t.explainShort : t.explainLink}
      </Button>
    </span>
  );
}

interface LinkTableProps {
  links: AccountLink[];
  selectedLinkId: string | null;
  onSelect(linkId: string): void;
}

export function LinkTable({ links, selectedLinkId, onSelect }: LinkTableProps) {
  const t = OPERATOR.linked;
  return (
    <div className="relative overflow-x-auto rounded-control border border-border">
      <table className="w-full text-dense" data-link-table>
        <caption className="sr-only">{t.table}</caption>
        <thead className="bg-surface-2 text-left text-caption text-ink-2">
          <tr>
            <th scope="col" className="px-2 py-1 font-medium">
              {t.accounts}
            </th>
            <th scope="col" className="px-2 py-1 font-medium">
              {t.kindsHeader}
            </th>
            <th scope="col" className="px-2 py-1 font-medium">
              {t.firstShared}
            </th>
            <th scope="col" className="hidden px-2 py-1 font-medium 2xl:table-cell">
              {t.lastShared}
            </th>
            <th scope="col" className="px-2 py-1 text-right font-medium">
              {t.events}
            </th>
            <th scope="col" className="px-2 py-1 text-right font-medium">
              {t.weightDecayed}
            </th>
            <th scope="col" className="px-2 py-1 font-medium">
              {t.state}
            </th>
            <th scope="col" className="px-2 py-1 font-medium">
              {t.actions}
            </th>
          </tr>
        </thead>
        <tbody>
          {links.map((link) => {
            const state = linkState(link);
            const selected = link.link_id === selectedLinkId;
            return (
              <tr
                key={link.link_id}
                id={refAnchorId({ kind: 'link', id: link.link_id })}
                aria-selected={selected}
                data-link-row={link.link_id}
                className={clsx(
                  'border-t border-border align-top',
                  selected && 'bg-accent-bg outline outline-2 -outline-offset-2 outline-accent',
                )}
              >
                <th scope="row" className="px-2 py-1 text-left font-normal">
                  <button
                    type="button"
                    onClick={() => onSelect(link.link_id)}
                    aria-pressed={selected}
                    className="rounded-chip text-left font-mono text-dense text-accent underline-offset-2 hover:underline"
                    data-id
                  >
                    {/* Long account ids wrap after the tilde, so the actions column stays in view. */}
                    <span className="whitespace-nowrap">{link.customer_a} ~</span> <span className="whitespace-nowrap">{link.customer_b}</span>
                  </button>
                  {isWeak(link) && (
                    <span className="block text-caption text-ink-2" data-weak-link>
                      <SharersNote link={link} />
                    </span>
                  )}
                </th>
                <td className="px-2 py-1">
                  <span className="flex flex-wrap gap-x-2">
                    {link.kinds.map((k) => (
                      <KindTag key={k} kind={k} />
                    ))}
                  </span>
                </td>
                <td className="px-2 py-1 whitespace-nowrap">
                  <DateText value={link.first_shared_at.slice(0, 10)} />
                </td>
                <td className="hidden px-2 py-1 whitespace-nowrap 2xl:table-cell">
                  <DateText value={link.last_shared_at.slice(0, 10)} />
                </td>
                <td className="px-2 py-1 text-right">
                  <PlainNumber value={link.n_shared_events} unit="count" />
                </td>
                <td className="px-2 py-1 text-right">
                  <PlainNumber value={link.weight_decayed} unit="score" />
                </td>
                <td className="px-2 py-1">
                  <span className={clsx(state !== 'active' && 'italic text-ink-2')} data-link-state={state}>
                    {t.states[state]}
                  </span>
                </td>
                <td className="px-2 py-1">
                  <LinkActions link={link} compact />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function LinkDetail({ link }: { link: AccountLink }) {
  const t = OPERATOR.linked;
  const state = linkState(link);
  return (
    <div className="flex flex-col gap-2 rounded-control border border-accent bg-surface p-3" data-link-detail={link.link_id}>
      <p className="text-caption text-ink-3">{t.selected}</p>
      <p className="flex flex-wrap items-center gap-2">
        <IdText id={link.customer_a} /> ~ <IdText id={link.customer_b} />
        <span className="flex flex-wrap gap-x-2 text-body">
          {link.kinds.map((k) => (
            <KindTag key={k} kind={k} />
          ))}
        </span>
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-dense">
        <dt className="text-ink-3">{t.weight}</dt>
        <dd>
          <PlainNumber value={link.weight} unit="score" />
        </dd>
        <dt className="text-ink-3">{t.weightDecayed}</dt>
        <dd>
          <PlainNumber value={link.weight_decayed} unit="score" />
        </dd>
        <dt className="text-ink-3">{t.firstShared}</dt>
        <dd>
          <DateText value={link.first_shared_at} />
        </dd>
        <dt className="text-ink-3">{t.lastShared}</dt>
        <dd>
          <DateText value={link.last_shared_at} />
        </dd>
        <dt className="text-ink-3">{t.events}</dt>
        <dd>
          <PlainNumber value={link.n_shared_events} unit="count" />
        </dd>
        <dt className="text-ink-3">{t.state}</dt>
        <dd>
          {t.states[state]}
          {link.dispute && (
            <span className="text-ink-2">
              {' '}
              · {t.disputedBy} {link.dispute.author} · <DateText value={link.dispute.created_at} />
            </span>
          )}
        </dd>
      </dl>
      <p className="text-caption text-ink-2">
        <SharersNote link={link} />
      </p>
      <LinkActions link={link} />
    </div>
  );
}

export function AccountDetail({ account }: { account: AccountNode }) {
  const t = OPERATOR.linked;
  return (
    <div className="flex flex-col gap-2 rounded-control border border-accent bg-surface p-3" data-account-detail={account.customer_id}>
      <p className="text-caption text-ink-3">{t.selectedAccount}</p>
      <p className="flex flex-wrap items-center gap-2" id={refAnchorId({ kind: 'customer', id: account.customer_id })}>
        <IdText id={account.customer_id} />
        <span>{account.display_name}</span>
        {account.is_case_customer && <span className="text-caption text-ink-3">({t.caseCustomer})</span>}
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-dense">
        <dt className="text-ink-3">{t.created}</dt>
        <dd>
          <DateText value={account.created_at} />
        </dd>
        <dt className="text-ink-3">{t.welcome}</dt>
        <dd>{account.welcome_coupon_redeemed_at ? <DateText value={account.welcome_coupon_redeemed_at} /> : t.noWelcome}</dd>
        <dt className="text-ink-3">{t.orders}</dt>
        <dd>
          <PlainNumber value={account.orders_12m} unit="count" />
        </dd>
        <dt className="text-ink-3">{t.returns}</dt>
        <dd>
          <PlainNumber value={account.returns_12m} unit="count" />
        </dd>
      </dl>
    </div>
  );
}

/** One row per account on a shared axis: ○ creation, ticket glyph welcome coupon (pitch beat 2). */
export function CouponTimeline({ accounts, onSelect }: { accounts: AccountNode[]; onSelect(customerId: string): void }) {
  const t = OPERATOR.linked;
  const range = timelineRange(accounts);
  const pct = (iso: string) => `${(positionOn(iso, range) * 100).toFixed(2)}%`;
  return (
    <figure className="flex flex-col gap-1" data-coupon-timeline>
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2 text-caption text-ink-3">
        <span className="font-medium text-ink-2">{t.timeline}</span>
        <span>{t.timelineHint}</span>
      </figcaption>
      <ul className="flex flex-col gap-0.5">
        {accounts.map((a) => (
          <li key={a.customer_id} className="grid grid-cols-[7.5rem_1fr] items-center gap-2" data-timeline-row={a.customer_id}>
            <button
              type="button"
              onClick={() => onSelect(a.customer_id)}
              className={clsx(
                'truncate rounded-chip text-left font-mono text-caption hover:underline',
                a.is_case_customer ? 'font-semibold text-ink-1' : 'text-ink-2',
              )}
              data-id
            >
              {a.customer_id}
            </button>
            {/* Inset by half a marker, so a marker at either end stays inside the column. */}
            <span className="relative mx-2 h-4 border-b border-grid" aria-hidden="true">
              <span
                className="absolute top-0.5 h-2.5 w-2.5 -translate-x-1/2 rounded-full border-2 border-ink-2 bg-surface"
                style={{ left: pct(a.created_at) }}
              />
              {a.welcome_coupon_redeemed_at && (
                <Ticket
                  size={13}
                  className="absolute top-0 -translate-x-1/2 text-ink-1"
                  style={{ left: pct(a.welcome_coupon_redeemed_at) }}
                />
              )}
            </span>
          </li>
        ))}
      </ul>
      <div className="grid grid-cols-[7.5rem_1fr] gap-2 text-caption text-ink-3">
        <span />
        <span className="mx-2 flex justify-between">
          <DateText value={range.start} />
          <DateText value={range.end} />
        </span>
      </div>
      <ul className="sr-only">
        {accounts.map((a) => (
          <li key={a.customer_id}>
            {a.customer_id}: {t.created} <DateText value={a.created_at} />
            {a.welcome_coupon_redeemed_at ? (
              <>
                , {t.welcome} <DateText value={a.welcome_coupon_redeemed_at} />
              </>
            ) : (
              `, ${t.noWelcome}`
            )}
          </li>
        ))}
      </ul>
    </figure>
  );
}

export function ClusterFacts({ view }: { view: LinkedAccountsView }) {
  const t = OPERATOR.linked;
  const f = view.features;
  const facts = [
    ['n_distinct_cards', f.n_distinct_cards],
    ['creation_spread_days', f.creation_spread_days],
    ['welcome_coupons_per_account', f.welcome_coupons_per_account],
    ['cluster_return_rate', f.cluster_return_rate],
    ['strong_id_share', f.strong_id_share],
  ] as const;
  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-caption font-medium text-ink-2">{t.facts}</h3>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-dense sm:grid-cols-3" data-cluster-facts>
        {facts.map(([key, metric]) =>
          metric ? (
            <div key={key} className="flex flex-col">
              <dt className="text-caption text-ink-3">{t.factLabels[key]}</dt>
              <dd>
                <Figure metric={metric} label={t.factLabels[key]} />
              </dd>
            </div>
          ) : null,
        )}
      </dl>
    </div>
  );
}
