// The shop pane (US-401..403, docs/ui-design.md 9.1): what the customer sees at checkout. It never
// shows a quadrant, a risk, a score, a cluster, linked accounts or the model; size advice appears
// only when the API gives it, without a probability; the withdrawal right is always stated. The
// customer texts (size advice, coupon pause: CustomerText of llm/phrasebook.py) are shown in the
// customer's language, verbatim; "Switch to" appears only for a specific size the API names.
import clsx from 'clsx';
import { Ruler, ShoppingBag, X } from 'lucide-react';
import { useId } from 'react';
import { LEGAL_MINIMUM_WINDOW_DAYS } from '@/api/contract.gen';
import type { ApiError } from '@/api/errors';
import type { CheckoutConfirmResponse, CheckoutEvaluateResponse, Lang, Provenance, Terms } from '@/api/models';
import { ErrorState, InlineNotice, Skeleton } from '@/components/feedback';
import { DateText, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { WriteGate } from '../shared/WriteGate';
import { adviceFor, checkReceipt, type Cart } from './checkout';
import { SHOP } from './copy';

/** waiting: a 429 asked to wait (Retry-After) before the next evaluation. */
export type EvaluationStatus = 'evaluating' | 'ready' | 'mismatch' | 'error' | 'waiting';

/** What the checkout tells the presenter after an answer it recovers from (ADR-15 sections 15, 16). */
export type CheckoutNotice =
  | { kind: 'rate_limited'; error: ApiError; seconds: number }
  | { kind: 'terms_changed'; error: ApiError }
  | { kind: 'read_only'; error: ApiError };

interface ShopViewProps {
  cart: Cart;
  evaluation: CheckoutEvaluateResponse | undefined;
  status: EvaluationStatus;
  evaluateError: unknown;
  onRetry(): void;
  /** Lines whose size advice the customer dismissed ("Keep size L"). */
  kept: ReadonlySet<number>;
  onKeep(index: number): void;
  onSwitch(index: number, size: string): void;
  onRemove(index: number): void;
  onCoupon(code: string): void;
  onPlace(): void;
  placing: boolean;
  confirmError: unknown;
  order: CheckoutConfirmResponse | undefined;
  onRestart(): void;
  lessGenerous: boolean;
  /** The language of the customer texts. */
  lang: Lang;
  notice: CheckoutNotice | null;
  /** Seconds left before the evaluation runs again after a 429, null when not waiting. */
  cooldownLeft: number | null;
}

function TermsList({ terms, provenance }: { terms: Terms; provenance: Provenance }) {
  const t = SHOP.term;
  return (
    <ProvenanceScope provenance={provenance} covers="the terms shown at checkout">
    <ul className="flex list-disc flex-col gap-1 pl-5 text-body" data-shop-terms>
      <li>
        <PlainNumber value={terms.return_window_days} unit="count" />-{t.window}
      </li>
      <li>{terms.free_return_shipping ? t.freeShipping : t.paidShipping}</li>
      <li>{terms.instant_refund ? t.instantRefund : t.legalRefund}</li>
      <li>{terms.free_size_exchange ? t.freeExchange : t.paidExchange}</li>
      <li>{terms.coupons_enabled ? t.coupons : t.noCoupons}</li>
    </ul>
    <ProvenanceFooter compact />
    </ProvenanceScope>
  );
}

function SizeTip({
  index,
  size,
  evaluation,
  kept,
  onKeep,
  onSwitch,
  locked,
  lang,
}: {
  index: number;
  size: string;
  evaluation: CheckoutEvaluateResponse;
  kept: boolean;
  onKeep(index: number): void;
  onSwitch(index: number, size: string): void;
  locked: boolean;
  lang: Lang;
}) {
  const line = evaluation.lines[index];
  if (!line) return null;
  const { advice } = adviceFor(line, evaluation.p_return_threshold);
  if (!advice) return null;
  if (kept) return <p className="text-caption text-ink-3">{SHOP.adviceKept}</p>;
  // A specific size only when the API names one (measurement and exchange data, US-401).
  const suggested = advice.kind === 'specific' ? advice.suggested_size : null;
  return (
    <div className="mt-2 flex flex-col gap-2 rounded-control border border-border bg-surface-2 px-3 py-2" data-size-advice={advice.kind}>
      <p className="flex items-start gap-2 text-body">
        <Ruler aria-hidden="true" size={16} className="mt-0.5 shrink-0 text-ink-2" />
        <span>
          <span className="font-medium">{SHOP.sizeTip}</span>{' '}
          <span lang={lang} data-verbatim data-customer-text={lang}>
            {advice.text[lang]}
          </span>
        </span>
      </p>
      {suggested && suggested !== size && (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => onKeep(index)} disabled={locked}>
            {SHOP.keepSize(size)}
          </Button>
          <Button variant="primary" onClick={() => onSwitch(index, suggested)} disabled={locked}>
            {SHOP.switchTo(suggested)}
          </Button>
        </div>
      )}
    </div>
  );
}

/** A recovered answer: the words of the UI, then the API's message verbatim. */
function NoticeLine({ notice, cooldownLeft }: { notice: CheckoutNotice; cooldownLeft: number | null }) {
  const lead =
    notice.kind === 'rate_limited'
      ? SHOP.rateLimited(cooldownLeft ?? notice.seconds)
      : notice.kind === 'terms_changed'
        ? SHOP.termsChanged
        : SHOP.readOnly;
  return (
    <div data-checkout-notice={notice.kind}>
      <InlineNotice tone="attention">
        <p data-constant>{lead}</p>
        <p className="mt-1 text-caption">
          {SHOP.serviceSaid}{' '}
          <span data-verbatim>{notice.error.message}</span>
        </p>
      </InlineNotice>
    </div>
  );
}

/** The customer's screen inside a device-like frame; provenance sits outside it (BehindTheScenes). */
export function ShopView(props: ShopViewProps) {
  const { cart, evaluation, status, order } = props;
  const couponId = useId();
  const locked = Boolean(order) || props.placing;
  const ready = status === 'ready' && evaluation;
  const block = evaluation?.coupon_block ?? null;

  return (
    <section aria-labelledby="shop-pane-title" className="flex min-w-0 flex-col gap-2" data-shop-pane>
      <h2 id="shop-pane-title" className="text-micro uppercase text-ink-3">
        {SHOP.shopPane}
      </h2>
      <div className="flex flex-col overflow-hidden rounded-card border border-border-strong bg-surface shadow-overlay">
        <div className="flex flex-wrap items-baseline gap-x-2 border-b border-border bg-surface-2 px-4 py-2">
          <ShoppingBag aria-hidden="true" size={16} className="self-center text-ink-2" />
          <span className="font-semibold">{SHOP.shopName}</span>
          <span className="text-caption text-ink-3">({SHOP.shopNote})</span>
        </div>
        <div className="flex flex-col gap-4 p-4">
          <section aria-label={SHOP.cart} className="flex flex-col gap-2">
            <h3 className="text-title">{SHOP.cart}</h3>
            <ul className="flex flex-col divide-y divide-border rounded-control border border-border" data-cart>
              {cart.lines.map((line, index) => (
                <li key={`${line.variant_id}-${index}`} className="flex flex-col px-3 py-2" data-cart-line={`${line.variant_id}:${line.size}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0">
                      <IdText id={line.variant_id} /> · {SHOP.size} <strong>{line.size}</strong>
                    </span>
                    {cart.lines.length > 1 && (
                      <button
                        type="button"
                        onClick={() => props.onRemove(index)}
                        disabled={locked}
                        aria-label={SHOP.removeLine(line.variant_id, line.size)}
                        className="rounded-control p-1 text-ink-3 hover:bg-surface-2 disabled:opacity-50"
                      >
                        <X aria-hidden="true" size={16} />
                      </button>
                    )}
                  </div>
                  {ready && (
                    <SizeTip
                      index={index}
                      size={line.size}
                      evaluation={evaluation}
                      kept={props.kept.has(index)}
                      onKeep={props.onKeep}
                      onSwitch={props.onSwitch}
                      locked={locked}
                      lang={props.lang}
                    />
                  )}
                </li>
              ))}
            </ul>
          </section>

          <section aria-label={SHOP.termsTitle} className="flex flex-col gap-2" aria-busy={status === 'evaluating'}>
            <h3 className="text-title">{SHOP.termsTitle}</h3>
            {props.notice && props.notice.kind !== 'read_only' && <NoticeLine notice={props.notice} cooldownLeft={props.cooldownLeft} />}
            {(status === 'evaluating' || status === 'waiting') && (
              <div className="flex flex-col gap-2" data-shop-evaluating>
                <span className="sr-only">{SHOP.evaluating}</span>
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-4 w-1/2" />
              </div>
            )}
            {status === 'error' && <ErrorState error={props.evaluateError} onRetry={props.onRetry} />}
            {status === 'mismatch' && (
              <div role="status" className="flex flex-col gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-attn-fg" data-cart-mismatch>
                <p className="font-medium">{SHOP.mismatchTitle}</p>
                <p className="text-body">{SHOP.mismatch}</p>
                <div>
                  <Button onClick={props.onRetry}>{SHOP.evaluateAgain}</Button>
                </div>
              </div>
            )}
            {ready && (
              <>
                <TermsList terms={evaluation.terms} provenance={evaluation.provenance} />
                {props.lessGenerous && evaluation.review_at && (
                  <p className="text-body text-ink-2" data-terms-until>
                    {SHOP.termsUntil} <DateText value={evaluation.review_at} />
                    {SHOP.termsUntilTail}
                  </p>
                )}
              </>
            )}
            <p className="text-body text-ink-1" data-withdrawal-line>
              <span data-legal-ref>{SHOP.withdrawal(LEGAL_MINIMUM_WINDOW_DAYS)}</span>
            </p>
          </section>

          <div className="flex flex-col gap-1">
            <label htmlFor={couponId} className="text-caption text-ink-2">
              {SHOP.couponLabel}
            </label>
            <input
              id={couponId}
              value={cart.coupon_code ?? ''}
              onChange={(e) => props.onCoupon(e.target.value)}
              disabled={locked}
              autoComplete="off"
              className="min-h-10 w-full max-w-xs rounded-control border border-border-strong bg-surface px-3 font-mono text-dense disabled:opacity-60"
            />
            {ready && block && (
              // The fixed customer text of the pause (rule 10): the customer of the case reads
              // "your previous returns", every other linked account a text that gives no cause.
              <p className="text-body text-ink-2" data-coupon-paused lang={props.lang} data-verbatim data-customer-text={props.lang}>
                {block.text[props.lang]}
              </p>
            )}
            {ready && !block && !evaluation.coupon_eligible && (
              <p className="text-body text-ink-2" data-coupon-paused>
                {SHOP.couponUnavailable}
              </p>
            )}
          </div>

          {order ? (
            // "Placed with the terms above" only when the stored snapshot is the one shown, for this
            // customer (UI7); otherwise the mismatch is said, never a success.
            evaluation && checkReceipt(evaluation, order.terms_snapshot).ok && order.terms_snapshot.customer_id === cart.customer_id ? (
              <div role="status" className="flex flex-col gap-2 rounded-control border border-border bg-surface-2 px-3 py-3" data-order-placed>
                <p className="font-medium">
                  {SHOP.orderPlaced(order.order_id)} {SHOP.orderPlacedTail}
                </p>
                <div>
                  <Button onClick={props.onRestart}>{SHOP.newCart}</Button>
                </div>
              </div>
            ) : (
              <div role="alert" className="flex flex-col gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-3 text-attn-fg" data-order-mismatch>
                <p className="font-medium">{SHOP.orderMismatch}</p>
                <div>
                  <Button onClick={props.onRestart}>{SHOP.newCart}</Button>
                </div>
              </div>
            )
          ) : (
            <WriteGate action="POST /api/checkout/confirm">
              {({ allowed }) => (
                <Button
                  variant="primary"
                  className={clsx('min-h-11 px-6')}
                  disabled={!allowed || !ready || props.placing}
                  onClick={props.onPlace}
                  data-place-order
                >
                  {props.placing ? SHOP.placing : SHOP.placeOrder}
                </Button>
              )}
            </WriteGate>
          )}
          {props.notice?.kind === 'read_only' && <NoticeLine notice={props.notice} cooldownLeft={null} />}
          {props.confirmError != null && <ErrorState error={props.confirmError} title={SHOP.confirmFailed} />}
        </div>
      </div>
    </section>
  );
}
