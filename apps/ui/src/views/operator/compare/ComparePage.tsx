// Compare /compare?a=RET-000101&b=RET-000102 (pitch beat 1, US-302, US-906; docs/ui-design.md
// 6.5): the band shows the signal the two returns share in the pitch, how soon after delivery each
// was notified (spec 16), and says "same signal" only when both are within
// SOON_AFTER_DELIVERY_DAYS; then the rows where they differ: the 12-month return rate, the top
// contribution to the score, the linked accounts, risk, value, position and the engine's terms.
// In presenter mode the rows below the band appear on → (step 2). Without ?a and ?b the pair is
// the demo's Giulia and Marco, by their live ids (GET /api/demo/cases, ADR-15 section 15).
import { ArrowRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router';
import type { CaseCard } from '@/api/models';
import { isApiError } from '@/api/errors';
import { useCase, useDemoCases } from '@/api/queries';
import { usePresent } from '@/app/display';
import { useHotkeys } from '@/app/hotkeys';
import { LeverList, OutcomeBadge } from '@/components/decision';
import { ErrorState, LoadingBlock } from '@/components/feedback';
import { DateText, Figure, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { DECISION } from '@/copy/common';
import { caseOf } from '@/domain/demoCases';
import { OPERATOR } from '../copy';
import { reasonLabel } from '../format';
import { ScoreTrack } from '../case/sections/RiskSection';
import { noticeSignal } from './signal';

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
            {/* Copy: its only numbers are named constants (the 12 months of the return rate). */}
            <span data-constant>{label}</span>
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

/** The 12-month return rate with its lines: one of the rows that differ, never the band's signal. */
function Rate({ card }: { card: Side }) {
  const t = OPERATOR.compare;
  const { lines, returned_lines: returned, return_rate: rate } = card.orders;
  // The rate next to "6 of 11 lines" is that exact ratio (UI32): a rate rounded upstream to four
  // decimals (0.5455) would print 54.6% beside counts that make 54.5%.
  const shown = lines.value > 0 ? { ...rate, value: returned.value / lines.value } : rate;
  return (
    <ProvenanceScope provenance={card.orders.provenance} covers="lines and returned lines of the 12 months">
      <span className="flex flex-wrap items-baseline gap-1" data-compare-rate>
        <Figure metric={shown} label={t.returnRate} />
        <span className="inline-flex flex-wrap items-center gap-1 text-caption text-ink-2">
          ({t.lines(String(returned.value), String(lines.value))}) <ProvenanceFooter compact />
        </span>
      </span>
    </ProvenanceScope>
  );
}

/** How soon after delivery the return was notified: the band's signal, with the card's provenance. */
function NoticeBand({ card }: { card: Side }) {
  return (
    <ProvenanceScope
      provenance={card.provenance}
      covers="days since delivery"
      className="flex flex-col items-center gap-1"
    >
      <span className="text-hero" data-compare-days={card.request.days_since_delivery}>
        <PlainNumber value={card.request.days_since_delivery} unit="days" />
      </span>
      <span className="inline-flex flex-wrap items-center gap-1 text-caption text-ink-2">
        <DateText value={card.request.delivered_on} /> → <DateText value={card.request.notice_on} /> <ProvenanceFooter compact />
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
  const signal = noticeSignal(a, b);
  const factor = (card: Side) => {
    if (!card.risk) return <span className="text-ink-2">{OPERATOR.queue.notScored}</span>;
    const [top] = card.risk.top_factors;
    return top ? <span data-verbatim>{top.phrase}</span> : <span className="text-ink-2">{t.noFactor}</span>;
  };
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
        {/* No refund deadline on the guarantee path (rule 4): refund_due_by is null there. */}
        {card.decision.refund_due_by ? (
          <>
            {t.thisReturn} <DateText value={card.decision.refund_due_by} />, {t.neverRefused}
          </>
        ) : (
          <>
            {DECISION.guaranteeFloor} <span data-legal-ref>{DECISION.guaranteeLaw}</span>
          </>
        )}
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
        <p className="text-caption text-ink-3">{t.noticeDays}</p>
        <div className="grid w-full grid-cols-1 items-center gap-3 sm:grid-cols-[1fr_auto_1fr]">
          <NoticeBand card={a} />
          <span
            className="text-center text-micro uppercase tracking-widest text-ink-2"
            data-same-signal={signal.same ? 'same' : 'different'}
            data-constant
          >
            {signal.same ? t.sameSignal : t.differentSignal}
          </span>
          <NoticeBand card={b} />
        </div>
      </div>
      {revealed ? (
        <>
          <Pair names={names} label={t.returnRate} left={<Rate card={a} />} right={<Rate card={b} />} wide />
          <Pair names={names} label={t.topFactor} left={factor(a)} right={factor(b)} wide />
          <Pair names={names} label={t.linked} left={<Linked card={a} />} right={<Linked card={b} />} wide />
          <Pair names={names} label={t.risk} left={risk(a)} right={risk(b)} />
          <Pair names={names} label={t.value} left={value(a)} right={value(b)} />
          <Pair names={names} label={t.position} left={position(a)} right={position(b)} />
          <Pair names={names} label={t.engine} left={<Engine card={a} />} right={<Engine card={b} />} wide />
          <Pair names={names} label={OPERATOR.railCopy.title} left={floor(a)} right={floor(b)} wide />
          <p className="text-h3 md:col-span-2" data-compare-closing={signal.same ? 'same' : 'different'}>
            {signal.same ? t.closing : t.closingDifferent}
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

/** The pitch pair of the demo cases, Giulia and Marco, or null when the store does not hold both. */
function usePitchPair(): { a: string; b: string } | null {
  const cases = useDemoCases();
  const a = caseOf(cases.data, 'giulia')?.return_id;
  const b = caseOf(cases.data, 'marco')?.return_id;
  return a && b ? { a, b } : null;
}

export default function ComparePage() {
  const [params] = useSearchParams();
  const cases = useDemoCases();
  const pitch = usePitchPair();
  const asked = { a: params.get('a'), b: params.get('b') };
  const a = asked.a ?? (asked.b ? undefined : pitch?.a);
  const b = asked.b ?? (asked.a ? undefined : pitch?.b);
  const left = useCase(a);
  const right = useCase(b);
  const t = OPERATOR.compare;
  const waiting = !a && !b && cases.isPending;
  // "Same signal, different action" only over a pair that shares the band's signal.
  const same = left.data && right.data ? noticeSignal(left.data, right.data).same : false;
  return (
    <div className="mx-auto flex max-w-content flex-col gap-4" data-page="compare">
      <h1 className="text-page">{same ? OPERATOR.titles.compareSame : OPERATOR.titles.compare}</h1>
      {waiting ? (
        <LoadingBlock rows={4} />
      ) : !a || !b ? (
        <div className="flex flex-col gap-1 text-body" data-compare-pick>
          <p>
            {t.pickTwo}{' '}
            {pitch && (
              <Link
                to={`/compare?a=${encodeURIComponent(pitch.a)}&b=${encodeURIComponent(pitch.b)}`}
                className="text-accent underline underline-offset-2"
              >
                {t.pitchPair}
              </Link>
            )}
          </p>
          {cases.isError && (
            <p className="text-caption text-ink-2">
              {t.noDemoCases}{' '}
              <span data-verbatim>{isApiError(cases.error) ? cases.error.message : String(cases.error)}</span>
            </p>
          )}
        </div>
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
