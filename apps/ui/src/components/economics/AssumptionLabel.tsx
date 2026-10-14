import clsx from 'clsx';
import { Link, useInRouterContext } from 'react-router';
import { DateText } from '@/components/provenance';
import { parseAssumptionLabel } from './assumptionLabel';
import { ECONOMICS } from './copy';

interface AssumptionLabelProps {
  /** `version` of the assumption set. */
  label: string;
  /** `based_on`: the stored version whose unit costs the set uses (null for a stored version). */
  basedOn: string | null;
  className?: string;
}

const L = ECONOMICS.label;

function Id({ id }: { id: string }) {
  return (
    <span className="font-mono text-dense" data-id>
      {id}
    </span>
  );
}

function DecisionLink({ decisionId, returnId }: { decisionId: string; returnId: string | null }) {
  const inRouter = useInRouterContext();
  if (!returnId || !inRouter) return <Id id={decisionId} />;
  return (
    <Link
      to={`/returns/${returnId}/versions/${decisionId}`}
      className="font-mono text-dense text-accent underline underline-offset-2 hover:text-accent-hover"
      data-id
    >
      {decisionId}
    </Link>
  );
}

/**
 * Which assumption set a net benefit uses, in words (docs/ui-design.md 5.6): "Scenario: logistics
 * halved, from assumptions-v1", "Decision DEC-000102-1 · alternative: proposal", "KPI week of
 * 21 Sep 2026". The raw label is kept for screen readers and copy.
 */
export function AssumptionLabel({ label, basedOn, className }: AssumptionLabelProps) {
  const parsed = parseAssumptionLabel(label);
  const based =
    basedOn && !(parsed.kind === 'scenario' && parsed.version === basedOn) ? (
      <>
        {' '}
        · {L.basedOn} <Id id={basedOn} />
      </>
    ) : null;
  return (
    <span className={clsx('inline-flex flex-wrap items-baseline gap-x-1 text-caption text-ink-2', className)} data-assumption-label={label}>
      {parsed.kind === 'scenario' && (
        <>
          {L.scenario} {ECONOMICS.scenarioLong[parsed.scenario]}, {L.from} <Id id={parsed.version} />
        </>
      )}
      {parsed.kind === 'decision' && (
        <>
          {L.decision} <DecisionLink decisionId={parsed.decisionId} returnId={parsed.returnId} /> · {L.alternative}{' '}
          {ECONOMICS.alternative[parsed.alternative] ?? parsed.alternative}
          {based}
        </>
      )}
      {parsed.kind === 'kpi' && (
        <>
          {L.kpiWeek} <DateText value={parsed.week} />
          {based}
        </>
      )}
      {parsed.kind === 'stored' && (
        <>
          {L.stored} <Id id={parsed.version} />
          {based}
        </>
      )}
      {parsed.kind === 'raw' && (
        <>
          <span data-verbatim>{parsed.label}</span>
          {based}
        </>
      )}
    </span>
  );
}
