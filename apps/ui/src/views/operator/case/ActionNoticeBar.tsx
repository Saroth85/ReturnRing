// The confirmation of the last action (docs/ui-design.md 6.3.3): who did what, the message
// queued (simulated) or why none was, the version a review superseded, the coupon pause the act
// confirmed or reversed, and the audit entries, with links. It survives the auto-advance to the next
// case, and it is a polite status, never an error (errors that need a decision stay inline).
import { CircleCheck, X } from 'lucide-react';
import { Link } from 'react-router';
import { DateText, IdText } from '@/components/provenance';
import { OPERATOR } from '../copy';
import { noticeStore, useActionNotice } from '../state/prefs';

export function ActionNoticeBar() {
  const notice = useActionNotice();
  if (!notice) return null;
  const t = OPERATOR.result;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-2 rounded-control border border-ok-fg bg-ok-bg px-3 py-2 text-body text-ink-1"
      data-action-notice
    >
      <CircleCheck aria-hidden="true" size={16} className="text-ok-fg" />
      <span>{notice.text}</span>
      <Link to={`/returns/${notice.returnId}`} className="font-mono text-dense text-accent underline underline-offset-2" data-id>
        {notice.returnId}
      </Link>
      {notice.messageId && (
        <span className="inline-flex items-center gap-1">
          ·{' '}
          <Link
            to={`/outbox?return_id=${notice.returnId}`}
            className="font-mono text-dense text-accent underline underline-offset-2"
            data-id
          >
            {notice.messageId}
          </Link>{' '}
          {t.messageQueued}
        </span>
      )}
      {notice.noMessage && (
        <span className="text-ink-2" data-notice-no-message>
          · {notice.noMessage}
        </span>
      )}
      {notice.supersededId && (
        <span className="inline-flex items-center gap-1" data-notice-superseded>
          · {t.supersedes} <IdText id={notice.supersededId} />
        </span>
      )}
      {notice.coupon && (
        <span className="inline-flex flex-wrap items-center gap-1" data-notice-coupon={notice.coupon.state}>
          · {t.couponPause} <IdText id={notice.coupon.applicationId} /> {t.couponStates[notice.coupon.state] ?? notice.coupon.state}{' '}
          <span data-constant>{t.couponFor(notice.coupon.accounts)}</span>
          {notice.coupon.state !== 'reversed' && (
            <>
              · {OPERATOR.queue.review} <DateText value={notice.coupon.reviewAt} />
            </>
          )}
        </span>
      )}
      {notice.auditEntryIds.length > 0 && (
        <span className="inline-flex flex-wrap items-center gap-1">
          · {t.auditEntry}
          {notice.auditEntryIds.map((id) => (
            <IdText key={id} id={`#${id}`} />
          ))}
        </span>
      )}
      <button
        type="button"
        onClick={() => noticeStore.set(null)}
        aria-label={t.dismiss}
        className="ml-auto rounded-control p-1 hover:bg-surface-2"
      >
        <X aria-hidden="true" size={14} />
      </button>
    </div>
  );
}
