// Simulated checkout /shop?preset=giulia|marco (US-401..403, docs/ui-design.md 9.1). Stub:
// evaluates the preset cart and shows the stored snapshot id.
import { useEffect } from 'react';
import { Link, useSearchParams } from 'react-router';
import { useCheckoutEvaluate } from '@/api/mutations';
import { ErrorState, LoadingBlock, ScreenPlaceholder } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { PLATFORM } from '../copy';
import { PRESETS } from './presets';

export default function ShopPage() {
  const [params] = useSearchParams();
  const preset = params.get('preset') ?? 'giulia';
  const cart = PRESETS[preset] ?? PRESETS.giulia;
  const evaluate = useCheckoutEvaluate();
  const { mutate } = evaluate;

  useEffect(() => {
    if (cart) mutate(cart);
  }, [cart, mutate]);

  const result = evaluate.data;
  return (
    <ScreenPlaceholder title={PLATFORM.titles.shop} area="platform" stories={['US-401', 'US-402', 'US-403']} spec="docs/ui-design.md 9.1">
      <nav className="flex flex-wrap gap-3">
        {Object.keys(PRESETS).map((key) => (
          <Link key={key} to={`/shop?preset=${key}`} className="text-accent underline underline-offset-2" aria-current={key === preset ? 'page' : undefined}>
            {PLATFORM.shop.presets[key] ?? key}
          </Link>
        ))}
      </nav>
      {evaluate.isPending && <LoadingBlock rows={4} />}
      {evaluate.isError && <ErrorState error={evaluate.error} onRetry={() => cart && mutate(cart)} />}
      {result && (
        <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4">
          <p className="flex flex-wrap items-center gap-2">
            <span className="text-ink-3">{PLATFORM.shop.snapshot}:</span>
            <IdText id={result.terms_snapshot_id} />
            <DateText value={result.shown_at} className="text-ink-3" />
          </p>
          <p>{result.coupon_eligible ? PLATFORM.shop.couponEligible.yes : PLATFORM.shop.couponEligible.no}</p>
        </section>
      )}
    </ScreenPlaceholder>
  );
}
