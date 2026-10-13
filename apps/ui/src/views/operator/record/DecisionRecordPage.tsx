// Decision record /returns/:returnId/versions/:decisionId (US-604, docs/ui-design.md 6.6).
// Stub: the version, its state and whether a later version supersedes it.
import { Link, useParams } from 'react-router';
import { useDecisionRecord } from '@/api/queries';
import { DataCheck, InlineNotice, ScreenPlaceholder } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { OPERATOR } from '../copy';

export default function DecisionRecordPage() {
  const { returnId = '', decisionId = '' } = useParams();
  const record = useDecisionRecord(decisionId);
  const decision = record.data?.decision;
  const t = OPERATOR.record;
  return (
    <ScreenPlaceholder title={OPERATOR.titles.record} area="operator" stories={['US-604']} spec="docs/ui-design.md 6.6">
      <p className="flex flex-wrap items-center gap-2">
        <Link to={`/returns/${returnId}`} className="text-accent underline underline-offset-2">
          <span data-id>{returnId}</span>
        </Link>
        <IdText id={decisionId} />
      </p>
      <DataCheck label={`GET /api/decisions/${decisionId}`} result={record} />
      {decision && (
        <>
          {decision.superseded_by ? (
            <InlineNotice tone="info">{t.superseded(decision.superseded_by)}</InlineNotice>
          ) : (
            <InlineNotice tone="info">{t.latest}</InlineNotice>
          )}
          <dl className="grid max-w-md grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-body">
            <dt className="text-ink-3">{t.state}</dt>
            <dd>{decision.state}</dd>
            <dt className="text-ink-3">{t.version}</dt>
            <dd data-id>v{decision.version}</dd>
            <dt className="text-ink-3">{OPERATOR.case.rules}</dt>
            <dd data-id>{decision.rules_version}</dd>
            <dt className="text-ink-3">{t.created}</dt>
            <dd>
              <DateText value={decision.created_at} />
            </dd>
          </dl>
        </>
      )}
    </ScreenPlaceholder>
  );
}
