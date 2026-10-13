import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { useId, useState } from 'react';
import { isApiError } from '@/api/errors';
import type { CouponRuleApplication } from '@/api/models';
import { useReverseCouponApplication } from '@/api/mutations';
import { usePersona } from '@/app/persona';
import { ErrorState, ForbiddenRoleNotice, InlineNotice } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { RECORDS } from './copy';

/** A reason has at least this many characters once trimmed (ADR-15 section 2, "Reasons"). */
export const MIN_REASON_CHARS = 3;

const R = RECORDS.reverse;

interface ReverseCouponDialogProps {
  application: CouponRuleApplication;
  open: boolean;
  onOpenChange(open: boolean): void;
  onDone?(result: CouponRuleApplication): void;
}

/**
 * Reverse a coupon pause on a cluster (US-402, US-607, docs/ui-design.md 5.7): a reason is required,
 * the acting person is the author, and nothing changes until the server answers.
 */
export function ReverseCouponDialog({ application, open, onOpenChange, onDone }: ReverseCouponDialogProps) {
  const persona = usePersona();
  const reverse = useReverseCouponApplication(application.application_id);
  const [reason, setReason] = useState('');
  const reasonId = useId();
  const hintId = useId();
  const tooShort = reason.trim().length < MIN_REASON_CHARS;
  const error = reverse.error;

  const submit = () => {
    if (tooShort || reverse.isPending) return;
    reverse.mutate(
      { author: persona.id, reason: reason.trim() },
      {
        onSuccess: (result) => {
          onDone?.(result);
          setReason('');
          onOpenChange(false);
        },
      },
    );
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) reverse.reset();
        onOpenChange(next);
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-[rgb(20_26_38/0.32)]" />
        <Dialog.Content
          className="fixed top-1/2 left-1/2 z-50 flex max-h-[calc(100vh-2rem)] w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 overflow-y-auto rounded-card border border-border bg-surface p-4 text-ink-1 shadow-overlay"
          data-reverse-dialog
        >
          <div className="flex items-start justify-between gap-3">
            <Dialog.Title className="text-h3">{R.title}</Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" aria-label={R.cancel} className="rounded-control p-1 text-ink-2 hover:bg-surface-2">
                <X aria-hidden="true" size={16} />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Description className="text-body text-ink-2">{R.intro}</Dialog.Description>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-dense">
            <dt className="text-ink-3">{RECORDS.coupons.cluster}</dt>
            <dd>
              <IdText id={application.cluster_id} />
            </dd>
            <dt className="text-ink-3">{RECORDS.coupons.decision}</dt>
            <dd>
              <IdText id={application.decision_id} />
            </dd>
            <dt className="text-ink-3">{RECORDS.coupons.reviewAt}</dt>
            <dd>
              <DateText value={application.review_at} />
            </dd>
          </dl>
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <label htmlFor={reasonId} className="text-body font-medium">
              {R.reason}
            </label>
            <textarea
              id={reasonId}
              aria-describedby={hintId}
              required
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className="rounded-control border border-border-strong bg-surface px-2 py-1 text-body text-ink-1"
            />
            <p id={hintId} className="text-caption text-ink-3">
              {tooShort && reason.length > 0 ? R.tooShort(MIN_REASON_CHARS) : R.reasonHint}
            </p>
            {error &&
              (isApiError(error) && error.code === 'forbidden_role' ? (
                <ForbiddenRoleNotice role={persona.role} action={R.confirm.toLowerCase()} />
              ) : (
                <div className="flex flex-col gap-1">
                  <ErrorState error={error} />
                  <InlineNotice tone="info">{R.nothingSaved}</InlineNotice>
                </div>
              ))}
            <div className="flex flex-wrap justify-end gap-2">
              <Dialog.Close asChild>
                <Button>{R.cancel}</Button>
              </Dialog.Close>
              <Button type="submit" variant="primary" disabled={tooShort || reverse.isPending}>
                {R.confirm}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
