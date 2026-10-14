import clsx from 'clsx';
import { CalendarClock, ChevronRight, History, UserCheck } from 'lucide-react';
import { DEMO_USERS } from '@/api/contract.gen';
import { HAS_RENEW } from '@/api/models';
import type { DecisionView } from '@/api/models';
import { SHELL } from '@/copy/common';
import { restrictionStatus, type RestrictionFields, type RestrictionKind } from '@/domain/decision';
import { DateText } from '@/components/provenance/DateText';
import { IdText } from '@/components/provenance/IdText';
import { Tooltip } from '@/components/ui/Tooltip';
import { CHAIN_COPY, RESTRICTION_COPY, STATE_COPY, STATEMENT_COPY } from './copy';

const CHIP = 'inline-flex flex-wrap items-center gap-1 rounded-chip px-1.5 py-0.5 text-dense';

function roleOf(actor: string): string | null {
  const user = DEMO_USERS.find((u) => u.id === actor);
  return user ? (SHELL.roles[user.role] ?? user.role) : null;
}

/**
 * The state of a decision version (docs/ui-design.md 5.3): "Proposed · v1 of 1", "Approved by
 * sara · 30 Sep 2026, 18:20 UTC", "Overridden by sara", "Proposal rejected (the return
 * proceeds)", "Superseded by DEC-…". Neutral ink: a state, never a verdict.
 */
export function StateBadge({
  decision,
  versions,
}: {
  decision: Pick<DecisionView, 'state' | 'version' | 'approver' | 'approved_at' | 'superseded_by' | 'revision_trigger'>;
  /** How many versions the case has (card.revisions.length); left out when unknown. */
  versions?: number;
}) {
  const version = (
    <span data-version className="text-ink-3">
      {versions ? STATE_COPY.versionOf(decision.version, versions) : STATE_COPY.version(decision.version)}
    </span>
  );
  let body;
  if (decision.superseded_by) {
    body = (
      <>
        {STATE_COPY.supersededBy} <IdText id={decision.superseded_by} />
      </>
    );
  } else if (decision.state === 'proposed') {
    body = <>{STATE_COPY.proposed}</>;
  } else if (decision.state === 'rejected') {
    body = <>{STATE_COPY.rejected}</>;
  } else {
    const word =
      decision.state === 'approved'
        ? STATE_COPY.approvedBy
        : decision.revision_trigger === 'review'
          ? STATE_COPY.liftedBy
          : STATE_COPY.overriddenBy;
    body = (
      <>
        {word} <strong className="font-semibold">{decision.approver}</strong>
        {decision.approved_at && (
          <>
            {' · '}
            <DateText value={decision.approved_at} />
          </>
        )}
      </>
    );
  }
  return (
    <span
      data-state-badge={decision.superseded_by ? 'superseded' : decision.state}
      className={clsx(CHIP, 'border border-border text-ink-1')}
    >
      {body}
      <span aria-hidden="true">·</span>
      {version}
    </span>
  );
}

const RESTRICTION_STYLE: Record<RestrictionKind, string> = {
  needs_approval: 'hatch-attn border border-attn-border',
  lapsed: 'bg-attn-bg text-attn-fg border border-attn-border',
  in_force: 'border border-border-strong text-ink-1',
  superseded: 'border border-border text-ink-3',
  lifted: 'border border-border text-ink-1',
  applies_itself: 'border border-border text-ink-1',
  benefit_in_force: 'border border-border text-ink-1',
  not_in_force: 'border border-border text-ink-3',
  rejected: 'border border-border text-ink-2',
  no_change: 'border border-border text-ink-2',
};

/**
 * Where a restriction stands (docs/ui-design.md 5.3), from the API fields only (P9,
 * T-SERVER-STATE): the client clock never turns a restriction into a lapsed one.
 */
export function RestrictionBadge({ decision, canRenew = HAS_RENEW }: { decision: RestrictionFields; canRenew?: boolean }) {
  const status = restrictionStatus(decision);
  const review = status.reviewAt ? <DateText value={status.reviewAt} /> : null;
  let body;
  switch (status.kind) {
    case 'needs_approval':
      body = (
        <>
          <UserCheck aria-hidden="true" size={14} />
          {RESTRICTION_COPY.needsApproval} {review}
        </>
      );
      break;
    case 'in_force':
      body = (
        <>
          <CalendarClock aria-hidden="true" size={14} />
          {RESTRICTION_COPY.inForce} {review}
        </>
      );
      break;
    case 'lapsed':
      body = (
        <span>
          <CalendarClock aria-hidden="true" size={14} className="mr-1 inline align-[-2px]" />
          {RESTRICTION_COPY.lapsedOn} {review}
          {RESTRICTION_COPY.lapsedTail(canRenew)}
        </span>
      );
      break;
    case 'superseded':
      body = (
        <>
          <History aria-hidden="true" size={14} />
          {RESTRICTION_COPY.supersededBy} {status.supersededBy && <IdText id={status.supersededBy} />}
        </>
      );
      break;
    case 'lifted':
      body = (
        <>
          {RESTRICTION_COPY.liftedOn} {status.approvedAt && <DateText value={status.approvedAt} />} {RESTRICTION_COPY.by}{' '}
          <strong className="font-semibold">{status.approver}</strong>
        </>
      );
      break;
    case 'applies_itself':
      body = <>{RESTRICTION_COPY.appliesItself}</>;
      break;
    case 'benefit_in_force':
      body = <>{RESTRICTION_COPY.benefitInForce}</>;
      break;
    case 'not_in_force':
      body = <>{RESTRICTION_COPY.notInForce}</>;
      break;
    case 'rejected':
      body = <>{RESTRICTION_COPY.rejected}</>;
      break;
    default:
      body = <>{RESTRICTION_COPY.noChange}</>;
  }
  return (
    <span data-restriction={status.kind} className={clsx(CHIP, RESTRICTION_STYLE[status.kind])}>
      {body}
    </span>
  );
}

/** "Approved by sara (operator) · 30 Sep 2026, 18:20 UTC": a person, never the system (rule 5). */
export function ApproverLine({ decision }: { decision: Pick<DecisionView, 'state' | 'approver' | 'approved_at' | 'revision_trigger'> }) {
  if (!decision.approver || decision.state === 'proposed') return null;
  const word =
    decision.state === 'approved'
      ? STATE_COPY.approvedBy
      : decision.state === 'rejected'
        ? STATE_COPY.rejectedBy
        : decision.revision_trigger === 'review'
          ? STATE_COPY.liftedBy
          : STATE_COPY.overriddenBy;
  const role = roleOf(decision.approver);
  return (
    <p data-approver-line className="flex flex-wrap items-center gap-1 text-body text-ink-1">
      <UserCheck aria-hidden="true" size={16} className="text-ink-2" />
      {word} <strong className="font-semibold">{decision.approver}</strong>
      {role && <span className="text-ink-3">({role})</span>}
      {decision.approved_at && (
        <>
          <span aria-hidden="true">·</span>
          <DateText value={decision.approved_at} />
        </>
      )}
    </p>
  );
}

type StatementLabel = keyof typeof STATEMENT_COPY.label;

/** "Observed" solid · "Score contribution" outlined · "To verify" dashed (docs/ui-design.md 5.3). */
export function StatementLabelChip({ label }: { label: StatementLabel }) {
  const style =
    label === 'OBSERVED'
      ? 'bg-ink-2 text-surface border border-ink-2'
      : label === 'SCORE'
        ? 'border border-ink-2 text-ink-1'
        : 'border border-dashed border-ink-3 text-ink-2 italic';
  return (
    <Tooltip content={STATEMENT_COPY.tip[label]}>
      <button
        type="button"
        data-statement-label={label}
        className={clsx('inline-flex shrink-0 items-center rounded-chip px-1.5 text-micro uppercase', style)}
      >
        {STATEMENT_COPY.label[label]}
      </button>
    </Tooltip>
  );
}

/**
 * Models score → Rules decide → Legal guard checked → A person approves (docs/ui-design.md 5.3):
 * the decision belongs to the rules and to a person, never to the language model (rule 1).
 */
export function DecisionChain({
  decision,
}: {
  decision: Pick<
    DecisionView,
    'path' | 'evidence_quality' | 'state' | 'requires_approval' | 'approver' | 'rules_version' | 'model_versions'
  >;
}) {
  const models =
    decision.path === 'guarantee'
      ? CHAIN_COPY.notScoredGuarantee
      : decision.evidence_quality === 'insufficient'
        ? CHAIN_COPY.notScoredInsufficient
        : CHAIN_COPY.models;
  const person =
    decision.state === 'proposed'
      ? decision.requires_approval
        ? CHAIN_COPY.pending
        : CHAIN_COPY.notRequired
      : CHAIN_COPY.by(CHAIN_COPY.words[decision.state] ?? decision.state, decision.approver ?? '');
  const modelVersions = Object.entries(decision.model_versions)
    .map(([k, v]) => `${k} ${v ?? ''}`)
    .join(' · ');
  const steps: Array<{ key: string; text: string; tip: string | null; done: boolean }> = [
    { key: 'models', text: models, tip: modelVersions, done: true },
    { key: 'rules', text: CHAIN_COPY.rules, tip: `${CHAIN_COPY.rulesVersion} ${decision.rules_version}`, done: true },
    { key: 'guard', text: CHAIN_COPY.guard, tip: null, done: true },
    {
      key: 'person',
      text: `${CHAIN_COPY.person} (${person})`,
      tip: null,
      done: decision.state !== 'proposed' || !decision.requires_approval,
    },
  ];
  return (
    <ol aria-label={CHAIN_COPY.label} data-decision-chain className="flex flex-wrap items-center gap-1 text-caption text-ink-2">
      {steps.map((step, i) => (
        <li key={step.key} className="inline-flex items-center gap-1" data-chain-step={step.key} data-done={step.done}>
          {i > 0 && <ChevronRight aria-hidden="true" size={12} className="text-ink-3" />}
          {step.tip ? (
            <Tooltip content={<span data-id>{step.tip}</span>}>
              <button type="button" className={clsx('rounded-chip px-1', step.done ? 'text-ink-1' : 'font-medium text-attn-fg')}>
                {step.text}
              </button>
            </Tooltip>
          ) : (
            <span className={clsx('px-1', step.done ? 'text-ink-1' : 'font-medium text-attn-fg')}>{step.text}</span>
          )}
        </li>
      ))}
    </ol>
  );
}
