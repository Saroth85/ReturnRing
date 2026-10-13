// Fairness /fairness (US-605, docs/ui-design.md 8.4). Stub: the report and its gap threshold.
import { useFairness } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { Figure, IdText } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function FairnessPage() {
  const fairness = useFairness();
  const data = fairness.data;
  return (
    <ScreenPlaceholder title={MANAGER.titles.fairness} area="manager" stories={['US-605']} spec="docs/ui-design.md 8.4">
      <DataCheck label="GET /api/fairness" result={fairness} />
      {data && (
        <p className="flex flex-wrap items-center gap-2">
          <IdText id={data.report_id} />
          <span className="text-ink-3">{MANAGER.fairness.threshold}:</span>
          <Figure metric={data.max_gap_threshold} label={MANAGER.fairness.threshold} />
        </p>
      )}
    </ScreenPlaceholder>
  );
}
