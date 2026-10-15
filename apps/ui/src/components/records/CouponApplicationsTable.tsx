import clsx from 'clsx';
import { CalendarClock, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { Link, useInRouterContext } from 'react-router';
import type { CouponRuleApplication } from '@/api/models';
import { EmptyState, RoleGate } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { Button } from '@/components/ui';
import { RECORDS } from './copy';
import { toLapseVM } from './lapse';
import { ReverseCouponDialog } from './ReverseCouponDialog';

const C = RECORDS.coupons;

interface CouponApplicationsTableProps {
  applications: CouponRuleApplication[];
  /** Show "Reverse…" on applications that are not reversed (operators and managers). */
  reversible?: boolean;
  /** After a reversal, with the server's answer. */
  onReversed?(result: CouponRuleApplication): void;
  className?: string;
}

/**
 * "Reverse…" for the persona's role and the service's read-only flag (/readyz public_read_only,
 * ADR-15 section 15): disabled with the reason, never hidden (docs/ui-design.md 5.8).
 */
function ReverseButton({ onClick }: { onClick(): void }) {
  return (
    <RoleGate action="POST /api/coupon-rules/applications/{application_id}/reverse">
      {(gate) => (
        <Button disabled={!gate.allowed} title={gate.reason ?? undefined} onClick={onClick}>
          {C.reverse}
        </Button>
      )}
    </RoleGate>
  );
}

function DecisionLink({ decisionId }: { decisionId: string }) {
  const inRouter = useInRouterContext();
  const match = /^DEC-([A-Za-z0-9]+)-[A-Za-z0-9]+$/.exec(decisionId);
  if (!inRouter || !match) return <IdText id={decisionId} />;
  return (
    <Link
      to={`/returns/RET-${match[1]}/versions/${decisionId}`}
      className="font-mono text-dense text-accent underline underline-offset-2 hover:text-accent-hover"
      data-id
    >
      {decisionId}
    </Link>
  );
}

function StateCell({ app }: { app: CouponRuleApplication }) {
  const lapse = toLapseVM(app as unknown as Record<string, unknown>);
  return (
    <span className="flex flex-col gap-0.5">
      <span className="font-medium" data-application-state={app.state}>
        {C.stateLabel[app.state]}
      </span>
      {app.state === 'confirmed' && lapse.state === 'lapsed' && (
        <span className="inline-flex items-center gap-1 rounded-chip bg-attn-bg px-1 text-caption text-attn-fg" data-lapsed>
          <TriangleAlert aria-hidden="true" size={12} />
          {C.lapsed}
        </span>
      )}
      {app.state === 'confirmed' && lapse.state === 'in_force' && <span className="text-caption text-ink-2">{C.inForce}</span>}
    </span>
  );
}

/**
 * Coupon pauses applied to clusters of linked accounts (docs/ui-design.md 5.7, US-402, US-607):
 * cluster, accounts, source decision, state, who confirmed it, review date and reversal. The lapse
 * comes from server fields only (toLapseVM); until they exist, the review date is what is shown.
 */
export function CouponApplicationsTable({ applications, reversible = false, onReversed, className }: CouponApplicationsTableProps) {
  const [reversing, setReversing] = useState<CouponRuleApplication | null>(null);
  if (applications.length === 0) return <EmptyState message={C.empty} />;
  return (
    <div className={clsx('relative overflow-x-auto', className)}>
      <table className="w-full min-w-[52rem] border-collapse text-dense" data-coupon-applications>
        <caption className="sr-only">{C.caption}</caption>
        <thead>
          <tr className="border-b border-border-strong text-left text-caption text-ink-2">
            {[C.cluster, C.accounts, C.decision, C.state, C.confirmedBy, C.reviewAt, C.reversal].map((h) => (
              <th key={h} scope="col" className="px-2 py-1.5 font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {applications.map((app) => (
            <tr key={app.application_id} className="border-b border-border align-top" data-application={app.application_id}>
              <td className="px-2 py-2">
                <span className="flex flex-col items-start gap-0.5">
                  <IdText id={app.cluster_id} className="whitespace-nowrap" />
                  <span className="text-caption whitespace-nowrap text-ink-3" data-id>
                    {app.application_id}
                  </span>
                </span>
              </td>
              <td className="px-2 py-2">
                <details>
                  <summary className="cursor-pointer text-accent">{C.showAccounts}</summary>
                  <ul className="mt-1 flex flex-col gap-0.5">
                    {app.affected_customer_ids.map((id) => (
                      <li key={id} className="flex">
                        <IdText id={id} />
                      </li>
                    ))}
                  </ul>
                </details>
              </td>
              <td className="px-2 py-2 whitespace-nowrap">
                <DecisionLink decisionId={app.decision_id} />
              </td>
              <td className="px-2 py-2">
                <StateCell app={app} />
              </td>
              <td className="px-2 py-2">
                {app.confirmed_by ? (
                  <span className="flex flex-col">
                    <span>{app.confirmed_by}</span>
                    {app.confirmed_at && <DateText value={app.confirmed_at} className="text-caption text-ink-3" />}
                  </span>
                ) : (
                  <span className="text-ink-3">{C.notConfirmed}</span>
                )}
              </td>
              <td className="px-2 py-2 whitespace-nowrap">
                <span className="inline-flex items-center gap-1">
                  <CalendarClock aria-hidden="true" size={14} />
                  <DateText value={app.review_at} />
                </span>
              </td>
              <td className="px-2 py-2">
                {app.state === 'reversed' ? (
                  <span className="flex flex-col gap-0.5">
                    <span>
                      {C.reversedBy} {app.reversed_by}
                    </span>
                    {app.reversed_at && <DateText value={app.reversed_at} className="text-caption text-ink-3" />}
                    {app.reversal_reason && (
                      <q className="text-caption text-ink-2" data-verbatim>
                        {app.reversal_reason}
                      </q>
                    )}
                  </span>
                ) : reversible ? (
                  <ReverseButton onClick={() => setReversing(app)} />
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {reversing && (
        <ReverseCouponDialog
          application={reversing}
          open
          onOpenChange={(open) => !open && setReversing(null)}
          onDone={(result) => onReversed?.(result)}
        />
      )}
    </div>
  );
}
