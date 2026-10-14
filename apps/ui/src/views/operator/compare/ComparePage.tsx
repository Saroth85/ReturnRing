// Compare /compare?a=RET-000101&b=RET-000102 (pitch beat 1, US-302, US-906; docs/ui-design.md
// 6.5): two customers with the same 12-month return rate, then the rows where they differ: the top
// contribution to the score, the linked accounts, risk, value, position and the engine's terms.
// In presenter mode the rows below the band appear on → (step 2).
import { ArrowRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { CaseCard } from '@/api/models';
import { useCase } from '@/api/queries';
import { usePresent } from '@/app/display';
import { useHotkeys } from '@/app/hotkeys';
import { LeverList, OutcomeBadge } from '@/components/decision';
import { ErrorState, LoadingBlock } from '@/components/feedback';
import { DateText, Figure, IdText, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { OPERATOR } from '../copy';
import { reasonLabel } from '../format';
import { ScoreTrack } from '../case/sections/RiskSection';

type Side = CaseCard;

interface PairProps {
  label: string;
  left: ReactNode;
  right: ReactNode;
  /** The two customers' names: on one column the rows stack, so each cell says whose it is. */
  names: [string, string];
  wide?: boolean;
}

function Pair({ label, left, right, names, wide = false }: PairProps) {
  return (
    <div className="contents" data-compare-row={label}>
      {[left, right].map((cell, i) => (
        <div key={names[i]} className="flex min-w-0 flex-col gap-1 rounded-control border border-border bg-surface p-3">
          <p className="text-caption text-ink-3">
            {label}
            <span className="md:hidden"> · {names[i]}</span>
          </p>
          <div className={wide ? 'text-body' : 'text-title'}>{cell}</div>
        </div>
      ))}
    </div>
  );
}

function Header({ card }: { card: Side }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-card border border-border bg-surface p-4" data-compare-column={card.return_id}>
      <h2 className="flex flex-wrap items-baseline gap-2 text-h2">
        {card.display_name} <IdText id={card.customer_id} />
      </h2>
      <p className="flex flex-wrap items-center gap-2 text-body text-ink-2">
        <IdText id={card.return_id} /> · {card.item.category} · {reasonLabel(card.request.reason)}
      </p>
    </div>
  );
}

function RateBand({ card }: { card: Side }) {
  const t = OPERATOR.compare;
  const { lines, returned_lines: returned, return_rate: rate } = card.orders;
  // The rate next to "6 of 11 lines" is that exact ratio (UI32): a rate rounded upstream to four
  // decimals (0.5455) would print 54.6% beside counts that make 54.5%.
  const shown = lines.value > 0 ? { ...rate, value: returned.value / lines.value } : rate;
  return (
    <ProvenanceScope
      provenance={card.orders.provenance}
      covers="lines and returned lines of the 12 months"
      className="flex flex-col items-center gap-1"
    >
      <span className="text-hero" data-compare-rate>
        <Figure metric={shown} label={t.returnRate} size="hero" />
      </span>
      <span className="inline-flex flex-wrap items-center gap-1 text-caption text-ink-2">
        ({t.lines(String(returned.value), String(lines.value))}) <ProvenanceFooter compact />
      </span>
    </ProvenanceScope>
  );
}

function Linked({ card }: { card: Side }) {
  const t = OPERATOR.compare;
  const la = card.linked_accounts;
  if (!la) return <span className="text-ink-2">{t.noLinked}</span>;
  return (
    <ProvenanceScope provenance={la.provenance} covers="cluster size">
      <span className="flex flex-wrap items-baseline gap-1">
        {t.clusterOf(String(la.cluster_size))}
        <Figure metric={la.features.creation_spread_days} label={OPERATOR.linked.factLabels.creation_spread_days} />
        <IdText id={la.cluster_id} />
        <ProvenanceFooter compact />
      </span>
    </ProvenanceScope>
  );
}

function Engine({ card }: { card: Side }) {
  const t = OPERATOR.compare;
  const d = card.decision;
  return (
    <div className="flex flex-col gap-2">
      <LeverList levers={d.proposed_levers} base={card.order_terms.base_terms} />
      <p className="text-body text-ink-2">
        {d.requires_approval ? t.needsPerson : d.lever_ids.length > 0 ? t.appliesItself : OPERATOR.queue.noChange}
      </p>
    </div>
  );
}

function Columns({ a, b }: { a: Side; b: Side }) {
  const t = OPERATOR.compare;
  const present = usePresent();
  const [revealed, setRevealed] = useState(!present);
  useHotkeys({ ArrowRight: () => setRevealed(true) });
  const names: [string, string] = [a.display_name ?? a.customer_id, b.display_name ?? b.customer_id];
  const factor = (card: Side) =>
    card.risk ? (
      <span data-verbatim>{card.risk.top_factors[0].phrase}</span>
    ) : (
      <span className="text-ink-2">{OPERATOR.queue.notScored}</span>
    );
  const risk = (card: Side) =>
    card.risk ? (
      <div className="flex flex-col gap-1">
        <Figure metric={card.risk.score} label={t.risk} compareTo={card.risk.threshold} />
        <ScoreTrack risk={card.risk} compact />
      </div>
    ) : (
      <span className="text-ink-2">{OPERATOR.queue.notScored}</span>
    );
  const value = (card: Side) =>
    card.value ? <Figure metric={card.value.value_eur} label={t.value} compareTo={card.value.threshold} /> : null;
  const position = (card: Side) => (
    <OutcomeBadge
      path={card.decision.path}
      evidenceQuality={card.decision.evidence_quality}
      quadrant={card.decision.quadrant}
      thresholds={card.risk && card.value ? { risk: card.risk.threshold, value: card.value.threshold } : undefined}
    />
  );
  const floor = (card: Side) => (
    <span className="flex flex-wrap items-center gap-2">
      <span>
        {t.thisReturn} <DateText value={card.decision.refund_due_by} />, {t.neverRefused}
      </span>
      <Link to={`/returns/${card.return_id}`} className="inline-flex items-center gap-1 text-accent underline underline-offset-2">
        {t.openCase} <ArrowRight aria-hidden="true" size={14} />
      </Link>
    </span>
  );
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2" data-compare>
      <Header card={a} />
      <Header card={b} />
      <div className="flex flex-col items-center gap-2 rounded-card border-2 border-ink-2 bg-surface p-4 md:col-span-2" data-compare-band>
        <p className="text-caption text-ink-3" data-constant>
          {t.returnRate}
        </p>
        <div className="grid w-full grid-cols-1 items-center gap-3 sm:grid-cols-[1fr_auto_1fr]">
          <RateBand card={a} />
          <span className="text-center text-micro uppercase tracking-widest text-ink-2" data-same-indicator>
            {t.sameIndicator}
          </span>
          <RateBand card={b} />
        </div>
      </div>
      {revealed ? (
        <>
          <Pair names={names} label={t.topFactor} left={factor(a)} right={factor(b)} wide />
          <Pair names={names} label={t.linked} left={<Linked card={a} />} right={<Linked card={b} />} wide />
          <Pair names={names} label={t.risk} left={risk(a)} right={risk(b)} />
          <Pair names={names} label={t.value} left={value(a)} right={value(b)} />
          <Pair names={names} label={t.position} left={position(a)} right={position(b)} />
          <Pair names={names} label={t.engine} left={<Engine card={a} />} right={<Engine card={b} />} wide />
          <Pair names={names} label={OPERATOR.railCopy.title} left={floor(a)} right={floor(b)} wide />
          <p className="text-h3 md:col-span-2" data-compare-closing>
            {t.closing}
          </p>
        </>
      ) : (
        <Button variant="primary" className="justify-self-center md:col-span-2" onClick={() => setRevealed(true)}>
          {t.reveal} →
        </Button>
      )}
    </div>
  );
}

export default function ComparePage() {
  const [params] = useSearchParams();
  const a = params.get('a') ?? undefined;
  const b = params.get('b') ?? undefined;
  const left = useCase(a);
  const right = useCase(b);
  const t = OPERATOR.compare;
  return (
    <div className="mx-auto flex max-w-content flex-col gap-4" data-page="compare">
      <h1 className="text-page">{OPERATOR.titles.compare}</h1>
      {!a || !b ? (
        <p className="text-body">
          {t.pickTwo}{' '}
          <Link to="/compare?a=RET-000101&b=RET-000102" className="text-accent underline underline-offset-2">
            {t.pitchPair}
          </Link>
        </p>
      ) : (
        <>
          {left.isError && <ErrorState error={left.error} title={OPERATOR.case.notFound(a)} onRetry={() => void left.refetch()} />}
          {right.isError && <ErrorState error={right.error} title={OPERATOR.case.notFound(b)} onRetry={() => void right.refetch()} />}
          {(left.isPending || right.isPending) && <LoadingBlock rows={8} />}
          {left.data && right.data && <Columns a={left.data} b={right.data} />}
        </>
      )}
    </div>
  );
}
