// Before and after a recompute (US-310, docs/ui-design.md 6.3.7): the two versions side by side,
// changed rows marked in words and with an arrow (never colour alone), the new decision, the
// regenerated draft, the audit entries and the other cases recomputed. Enter opens the new version.
// Evidence or a dispute often changes the cluster and the risk but not the decision (ADR-15
// section 18): with the same quadrant and the same levers it says "decision unchanged".
import * as Dialog from '@radix-ui/react-dialog';
import { ArrowRight, X } from 'lucide-react';
import { useRef } from 'react';
import type { CaseSnapshot, RecomputeResponse } from '@/api/models';
import { QuadrantBadge, RestrictionBadge, StateBadge } from '@/components/decision';
import { GenerationBadge } from '@/components/message';
import { Figure, IdText, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button, Kbd } from '@/components/ui';
import { changedSnapshotRows, SNAPSHOT_ROWS, type SnapshotRowKey } from '@/domain/decision';
import { OPERATOR } from '../../copy';
import { accountsLabel } from '../../format';

function Cell({ snapshot, row }: { snapshot: CaseSnapshot; row: SnapshotRowKey }) {
  const r = OPERATOR.recompute;
  switch (row) {
    case 'decision':
      return (
        <span className="flex flex-wrap gap-1">
          <IdText id={snapshot.decision_id} />
          <span data-version>
            · {r.version} {snapshot.version}
          </span>
        </span>
      );
    case 'quadrant':
      return snapshot.quadrant ? <QuadrantBadge quadrant={snapshot.quadrant} /> : <span>{OPERATOR.explanation.noQuadrant}</span>;
    case 'evidence_quality':
      return <span>{OPERATOR.queue.evidenceValues[snapshot.evidence_quality]}</span>;
    case 'risk':
      return snapshot.risk ? <Figure metric={snapshot.risk} label={r.rows.risk} /> : <span>{OPERATOR.queue.notScored}</span>;
    case 'top_factors':
      return (
        <ul className="list-disc pl-4" data-verbatim>
          {snapshot.top_factors.map((f) => (
            <li key={f.feature}>{f.phrase}</li>
          ))}
        </ul>
      );
    case 'lever_ids':
      return snapshot.lever_ids.length === 0 ? (
        <span>{OPERATOR.queue.noChange}</span>
      ) : (
        <ul className="flex flex-col">
          {snapshot.lever_ids.map((id) => (
            <li key={id} className="font-mono text-caption" data-id>
              {id}
            </li>
          ))}
        </ul>
      );
    case 'requires_approval':
      return <span>{snapshot.requires_approval ? r.needsApproval : r.noApproval}</span>;
    case 'cluster_size':
      return <span>{snapshot.cluster_size === null ? r.noCluster : accountsLabel(snapshot.cluster_size)}</span>;
    case 'active_links':
      return <span>{snapshot.active_links}</span>;
    default:
      return null;
  }
}

interface RecomputeComparisonProps {
  recompute: RecomputeResponse;
  /** Other returns the server recomputed (a dispute recomputes every open case of the cluster). */
  alsoRecomputed: string[];
  versions: number;
  onOpenVersion(): void;
}

export function RecomputeComparison({ recompute, alsoRecomputed, versions, onOpenVersion }: RecomputeComparisonProps) {
  const r = OPERATOR.recompute;
  const { before, after } = recompute;
  const changed = changedSnapshotRows(before, after);
  const decisionChanged = changed.has('quadrant') || changed.has('lever_ids');
  const contentRef = useRef<HTMLDivElement>(null);
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onOpenVersion()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-x-0 top-[var(--mode-banner-h,0px)] bottom-0 z-40 bg-ink-1/30" />
        <Dialog.Content
          className="fixed top-[calc(50%_+_var(--mode-banner-h,0px)_/_2)] left-1/2 z-50 flex max-h-[calc(100vh_-_2rem_-_var(--mode-banner-h,0px))] w-[min(56rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-card border border-border bg-surface shadow-overlay"
          data-recompute-comparison
          aria-describedby={undefined}
          ref={contentRef}
          onOpenAutoFocus={(event) => {
            // Enter opens the new version (docs/ui-design.md 6.3.7): the button takes the focus.
            event.preventDefault();
            contentRef.current?.querySelector<HTMLElement>('[data-open-new-version]')?.focus();
          }}
        >
          <div className="flex items-start justify-between gap-2 border-b border-border px-4 py-3">
            <Dialog.Title className="text-h3">
              {r.title} · <IdText id={recompute.return_id} />
            </Dialog.Title>
            <Dialog.Close className="rounded-control p-1 hover:bg-surface-2" aria-label={OPERATOR.sheets.close}>
              <X aria-hidden="true" size={18} />
            </Dialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            <ProvenanceScope provenance={recompute.provenance} covers="active links and cluster sizes before and after">
              <table className="w-full text-dense" data-comparison-table>
                <thead className="text-left text-caption text-ink-3">
                  <tr>
                    <th scope="col" className="py-1 pr-2 font-normal">
                      <span className="sr-only">{r.rows.decision}</span>
                    </th>
                    <th scope="col" className="py-1 pr-2 font-normal">
                      {r.before} ·{' '}
                      <span data-version>
                        {r.version} {before.version}
                      </span>
                    </th>
                    <th scope="col" className="w-6" />
                    <th scope="col" className="py-1 font-normal">
                      {r.after} ·{' '}
                      <span data-version>
                        {r.version} {after.version}
                      </span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {SNAPSHOT_ROWS.map((row) => {
                    const isChanged = changed.has(row);
                    return (
                      <tr key={row} className="border-t border-border align-top" data-row={row} data-changed={isChanged || undefined}>
                        <th scope="row" className="py-1.5 pr-2 text-left font-normal text-ink-2">
                          {r.rows[row]}
                          {isChanged && (
                            <span className="ml-1 rounded-chip border border-ink-2 px-1 text-micro uppercase text-ink-1">{r.changed}</span>
                          )}
                        </th>
                        <td className="py-1.5 pr-2">
                          <Cell snapshot={before} row={row} />
                        </td>
                        <td className="py-1.5 text-ink-3">{isChanged && <ArrowRight aria-label={r.changed} size={14} />}</td>
                        <td className={isChanged ? 'py-1.5 font-medium' : 'py-1.5'}>
                          <Cell snapshot={after} row={row} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <ProvenanceFooter />
            </ProvenanceScope>
            <p className="mt-3 text-body font-medium" data-decision-unchanged={decisionChanged ? 'false' : 'true'}>
              {decisionChanged ? r.changedDecision : r.unchanged}
            </p>
            <div className="mt-3 flex flex-col gap-2 text-body">
              <p className="flex flex-wrap items-center gap-2">
                <span className="text-ink-3">{r.newState}:</span>
                <StateBadge decision={recompute.decision} versions={versions} />
                <RestrictionBadge decision={recompute.decision} />
              </p>
              <p className="flex flex-wrap items-center gap-2">
                <span className="text-ink-3">{r.draft}:</span>
                <GenerationBadge generation={recompute.draft.generation} />
              </p>
              <p className="flex flex-wrap items-center gap-2">
                <span className="text-ink-3">{r.auditEntries}:</span>
                {recompute.audit_entry_ids.map((id) => (
                  <IdText key={id} id={`#${id}`} />
                ))}
              </p>
              {alsoRecomputed.length > 0 && (
                <p className="flex flex-wrap items-center gap-2">
                  <span className="text-ink-3">{r.alsoRecomputed}:</span>
                  {alsoRecomputed.map((id) => (
                    <IdText key={id} id={id} />
                  ))}
                </p>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-3">
            <p className="text-caption text-ink-2">{r.footer}</p>
            <Button variant="primary" onClick={onOpenVersion} data-open-new-version>
              {r.open(after.version)} <Kbd>Enter</Kbd>
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
