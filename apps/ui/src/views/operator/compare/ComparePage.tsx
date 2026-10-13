// Compare /compare?a=&b= (pitch beat 1, US-302, US-906; docs/ui-design.md 6.5). Stub: the shared
// indicator (12-month return rate) and the outcome of each case side by side.
import { Link, useSearchParams } from 'react-router';
import type { CaseCard } from '@/api/models';
import { useCase } from '@/api/queries';
import { OutcomeBadge } from '@/components/decision';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { Figure, IdText } from '@/components/provenance';
import { OPERATOR } from '../copy';

function Column({ card }: { card: CaseCard }) {
  const t = OPERATOR.compare;
  return (
    <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4" data-compare-column={card.return_id}>
      <h2 className="text-title">
        {card.display_name} · <IdText id={card.customer_id} />
      </h2>
      <p className="text-caption text-ink-3">
        <IdText id={card.return_id} />
      </p>
      <p>
        <span className="text-ink-3" data-constant>
          {t.returnRate}:{' '}
        </span>
        <Figure metric={card.orders.return_rate} label={t.returnRate} />
      </p>
      <OutcomeBadge path={card.decision.path} evidenceQuality={card.decision.evidence_quality} quadrant={card.decision.quadrant} />
      <Link to={`/returns/${card.return_id}`} className="text-accent underline underline-offset-2">
        {t.openCase}
      </Link>
    </section>
  );
}

export default function ComparePage() {
  const [params] = useSearchParams();
  const a = params.get('a') ?? undefined;
  const b = params.get('b') ?? undefined;
  const left = useCase(a);
  const right = useCase(b);
  return (
    <ScreenPlaceholder title={OPERATOR.titles.compare} area="operator" stories={['US-302', 'US-906']} spec="docs/ui-design.md 6.5">
      {!a || !b ? (
        <p className="text-body">
          {OPERATOR.compare.pickTwo}{' '}
          <Link to="/compare?a=RET-000101&b=RET-000102" className="text-accent underline underline-offset-2">
            {OPERATOR.compare.pitchPair}
          </Link>
        </p>
      ) : (
        <>
          <DataCheck label={`GET /api/returns/${a}`} result={left} />
          <DataCheck label={`GET /api/returns/${b}`} result={right} />
          <div className="grid gap-4 md:grid-cols-2">
            {left.data && <Column card={left.data} />}
            {right.data && <Column card={right.data} />}
          </div>
        </>
      )}
    </ScreenPlaceholder>
  );
}
