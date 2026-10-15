// The decision sheets (docs/ui-design.md 6.3.2, 6.3.4, 6.3.5): approve a restriction, override
// the proposal, reject it, review a restriction (lift or renew). Each sends the decision it was
// shown (`shown`, snapshotted when the sheet opened, UI38) and the acting person; override, reject
// and lift need a reason, approve and renew do not. Approve, override and reject send a message;
// lift and renew send none (the customer sees the terms at checkout). A newer version blocks the
// confirm until loaded. On the service's clock (ADR-15 sections 19, 20): past the refund deadline
// a proposal can only be rejected or set to the standard terms, with no message, and the case
// stays refund overdue; a renewal opens on the review date (US-607).
import { Check, MailX } from 'lucide-react';
import { useState } from 'react';
import { LEGAL_MINIMUM_WINDOW_DAYS, OVERRIDE_CATEGORIES } from '@/api/contract.gen';
import { messageFor } from '@/api/adapters/messageSegments';
import { useDemoClock } from '@/api/clock';
import { HAS_RENEW } from '@/api/models';
import type { DecisionResult, DecisionView, Lever } from '@/api/models';
import { usePersona } from '@/app/persona';
import { InlineNotice } from '@/components/feedback';
import { LEVER_COPY, LeverList, LinkedAccountsCount, RestrictionBadge, TERMS_COPY, TermsDiff, leverLabel } from '@/components/decision';
import { MessagePreview } from '@/components/message';
import { DateText, IdText } from '@/components/provenance';
import { Kbd } from '@/components/ui';
import { overrideLock, termsRows, type OverrideLock } from '@/domain/decision';
import {
  admissibleValue,
  baseValue,
  hasRestrictiveLever,
  isGrantOnly,
  leverDirection,
  LEVER_SPECS,
  leversFromValues,
  leverValues,
  sameLevers,
  type LeverId,
  type LeverValues,
} from '@/domain/levers';
import { OPERATOR } from '../../copy';
import { lateProposal } from '../../state/refund';
import { approveBody, liftBody, overrideBody, rejectBody, renewBody } from '../../state/requests';
import { CategorySelect, LangChoice, ReasonField, type OverrideCategory } from '../../ui/fields';
import { Sheet } from '../../ui/Sheet';
import { reasonValid } from '../../ui/validation';
import { useCaseUi } from '../context';
import { ActionError } from './ActionError';
import { ActingAs, CaseIdsLine, ConfirmFooter, StaleDecisionPanel } from './common';
import { useNewerVersion } from './useNewerVersion';
import { useDecisionAction } from './useDecisionAction';

export interface DecisionSheetProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  onDone(result: DecisionResult): void;
  onReload(): void;
  /** What changed after a reload (stale decision), if anything was reloaded. */
  changes: string[] | null;
  /** The decision on screen when the sheet opened: the request is built from it (UI38). */
  shown: DecisionView;
}

/** Past the refund deadline the act sends no message: said where the message choice would be. */
function NoMessageLate() {
  return (
    <p
      className="flex gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-body text-attn-fg"
      data-no-message="late"
    >
      <MailX aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
      <span>{OPERATOR.refund.noMessage}</span>
    </p>
  );
}

/** Whether the decision on the sheet is a proposal past its refund deadline, on the service's clock. */
function useLate(shown: DecisionView): boolean {
  const clock = useDemoClock();
  return lateProposal(shown, clock.today);
}

/**
 * The message language, and what the customer will read. Only an approval sends the draft written
 * for the proposal, so only the approve sheet previews it (rule 10). A rejection or an override
 * sends a message the service composes for the decision acted on, with its own terms, dates and
 * clause: the sheet says so and previews nothing; the sent message is in the outbox afterwards.
 */
function MessageChoice({ late = false, sends }: { late?: boolean; sends: 'draft' | 'composed' }) {
  const { card, sendLang, setSendLang } = useCaseUi();
  const message = sends === 'draft' ? messageFor(card.draft, sendLang) : null;
  if (late) return <NoMessageLate />;
  return (
    <div className="flex flex-col gap-2">
      <LangChoice value={sendLang} onChange={setSendLang} />
      {sends === 'composed' && (
        <p className="text-caption text-ink-2" data-message-composed>
          {OPERATOR.sheets.composedMessage}
        </p>
      )}
      {message && (
        <details>
          <summary className="cursor-pointer text-caption text-accent">{OPERATOR.railCopy.preview}</summary>
          <div className="mt-2">
            <MessagePreview message={message} />
          </div>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------

export function ApproveRestrictionSheet({ open, onOpenChange, onDone, onReload, changes, shown }: DecisionSheetProps) {
  const { card, sendLang, openSheet, gates, draftPending } = useCaseUi();
  const persona = usePersona();
  const action = useDecisionAction(card, onDone);
  const newer = useNewerVersion(shown);
  const late = useLate(shown);
  const [ack, setAck] = useState(false);
  const t = OPERATOR.sheets.approve;
  const d = shown;
  const rows = termsRows(d.current_return_terms, d.future_terms, d.levers);
  const unchanged = rows.filter((r) => r.change === 'same' && r.future === true);
  const rule = d.coupon_rule;
  const reasons = [
    ...(newer ? [OPERATOR.sheets.staleReason] : []),
    ...(late ? [OPERATOR.refund.primaryReason] : []),
    // Rule 5: a restriction is approved only with its review date (the server checks it too).
    ...(!d.review_at ? [t.noReviewDate] : []),
    ...(rule && !ack ? [t.couponRequired] : []),
    ...(draftPending ? [OPERATOR.actions.draftPendingReason] : []),
    ...(!gates.decide.allowed && gates.decideNote ? [gates.decideNote] : []),
  ];
  const confirm = () => {
    if (reasons.length > 0 || action.pending) return;
    action.submit(approveBody(d, sendLang, persona.id));
  };
  return (
    <Sheet
      name="approve"
      open={open}
      onOpenChange={(next) => {
        if (!next) action.reset();
        onOpenChange(next);
      }}
      title={t.title}
      description={<CaseIdsLine decision={d} />}
      onConfirm={confirm}
      footer={
        <ConfirmFooter
          label={t.submit}
          restrictive
          disabled={reasons.length > 0}
          pending={action.pending}
          reasons={reasons}
          onConfirm={confirm}
          name="approve"
        />
      }
    >
      <div className="flex flex-col gap-4">
        <StaleDecisionPanel shown={d} onReload={onReload} changes={changes} />
        <section className="flex flex-col gap-2">
          <h3 className="text-title">{t.changes}</h3>
          <TermsDiff
            current={d.current_return_terms}
            future={d.future_terms}
            levers={d.levers}
            onlyChanges
            cluster={rule ? { size: rule.affected_customer_ids.length, provenance: d.inputs.provenance } : undefined}
            snapshotId={card.order_terms.terms_snapshot_id}
          />
        </section>
        <section className="flex flex-col gap-1">
          <h3 className="text-title">{t.unchanged}</h3>
          <ul className="flex flex-col gap-1 text-body">
            <li className="flex gap-2">
              <Check aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
              <span data-verbatim>{t.thisReturn(card.order_id, card.order_terms.terms_snapshot_id)}</span>
            </li>
            {/* No refund deadline on the guarantee path (rule 4): refund_due_by is null there. */}
            {d.refund_due_by && (
              <li className="flex gap-2">
                <Check aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
                <span>
                  {t.refundBy} <DateText value={d.refund_due_by} /> · <span data-legal-ref>{t.withdrawalStays}</span>
                </span>
              </li>
            )}
            {unchanged.map((r) => (
              <li key={r.lever} className="flex gap-2">
                <Check aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
                <span data-constant>{leverLabel({ id: r.lever, value: r.future })}</span>
              </li>
            ))}
          </ul>
        </section>
        <p className="text-body">{t.whenShown}</p>
        {d.review_at ? (
          <p className="text-body">
            {t.untilWhen} <DateText value={d.review_at} className="font-medium" /> · {t.lapses}
          </p>
        ) : (
          <InlineNotice tone="attention">{t.noReviewDate}</InlineNotice>
        )}
        {rule && (
          <div className="flex flex-col gap-2 rounded-control border-2 border-attn-border p-3" data-coupon-ack>
            <label className="flex items-start gap-2 text-body">
              <input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} data-coupon-ack-box />
              <span className="inline-flex flex-wrap items-baseline gap-x-1">
                <LinkedAccountsCount
                  count={rule.affected_customer_ids.length}
                  provenance={d.inputs.provenance}
                  before={t.couponAckBefore}
                  after={t.couponAckAfter}
                />
                <IdText id={rule.cluster_id} /> (<IdText id={rule.rule_version} />, <IdText id={rule.application_id} />).{' '}
                {t.couponAckReversible}
              </span>
            </label>
            <details>
              <summary className="cursor-pointer text-caption text-accent">{OPERATOR.linked.accounts}</summary>
              <p className="mt-1 flex flex-wrap gap-1">
                {rule.affected_customer_ids.map((id) => (
                  <IdText key={id} id={id} />
                ))}
              </p>
            </details>
          </div>
        )}
        <MessageChoice sends="draft" />
        <ActingAs />
        <button
          type="button"
          className="self-start text-body text-accent underline underline-offset-2"
          onClick={() => openSheet({ kind: 'override' })}
        >
          {t.overrideInstead} <Kbd>O</Kbd>
        </button>
        {action.error ? <ActionError error={action.error} onLoadNew={onReload} onShowCurrent={onReload} changes={changes} /> : null}
        {action.mismatch && <InlineNotice tone="attention">{action.mismatch}</InlineNotice>}
      </div>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------------------------

/**
 * What keeps the editor's levers where they are: the guarantee path (no restrictive change, rule
 * 4), not enough evidence (the base terms only, US-307) and a proposal past its refund deadline
 * (the standard terms only: other terms would need a message with a past date, ADR-15 section 19).
 */
type EditorLock = OverrideLock | 'late';

/** The lever editor of the override sheet (docs/ui-design.md 6.3.4). */
function LeverEditor({ values, onChange, lock }: { values: LeverValues; onChange(values: LeverValues): void; lock: EditorLock | null }) {
  const { card } = useCaseUi();
  const base = card.order_terms.base_terms;
  const o = OPERATOR.sheets.override;
  const set = (id: LeverId, value: number | boolean) => onChange({ ...values, [id]: value });
  return (
    <fieldset className="flex flex-col divide-y divide-border rounded-control border border-border" data-lever-editor>
      <legend className="mb-1 text-title">{o.levers}</legend>
      {lock === 'insufficient' && (
        <p className="px-3 py-2 text-body text-ink-2" data-insufficient-lock>
          {o.insufficientLocked}
        </p>
      )}
      {lock === 'late' && (
        <p className="px-3 py-2 text-body text-ink-2" data-late-lock>
          {o.lateLocked}
        </p>
      )}
      {LEVER_SPECS.map((spec) => {
        const value = values[spec.id];
        const direction = leverDirection({ id: spec.id, value }, base);
        const scope = LEVER_COPY.scope[spec.scope];
        const tag =
          direction === 'less' ? (
            <span className="text-micro uppercase text-attn-fg">{o.restrictive}</span>
          ) : direction === 'more' ? (
            <span className="text-micro uppercase text-ink-3">{LEVER_COPY.moreGenerous}</span>
          ) : null;
        const guarantee = lock === 'guarantee';
        // Not enough evidence, or past the refund deadline: every lever stays at the base terms
        // (US-307, UI13; ADR-15 section 19).
        const frozen = lock === 'insufficient' || lock === 'late';
        if (spec.valueType === 'integer') {
          const min = guarantee ? Math.max(LEGAL_MINIMUM_WINDOW_DAYS, Number(base.return_window_days)) : LEGAL_MINIMUM_WINDOW_DAYS;
          return (
            <label key={spec.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-body" data-lever-row={spec.id}>
              <span className="min-w-40">{TERMS_COPY.row.return_window_days}</span>
              <input
                type="number"
                min={min}
                step={1}
                value={Number(value)}
                disabled={frozen}
                onChange={(e) => set(spec.id, Math.max(min, Math.round(Number(e.target.value) || min)))}
                className="w-20 rounded-control border border-border-strong bg-surface px-2 py-1"
                aria-describedby={frozen ? undefined : `hint-${spec.id}`}
              />
              <span className="text-caption text-ink-3">{o.days}</span>
              <span className="text-micro uppercase text-ink-3">· {scope}</span>
              {tag}
              {!frozen && (
                <span id={`hint-${spec.id}`} className="basis-full text-caption text-ink-3">
                  {guarantee ? o.notOnGuarantee : o.windowHint}
                </span>
              )}
            </label>
          );
        }
        const checked = value === true;
        // On the guarantee path every change less generous than the base is disabled (rule 4).
        const wouldRestrict = leverDirection({ id: spec.id, value: !checked }, base) === 'less';
        const guarded = guarantee && wouldRestrict && direction !== 'less';
        // A lever of scope all_orders holds for orders already placed: it can only be granted
        // (true), never withdrawn (contracts/levers.py, rule 3). Unticking it is offered only when
        // that returns to the base terms (no lever at all).
        const next = !checked;
        const grantOnly = isGrantOnly(spec) && !admissibleValue(spec.id, next) && baseValue(spec.id, base) !== next;
        const locked = frozen || guarded || grantOnly;
        return (
          <label key={spec.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-body" data-lever-row={spec.id}>
            <input type="checkbox" checked={checked} disabled={locked} onChange={(e) => set(spec.id, e.target.checked)} />
            <span>{LEVER_COPY.label[spec.id as Exclude<LeverId, 'return_window_days'>].true}</span>
            <span className="text-micro uppercase text-ink-3">· {scope}</span>
            {tag}
            {guarded && !frozen && <span className="basis-full text-caption text-ink-3">{o.notOnGuarantee}</span>}
            {grantOnly && !frozen && !guarded && (
              <span className="basis-full text-caption text-ink-3" data-grant-only>
                {o.grantOnly}
              </span>
            )}
          </label>
        );
      })}
    </fieldset>
  );
}

export function OverrideSheet({
  open,
  onOpenChange,
  onDone,
  onReload,
  changes,
  shown,
  prefill,
}: DecisionSheetProps & { prefill?: Lever[] }) {
  const { card, sendLang, gates, draftPending } = useCaseUi();
  const persona = usePersona();
  const action = useDecisionAction(card, onDone);
  const newer = useNewerVersion(shown);
  const late = useLate(shown);
  const d = shown;
  const base = card.order_terms.base_terms;
  const o = OPERATOR.sheets.override;
  // Past the refund deadline only the standard terms (no lever) can be set (ADR-15 section 19).
  const lock: EditorLock | null = overrideLock({ path: card.path, decision: d }) ?? (late ? 'late' : null);
  const atBase = lock === 'insufficient' || lock === 'late';
  const starts = [
    { key: 'proposal', label: o.proposal, levers: d.proposed_levers },
    ...card.alternatives
      .filter((a) => a.kind !== 'proposal')
      .map((a) => ({ key: a.kind, label: OPERATOR.alternatives.kinds[a.kind] ?? a.kind, levers: a.levers })),
  ];
  // Not enough evidence, or past the refund deadline: the editor starts and stays on the base
  // terms, whatever the prefill (UI13).
  const initial = atBase ? [] : (prefill ?? d.proposed_levers);
  const [start, setStart] = useState(() => starts.find((s) => sameLevers(s.levers, initial))?.key ?? 'proposal');
  const [values, setValues] = useState<LeverValues>(() => leverValues(initial, base));
  // "Load the new version" moves the sheet onto the new decision: the levers start again from its
  // proposal, the reason and the category stay (docs/ui-design.md 6.4).
  const [basedOn, setBasedOn] = useState(d.decision_id);
  if (basedOn !== d.decision_id) {
    setBasedOn(d.decision_id);
    setStart('proposal');
    setValues(leverValues(atBase ? [] : d.proposed_levers, base));
  }
  const [reason, setReason] = useState('');
  const [category, setCategory] = useState<OverrideCategory | ''>('');
  const [tried, setTried] = useState(false);
  const levers = leversFromValues(values, base);
  const restrictive = hasRestrictiveLever(levers, base);
  const asProposal = sameLevers(levers, d.proposed_levers);
  const reasons = [
    ...(newer ? [OPERATOR.sheets.staleReason] : []),
    ...(lock === 'insufficient' && levers.length > 0 ? [o.insufficientLocked] : []),
    ...(lock === 'late' && levers.length > 0 ? [o.lateLocked] : []),
    ...(asProposal ? [late ? o.lateNoChange : o.noChange] : []),
    ...(!reasonValid(reason) ? [OPERATOR.sheets.reasonTooShort] : []),
    ...(category === '' ? [OPERATOR.sheets.chooseCategory] : []),
    ...(draftPending ? [OPERATOR.actions.draftPendingReason] : []),
    ...(!gates.decide.allowed && gates.decideNote ? [gates.decideNote] : []),
  ];
  const confirm = () => {
    setTried(true);
    if (reasons.length > 0 || action.pending || category === '') return;
    action.submit(overrideBody(d, levers, reason, category, sendLang, persona.id));
  };
  return (
    <Sheet
      name="override"
      open={open}
      onOpenChange={(next) => {
        if (!next) action.reset();
        onOpenChange(next);
      }}
      title={o.title}
      description={<CaseIdsLine decision={d} />}
      onConfirm={confirm}
      dirty={reason.trim().length > 0}
      footer={
        <ConfirmFooter
          label={late ? o.submitStandard : restrictive ? o.submitRestrictive : o.submit}
          restrictive={restrictive}
          disabled={reasons.length > 0}
          pending={action.pending}
          reasons={reasons}
          onConfirm={confirm}
          name="override"
        />
      }
    >
      <div className="flex flex-col gap-4">
        <StaleDecisionPanel shown={d} onReload={onReload} changes={changes} />
        {action.error ? <ActionError error={action.error} onLoadNew={onReload} onShowCurrent={onReload} changes={changes} /> : null}
        {action.mismatch && <InlineNotice tone="attention">{action.mismatch}</InlineNotice>}
        <label className="flex flex-col gap-1 text-body">
          <span className="font-medium">{o.startFrom}</span>
          <select
            value={start}
            disabled={atBase}
            onChange={(e) => {
              const chosen = starts.find((s) => s.key === e.target.value);
              setStart(e.target.value);
              if (chosen) setValues(leverValues(chosen.levers, base));
            }}
            className="rounded-control border border-border-strong bg-surface px-2 py-1"
          >
            {starts.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <LeverEditor values={values} onChange={setValues} lock={lock} />
        <div className="rounded-control border border-border bg-surface-2 px-3 py-2 text-caption text-ink-2" data-guard-preview>
          <p className="font-medium text-ink-1">{o.guardTitle}</p>
          <ul className="list-disc pl-5" data-constant>
            {o.guardRules.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
          <p>{o.guardNote}</p>
        </div>
        {restrictive && (
          <div className="hatch-attn flex flex-col gap-1 rounded-control border border-attn-border px-3 py-2 text-body" data-override-gate>
            <p className="font-medium">{o.restrictiveGate}</p>
            <p data-constant>{o.reviewSetByServer}</p>
          </div>
        )}
        <CategorySelect value={category} onChange={setCategory} showError={tried} />
        <ReasonField value={reason} onChange={setReason} showError={tried} />
        <MessageChoice late={late} sends="composed" />
        <ActingAs />
      </div>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------------------------

export function RejectSheet({ open, onOpenChange, onDone, onReload, changes, shown }: DecisionSheetProps) {
  const { card, sendLang, gates, draftPending } = useCaseUi();
  const persona = usePersona();
  const action = useDecisionAction(card, onDone);
  const newer = useNewerVersion(shown);
  const late = useLate(shown);
  const [reason, setReason] = useState('');
  const [category, setCategory] = useState<OverrideCategory | ''>('');
  const [tried, setTried] = useState(false);
  const r = OPERATOR.sheets.reject;
  const reasons = [
    ...(newer ? [OPERATOR.sheets.staleReason] : []),
    ...(!reasonValid(reason) ? [OPERATOR.sheets.reasonTooShort] : []),
    ...(category === '' ? [OPERATOR.sheets.chooseCategory] : []),
    ...(draftPending ? [OPERATOR.actions.draftPendingReason] : []),
    ...(!gates.decide.allowed && gates.decideNote ? [gates.decideNote] : []),
  ];
  const confirm = () => {
    setTried(true);
    if (reasons.length > 0 || action.pending || category === '') return;
    action.submit(rejectBody(shown, reason, category, sendLang, persona.id));
  };
  return (
    <Sheet
      name="reject"
      open={open}
      onOpenChange={(next) => {
        if (!next) action.reset();
        onOpenChange(next);
      }}
      title={r.title}
      description={<CaseIdsLine decision={shown} />}
      onConfirm={confirm}
      dirty={reason.trim().length > 0}
      footer={
        <ConfirmFooter
          label={r.submit}
          disabled={reasons.length > 0}
          pending={action.pending}
          reasons={reasons}
          onConfirm={confirm}
          name="reject"
        />
      }
    >
      <div className="flex flex-col gap-4">
        <StaleDecisionPanel shown={shown} onReload={onReload} changes={changes} />
        <p className="text-body text-ink-1">{r.body}</p>
        <CategorySelect value={category} onChange={setCategory} showError={tried} />
        <ReasonField value={reason} onChange={setReason} showError={tried} />
        <MessageChoice late={late} sends="composed" />
        <ActingAs />
        {action.error ? <ActionError error={action.error} onLoadNew={onReload} onShowCurrent={onReload} changes={changes} /> : null}
        {action.mismatch && <InlineNotice tone="attention">{action.mismatch}</InlineNotice>}
      </div>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------------------------

const REVIEW_CATEGORIES = OVERRIDE_CATEGORIES.filter((c) => c === 'periodic_review' || c === 'customer_context');

export function ReviewSheet({
  open,
  onOpenChange,
  onDone,
  onReload,
  changes,
  shown,
  initial = 'lift',
}: DecisionSheetProps & { initial?: 'lift' | 'renew' }) {
  const { card, gates, draftPending } = useCaseUi();
  const persona = usePersona();
  const clock = useDemoClock();
  const action = useDecisionAction(card, onDone);
  const newer = useNewerVersion(shown);
  const d = shown;
  // A renewal opens on the review date (409 before it, US-607); with no clock yet, `in_force`
  // says whether the date has passed.
  const renewOpen =
    HAS_RENEW && d.review_at !== null && (clock.today !== null ? clock.today >= d.review_at : !d.in_force);
  const [picked, setChoice] = useState<'lift' | 'renew'>(initial);
  const choice = picked === 'renew' && !renewOpen ? 'lift' : picked;
  const [reason, setReason] = useState('');
  const [category, setCategory] = useState<OverrideCategory | ''>('periodic_review');
  const [tried, setTried] = useState(false);
  const v = OPERATOR.sheets.review;
  // A lift needs a reason and a category; a renewal needs neither (restriction_renewed is not a
  // reason-required action). Neither sends a message.
  const lifting = choice === 'lift';
  const reasons = [
    ...(newer ? [OPERATOR.sheets.staleReason] : []),
    ...(lifting && !reasonValid(reason) ? [OPERATOR.sheets.reasonTooShort] : []),
    ...(lifting && category === '' ? [OPERATOR.sheets.chooseCategory] : []),
    ...(draftPending ? [OPERATOR.actions.draftPendingReason] : []),
    ...(!gates.decide.allowed && gates.decideNote ? [gates.decideNote] : []),
  ];
  const confirm = () => {
    setTried(true);
    if (reasons.length > 0 || action.pending) return;
    if (!lifting) action.submit(renewBody(d, persona.id));
    else if (category !== '') action.submit(liftBody(d, reason, category, persona.id));
  };
  return (
    <Sheet
      name="review"
      open={open}
      onOpenChange={(next) => {
        if (!next) action.reset();
        onOpenChange(next);
      }}
      title={v.title}
      description={<CaseIdsLine decision={d} />}
      onConfirm={confirm}
      dirty={reason.trim().length > 0}
      footer={
        <ConfirmFooter
          label={lifting ? v.submitLift : v.submitRenew}
          disabled={reasons.length > 0}
          pending={action.pending}
          reasons={reasons}
          onConfirm={confirm}
          name="review"
        />
      }
    >
      <div className="flex flex-col gap-4">
        <StaleDecisionPanel shown={d} onReload={onReload} changes={changes} />
        <section className="flex flex-col gap-2 rounded-control border border-border p-3">
          <h3 className="text-title">{v.restriction}</h3>
          <LeverList levers={d.levers} base={card.order_terms.base_terms} />
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-dense">
            <dt className="text-ink-3">{v.approvedBy}</dt>
            <dd>
              {d.approver} {d.approved_at && <DateText value={d.approved_at} className="text-ink-3" />}
            </dd>
            <dt className="text-ink-3">{v.reviewDate}</dt>
            <dd>{d.review_at ? <DateText value={d.review_at} /> : '—'}</dd>
            <dt className="text-ink-3">{v.state}</dt>
            <dd>
              <RestrictionBadge decision={d} />
            </dd>
          </dl>
        </section>
        <fieldset className="flex flex-col gap-2" data-review-choice={choice}>
          <legend className="sr-only">{v.title}</legend>
          <label className="grid grid-cols-[auto_1fr] items-start gap-x-2 text-body">
            <input type="radio" name="review-choice" className="mt-1" checked={choice === 'lift'} onChange={() => setChoice('lift')} />
            <span className="font-medium">{v.lift}</span>
            <span className="col-start-2 text-caption text-ink-2" data-version>
              {v.liftBody(`v${d.version + 1}`)}
            </span>
          </label>
          {HAS_RENEW && (
            <label className="grid grid-cols-[auto_1fr] items-start gap-x-2 text-body">
              <input
                type="radio"
                name="review-choice"
                className="mt-1"
                checked={choice === 'renew'}
                disabled={!renewOpen}
                onChange={() => setChoice('renew')}
                data-renew-choice
              />
              <span className={renewOpen ? 'font-medium' : 'font-medium text-ink-3'}>{v.renew}</span>
              <span className="col-start-2 text-caption text-ink-2">{v.renewBody}</span>
              {!renewOpen && d.review_at && (
                <span className="col-start-2 text-caption text-ink-3" data-renew-reason>
                  {v.renewFrom} <DateText value={d.review_at} />
                </span>
              )}
            </label>
          )}
        </fieldset>
        <p className="text-caption text-ink-2" data-no-message="review">
          {v.noMessage}
        </p>
        {lifting ? (
          <>
            <CategorySelect value={category} onChange={setCategory} showError={tried} allowed={REVIEW_CATEGORIES} />
            <ReasonField value={reason} onChange={setReason} showError={tried} />
          </>
        ) : (
          <p className="text-caption text-ink-2" data-renew-no-reason>
            {v.renewNoReason}
          </p>
        )}
        <ActingAs />
        {action.error ? <ActionError error={action.error} onLoadNew={onReload} onShowCurrent={onReload} changes={changes} /> : null}
        {action.mismatch && <InlineNotice tone="attention">{action.mismatch}</InlineNotice>}
      </div>
    </Sheet>
  );
}
