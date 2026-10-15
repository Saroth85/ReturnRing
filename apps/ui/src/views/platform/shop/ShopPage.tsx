// Simulated checkout /shop?preset=giulia|ring (US-401..403, docs/ui-design.md 9.1). The presenter
// picks a test cart of the demo reset (GET /api/demo/cases; ?preset=marco opens the ring cart);
// POST /api/checkout/evaluate runs on load and after every cart change (debounced), the API stores
// the shown terms as a snapshot before it answers, and "Place order" binds that snapshot to the
// order (POST /api/checkout/confirm, 201). The receipt then checks that the terms shown are the
// terms stored (E2E-02). A 409 on the order (the terms lapsed meanwhile) evaluates the cart again;
// a 429 on the evaluation waits for Retry-After; the read-only public demo never places an order.
import clsx from 'clsx';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { API_MODE } from '@/api/adapters';
import { useDemoClock } from '@/api/clock';
import { isApiError } from '@/api/errors';
import { useCheckoutConfirm, useCheckoutEvaluate } from '@/api/mutations';
import type { CheckoutEvaluateRequest, DemoCasesResponse, Lang } from '@/api/models';
import { useCase, useDemoCases } from '@/api/queries';
import { EmptyState, ErrorState, LoadingBlock } from '@/components/feedback';
import { IdText } from '@/components/provenance';
import { MESSAGE } from '@/copy/common';
import { caseOf, cartOf } from '@/domain/demoCases';
import { PLATFORM } from '../copy';
import { EVALUATE_DEBOUNCE_MS } from '../constants';
import { PageHeader } from '../shared/layout';
import { NoResetNotice } from '../shared/NoResetNotice';
import { BehindTheScenes } from './BehindTheScenes';
import {
  compareTerms,
  confirmRequest,
  evaluationMatchesCart,
  latencyMetric,
  lessGenerous,
  removeLine,
  switchSize,
  toEvaluateRequest,
  type Cart,
} from './checkout';
import { SHOP } from './copy';
import { linkedCart, PRESET_IDS, presetCart, presetId, SHOP_LANGS, shopLang, type PresetId } from './presets';
import { ShopView, type CheckoutNotice, type EvaluationStatus } from './ShopView';

/** Retry-After of a 429 when the header is missing (the public limit's window). */
const RATE_LIMIT_FALLBACK_S = 60;

type Carts = Record<PresetId, Cart | null>;

/**
 * The test carts: the two of the demo reset, and the ring cart for another linked account of the
 * Marco case (pending while its card loads).
 */
function useCarts(cases: DemoCasesResponse | undefined): { carts: Carts; linkedPending: boolean } {
  const marcoId = caseOf(cases, 'marco')?.return_id;
  const marco = useCase(marcoId);
  const giulia = cartOf(cases, 'giulia');
  const ring = cartOf(cases, 'ring');
  return {
    carts: {
      giulia: giulia ? presetCart(giulia) : null,
      ring: ring ? presetCart(ring) : null,
      linked: ring ? linkedCart(ring, marco.data) : null,
    },
    linkedPending: Boolean(marcoId) && marco.isPending,
  };
}

function PresetPicker({ current, carts, cluster, lang }: { current: PresetId; carts: Carts; cluster: string | null; lang: Lang }) {
  return (
    <nav aria-label={SHOP.presetsLabel} className="flex flex-wrap items-center gap-2" data-presets>
      <span className="text-caption text-ink-3">{SHOP.presetsLabel}</span>
      {PRESET_IDS.map((id) => {
        const cart = carts[id];
        if (!cart) return null;
        const query = new URLSearchParams({ preset: id });
        if (lang !== 'it') query.set('lang', lang);
        return (
          <Link
            key={id}
            to={`/shop?${query.toString()}`}
            aria-current={id === current ? 'page' : undefined}
            className={clsx(
              'inline-flex min-h-9 flex-wrap items-baseline gap-x-1 rounded-control border px-3 py-1 text-body',
              id === current ? 'border-accent bg-accent-bg font-medium text-ink-1' : 'border-border-strong bg-surface text-ink-1 hover:bg-surface-2',
            )}
            data-preset={id}
          >
            {SHOP.presets[id].name}
            <IdText id={cart.customer_id} className="text-caption font-normal" />
            <span className="text-caption font-normal text-ink-3">{SHOP.presets[id].detail}</span>
            {id !== 'giulia' && cluster && <IdText id={cluster} className="text-caption font-normal" />}
          </Link>
        );
      })}
    </nav>
  );
}

function LangPicker({ lang, onChange }: { lang: Lang; onChange(lang: Lang): void }) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={SHOP.langLabel} data-shop-lang={lang}>
      <span className="text-caption text-ink-3">{SHOP.langLabel}</span>
      {SHOP_LANGS.map((l) => (
        <button
          key={l}
          type="button"
          aria-pressed={lang === l}
          onClick={() => onChange(l)}
          className={clsx(
            'min-h-8 rounded-control border px-3 text-dense',
            lang === l ? 'border-accent bg-accent-bg font-medium text-ink-1' : 'border-border text-ink-2 hover:bg-surface-2',
          )}
        >
          {MESSAGE.langName[l]}
        </button>
      ))}
      <span className="text-caption text-ink-3">{SHOP.langHint}</span>
    </div>
  );
}

/**
 * A countdown from Retry-After: the seconds left (null when not waiting), and `onDone` once the
 * wait is over. Timers only: the clock of the wait is the browser's, it dates no figure.
 */
function useCooldown(onDone: () => void) {
  const [deadline, setDeadline] = useState<number | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const done = useRef(onDone);
  useEffect(() => {
    done.current = onDone;
  });
  useEffect(() => {
    if (deadline === null) return undefined;
    const tick = setInterval(() => setLeft(Math.max(0, Math.ceil((deadline - Date.now()) / 1000))), 1000);
    const end = setTimeout(
      () => {
        setDeadline(null);
        setLeft(null);
        done.current();
      },
      Math.max(0, deadline - Date.now()),
    );
    return () => {
      clearInterval(tick);
      clearTimeout(end);
    };
  }, [deadline]);
  const start = useCallback((seconds: number) => {
    const wait = Math.max(1, Math.ceil(seconds));
    setLeft(wait);
    setDeadline(Date.now() + wait * 1000);
  }, []);
  const stop = useCallback(() => {
    setDeadline(null);
    setLeft(null);
  }, []);
  return { left, start, stop };
}

function Checkout({ preset, initial, lang }: { preset: PresetId; initial: Cart; lang: Lang }) {
  const [cart, setCart] = useState<Cart>(() => ({ ...initial, lines: initial.lines.map((l) => ({ ...l })) }));
  const [kept, setKept] = useState<ReadonlySet<number>>(() => new Set());
  const [latencyMs, setLatencyMs] = useState<number | null>(null);
  const [sentKey, setSentKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<CheckoutNotice | null>(null);
  const clock = useDemoClock();
  const evaluate = useCheckoutEvaluate();
  const confirm = useCheckoutConfirm();
  const { mutate: evaluateMutate } = evaluate;
  const seq = useRef(0);

  const request = useMemo(() => toEvaluateRequest(cart), [cart]);
  const requestKey = JSON.stringify(request);
  const latestRequest = useRef(request);
  const runRef = useRef<(body: CheckoutEvaluateRequest) => void>(() => undefined);
  useEffect(() => {
    latestRequest.current = request;
  });

  // After a 429 the cart is evaluated again once Retry-After has passed.
  const { left: cooldownLeft, start: startCooldown, stop: stopCooldown } = useCooldown(() => runRef.current(latestRequest.current));

  const run = useCallback(
    (body: CheckoutEvaluateRequest) => {
      const id = ++seq.current;
      const started = performance.now();
      setSentKey(JSON.stringify(body));
      evaluateMutate(body, {
        onSuccess: () => {
          if (id !== seq.current) return;
          setLatencyMs(performance.now() - started);
          setNotice((n) => (n?.kind === 'rate_limited' ? null : n));
        },
        onError: (error) => {
          if (id !== seq.current) return;
          // 429: the public limit (ADR-15 sections 15 and 16); evaluate again after Retry-After.
          if (isApiError(error) && error.status === 429) {
            const seconds = error.retryAfterS ?? RATE_LIMIT_FALLBACK_S;
            setNotice({ kind: 'rate_limited', error, seconds });
            startCooldown(seconds);
          }
        },
      });
    },
    [evaluateMutate, startCooldown],
  );
  useEffect(() => {
    runRef.current = run;
  });

  // On load at once, then debounced after every cart change (docs/ui-design.md 9.1).
  const loaded = useRef(false);
  useEffect(() => {
    const delay = loaded.current ? EVALUATE_DEBOUNCE_MS : 0;
    loaded.current = true;
    const timer = setTimeout(() => run(request), delay);
    return () => clearTimeout(timer);
  }, [request, run]);

  const evaluation = evaluate.data;
  const rateLimited = notice?.kind === 'rate_limited';
  let status: EvaluationStatus;
  if (rateLimited && !evaluate.isPending) status = 'waiting';
  else if (requestKey !== sentKey || evaluate.isPending) status = 'evaluating';
  else if (evaluate.isError) status = 'error';
  else if (evaluation && !evaluationMatchesCart(cart, evaluation)) status = 'mismatch';
  else status = evaluation ? 'ready' : 'evaluating';

  const order = confirm.data;
  const edit = (next: Cart) => {
    if (order) return;
    setNotice(null);
    setCart(next);
  };

  const restart = () => {
    confirm.reset();
    setNotice(null);
    setKept(new Set());
    setCart({ ...initial, lines: initial.lines.map((l) => ({ ...l })) });
  };

  const place = () => {
    if (!evaluation || status !== 'ready') return;
    setNotice(null);
    confirm.mutate(confirmRequest(cart, evaluation), {
      onError: (error) => {
        if (!isApiError(error)) return;
        if (error.status === 409) {
          // The snapshot can no longer be confirmed (its terms lapsed): evaluate the cart again.
          confirm.reset();
          setNotice({ kind: 'terms_changed', error });
          run(request);
        } else if (error.status === 403) {
          confirm.reset();
          setNotice({ kind: 'read_only', error });
        }
      },
    });
  };

  const latency = latencyMs !== null && clock.today ? latencyMetric(latencyMs, clock.today, API_MODE) : null;

  return (
    <div className="grid min-w-0 gap-6 lg:grid-cols-2" data-checkout={preset} data-evaluation-status={status}>
      <ShopView
        cart={cart}
        evaluation={evaluation}
        status={status}
        evaluateError={evaluate.error}
        onRetry={() => {
          stopCooldown();
          setNotice(null);
          run(request);
        }}
        kept={kept}
        onKeep={(index) => setKept((prev) => new Set(prev).add(index))}
        onSwitch={(index, size) => edit(switchSize(cart, index, size))}
        onRemove={(index) => {
          setKept(new Set());
          edit(removeLine(cart, index));
        }}
        onCoupon={(code) => edit({ ...cart, coupon_code: code })}
        onPlace={place}
        placing={confirm.isPending}
        confirmError={confirm.error}
        order={order}
        onRestart={restart}
        lessGenerous={evaluation ? lessGenerous(compareTerms(evaluation.base_terms, evaluation.terms)) : false}
        lang={lang}
        notice={notice}
        cooldownLeft={cooldownLeft}
      />
      <BehindTheScenes evaluation={status === 'ready' || status === 'mismatch' ? evaluation : undefined} latency={latency} order={order} />
    </div>
  );
}

export default function ShopPage() {
  const [params, setParams] = useSearchParams();
  const preset = presetId(params.get('preset'));
  const lang = shopLang(params.get('lang'));
  const cases = useDemoCases();
  const { carts, linkedPending } = useCarts(cases.data);
  const cart = carts[preset];
  const waiting = cases.isPending || (preset === 'linked' && !cart && linkedPending);
  const noReset = isApiError(cases.error) && cases.error.status === 404;
  const setLang = (next: Lang) =>
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        if (next === 'it') out.delete('lang');
        else out.set('lang', next);
        return out;
      },
      { replace: true },
    );
  return (
    <div className="mx-auto flex max-w-content flex-col gap-6" data-page="shop">
      <PageHeader title={PLATFORM.titles.shop} intro={SHOP.intro} stories={SHOP.stories} />
      <div className="flex flex-col gap-2">
        <PresetPicker current={preset} carts={carts} cluster={caseOf(cases.data, 'marco')?.cluster_id ?? null} lang={lang} />
        <LangPicker lang={lang} onChange={setLang} />
      </div>
      {waiting && <LoadingBlock rows={4} />}
      {cases.isError && (noReset ? <NoResetNotice error={cases.error} /> : <ErrorState error={cases.error} onRetry={() => void cases.refetch()} />)}
      {cases.data && !cart && !waiting && <EmptyState message={SHOP.noCart} />}
      {cart && <Checkout key={`${preset}:${JSON.stringify(cart)}`} preset={preset} initial={cart} lang={lang} />}
    </div>
  );
}
