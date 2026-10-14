// Risk (docs/ui-design.md 6.2.3 item 2, US-302): the score on a neutral track against its
// threshold, the three contributions to the score (not causes), the residual indicator, and the
// evidence on both sides, side by side. "High" is never said about a person.
import clsx from 'clsx';
import { CircleDashed, Plus, ShieldCheck } from 'lucide-react';
import type { CaseCard, EntityRef } from '@/api/models';
import { StatementLabelChip } from '@/components/decision';
import { Figure, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button, Kbd } from '@/components/ui';
import { refAnchorId } from '@/domain/refs';
import { OPERATOR } from '../../copy';
import { useCaseUi } from '../context';
import { Section } from '../../ui/Section';

type Risk = NonNullable<CaseCard['risk']>;
type Point = Risk['favourable'][number];
const LABEL_ORDER: Point['label'][] = ['OBSERVED', 'SCORE', 'HYPOTHESIS'];
/** The ends of the score track: a score lives between 0 and 1. */
const TRACK_MIN = 0;
const TRACK_MAX = 1;
const pct = (v: number) => `${Math.min(100, Math.max(0, v * 100))}%`;

export function ScoreTrack({ risk, compact = false }: { risk: Pick<Risk, 'score' | 'threshold' | 'band'>; compact?: boolean }) {
  const t = OPERATOR.risk;
  return (
    <div className="flex flex-col gap-1" data-score-track>
      <div className={clsx('relative rounded-full bg-surface-2', compact ? 'h-1.5' : 'h-2')} aria-hidden="true">
        <span className="absolute -top-1.5 h-5 w-0.5 bg-ink-1" style={{ left: pct(risk.threshold.value) }} />
        <span
          className="absolute -top-1 h-4 w-4 -translate-x-1/2 rounded-full border-2 border-surface bg-ink-2 shadow"
          style={{ left: pct(risk.score.value) }}
        />
      </div>
      {!compact && (
        <p className="relative flex h-4 justify-between text-caption text-ink-3">
          <span data-constant>{TRACK_MIN}</span>
          <span className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: pct(risk.threshold.value) }}>
            {t.threshold}
          </span>
          <span data-constant>{TRACK_MAX}</span>
        </p>
      )}
      <p className="text-body text-ink-1" data-band={risk.band}>
        {risk.band === 'high' ? t.above : t.below}
      </p>
    </div>
  );
}

function FactorList({ risk }: { risk: Risk }) {
  const t = OPERATOR.risk;
  const max = Math.max(...risk.top_factors.map((f) => Math.abs(f.contribution))) || 1;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-title">{t.factorsTitle}</h3>
      <ol className="flex flex-col gap-2" data-factor-list>
        {risk.top_factors.map((f) => {
          const width = `${(Math.abs(f.contribution) / max) * 50}%`;
          return (
            <li
              key={f.feature}
              className="grid gap-x-3 gap-y-1 sm:grid-cols-[minmax(0,1fr)_10rem]"
              data-factor={f.feature}
              data-direction={f.direction}
            >
              <span className="flex flex-wrap items-baseline gap-2">
                <StatementLabelChip label="SCORE" />
                <span data-verbatim>{f.phrase}</span>
              </span>
              <span className="flex flex-col gap-0.5">
                <span className="relative h-3 rounded-chip bg-surface-2" aria-hidden="true">
                  <span className="absolute inset-y-0 left-1/2 w-px bg-border-strong" />
                  <span
                    className={clsx(
                      'absolute inset-y-0',
                      f.direction === 'raises' ? 'left-1/2 bg-ink-2' : 'right-1/2 hatch-sim border border-ink-2',
                    )}
                    style={{ width }}
                  />
                </span>
                <span className="flex justify-between text-caption text-ink-2">
                  <span>{f.direction === 'raises' ? t.raises : t.lowers}</span>
                  <PlainNumber value={f.contribution} unit="score" signed />
                </span>
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function RefButtons({ refs }: { refs: EntityRef[] }) {
  const { selectRef } = useCaseUi();
  if (refs.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1" aria-label={OPERATOR.risk.refsLabel}>
      {refs.map((ref) => (
        <button
          key={`${ref.kind}-${ref.id}`}
          type="button"
          onClick={() => selectRef(ref)}
          className="rounded-chip border border-border px-1 font-mono text-caption text-accent hover:bg-accent-bg"
          data-id
          data-ref={refAnchorId(ref)}
        >
          {ref.id}
        </button>
      ))}
    </span>
  );
}

function BalanceColumn({ title, points, favourable }: { title: string; points: Point[]; favourable: boolean }) {
  const { openSheet, gates } = useCaseUi();
  const t = OPERATOR.risk;
  const sorted = [...points].sort((a, b) => LABEL_ORDER.indexOf(a.label) - LABEL_ORDER.indexOf(b.label));
  return (
    <div className="flex flex-col gap-2 rounded-control border border-border p-3" data-balance={favourable ? 'favourable' : 'unfavourable'}>
      <h4 className="flex flex-wrap items-center justify-between gap-2 text-body font-semibold">
        <span>
          {title} (<span data-constant>{points.length}</span>)
        </span>
        {favourable && (
          <Button
            variant="quiet"
            className="min-h-7 px-2"
            disabled={!gates.evidence.allowed}
            title={gates.evidence.reason ?? undefined}
            onClick={() => openSheet({ kind: 'evidence' })}
            data-add-evidence
          >
            <Plus aria-hidden="true" size={14} />
            {t.addEvidence} <Kbd>E</Kbd>
          </Button>
        )}
      </h4>
      {sorted.length === 0 ? (
        <p className="text-body text-ink-3">{t.noneRecorded}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {sorted.map((p, i) => (
            <li key={`${p.label}-${i}`} className="flex flex-col gap-1">
              <span className="flex flex-wrap items-baseline gap-2">
                <StatementLabelChip label={p.label} />
                <span className={clsx(p.label === 'HYPOTHESIS' && 'italic')} data-verbatim>
                  {p.statement}
                </span>
              </span>
              <RefButtons refs={p.refs} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function EvidenceBalance({ risk }: { risk: Risk }) {
  const t = OPERATOR.risk;
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-title">{t.balanceTitle}</h3>
      <div className="grid gap-3 md:grid-cols-2" data-evidence-balance>
        <BalanceColumn title={t.favourable} points={risk.favourable} favourable />
        <BalanceColumn title={t.unfavourable} points={risk.unfavourable} favourable={false} />
      </div>
    </div>
  );
}

export function RiskSection({ card }: { card: CaseCard }) {
  const t = OPERATOR.risk;
  const risk = card.risk;
  if (!risk) {
    const guarantee = card.path === 'guarantee';
    return (
      <Section id="risk" title={t.title}>
        <p className="flex items-center gap-2 text-body text-ink-2" data-not-scored={guarantee ? 'guarantee' : 'insufficient'}>
          {guarantee ? <ShieldCheck aria-hidden="true" size={16} /> : <CircleDashed aria-hidden="true" size={16} />}
          {guarantee ? OPERATOR.hero.guaranteeNotScored : t.notScoredInsufficient}
        </p>
      </Section>
    );
  }
  return (
    <Section
      id="risk"
      title={t.title}
      aside={
        <span className="text-caption text-ink-3">
          {t.model} <IdText id={risk.model_version} />
        </span>
      }
    >
      <ProvenanceScope provenance={risk.provenance} covers="contributions to the score and feature values">
        <div className="flex flex-col gap-4" data-risk-panel>
          <div className="grid items-start gap-4 sm:grid-cols-[auto_minmax(0,1fr)]">
            <div>
              <p className="text-caption text-ink-3">{t.score}</p>
              <p className="text-hero">
                <Figure metric={risk.score} label={t.score} size="hero" distinctFrom={risk.threshold.value} />
              </p>
              <p className="text-caption text-ink-3">
                {t.threshold} <Figure metric={risk.threshold} label={`${t.score} · ${t.threshold}`} distinctFrom={risk.score.value} />
              </p>
            </div>
            <ScoreTrack risk={risk} />
          </div>
          {risk.residual_z && (
            <p className="flex flex-wrap items-baseline gap-2 text-body">
              <span className="text-ink-3">{t.residual}</span>
              <Figure metric={risk.residual_z} label={t.residual} />
              <span className="text-ink-2" data-verbatim>
                {risk.residual_note}
              </span>
            </p>
          )}
          <FactorList risk={risk} />
          <EvidenceBalance risk={risk} />
        </div>
        <ProvenanceFooter />
      </ProvenanceScope>
    </Section>
  );
}
