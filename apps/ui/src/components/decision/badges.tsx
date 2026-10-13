import clsx from 'clsx';
import { CalendarClock, CircleDashed, ShieldCheck, UserCheck } from 'lucide-react';
import type { DecisionState, EvidenceQualityId, PathId, QuadrantId } from '@/api/models';
import { DECISION } from '@/copy/common';
import { REVIEW_HORIZON_DAYS } from '@/copy/constants';
import { DateText } from '@/components/provenance/DateText';
import { Tooltip } from '@/components/ui/Tooltip';
import { QuadrantBadge } from './QuadrantBadge';
import type { QuadrantThresholds } from './quadrant';

const CHIP = 'inline-flex items-center gap-1 rounded-chip px-1.5 py-0.5 text-dense';

/** "Withdrawal" or "Guarantee" (docs/ui-design.md 5.3). */
export function PathBadge({ path }: { path: PathId }) {
  return (
    <span data-path-badge={path} className={clsx(CHIP, 'border border-border-strong text-ink-1')}>
      {path === 'guarantee' && <ShieldCheck aria-hidden="true" size={14} />}
      {DECISION.path[path]}
    </span>
  );
}

/** "Evidence sufficient" or "Not enough evidence" (dashed). */
export function EvidenceBadge({ quality }: { quality: EvidenceQualityId }) {
  return (
    <span
      data-evidence-badge={quality}
      className={clsx(CHIP, 'border text-ink-2', quality === 'insufficient' ? 'border-dashed border-ink-3' : 'border-border')}
    >
      {quality === 'insufficient' && <CircleDashed aria-hidden="true" size={14} />}
      {DECISION.evidence[quality]}
    </span>
  );
}

/**
 * Exactly three families (docs/ui-design.md 5.3): the guarantee path (never scored), not enough
 * evidence (standard terms, no quadrant), or the quadrant position.
 */
export function OutcomeBadge({
  path,
  evidenceQuality,
  quadrant,
  thresholds,
}: {
  path: PathId;
  evidenceQuality: EvidenceQualityId;
  quadrant: QuadrantId | null;
  thresholds?: QuadrantThresholds;
}) {
  if (path === 'guarantee') {
    return (
      <span data-outcome="guarantee" className={clsx(CHIP, 'border border-border-strong text-ink-1')}>
        <ShieldCheck aria-hidden="true" size={14} />
        {DECISION.guaranteeOutcome}
      </span>
    );
  }
  if (evidenceQuality === 'insufficient' || quadrant === null) {
    return (
      <span data-outcome="insufficient" className={clsx(CHIP, 'border border-dashed border-ink-3 text-ink-2')}>
        <CircleDashed aria-hidden="true" size={14} />
        {DECISION.insufficientOutcome}
      </span>
    );
  }
  return <QuadrantBadge quadrant={quadrant} thresholds={thresholds} />;
}

/** "Needs approval" on the attention hatch, while a restriction waits for a person (rule 5). */
export function ApprovalFlag({ requiresApproval, state }: { requiresApproval: boolean; state: DecisionState }) {
  if (!requiresApproval || state !== 'proposed') return null;
  return (
    <span data-approval-flag className={clsx(CHIP, 'hatch-attn border border-attn-border font-medium whitespace-nowrap')}>
      <UserCheck aria-hidden="true" size={14} />
      {DECISION.needsApproval}
    </span>
  );
}

/** The review date of a restriction; "—" for anything else (docs/ui-design.md 5.3). */
export function ReviewDateChip({ reviewAt }: { reviewAt: string | null }) {
  if (reviewAt === null) {
    return (
      <span className="text-ink-3">
        <span aria-hidden="true">—</span>
        <span className="sr-only">{DECISION.noReview}</span>
      </span>
    );
  }
  return (
    <Tooltip content={DECISION.reviewTip(REVIEW_HORIZON_DAYS)}>
      <button type="button" data-review-date className={clsx(CHIP, 'border border-border text-ink-1')}>
        <CalendarClock aria-hidden="true" size={14} />
        <span className="sr-only">{DECISION.reviewWord} </span>
        <DateText value={reviewAt} />
      </button>
    </Tooltip>
  );
}

/** The customer's floor, first in every decision (rules 3 and 4, docs/ui-design.md 5.3). */
export function RefundFloorNotice({ refundDueBy, path }: { refundDueBy: string; path: PathId }) {
  if (path === 'guarantee') {
    return (
      <p data-refund-floor="guarantee" className="flex items-center gap-2 text-body text-ink-1">
        <ShieldCheck aria-hidden="true" size={16} />
        <span>
          {DECISION.guaranteeFloor} <span data-legal-ref>{DECISION.guaranteeLaw}</span>
        </span>
      </p>
    );
  }
  return (
    <p data-refund-floor="withdrawal" className="text-body text-ink-1">
      {DECISION.refundDueBy} <DateText value={refundDueBy} className="font-medium" /> · {DECISION.withdrawalIntact}
    </p>
  );
}
