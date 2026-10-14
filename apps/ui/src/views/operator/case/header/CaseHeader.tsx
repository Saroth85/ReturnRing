// CaseHeader (docs/ui-design.md 6.2.1): the case loop of the `?from` view, who and what is being
// returned, the customer's words as data, the badges, and the case assistant.
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import { Link } from 'react-router';
import type { Provenance } from '@/api/models';
import { EvidenceBadge, OutcomeBadge, PathBadge, RestrictionBadge, StateBadge } from '@/components/decision';
import { RoleGate } from '@/components/feedback';
import { DateText, IdText, Money, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button, Kbd } from '@/components/ui';
import { OPERATOR } from '../../copy';
import { reasonLabel } from '../../format';
import { useCaseUi } from '../context';

export interface CaseNav {
  tabLabel: string;
  position: number;
  total: number;
  prev: string | null;
  next: string | null;
  from: string;
  provenance: Provenance;
}

function NavLinks({ nav }: { nav: CaseNav }) {
  const t = OPERATOR.case;
  const link = (id: string | null, label: string, icon: 'prev' | 'next', key: string) =>
    id ? (
      <Link
        to={`/returns/${id}?from=${nav.from}`}
        aria-label={`${label} (${id})`}
        className="inline-flex min-h-8 items-center gap-1 rounded-control border border-border-strong px-2 text-body hover:bg-surface-2"
      >
        {icon === 'prev' && <ChevronLeft aria-hidden="true" size={16} />}
        <Kbd>{key}</Kbd>
        {icon === 'next' && <ChevronRight aria-hidden="true" size={16} />}
      </Link>
    ) : (
      <span aria-hidden="true" className="inline-flex min-h-8 items-center rounded-control border border-border px-2 text-ink-3 opacity-50">
        <Kbd>{key}</Kbd>
      </span>
    );
  return (
    <nav aria-label={t.queueNav} className="flex items-center gap-2 text-caption text-ink-2">
      {link(nav.prev, t.previousCase, 'prev', '[')}
      <ProvenanceScope provenance={nav.provenance} covers="position of the case in the queue view" className="inline">
        <Link to={nav.from === 'approval' ? '/queue' : `/queue?tab=${nav.from}`} className="text-accent underline underline-offset-2">
          {nav.tabLabel}
        </Link>{' '}
        · {nav.position} / {nav.total} <ProvenanceFooter compact />
      </ProvenanceScope>
      {link(nav.next, t.nextCase, 'next', ']')}
    </nav>
  );
}

export function CaseHeader({ nav, onAsk }: { nav: CaseNav | null; onAsk(): void }) {
  const { card } = useCaseUi();
  const { decision, item, request } = card;
  const t = OPERATOR.case;
  return (
    <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4" data-case-header aria-label={t.item}>
      {nav && <NavLinks nav={nav} />}
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <h1 className="flex flex-wrap items-baseline gap-x-2 text-page">
          <span className="font-mono" data-id>
            {card.return_id}
          </span>
          <span className="text-ink-3" aria-hidden="true">
            ·
          </span>
          <span>{card.display_name ?? card.customer_id}</span>
        </h1>
        <span className="ml-auto">
          <RoleGate action="POST /api/agent/ask">
            {({ allowed }) => (
              <Button onClick={onAsk} disabled={!allowed} data-ask-button>
                <Search aria-hidden="true" size={16} />
                {t.ask} <Kbd>I</Kbd>
              </Button>
            )}
          </RoleGate>
        </span>
      </div>
      <ProvenanceScope provenance={card.provenance} covers="price of the returned item and days since delivery">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-body text-ink-2">
          <IdText id={card.customer_id} />
          <span aria-hidden="true">·</span>
          <IdText id={card.order_id} />
          <span aria-hidden="true">·</span>
          <span>
            {item.category}
            {item.brand ? ` · ${item.brand}` : ''}
          </span>
          <IdText id={item.variant_id} />
          {item.size && (
            <span>
              {t.size} <span data-constant>{item.size}</span>
            </span>
          )}
          <span aria-hidden="true">·</span>
          <Money value={item.price_eur} />
          <span aria-hidden="true">·</span>
          <span className="font-medium text-ink-1">{reasonLabel(request.reason)}</span>
          <span aria-hidden="true">·</span>
          <span>
            {t.notice} <DateText value={request.notice_on} />, <PlainNumber value={request.days_since_delivery} unit="days" />{' '}
            {t.afterDelivery}
          </span>
          <ProvenanceFooter compact />
        </p>
      </ProvenanceScope>
      <p className="flex flex-wrap items-baseline gap-1 text-caption text-ink-3">
        <span>{t.customerWords}:</span>
        {request.customer_text ? (
          <q lang="it" className="rounded-chip bg-surface-2 px-1 text-body text-ink-1" data-verbatim data-customer-text>
            {request.customer_text}
          </q>
        ) : (
          <span>{t.noCustomerWords}</span>
        )}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <PathBadge path={card.path} />
        <EvidenceBadge quality={card.evidence.quality} />
        <OutcomeBadge
          path={decision.path}
          evidenceQuality={decision.evidence_quality}
          quadrant={decision.quadrant}
          thresholds={card.risk && card.value ? { risk: card.risk.threshold, value: card.value.threshold } : undefined}
        />
        <StateBadge decision={decision} versions={card.revisions.length} />
        <RestrictionBadge decision={decision} />
      </div>
    </section>
  );
}
