import type { ReactNode } from 'react';
import type { Provenance } from '@/api/models';
import { PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';

/**
 * How many linked accounts a cluster coupon pause covers (affected_customer_ids.length): a count
 * of the synthetic account graph, never a named constant, so it carries the provenance of the
 * decision's inputs (decision.inputs.provenance: the customer snapshot that holds the cluster) with
 * a compact footer (rule 11). Inline, inside running text.
 */
export function LinkedAccountsCount({
  count,
  provenance,
  before,
  after,
}: {
  count: number;
  provenance: Provenance;
  before?: ReactNode;
  after?: ReactNode;
}) {
  return (
    <ProvenanceScope
      as="span"
      provenance={provenance}
      covers="the linked accounts of the cluster"
      className="inline-flex flex-wrap items-baseline gap-x-1"
    >
      {before}
      <span data-linked-accounts-count={count}>
        <PlainNumber value={count} unit="count" />
      </span>
      {after}
      <ProvenanceFooter compact />
    </ProvenanceScope>
  );
}
