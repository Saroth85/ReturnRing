// LinkedAccountsPanel (docs/ui-design.md 5.5), the hero of a cluster case: the graph of the linked
// accounts with the identifier filter, the selected link's evidence, the link table, the coupon
// sequence and the cluster features. "Linked accounts", never "same person" (rule 11).
// Operator-local until src/components/linked-accounts exists (open issue).
import clsx from 'clsx';
import { Link2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { LinkedAccountsView } from '@/api/models';
import { IdText, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { refAnchorId } from '@/domain/refs';
import { OPERATOR } from '../../copy';
import { accountsLabel, linksLabel } from '../../format';
import { CountChip } from '../../ui/CountChip';
import { useCaseUi } from '../context';
import { AccountGraph } from './AccountGraph';
import { kindCounts, linksByStrength, matchesKinds, orderedAccounts, STRONGEST_SHOWN, type IdentifierKind } from './linkModel';
import { AccountDetail, ClusterFacts, CouponTimeline, KindTag, LinkDetail, LinkTable } from './parts';

export function LinkedAccountsPanel({ view }: { view: LinkedAccountsView }) {
  const { selectedLinkId, selectLink } = useCaseUi();
  const [filter, setFilter] = useState<ReadonlySet<IdentifierKind>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [accountId, setAccountId] = useState<string | null>(null);
  const t = OPERATOR.linked;

  const sorted = useMemo(() => linksByStrength(view.links), [view.links]);
  const filtered = sorted.filter((l) => matchesKinds(l, filter));
  const selectedLink = view.links.find((l) => l.link_id === selectedLinkId) ?? null;
  // The selected link stays in the table even when it is not among the strongest.
  const shown = showAll ? filtered : filtered.slice(0, STRONGEST_SHOWN);
  const rows = selectedLink && !shown.includes(selectedLink) && filtered.includes(selectedLink) ? [...shown, selectedLink] : shown;
  const account = view.accounts.find((a) => a.customer_id === accountId) ?? null;
  const accounts = orderedAccounts(view);

  const toggleKind = (kind: IdentifierKind) =>
    setFilter((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  const pickLink = (id: string) => {
    setAccountId(null);
    selectLink(id);
  };
  const pickAccount = (id: string) => {
    selectLink(null);
    setAccountId(id);
  };

  return (
    <ProvenanceScope provenance={view.provenance} covers="link weights, shared events and account counts of the cluster">
      <div className="flex flex-col gap-3" id={refAnchorId({ kind: 'cluster', id: view.cluster_id })}>
        <header className="flex flex-col gap-1">
          <h2 className="flex flex-wrap items-center gap-2 text-h3">
            <Link2 aria-hidden="true" size={18} />
            {t.title}
            <span className="font-normal text-ink-2">· {t.clusterOf(accountsLabel(view.cluster_size), linksLabel(view.links.length))}</span>
            <span className="inline-flex items-center gap-1 text-body font-normal text-ink-3">
              <IdText id={view.cluster_id} /> ({t.graphId})
            </span>
          </h2>
          <p className="text-body text-ink-2" data-linked-fixed-line>
            {t.fixedLine}
          </p>
        </header>
        <fieldset className="flex flex-wrap items-center gap-2" data-identifier-filter>
          <legend className="sr-only">{t.filter}</legend>
          <span className="text-caption text-ink-3">{t.filter}:</span>
          {kindCounts(view.links).map(({ kind, count }) => (
            <button
              key={kind}
              type="button"
              aria-pressed={filter.has(kind)}
              onClick={() => toggleKind(kind)}
              data-kind-chip={kind}
              className={clsx(
                'inline-flex min-h-8 items-center gap-1 rounded-full border px-2 text-body',
                filter.has(kind) ? 'border-accent bg-accent-bg text-ink-1' : 'border-border-strong bg-surface text-ink-2',
              )}
            >
              <KindTag kind={kind} />
              <CountChip value={count} provenance={view.provenance} />
            </button>
          ))}
          <span className="text-caption text-ink-3">{t.filterHint}</span>
        </fieldset>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_16rem]">
          <AccountGraph
            view={view}
            selectedLinkId={selectedLinkId}
            selectedAccountId={accountId}
            filterKinds={filter}
            onSelectLink={pickLink}
            onSelectAccount={pickAccount}
          />
          <div className="flex flex-col gap-2">
            {selectedLink ? (
              <LinkDetail link={selectedLink} />
            ) : account ? (
              <AccountDetail account={account} />
            ) : (
              <p className="rounded-control border border-dashed border-border-strong p-3 text-caption text-ink-3">{t.pickHint}</p>
            )}
          </div>
        </div>
        <LinkTable links={rows} selectedLinkId={selectedLinkId} onSelect={pickLink} />
        {filtered.length > STRONGEST_SHOWN && (
          <Button variant="quiet" className="self-start" onClick={() => setShowAll((v) => !v)} data-show-all-links>
            {showAll ? t.showStrongest : t.showAll(String(filtered.length))}
          </Button>
        )}
        <CouponTimeline accounts={accounts} onSelect={pickAccount} />
        <ClusterFacts view={view} />
        <ProvenanceFooter />
      </div>
    </ProvenanceScope>
  );
}
