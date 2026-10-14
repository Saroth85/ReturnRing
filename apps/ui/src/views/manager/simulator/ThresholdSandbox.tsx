import clsx from 'clsx';
import { useId, useMemo, useState } from 'react';
import type { PortfolioMatrix } from '@/api/models';
import { QuadrantGlyph } from '@/components/decision';
import { Money, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { bucketDelta, matrixThresholds, QUADRANT_ORDER, rebucket, type SandboxThresholds } from '@/sim';
import { MANAGER } from '../copy';

const T = MANAGER.simulator.sandbox;

/**
 * Move the matrix thresholds and see the customers per quadrant before -> after, re-bucketed in the
 * browser (docs/ui-design.md 8.2). A preview only: it never changes the engine and does not feed
 * the net benefit (open question O-6).
 */
export function ThresholdSandbox({ matrix }: { matrix: PortfolioMatrix }) {
  const engine = useMemo(() => matrixThresholds(matrix), [matrix]);
  const [t, setT] = useState<SandboxThresholds>(engine);
  const riskId = useId();
  const valueId = useId();
  const engineBuckets = useMemo(() => rebucket(matrix.points, engine), [matrix.points, engine]);
  const sandbox = useMemo(() => rebucket(matrix.points, t), [matrix.points, t]);
  const delta = bucketDelta(engineBuckets, sandbox);
  const moved = t.risk !== engine.risk || t.valueEur !== engine.valueEur;
  const valueMax = Math.ceil(Math.max(...matrix.points.map((p) => p.value_eur), engine.valueEur) / 10) * 10;

  return (
    <ProvenanceScope
      provenance={matrix.points_provenance}
      covers="the matrix points re-bucketed in the browser at the sandbox thresholds"
      className="flex flex-col gap-3"
    >
      <div className="flex flex-col gap-1">
        <label htmlFor={riskId} className="flex items-baseline justify-between text-dense">
          <span className="text-ink-2">{T.risk}</span>
          <PlainNumber value={t.risk} unit="probability" />
        </label>
        <input
          id={riskId}
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={t.risk}
          onChange={(event) => setT((prev) => ({ ...prev, risk: Number(event.target.value) }))}
          className="accent-[var(--accent)]"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={valueId} className="flex items-baseline justify-between text-dense">
          <span className="text-ink-2">{T.value}</span>
          <Money value={t.valueEur} />
        </label>
        <input
          id={valueId}
          type="range"
          min={0}
          max={valueMax}
          step={1}
          value={t.valueEur}
          onChange={(event) => setT((prev) => ({ ...prev, valueEur: Number(event.target.value) }))}
          className="accent-[var(--accent)]"
        />
      </div>
      <table className="w-full border-collapse text-dense tabular-nums" data-sandbox>
        <caption className="sr-only">{T.caption}</caption>
        <thead>
          <tr className="border-b border-border-strong text-caption text-ink-2">
            <th scope="col" className="py-1 text-left font-medium">
              <span className="sr-only">{MANAGER.portfolio.table.quadrant}</span>
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              {T.before}
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              {T.after}
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              <span aria-hidden="true">±</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {QUADRANT_ORDER.map((q) => {
            const d = delta[q];
            return (
              <tr key={q} className="border-b border-border" data-sandbox-row={q}>
                <th scope="row" className="py-1 text-left font-normal">
                  <span className="inline-flex items-center gap-1">
                    <QuadrantGlyph quadrant={q} />
                    <span data-id>{q}</span>
                  </span>
                </th>
                <td className="py-1 text-right">
                  <PlainNumber value={d.before} unit="count" />
                </td>
                <td className="py-1 text-right">
                  <PlainNumber value={d.after} unit="count" />
                </td>
                <td className={clsx('py-1 text-right', d.change === 0 ? 'text-ink-3' : 'font-medium')}>
                  {d.change === 0 ? <span aria-hidden="true">·</span> : <PlainNumber value={d.change} unit="count" signed />}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-caption text-ink-2">{T.previewOnly}</p>
      {moved && (
        <button type="button" onClick={() => setT(engine)} className="self-start text-caption text-accent underline underline-offset-2">
          {T.reset}
        </button>
      )}
      <ProvenanceFooter />
    </ProvenanceScope>
  );
}
