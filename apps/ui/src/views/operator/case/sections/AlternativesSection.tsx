// Alternatives (US-308, docs/ui-design.md 6.2.3 item 5): up to three admissible actions side by
// side, each with its levers, its net benefit in disjoint components (simulated, rule 9: the
// refund owed to the customer is never counted as a saving) and the assumptions it rests on.
import { Star } from 'lucide-react';
import type { Alternative, CaseCard } from '@/api/models';
import { LeverList } from '@/components/decision';
import { AssumptionLabel } from '@/components/economics';
import { Figure, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { sameLevers } from '@/domain/levers';
import { OPERATOR } from '../../copy';
import { useCaseUi } from '../context';
import { Section } from '../../ui/Section';

const GUIDANCE_KINDS: ReadonlySet<Alternative['kind']> = new Set(['verification', 'size_exchange', 'product_report']);
const EUR_INPUTS = new Set([
  'logistics_cost_per_return_eur',
  'avg_undue_discount_eur',
  'hourly_cost_eur',
  'incentives_eur',
  'system_cost_eur',
  'error_cost_eur',
  'abandonment_margin_lost_eur',
  'incremental_margin_eur',
]);
const COUNT_INPUTS = ['logistics_returns_avoided', 'undue_discounts_avoided', 'minutes_saved'];

function NetBenefitList({ alternative }: { alternative: Alternative }) {
  const t = OPERATOR.alternatives;
  const nb = alternative.net_benefit;
  return (
    <div className="flex flex-col gap-1" data-net-benefit>
      <dl className="flex flex-col text-dense">
        {nb.components.map((c) => (
          <div key={c.id} className="flex items-baseline justify-between gap-2 border-b border-grid py-0.5">
            <dt className="text-ink-2">{t.components[c.id] ?? c.id}</dt>
            <dd>
              <Figure metric={c.amount} label={t.components[c.id]} signed />
            </dd>
          </div>
        ))}
        <div className="flex items-baseline justify-between gap-2 py-1 font-semibold">
          <dt>{t.total}</dt>
          <dd data-net-total>
            <Figure metric={nb.total} label={t.netBenefit} signed />
          </dd>
        </div>
      </dl>
      <p className="text-caption text-ink-2">{t.refundNote}</p>
    </div>
  );
}

function AssumptionInputs({ alternative }: { alternative: Alternative }) {
  const t = OPERATOR.alternatives;
  const a = alternative.net_benefit.assumptions;
  return (
    <details className="text-dense">
      <summary className="cursor-pointer text-caption text-accent">{t.assumptions}</summary>
      <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5">
        {[...COUNT_INPUTS, ...EUR_INPUTS].map((key) => (
          <div key={key} className="contents">
            <dt className="text-ink-3">{t.inputs[key] ?? key}</dt>
            <dd className="text-right">
              {/* minutes_saved is a float (AssumptionInputs): 7.5 minutes, not 8 (UI10). */}
              <PlainNumber
                value={Number(a[key as keyof typeof a])}
                unit={EUR_INPUTS.has(key) ? 'eur' : 'count'}
                maxFractionDigits={key === 'minutes_saved' ? 1 : undefined}
              />
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function AlternativeCard({ card, alternative }: { card: CaseCard; alternative: Alternative }) {
  const { openSheet, gates, card: current } = useCaseUi();
  const t = OPERATOR.alternatives;
  const a = alternative.net_benefit.assumptions;
  const differs = !alternative.recommended && !sameLevers(alternative.levers, card.decision.proposed_levers);
  const canOverride = current.decision.state === 'proposed' && !current.decision.superseded_by;
  return (
    <article
      className={`flex min-w-0 flex-col gap-2 rounded-card border p-3 ${alternative.recommended ? 'border-2 border-ink-2' : 'border-border'}`}
      data-alternative={alternative.kind}
      data-recommended={alternative.recommended || undefined}
    >
      <ProvenanceScope
        provenance={alternative.net_benefit.total.provenance}
        covers="assumption values of this alternative"
        className="flex flex-col gap-2"
      >
        <header className="flex flex-col gap-1">
          <h3 className="text-title">{t.kinds[alternative.kind] ?? alternative.kind}</h3>
          {alternative.recommended && (
            <span className="inline-flex items-center gap-1 self-start rounded-chip border border-ink-2 px-1.5 text-micro uppercase text-ink-1">
              <Star aria-hidden="true" size={12} />
              {t.recommended}
            </span>
          )}
          <p className="text-body text-ink-2" data-verbatim>
            {alternative.summary}
          </p>
        </header>
        <LeverList levers={alternative.levers} base={card.order_terms.base_terms} />
        {GUIDANCE_KINDS.has(alternative.kind) && <p className="text-caption text-ink-3">{t.guidance}</p>}
        <NetBenefitList alternative={alternative} />
        <p className="text-caption text-ink-3">
          <AssumptionLabel label={a.version} basedOn={a.based_on} />
        </p>
        <p className="text-caption text-ink-3">
          {t.period}: <span data-verbatim>{a.period}</span>
        </p>
        <AssumptionInputs alternative={alternative} />
        {differs && canOverride && (
          <Button
            variant="secondary"
            className="self-start"
            disabled={!gates.decide.allowed}
            title={gates.decide.reason ?? undefined}
            onClick={() => openSheet({ kind: 'override', prefill: alternative.levers })}
          >
            {t.useInstead}
          </Button>
        )}
        <ProvenanceFooter />
      </ProvenanceScope>
    </article>
  );
}

export function AlternativesSection({ card }: { card: CaseCard }) {
  const t = OPERATOR.alternatives;
  return (
    <Section id="alternatives" title={t.title}>
      <p className="mb-3 text-body text-ink-2">{t.subtitle}</p>
      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3" data-alternatives>
        {card.alternatives.map((alt) => (
          <AlternativeCard key={alt.kind} card={card} alternative={alt} />
        ))}
      </div>
    </Section>
  );
}
