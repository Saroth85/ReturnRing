// Case card /returns/:returnId (US-302..310, US-607; docs/ui-design.md 6.2). Stub: the layout
// (sticky decision rail on the right, evidence column with the hero on the left), the header
// badges, the message layers and the case assistant drawer. Deep-link parameters (?action=,
// ?link=, ?focus=, ?from=) are read here once the sheets exist.
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { isApiError } from '@/api/errors';
import { useCase } from '@/api/queries';
import { ErrorState, LoadingBlock, ScreenPlaceholder } from '@/components/feedback';
import { CaseAssistant } from '@/views/agent';
import { OPERATOR } from '../copy';
import { CaseHeader } from './header/CaseHeader';
import { CaseHero } from './hero/CaseHero';
import { DecisionRail } from './rail/DecisionRail';
import { EvidenceColumn } from './sections/EvidenceColumn';

export default function CasePage() {
  const { returnId = '' } = useParams();
  const card = useCase(returnId);
  const [assistantOpen, setAssistantOpen] = useState(false);

  if (card.isPending) return <LoadingBlock rows={10} />;
  if (card.isError) {
    const notFound = isApiError(card.error) && card.error.code === 'not_found';
    return (
      <div className="mx-auto max-w-xl py-8">
        <h1 className="mb-4 text-page">{OPERATOR.titles.case}</h1>
        <ErrorState
          error={card.error}
          title={notFound ? OPERATOR.case.notFound(returnId) : undefined}
          onRetry={notFound ? undefined : () => void card.refetch()}
        />
        <Link to="/queue" className="mt-4 inline-block text-accent underline underline-offset-2">
          {OPERATOR.case.backToQueue}
        </Link>
      </div>
    );
  }

  const data = card.data;
  return (
    <ScreenPlaceholder title={OPERATOR.titles.case} area="operator" stories={['US-302', 'US-303', 'US-306', 'US-310', 'US-607']} spec="docs/ui-design.md 6.2">
      <CaseHeader card={data} onAsk={() => setAssistantOpen(true)} />
      <div className="grid gap-6 xl:grid-cols-12">
        <div className="flex min-w-0 flex-col gap-6 xl:col-span-7">
          <CaseHero card={data} />
          <EvidenceColumn card={data} />
        </div>
        <div className="xl:col-span-5">
          <DecisionRail card={data} />
        </div>
      </div>
      <CaseAssistant returnId={data.return_id} open={assistantOpen} onOpenChange={setAssistantOpen} />
    </ScreenPlaceholder>
  );
}
