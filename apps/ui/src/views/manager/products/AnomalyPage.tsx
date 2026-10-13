// Product issue /products/:anomalyId (US-208, US-606; docs/ui-design.md 8.5). Stub: observed vs
// expected for one anomaly.
import { useParams } from 'react-router';
import { useAnomalies } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { Figure, IdText } from '@/components/provenance';
import { MANAGER } from '../copy';

export default function AnomalyPage() {
  const { anomalyId = '' } = useParams();
  const anomalies = useAnomalies();
  const anomaly = anomalies.data?.items.find((a) => a.anomaly_id === anomalyId);
  return (
    <ScreenPlaceholder title={MANAGER.titles.anomaly} area="manager" stories={['US-208', 'US-606']} spec="docs/ui-design.md 8.5">
      <IdText id={anomalyId} />
      <DataCheck label="GET /api/products/anomalies" result={anomalies} />
      {anomaly && (
        <p className="flex flex-wrap items-center gap-3">
          <Figure metric={anomaly.observed} label="observed" />
          <Figure metric={anomaly.expected} label="expected" />
          <Figure metric={anomaly.z} label="z" />
          <Figure metric={anomaly.sales} label="sales" />
        </p>
      )}
    </ScreenPlaceholder>
  );
}
