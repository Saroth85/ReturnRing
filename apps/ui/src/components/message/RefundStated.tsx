import type { Metric } from '@/api/models';
import { Figure } from '@/components/provenance';
import { MESSAGE } from '@/copy/common';

/**
 * The refund a sent message states (OutboxMessage.refund, contract 1.7.0, ADR-15 section 19) as a
 * Figure, with its origin, period and nature (rule 11). The {amount} chip of the message is the
 * same value as text; this line is where its provenance is read. Nothing when the message states
 * no refund.
 */
export function RefundStated({ refund }: { refund: Metric | null | undefined }) {
  if (!refund) return null;
  return (
    <p className="flex flex-wrap items-baseline gap-1 text-body" data-refund-stated>
      <span>{MESSAGE.refundStated}</span>
      <Figure metric={refund} label={MESSAGE.refundStated} />
    </p>
  );
}
