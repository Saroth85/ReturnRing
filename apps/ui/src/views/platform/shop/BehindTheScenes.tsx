// Behind the scenes of the checkout (presenter only; US-401..403, docs/ui-design.md 9.1): the line
// probabilities against the threshold, where the terms come from (base or a decision, with its
// state, approver and review date), the shown terms against the base, the welcome coupon pause
// with its rule, the stored snapshot and the latency measured in this browser.
import clsx from 'clsx';
import { CalendarClock, CircleCheck, OctagonAlert, Ticket, TriangleAlert } from 'lucide-react';
import { Link } from 'react-router';
import { isApiError } from '@/api/errors';
import { API_MODE } from '@/api/adapters';
import type { CheckoutConfirmResponse, CheckoutEvaluateResponse, Metric } from '@/api/models';
import { useCouponRules, useDecisionRecord } from '@/api/queries';
import { QuadrantBadge, ReviewDateChip } from '@/components/decision';
import { ErrorState, InlineNotice, Skeleton } from '@/components/feedback';
import { DateText, Figure, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { formatValue } from '@/format';
import { serverLapse } from '../shared/lapse';
import { Fact, Panel } from '../shared/layout';
import { CHECKOUT_TARGET_MS } from '../constants';
import { adviceFor, checkReceipt, compareTerms, type TermRow } from './checkout';
import { SHOP } from './copy';

const TH = 'px-2 py-1.5 text-left text-caption font-medium text-ink-3';
const TD = 'px-2 py-1.5 align-top';

/**
 * A missing record (404) adds nothing to the decision id already shown: the presenter panel stays
 * free of error codes (UI31). Any other error is shown with its request id.
 */
function ApiErrorLine({ error }: { error: unknown }) {
  if (isApiError(error) && error.status === 404) return <span className="sr-only" data-record-missing />;
  return <ErrorState error={error} />;
}

/** The decision behind the terms, from GET /api/decisions/{id} (server state only, P9). */
function DecisionSource({ decisionId }: { decisionId: string }) {
  const record = useDecisionRecord(decisionId);
  const t = SHOP.record;
  if (record.isPending) {
    return (
      <span className="flex items-center gap-2" role="status">
        <span className="sr-only">{t.loading}</span>
        <Skeleton className="h-4 w-48" />
      </span>
    );
  }
  if (record.isError) return <ApiErrorLine error={record.error} />;
  const d = record.data.decision;
  return (
    <span className="flex flex-col gap-1" data-decision-source={d.state}>
      <span className="flex flex-wrap items-center gap-x-1">
        <span>{t.state[d.state] ?? d.state}</span>
        <span>·</span>
        {d.approver ? (
          <>
            {t.approvedBy} <strong>{d.approver}</strong>
            {d.approved_at && (
              <>
                {' '}
                · <DateText value={d.approved_at} />
              </>
            )}
          </>
        ) : (
          <span className="text-ink-2">{t.noApprover}</span>
        )}
        <span>·</span>
        <span>{d.in_force ? t.inForce : t.notInForce}</span>
      </span>
      <Link
        to={`/returns/${encodeURIComponent(d.return_id)}/versions/${encodeURIComponent(d.decision_id)}`}
        className="text-caption text-accent underline underline-offset-2"
      >
        {t.open}
      </Link>
    </span>
  );
}

function TermValue({ row, side }: { row: TermRow; side: 'base' | 'shown' }) {
  const value = row[side];
  switch (row.key) {
    case 'return_window_days':
      return (
        <span>
          <PlainNumber value={Number(value)} unit="days" />
          {side === 'shown' && row.legalMinimum && <span className="text-ink-3"> ({SHOP.legalMinimum})</span>}
        </span>
      );
    case 'free_return_shipping':
      return <span>{value ? SHOP.free : SHOP.paid}</span>;
    case 'instant_refund':
      return <span>{value ? SHOP.yes : SHOP.legalTerms}</span>;
    case 'free_size_exchange':
      return <span>{value ? SHOP.free : SHOP.no}</span>;
    case 'coupons_enabled':
      return <span>{value ? SHOP.on : SHOP.off}</span>;
  }
}

function TermsDiffTable({ evaluation }: { evaluation: CheckoutEvaluateResponse }) {
  const rows = compareTerms(evaluation.base_terms, evaluation.terms);
  const c = SHOP.diffCols;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-title">{SHOP.diffTitle}</h3>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-dense" data-terms-diff>
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className={TH}>
                {c.term}
              </th>
              <th scope="col" className={TH}>
                {c.base}
              </th>
              <th scope="col" className={TH}>
                {c.shown}
              </th>
              <th scope="col" className={TH}>
                {c.change}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-b border-border last:border-b-0" data-term-row={row.key} data-direction={row.direction}>
                <th scope="row" className={clsx(TD, 'text-left font-normal text-ink-2')}>
                  {SHOP.termName[row.key]}
                </th>
                <td className={TD}>
                  <TermValue row={row} side="base" />
                </td>
                <td className={TD}>
                  <TermValue row={row} side="shown" />
                </td>
                <td
                  className={clsx(
                    TD,
                    row.direction === 'less' ? 'font-medium text-attn-fg' : row.direction === 'more' ? 'text-ink-1' : 'text-ink-3',
                  )}
                >
                  {SHOP.direction[row.direction]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** A neutral 0..1 track with the threshold tick and the line's marker (no colour for a score). */
function ThresholdTrack({ value, threshold }: { value: number; threshold: number }) {
  const pct = (n: number) => `${Math.min(100, Math.max(0, n * 100))}%`;
  return (
    <span aria-hidden="true" className="relative block h-2 w-24 rounded-full bg-surface-2 ring-1 ring-border">
      <span className="absolute top-[-3px] h-[14px] w-0.5 bg-ink-3" style={{ left: pct(threshold) }} />
      <span className="absolute top-[-2px] h-3 w-3 -translate-x-1/2 rounded-full border-2 border-surface bg-ink-2" style={{ left: pct(value) }} />
    </span>
  );
}

function LineProbabilities({ evaluation }: { evaluation: CheckoutEvaluateResponse }) {
  const threshold = evaluation.p_return_threshold;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-title">
        {SHOP.linesTitle} <span className="font-normal text-ink-2">· {SHOP.threshold}</span>{' '}
        <PlainNumber value={threshold} unit="probability" />
      </h3>
      <p className="text-caption text-ink-3">{SHOP.linesCaption}</p>
      <ul className="flex flex-col divide-y divide-border rounded-control border border-border" data-line-probabilities>
        {evaluation.lines.map((line, i) => {
          const view = adviceFor(line, threshold);
          return (
            <li key={`${line.variant_id}-${i}`} className="flex flex-col gap-1 px-3 py-2" data-line-advice={view.aboveThreshold ? 'above' : 'below'}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="min-w-0">
                  <IdText id={line.variant_id} /> · {SHOP.size} <strong>{line.size}</strong>
                </span>
                <span className="inline-flex items-center gap-2">
                  <span className="text-caption text-ink-3">{SHOP.probability}</span>
                  <strong className="tabular-nums">
                    <PlainNumber value={line.p_return} unit="probability" />
                  </strong>
                  <ThresholdTrack value={line.p_return} threshold={threshold} />
                </span>
                <span className="text-body text-ink-2">
                  {view.aboveThreshold ? SHOP.above : SHOP.below}
                  {view.advice && <> · {SHOP.adviceKind[view.advice.kind] ?? view.advice.kind}</>}
                </span>
              </div>
              {view.advice && (
                <p className="flex flex-col text-caption text-ink-3" data-verbatim>
                  <span lang="it">“{view.advice.text.it}”</span>
                  <span lang="en">“{view.advice.text.en}”</span>
                </p>
              )}
              {!view.consistent && (
                <InlineNotice tone="attention">
                  <p>{SHOP.adviceInconsistent}</p>
                </InlineNotice>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function CouponPause({ evaluation }: { evaluation: CheckoutEvaluateResponse }) {
  const block = evaluation.coupon_block;
  const rules = useCouponRules();
  if (!block) {
    return (
      <div className="flex flex-col gap-1">
        <h3 className="flex items-center gap-2 text-title">
          <Ticket aria-hidden="true" size={16} />
          {SHOP.couponTitle}
        </h3>
        <p className="text-body text-ink-2">{evaluation.coupon_eligible ? SHOP.couponNone : SHOP.couponUnavailable}</p>
      </div>
    );
  }
  const rule = rules.data ? [rules.data.current, ...rules.data.history].find((r) => r.rule_version === block.rule_version) : undefined;
  const application = rules.data?.applications.find((a) => a.application_id === block.application_id);
  const lapse = application ? serverLapse(application) : { kind: 'unknown' as const };
  return (
    <div className="flex flex-col gap-2" data-coupon-pause>
      <h3 className="flex items-center gap-2 text-title">
        <Ticket aria-hidden="true" size={16} />
        {SHOP.couponTitle}
      </h3>
      <dl className="flex flex-col gap-2">
        <Fact label={SHOP.couponRule}>
          <span className="flex flex-col gap-1">
            <span className="flex flex-wrap items-center gap-x-1">
              <span className="font-mono text-dense" data-version>
                {block.rule_version}
              </span>
              {rule && (
                <>
                  · {SHOP.ruleApprovedBy} <strong>{rule.approved_by}</strong> · <DateText value={rule.approved_at} />
                </>
              )}
            </span>
            {rule && (
              <span className="text-body text-ink-2" data-verbatim>
                {rule.text}
              </span>
            )}
          </span>
        </Fact>
        <Fact label={SHOP.couponApplication}>
          <span className="flex flex-wrap items-center gap-x-1">
            <IdText id={block.application_id} />
            {application ? (
              <>
                · {SHOP.applicationState[application.state] ?? application.state}
                {application.confirmed_by && (
                  <>
                    {' '}
                    · {SHOP.confirmedBy} <strong>{application.confirmed_by}</strong>
                  </>
                )}
                {application.confirmed_at && (
                  <>
                    {' '}
                    · <DateText value={application.confirmed_at} />
                  </>
                )}
              </>
            ) : rules.isPending ? (
              <Skeleton className="h-4 w-32" />
            ) : rules.isError ? null : (
              <span className="text-caption text-ink-3">{SHOP.applicationMissing(block.application_id)}</span>
            )}
          </span>
        </Fact>
        <Fact label={SHOP.couponReview}>
          <span className="inline-flex items-center gap-1">
            <CalendarClock aria-hidden="true" size={14} />
            <DateText value={block.review_at} />
          </span>
        </Fact>
      </dl>
      {lapse.kind === 'lapsed' && (
        <InlineNotice tone="attention">
          <p>{SHOP.lapsed(block.application_id)}</p>
        </InlineNotice>
      )}
      <p className="text-body">
        {SHOP.couponReversible} ·{' '}
        <Link to="/coupons" className="text-accent underline underline-offset-2">
          {SHOP.couponReverseOn}
        </Link>
      </p>
      <div className="flex flex-col gap-1 text-caption text-ink-3" data-coupon-text>
        <p>{SHOP.couponCustomerText}</p>
        <p data-verbatim className="flex flex-col text-ink-2">
          <span lang="it">“{block.text.it}”</span>
          <span lang="en">“{block.text.en}”</span>
        </p>
        <p>{SHOP.couponWording}</p>
      </div>
      {rules.isError && <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />}
    </div>
  );
}

function SnapshotFacts({ evaluation, latency }: { evaluation: CheckoutEvaluateResponse; latency: Metric | null }) {
  const within = latency ? latency.value < CHECKOUT_TARGET_MS : null;
  return (
    <dl className="flex flex-col gap-2" data-snapshot-facts>
      <Fact label={SHOP.snapshotTitle}>
        <IdText id={evaluation.terms_snapshot_id} /> · {SHOP.snapshotStored} <DateText value={evaluation.shown_at} />,{' '}
        {SHOP.snapshotBefore}
      </Fact>
      <Fact label={SHOP.model}>
        <span className="font-mono text-dense" data-version>
          {evaluation.model_version}
        </span>
      </Fact>
      <Fact label={SHOP.latency}>
        {latency ? (
          <span className="flex flex-wrap items-center gap-x-2" data-latency={within ? 'within' : 'above'}>
            <Figure metric={latency} label={SHOP.latency} />
            {/* On the mock adapter the timing says nothing about the service: no verdict (UI31). */}
            <span className="text-caption text-ink-3">
              {API_MODE === 'mock' ? SHOP.latencyNoteMock : SHOP.latencyNote} · {SHOP.latencyClock}
            </span>
            {API_MODE !== 'mock' && (
              <span className="text-caption text-ink-2">
                · {SHOP.latencyTarget} <span data-constant>{formatValue(CHECKOUT_TARGET_MS / 1000, 'seconds')}</span>:{' '}
                {within ? SHOP.latencyWithin : SHOP.latencyAbove}
              </span>
            )}
          </span>
        ) : (
          <Skeleton className="h-4 w-24" />
        )}
      </Fact>
    </dl>
  );
}

/** E2E-02: the stored snapshot of the order holds exactly the terms shown. */
export function SnapshotReceipt({ evaluation, order }: { evaluation: CheckoutEvaluateResponse; order: CheckoutConfirmResponse }) {
  const stored = order.terms_snapshot;
  const check = checkReceipt(evaluation, stored);
  return (
    <div className="flex flex-col gap-2" data-snapshot-receipt={check.ok ? 'ok' : 'differs'}>
      <h3 className="text-title">{SHOP.afterOrder}</h3>
      <p className="flex flex-wrap items-center gap-x-1 text-body">
        <IdText id={order.order_id} />
        {stored.confirmed_at && (
          <>
            · {SHOP.confirmedAt} <DateText value={stored.confirmed_at} />
          </>
        )}
        · {order.coupon_applied ? SHOP.couponApplied.yes : SHOP.couponApplied.no}
      </p>
      {check.ok ? (
        <p role="status" className="flex items-center gap-2 rounded-control bg-ok-bg px-3 py-2 font-medium text-ok-fg">
          <CircleCheck aria-hidden="true" size={16} />
          {SHOP.receiptOk(stored.terms_snapshot_id)}
        </p>
      ) : (
        <div role="alert" className="flex flex-col gap-1 rounded-control border border-danger-fg bg-danger-bg px-3 py-2 text-danger-fg">
          <p className="flex items-center gap-2 font-medium">
            <OctagonAlert aria-hidden="true" size={16} />
            {SHOP.receiptBad}
          </p>
          <p className="text-body text-ink-1">
            {SHOP.receiptDiffers} {check.mismatches.map((m) => SHOP.receiptFields[m]).join(', ')} ·{' '}
            <IdText id={evaluation.terms_snapshot_id} /> → <IdText id={stored.terms_snapshot_id} />
          </p>
        </div>
      )}
    </div>
  );
}

interface BehindTheScenesProps {
  evaluation: CheckoutEvaluateResponse | undefined;
  latency: Metric | null;
  order: CheckoutConfirmResponse | undefined;
}

export function BehindTheScenes({ evaluation, latency, order }: BehindTheScenesProps) {
  const lapse = evaluation ? serverLapse(evaluation) : { kind: 'unknown' as const };
  const fromDecision = evaluation && evaluation.terms_source !== 'base';
  return (
    <section aria-labelledby="scenes-pane-title" className="flex min-w-0 flex-col gap-2" data-scenes-pane>
      <h2 id="scenes-pane-title" className="text-micro uppercase text-ink-3">
        {SHOP.scenesPane}
      </h2>
      <Panel>
        {!evaluation ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-5/6" />
          </div>
        ) : (
          <ProvenanceScope provenance={evaluation.provenance} covers="line probabilities, the threshold and the terms" className="flex flex-col gap-5">
            <dl className="flex flex-col gap-2">
              <Fact label={SHOP.customer}>
                <span className="flex flex-wrap items-center gap-2">
                  <IdText id={evaluation.customer_id} />
                  <span className="text-caption text-ink-3">{SHOP.position}</span>
                  {evaluation.quadrant ? <QuadrantBadge quadrant={evaluation.quadrant} /> : <span className="text-ink-3">{SHOP.noPosition}</span>}
                </span>
              </Fact>
              <Fact label={SHOP.termsSource}>
                {fromDecision ? (
                  <span className="flex flex-col gap-1">
                    <span className="flex flex-wrap items-center gap-x-1">
                      {SHOP.decisionSource} <IdText id={evaluation.terms_source} />
                    </span>
                    <DecisionSource decisionId={evaluation.terms_source} />
                  </span>
                ) : (
                  <span>{SHOP.baseSource}</span>
                )}
              </Fact>
              <Fact label={SHOP.reviewDate}>
                {evaluation.review_at ? <ReviewDateChip reviewAt={evaluation.review_at} /> : <span className="text-ink-3">{SHOP.noReviewDate}</span>}
              </Fact>
            </dl>
            {lapse.kind === 'lapsed' && (
              <p role="status" className="flex items-center gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-attn-fg" data-terms-lapsed>
                <TriangleAlert aria-hidden="true" size={16} />
                {SHOP.lapsed(evaluation.terms_source)}
              </p>
            )}
            {lapse.kind === 'in_force' && fromDecision && <p className="text-caption text-ink-2">{SHOP.inForce}</p>}
            <LineProbabilities evaluation={evaluation} />
            <TermsDiffTable evaluation={evaluation} />
            <CouponPause evaluation={evaluation} />
            <SnapshotFacts evaluation={evaluation} latency={latency} />
            {order && <SnapshotReceipt evaluation={evaluation} order={order} />}
            <ProvenanceFooter />
          </ProvenanceScope>
        )}
      </Panel>
    </section>
  );
}
