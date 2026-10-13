// Simulated checkout /shop?preset=giulia|marco (US-401..403, docs/ui-design.md 9.1). The presenter
// picks a test cart; POST /api/checkout/evaluate runs on load and after every cart change
// (debounced), the API stores the shown terms as a snapshot before it answers, and "Place order"
// binds that snapshot to the order (POST /api/checkout/confirm, 201). The receipt then checks that
// the terms shown are the terms stored (E2E-02).
import clsx from 'clsx';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { API_MODE } from '@/api/adapters';
import { useCheckoutConfirm, useCheckoutEvaluate } from '@/api/mutations';
import type { CheckoutEvaluateRequest, Metric } from '@/api/models';
import { PLATFORM } from '../copy';
import { PageHeader } from '../shared/layout';
import { EVALUATE_DEBOUNCE_MS } from '../constants';
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
import { PRESET_IDS, presetCart, presetId, type PresetId } from './presets';
import { ShopView, type EvaluationStatus } from './ShopView';

function PresetPicker({ current }: { current: PresetId }) {
  return (
    <nav aria-label={SHOP.presetsLabel} className="flex flex-wrap items-center gap-2" data-presets>
      <span className="text-caption text-ink-3">{SHOP.presetsLabel}</span>
      {PRESET_IDS.map((id) => (
        <Link
          key={id}
          to={`/shop?preset=${id}`}
          aria-current={id === current ? 'page' : undefined}
          className={clsx(
            'inline-flex min-h-9 flex-wrap items-baseline gap-x-1 rounded-control border px-3 py-1 text-body',
            id === current ? 'border-accent bg-accent-bg font-medium text-ink-1' : 'border-border-strong bg-surface text-ink-1 hover:bg-surface-2',
          )}
        >
          {SHOP.presets[id].name}
          <span className="text-caption font-normal text-ink-3">{SHOP.presets[id].detail}</span>
        </Link>
      ))}
    </nav>
  );
}

function Checkout({ preset }: { preset: PresetId }) {
  const [cart, setCart] = useState<Cart>(() => presetCart(preset));
  const [kept, setKept] = useState<ReadonlySet<number>>(() => new Set());
  const [latency, setLatency] = useState<Metric | null>(null);
  const [sentKey, setSentKey] = useState<string | null>(null);
  const evaluate = useCheckoutEvaluate();
  const confirm = useCheckoutConfirm();
  const { mutate: evaluateMutate } = evaluate;
  const seq = useRef(0);

  const request = useMemo(() => toEvaluateRequest(cart), [cart]);
  const requestKey = JSON.stringify(request);

  const run = useCallback((body: CheckoutEvaluateRequest) => {
    const id = ++seq.current;
    const started = performance.now();
    setSentKey(JSON.stringify(body));
    evaluateMutate(body, {
      onSuccess: () => {
        if (id !== seq.current) return;
        setLatency(latencyMetric(performance.now() - started, new Date().toISOString(), API_MODE));
      },
    });
  }, [evaluateMutate]);

  // On load at once, then debounced after every cart change (docs/ui-design.md 9.1).
  const loaded = useRef(false);
  useEffect(() => {
    const delay = loaded.current ? EVALUATE_DEBOUNCE_MS : 0;
    loaded.current = true;
    const timer = setTimeout(() => run(request), delay);
    return () => clearTimeout(timer);
  }, [request, run]);

  const evaluation = evaluate.data;
  let status: EvaluationStatus;
  if (requestKey !== sentKey || evaluate.isPending) status = 'evaluating';
  else if (evaluate.isError) status = 'error';
  else if (evaluation && !evaluationMatchesCart(cart, evaluation)) status = 'mismatch';
  else status = evaluation ? 'ready' : 'evaluating';

  const order = confirm.data;
  const edit = (next: Cart) => {
    if (order) return;
    setCart(next);
  };

  const restart = () => {
    confirm.reset();
    setKept(new Set());
    setCart(presetCart(preset));
  };

  return (
    <div className="grid min-w-0 gap-6 lg:grid-cols-2" data-checkout={preset} data-evaluation-status={status}>
      <ShopView
        cart={cart}
        evaluation={evaluation}
        status={status}
        evaluateError={evaluate.error}
        onRetry={() => run(request)}
        kept={kept}
        onKeep={(index) => setKept((prev) => new Set(prev).add(index))}
        onSwitch={(index, size) => edit(switchSize(cart, index, size))}
        onRemove={(index) => {
          setKept(new Set());
          edit(removeLine(cart, index));
        }}
        onCoupon={(code) => edit({ ...cart, coupon_code: code })}
        onPlace={() => evaluation && status === 'ready' && confirm.mutate(confirmRequest(cart, evaluation))}
        placing={confirm.isPending}
        confirmError={confirm.error}
        order={order}
        onRestart={restart}
        lessGenerous={evaluation ? lessGenerous(compareTerms(evaluation.base_terms, evaluation.terms)) : false}
      />
      <BehindTheScenes evaluation={status === 'ready' || status === 'mismatch' ? evaluation : undefined} latency={latency} order={order} />
    </div>
  );
}

export default function ShopPage() {
  const [params] = useSearchParams();
  const preset = presetId(params.get('preset'));
  return (
    <div className="mx-auto flex max-w-content flex-col gap-6" data-page="shop">
      <PageHeader title={PLATFORM.titles.shop} intro={SHOP.intro} stories={SHOP.stories} />
      <PresetPicker current={preset} />
      <Checkout key={preset} preset={preset} />
    </div>
  );
}
