// Coupon rules /coupons (US-402, docs/ui-design.md 9.3). Stub: the current rule verbatim.
import { useCouponRules } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { DateText } from '@/components/provenance';
import { PLATFORM } from '../copy';

export default function CouponsPage() {
  const rules = useCouponRules();
  const current = rules.data?.current;
  return (
    <ScreenPlaceholder title={PLATFORM.titles.coupons} area="platform" stories={['US-402']} spec="docs/ui-design.md 9.3">
      <p className="max-w-prose text-body text-ink-2">{PLATFORM.coupons.note}</p>
      <DataCheck label="GET /api/coupon-rules" result={rules} />
      {current && (
        <section className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4">
          <p className="text-caption text-ink-3">
            <span data-id>{current.rule_version}</span> · {PLATFORM.coupons.approvedBy} {current.approved_by} ·{' '}
            <DateText value={current.approved_at} />
          </p>
          <p className="whitespace-pre-wrap" data-verbatim>
            {current.text}
          </p>
        </section>
      )}
    </ScreenPlaceholder>
  );
}
