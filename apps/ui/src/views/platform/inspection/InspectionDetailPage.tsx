// Inspect a return /inspection/:returnId (US-501..503, docs/ui-design.md 9.2). Stub: the return
// and the warehouse-only start control, disabled with the reason for other roles.
import { useParams } from 'react-router';
import { useCase } from '@/api/queries';
import { DataCheck, RoleGate, ScreenPlaceholder } from '@/components/feedback';
import { IdText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { PLATFORM } from '../copy';

export default function InspectionDetailPage() {
  const { returnId = '' } = useParams();
  const card = useCase(returnId);
  return (
    <ScreenPlaceholder title={PLATFORM.titles.inspectionDetail} area="platform" stories={['US-501', 'US-502', 'US-503']} spec="docs/ui-design.md 9.2">
      <p className="flex items-center gap-2">
        <IdText id={returnId} />
        {card.data && <span>{card.data.display_name}</span>}
      </p>
      <DataCheck label={`GET /api/returns/${returnId}`} result={card} />
      <RoleGate action="POST /api/inspections">
        {({ allowed }) => (
          <Button variant="primary" disabled={!allowed} className="min-h-12">
            {PLATFORM.inspection.start}
          </Button>
        )}
      </RoleGate>
    </ScreenPlaceholder>
  );
}
