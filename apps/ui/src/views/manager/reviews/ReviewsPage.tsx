// Restrictions under review /reviews (US-607, docs/ui-design.md 8.6). Stub: the restrictions due,
// each with "Review" opening the operator's review sheet by URL (/returns/:id?action=review).
import { Link } from 'react-router';
import { useCouponRules, useQueue } from '@/api/queries';
import { ReviewDateChip } from '@/components/decision';
import { DataCheck, EmptyState, ScreenPlaceholder } from '@/components/feedback';
import { IdText } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function ReviewsPage() {
  const due = useQueue({ review_due: true });
  const coupons = useCouponRules();
  return (
    <ScreenPlaceholder title={MANAGER.titles.reviews} area="manager" stories={['US-607']} spec="docs/ui-design.md 8.6">
      <p className="max-w-prose text-body text-ink-2">{MANAGER.reviews.note}</p>
      <DataCheck label="GET /api/returns?review_due=true" result={due} />
      <DataCheck label="GET /api/coupon-rules" result={coupons} />
      {due.data && due.data.items.length === 0 && <EmptyState message={MANAGER.reviews.empty} />}
      {due.data && due.data.items.length > 0 && (
        <ul className="divide-y divide-border rounded-card border border-border bg-surface">
          {due.data.items.map((item) => (
            <li key={item.return_id} className="flex flex-wrap items-center gap-3 px-3 py-2">
              <IdText id={item.return_id} />
              <span>{item.display_name}</span>
              <ReviewDateChip reviewAt={item.review_at} />
              <Link to={`/returns/${item.return_id}?action=review`} className="text-accent underline underline-offset-2">
                {MANAGER.reviews.review}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </ScreenPlaceholder>
  );
}
