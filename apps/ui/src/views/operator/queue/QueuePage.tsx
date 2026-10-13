// Returns queue /queue (US-301, docs/ui-design.md 6.1). Stub: a plain table over GET /api/returns.
import { Link } from 'react-router';
import { useQueue } from '@/api/queries';
import { ApprovalFlag, OutcomeBadge } from '@/components/decision';
import { DataCheck, EmptyState, ErrorState, LoadingBlock, ScreenPlaceholder } from '@/components/feedback';
import { DateText, Figure, IdText, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { OPERATOR } from '../copy';

export default function QueuePage() {
  const queue = useQueue();
  const c = OPERATOR.queue.columns;
  return (
    <ScreenPlaceholder title={OPERATOR.titles.queue} area="operator" stories={['US-301']} spec="docs/ui-design.md 6.1">
      <DataCheck label="GET /api/returns" result={queue} />
      {queue.isPending && <LoadingBlock rows={8} />}
      {queue.isError && <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />}
      {queue.data && queue.data.items.length === 0 && <EmptyState message={OPERATOR.queue.empty} />}
      {queue.data && queue.data.items.length > 0 && (
        <ProvenanceScope provenance={queue.data.provenance} covers="linked-account counts of the items">
          <div className="overflow-x-auto rounded-card border border-border bg-surface">
            <table className="w-full text-dense">
              <caption className="px-3 py-2 text-left text-caption text-ink-3">
                {OPERATOR.queue.caption} · <DateText value={queue.data.as_of} />
              </caption>
              <thead className="bg-surface-2 text-left text-caption text-ink-2">
                <tr>
                  <th className="px-3 py-2">{c.ret}</th>
                  <th className="px-3 py-2">{c.outcome}</th>
                  <th className="px-3 py-2">{c.status}</th>
                  <th className="px-3 py-2">{c.risk}</th>
                  <th className="px-3 py-2">{c.value}</th>
                  <th className="px-3 py-2">{c.netBenefit}</th>
                </tr>
              </thead>
              <tbody>
                {queue.data.items.map((item) => (
                  <tr key={item.return_id} className="border-t border-border align-top" data-return-row={item.return_id}>
                    <td className="px-3 py-2">
                      <Link to={`/returns/${item.return_id}?from=all`} className="font-mono text-accent underline-offset-2 hover:underline" data-id>
                        {item.return_id}
                      </Link>
                      <div className="text-ink-2">
                        {item.display_name} · <IdText id={item.customer_id} />
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <OutcomeBadge path={item.path} evidenceQuality={item.evidence_quality} quadrant={item.quadrant} />
                    </td>
                    <td className="px-3 py-2">
                      <ApprovalFlag requiresApproval={item.requires_approval} state={item.state} />
                      {!(item.requires_approval && item.state === 'proposed') && <span className="text-ink-2">{item.state}</span>}
                    </td>
                    <td className="px-3 py-2">
                      {item.risk ? <Figure metric={item.risk} label={c.risk} /> : <span className="text-ink-3">{OPERATOR.queue.notScored}</span>}
                    </td>
                    <td className="px-3 py-2">{item.value && <Figure metric={item.value} label={c.value} />}</td>
                    <td className="px-3 py-2">
                      <Figure metric={item.expected_net_benefit} label={c.netBenefit} signed />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ProvenanceFooter />
        </ProvenanceScope>
      )}
    </ScreenPlaceholder>
  );
}
