// Restrictions under review /reviews (US-607, docs/ui-design.md 8.6): every restriction has a review
// date and lapses there; a person renews it with a new decision or lifts it, in the operator's review
// sheet (/returns/:id?action=review). The lapse comes from the server (review_due), never from the
// browser's clock (P9). Coupon pauses on clusters carry their own review dates and can be reversed.
import { CalendarClock, TriangleAlert, UserCheck } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import type { CouponRuleApplication, QueueItem } from '@/api/models';
import { HAS_RENEW } from '@/api/models';
import { useCase, useCouponRules, useQueue } from '@/api/queries';
import { QuadrantBadge } from '@/components/decision';
import { EmptyState, InlineNotice, useReadOnly } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { CouponApplicationsTable, leverText } from '@/components/records';
import { MANAGER } from '../copy';
import { Card, Loadable, ManagerPage } from '../ui';

const R = MANAGER.reviews;

function Approver({ returnId, decisionId }: { returnId: string; decisionId: string }) {
  const card = useCase(returnId);
  const decision = card.data?.decision;
  if (card.isPending) return <span className="text-caption text-ink-3">{R.approverPending}</span>;
  if (!decision || decision.decision_id !== decisionId || !decision.approver) return <span className="text-caption text-ink-3">{R.noApprover}</span>;
  return (
    <span className="flex flex-col">
      <span className="inline-flex items-center gap-1">
        <UserCheck aria-hidden="true" size={14} />
        <span className="font-medium">{decision.approver}</span>
      </span>
      {decision.approved_at && <DateText value={decision.approved_at} className="text-caption text-ink-3" />}
    </span>
  );
}

function DueRow({ item, readOnly }: { item: QueueItem; readOnly: boolean }) {
  return (
    <tr className="border-b border-border align-top" data-review-row={item.return_id}>
      <td className="px-3 py-2">
        <span className="flex flex-col items-start gap-0.5">
          <IdText id={item.return_id} className="whitespace-nowrap" />
          <span>{item.display_name}</span>
          <span className="text-caption whitespace-nowrap text-ink-3" data-id>
            {item.customer_id}
          </span>
        </span>
      </td>
      <td className="px-3 py-2">
        <span className="mb-1 flex items-center gap-1">{item.quadrant && <QuadrantBadge quadrant={item.quadrant} />}</span>
        <ul className="flex flex-col gap-0.5 text-dense">
          {item.lever_ids.map((id) => (
            <li key={id} data-constant>
              {leverText(id).label}
            </li>
          ))}
        </ul>
      </td>
      <td className="px-3 py-2">
        <Approver returnId={item.return_id} decisionId={item.decision_id} />
      </td>
      <td className="px-3 py-2">
        <span className="flex flex-col gap-1">
          <span className="inline-flex items-center gap-1">
            <CalendarClock aria-hidden="true" size={14} />
            {item.review_at ? <DateText value={item.review_at} /> : '—'}
          </span>
          {item.review_due && item.review_at && (
            <span className="inline-flex items-start gap-1 rounded-chip bg-attn-bg px-1.5 py-0.5 text-caption text-attn-fg" data-lapsed>
              <TriangleAlert aria-hidden="true" size={12} className="mt-0.5 shrink-0" />
              <span>
                {R.lapsedOn} <DateText value={item.review_at} />
                {R.lapsedTail(HAS_RENEW)}
              </span>
            </span>
          )}
        </span>
      </td>
      <td className="px-3 py-2">
        <span className="flex flex-col items-start gap-1">
          <Link
            to={`/returns/${encodeURIComponent(item.return_id)}?action=review`}
            className="inline-flex min-h-8 items-center rounded-control bg-accent-btn px-3 text-body font-medium text-accent-on hover:bg-accent-hover"
            data-review-link={item.return_id}
          >
            {HAS_RENEW ? R.reviewLiftRenew : R.reviewLift}
          </Link>
          {readOnly && (
            <span className="text-caption text-ink-3" data-role-reason>
              {R.readOnly}
            </span>
          )}
          <Link to={`/returns/${encodeURIComponent(item.return_id)}`} className="text-dense text-accent underline underline-offset-2">
            {R.openCase}
          </Link>
        </span>
      </td>
    </tr>
  );
}

export default function ReviewsPage() {
  const due = useQueue({ review_due: true });
  const coupons = useCouponRules();
  const [reversed, setReversed] = useState<CouponRuleApplication | null>(null);
  const readOnly = useReadOnly();
  const c = R.cols;
  return (
    <ManagerPage screen="reviews" title={MANAGER.titles.reviews} intro={<p>{R.note}</p>}>
      <Card title={R.dueTitle}>
        <Loadable query={due} label="GET /api/returns?review_due=true" rows={3}>
          {(page) =>
            page.items.length === 0 ? (
              <EmptyState message={R.empty} />
            ) : (
              <div className="relative overflow-x-auto">
                <table className="w-full min-w-[48rem] border-collapse text-dense" data-reviews-due>
                  <thead>
                    <tr className="border-b border-border-strong text-left text-caption text-ink-2">
                      {[c.ret, c.terms, c.approved, c.review, c.actions].map((h) => (
                        <th key={h} scope="col" className="px-3 py-1.5 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {page.items.map((item) => (
                      <DueRow key={item.return_id} item={item} readOnly={readOnly} />
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </Loadable>
      </Card>
      <Card title={R.couponsTitle}>
        <p className="text-body text-ink-2">{R.couponsNote}</p>
        <Loadable query={coupons} label="GET /api/coupon-rules" rows={3}>
          {(data) => (
            <>
              <p className="text-caption text-ink-2">
                {R.couponRule}{' '}
                <span className="font-mono" data-version>
                  {data.current.rule_version}
                </span>{' '}
                · {R.approvedBy} {data.current.approved_by} · <DateText value={data.current.approved_at} />
              </p>
              <blockquote className="border-l-2 border-border-strong pl-2 text-dense text-ink-2" data-verbatim>
                {data.current.text}
              </blockquote>
              {reversed && (
                <InlineNotice tone="info">
                  <span data-reversed={reversed.application_id}>{R.reversed(reversed.application_id, reversed.reversed_by ?? '')}</span>
                </InlineNotice>
              )}
              {/* The read-only public demo refuses a reversal (403): none is offered, and the reason says why. */}
              {readOnly && (
                <p className="text-caption text-ink-3" data-reversal-read-only>
                  {R.reversalReadOnly}
                </p>
              )}
              <CouponApplicationsTable applications={data.applications} reversible={!readOnly} onReversed={setReversed} />
            </>
          )}
        </Loadable>
      </Card>
    </ManagerPage>
  );
}
