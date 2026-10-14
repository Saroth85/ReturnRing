// Decision rail (docs/ui-design.md 6.2.4): the only place where the outcome and the actions live,
// headed by the engine and its rules (rule 1). The customer's floor comes first (rules 3, 4); a
// restriction shows the person it needs and its review date (rule 5).
import { Ticket, UserCheck } from 'lucide-react';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { Link } from 'react-router';
import type { CaseCard } from '@/api/models';
import { ApproverLine, DecisionChain, RefundFloorNotice, RestrictionBadge, TermsDiff } from '@/components/decision';
import { GenerationBadge } from '@/components/message';
import { DateText, Figure, IdText } from '@/components/provenance';
import { restrictionStatus } from '@/domain/decision';
import { OPERATOR } from '../../copy';
import { useCaseUi } from '../context';
import { ActionBar, type ActionBarProps } from './ActionBar';

const WIDE = '(min-width: 1280px)';
const BOTTOM_GAP_PX = 12;
const CORNER_GAP_PX = 6;
const MIN_RAIL_PX = 320;

/**
 * On wide screens the rail is sticky and its body scrolls inside it; its height follows the
 * viewport from wherever the rail starts, so the action bar is always on screen (6.2.4).
 */
function useFitToViewport(ref: RefObject<HTMLElement | null>): number | undefined {
  const [maxHeight, setMaxHeight] = useState<number | undefined>(undefined);
  useEffect(() => {
    const media = window.matchMedia?.(WIDE);
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const el = ref.current;
        if (!el || !media?.matches) {
          setMaxHeight(undefined);
          return;
        }
        const rect = el.getBoundingClientRect();
        const top = Math.max(rect.top, 0);
        // Whatever is fixed or sticky at the bottom over the rail's column (the presenter's demo
        // rail, the mode corner tag) ends the rail above it, so the action bar stays visible (UI22).
        let gap = BOTTOM_GAP_PX;
        for (const obstacle of document.querySelectorAll<HTMLElement>('[data-demo-rail], [data-mode-corner]')) {
          const o = obstacle.getBoundingClientRect();
          const overColumn = o.right > rect.left && o.left < rect.right && o.height > 0;
          if (overColumn && o.top < window.innerHeight) gap = Math.max(gap, window.innerHeight - o.top + CORNER_GAP_PX);
        }
        setMaxHeight(Math.max(MIN_RAIL_PX, Math.round(window.innerHeight - top - gap)));
      });
    };
    update();
    window.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    media?.addEventListener?.('change', update);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
      media?.removeEventListener?.('change', update);
    };
  }, [ref]);
  return maxHeight;
}

function ApprovalGate({ card }: { card: CaseCard }) {
  const r = OPERATOR.railCopy;
  const { decision } = card;
  const coupons = decision.coupon_rule?.affected_customer_ids.length ?? 0;
  return (
    <div className="hatch-attn flex gap-2 rounded-control border border-attn-border px-3 py-2 text-body" data-approval-gate>
      <UserCheck aria-hidden="true" size={18} className="mt-0.5 shrink-0" />
      <p>
        <strong className="font-semibold">{r.gateTitle}</strong> · {r.gateBody}
        {decision.review_at ? (
          <>
            {' · '}
            {r.gateReview} <DateText value={decision.review_at} />
          </>
        ) : (
          <>
            {' · '}
            <span className="font-medium" data-gate-no-review>
              {r.gateNoReview}
            </span>
          </>
        )}
        {coupons > 0 && (
          <>
            {' · '}
            <span data-constant>{r.gateCoupons(coupons)}</span>
          </>
        )}
        . {r.gateKeeps}
      </p>
    </div>
  );
}

function CouponApplicationNotice({ card }: { card: CaseCard }) {
  const rule = card.decision.coupon_rule;
  if (!rule) return null;
  const r = OPERATOR.railCopy;
  return (
    <div className="flex gap-2 rounded-control border border-border px-3 py-2 text-dense" data-coupon-notice>
      <Ticket aria-hidden="true" size={16} className="mt-0.5 shrink-0 text-ink-2" />
      <p className="flex flex-wrap items-center gap-x-1">
        {r.couponPause} <IdText id={rule.application_id} />{' '}
        <span data-constant>{r.forAccounts(rule.affected_customer_ids.length, rule.cluster_id)}</span> · {r.rule}{' '}
        <IdText id={rule.rule_version} /> · {r.reversible} ·{' '}
        {rule.confirmed_by ? (
          <>
            {r.confirmedBy} <strong className="font-semibold">{rule.confirmed_by}</strong>
            {card.decision.review_at && (
              <>
                {' · '}
                {OPERATOR.queue.review} <DateText value={card.decision.review_at} />
              </>
            )}{' '}
            ·{' '}
            <Link to="/coupons" className="text-accent underline underline-offset-2">
              {r.reverse}
            </Link>
          </>
        ) : (
          r.approvingConfirms
        )}
      </p>
    </div>
  );
}

function ClaimNotice({ card }: { card: CaseCard }) {
  const claim = card.decision.diminished_value_claim;
  if (!claim) return null;
  const r = OPERATOR.railCopy;
  return (
    <p className="flex flex-wrap items-center gap-1 rounded-control border border-border px-3 py-2 text-dense" data-claim-notice>
      {r.claim} <Figure metric={claim.amount} label={r.claim} /> · {r.claimBy} <strong>{claim.confirmed_by}</strong> · {r.claimRule}{' '}
      <IdText id={claim.rule_id} />
    </p>
  );
}

function AlternativesStrip({ card }: { card: CaseCard }) {
  const a = OPERATOR.alternatives;
  return (
    <div className="flex flex-col gap-1" data-alternatives-strip>
      <p className="text-caption text-ink-3">{a.strip}</p>
      <ul className="flex flex-wrap gap-1">
        {card.alternatives.map((alt) => (
          <li key={alt.kind} className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-dense">
            <a href="#section-alternatives" className="text-ink-1 hover:text-accent">
              {a.kinds[alt.kind] ?? alt.kind}
            </a>
            <Figure metric={alt.net_benefit.total} label={a.netBenefit} signed />
            {alt.recommended && <span className="text-micro uppercase text-ink-3">{a.engine}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function DecisionRail(props: ActionBarProps) {
  const { card, sendLang } = useCaseUi();
  const { decision } = card;
  const r = OPERATOR.railCopy;
  const status = restrictionStatus(decision);
  // Model versions only for a scored case: the guarantee path and not enough evidence are not scored.
  const scores = decision.path === 'withdrawal' && decision.evidence_quality === 'sufficient' ? decision.model_versions.risk : undefined;
  const railRef = useRef<HTMLElement>(null);
  const maxHeight = useFitToViewport(railRef);
  return (
    <aside
      ref={railRef}
      aria-label={r.title}
      tabIndex={-1}
      style={maxHeight ? { maxHeight } : undefined}
      className="flex flex-col rounded-card border border-border bg-surface xl:sticky xl:top-10 xl:overflow-hidden"
      data-decision-rail
    >
      <div className="flex min-h-0 flex-col gap-3 p-4 *:shrink-0 xl:flex-1 xl:overflow-y-auto" data-rail-body>
        <header className="flex flex-col gap-1">
          <h2 className="text-h3">{r.title}</h2>
          <p className="flex flex-wrap items-center gap-x-1 text-caption text-ink-3">
            {r.policyEngine} · {r.rules} <IdText id={decision.rules_version} className="text-caption" />
            {scores && (
              <>
                · {r.scores} <IdText id={scores} className="text-caption" />
              </>
            )}
          </p>
          <p className="text-caption text-ink-2">{r.rulesDecided}</p>
        </header>
        <RefundFloorNotice refundDueBy={decision.refund_due_by} path={decision.path} />
        {status.kind === 'needs_approval' && <ApprovalGate card={card} />}
        <TermsDiff
          current={decision.current_return_terms}
          future={decision.future_terms}
          levers={decision.levers}
          snapshotId={card.order_terms.terms_snapshot_id}
          clusterSize={decision.coupon_rule?.affected_customer_ids.length}
        />
        <div className="flex flex-wrap items-center gap-2">
          <RestrictionBadge decision={decision} />
        </div>
        <ApproverLine decision={decision} />
        {decision.override_reason && (
          <p className="text-dense text-ink-2">
            {OPERATOR.sheets.reason}: <span data-verbatim>“{decision.override_reason}”</span>
            {decision.override_category && <> · {OPERATOR.sheets.categories[decision.override_category]}</>}
          </p>
        )}
        <CouponApplicationNotice card={card} />
        <ClaimNotice card={card} />
        <DecisionChain decision={decision} />
        <AlternativesStrip card={card} />
        <div className="flex flex-wrap items-center gap-2 text-dense" data-rail-message>
          <span className="text-ink-3">{r.message}</span>
          <span>{OPERATOR.sheets.langs[sendLang]}</span>
          {card.draft ? (
            <GenerationBadge generation={card.draft.generation} />
          ) : (
            <span className="text-ink-3">{r.noDraft}</span>
          )}
          <a href="#section-message" className="text-accent underline underline-offset-2">
            {r.preview} ↓
          </a>
        </div>
      </div>
      <ActionBar {...props} />
    </aside>
  );
}
