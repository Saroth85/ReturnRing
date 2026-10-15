// Decision record /returns/:returnId/versions/:decisionId (US-604, docs/ui-design.md 6.6): a 640 px
// drawer over the case card with one version of the decision: the inputs given to the engine, its
// output, the alternatives and their assumptions, the human outcome and every version used.
import { History } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import type { DecisionRecord, Terms } from '@/api/models';
import { useDecisionRecord } from '@/api/queries';
import { ApproverLine, LeverList, leverLabel, RestrictionBadge, StateBadge } from '@/components/decision';
import { ErrorState, InlineNotice, LoadingBlock } from '@/components/feedback';
import { proposalNeedsApproval } from '@/domain/decision';
import { DateText, Figure, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { OPERATOR } from '../copy';
import { reasonLabel } from '../format';
import CasePage from '../case/CasePage';
import { AssumptionLabelText } from '../case/sections/AlternativesSection';
import { Sheet } from '../ui/Sheet';

function TermsLine({ terms }: { terms: Terms }) {
  return (
    <span className="flex flex-wrap gap-1" data-constant>
      {(Object.keys(terms) as Array<keyof Terms>).map((key) => (
        <span key={key} className="rounded-chip border border-border px-1 text-caption">
          {leverLabel({ id: key, value: terms[key] })}
        </span>
      ))}
    </span>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-3">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  );
}

function RecordBody({ record, returnId }: { record: DecisionRecord; returnId: string }) {
  const t = OPERATOR.record;
  const d = record.decision;
  const inputs = d.inputs;
  return (
    <div className="flex flex-col gap-4" data-decision-record={d.decision_id}>
      {d.superseded_by ? (
        <InlineNotice tone="info">
          <span className="flex flex-wrap items-center gap-2">
            {t.superseded(d.superseded_by)}
            <Link to={`/returns/${returnId}`} className="text-accent underline underline-offset-2">
              {OPERATOR.banners.goLatest}
            </Link>
          </span>
        </InlineNotice>
      ) : (
        <InlineNotice tone="info">{t.latest}</InlineNotice>
      )}
      <p className="flex flex-wrap items-center gap-2">
        <StateBadge decision={d} />
        {!d.superseded_by && <RestrictionBadge decision={d} />}
      </p>
      <ProvenanceScope provenance={inputs.provenance} covers="the inputs given to the engine (risk, value, thresholds)">
        <section className="flex flex-col gap-2">
          <h3 className="text-title">{t.inputs}</h3>
          <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1 text-dense">
            <Row label={t.order}>
              <IdText id={inputs.order_id} />
            </Row>
            <Row label={t.customer}>
              <IdText id={inputs.customer_id} />
            </Row>
            <Row label={t.reason}>{reasonLabel(inputs.reason)}</Row>
            <Row label={t.delivered}>
              <DateText value={inputs.delivered_on} />
            </Row>
            <Row label={t.notice}>
              <DateText value={inputs.notice_on} />
            </Row>
            <Row label={t.orderTerms}>
              <TermsLine terms={inputs.order_terms} />
            </Row>
            <Row label={t.baseTerms}>
              <TermsLine terms={inputs.base_terms} />
            </Row>
            <Row label={t.risk}>
              {inputs.risk === null ? t.notScored : <PlainNumber value={inputs.risk} unit="probability" />}
              <span className="text-ink-3">
                {' '}
                ({t.thresholdWord} <PlainNumber value={inputs.thresholds.risk} unit="probability" />)
              </span>
            </Row>
            <Row label={t.value}>
              {inputs.value_eur === null ? '—' : <PlainNumber value={inputs.value_eur} unit="eur" />}
              <span className="text-ink-3">
                {' '}
                ({t.thresholdWord} <PlainNumber value={inputs.thresholds.value_eur} unit="eur" />)
              </span>
            </Row>
            <Row label={t.evidence}>{OPERATOR.queue.evidenceValues[inputs.evidence_quality]}</Row>
            <Row label={t.cluster}>{inputs.cluster_id ? <IdText id={inputs.cluster_id} /> : t.noCluster}</Row>
          </dl>
        </section>
        <ProvenanceFooter />
      </ProvenanceScope>
      <section className="flex flex-col gap-2">
        <h3 className="text-title">{t.output}</h3>
        <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1 text-dense">
          <Row label={t.quadrant}>{d.quadrant ? <span data-id>{d.quadrant}</span> : OPERATOR.explanation.noQuadrant}</Row>
          <Row label={t.levers}>
            <LeverList levers={d.proposed_levers} base={inputs.base_terms} />
          </Row>
          {/* From the proposal itself (rule 5): requires_approval and review_at follow the current
              levers and move to the outcome below (UI3). */}
          <Row label={t.approval}>
            <span data-engine-approval={proposalNeedsApproval(d, inputs.base_terms) ? 'yes' : 'no'}>
              {proposalNeedsApproval(d, inputs.base_terms) ? t.proposalApprovalYes : t.no}
            </span>
          </Row>
          {/* No refund deadline on the guarantee path (rule 4): refund_due_by is null there. */}
          {d.refund_due_by && (
            <Row label={t.refundBy}>
              <DateText value={d.refund_due_by} />
            </Row>
          )}
        </dl>
      </section>
      <section className="flex flex-col gap-2">
        <h3 className="text-title">{t.alternatives}</h3>
        <ul className="flex flex-col gap-1 text-dense">
          {record.alternatives.map((alt) => (
            <li key={alt.kind} className="flex flex-wrap items-center gap-2 border-b border-grid py-1">
              <span className="font-medium">{OPERATOR.alternatives.kinds[alt.kind] ?? alt.kind}</span>
              <Figure metric={alt.net_benefit.total} label={OPERATOR.alternatives.netBenefit} signed />
              <span className="text-caption text-ink-3">
                <AssumptionLabelText label={alt.net_benefit.assumptions.version} basedOn={alt.net_benefit.assumptions.based_on} />
              </span>
            </li>
          ))}
        </ul>
        <p className="text-caption text-ink-2">{OPERATOR.alternatives.refundNote}</p>
      </section>
      <section className="flex flex-col gap-2">
        <h3 className="text-title">{t.outcome}</h3>
        <ApproverLine decision={d} />
        <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1 text-dense">
          <Row label={t.state}>{OPERATOR.queue.states[d.state] ?? d.state}</Row>
          <Row label={t.levers}>
            <LeverList levers={d.levers} base={inputs.base_terms} />
          </Row>
          <Row label={t.currentApproval}>
            <span data-current-approval={d.requires_approval ? 'yes' : 'no'}>{d.requires_approval ? t.yes : t.no}</span>
          </Row>
          <Row label={t.currentReviewAt}>{d.review_at ? <DateText value={d.review_at} /> : t.none}</Row>
          {d.override_reason && (
            <Row label={t.overrideReason}>
              <span data-verbatim>“{d.override_reason}”</span>
            </Row>
          )}
          {d.override_category && <Row label={t.overrideCategory}>{OPERATOR.sheets.categories[d.override_category]}</Row>}
        </dl>
      </section>
      <section className="flex flex-col gap-2">
        <h3 className="text-title">{t.versions}</h3>
        <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1 text-dense">
          <Row label={t.trigger}>{OPERATOR.history.triggers[d.revision_trigger] ?? d.revision_trigger}</Row>
          <Row label={t.created}>
            <DateText value={d.created_at} />
          </Row>
          {d.supersedes && (
            <Row label={t.supersedes}>
              <IdText id={d.supersedes} />
            </Row>
          )}
          <Row label={t.rulesVersion}>
            <IdText id={d.rules_version} />
          </Row>
          <Row label={t.models}>
            <span className="flex flex-wrap gap-1">
              {Object.entries(d.model_versions).map(([k, v]) => (
                <span key={k} className="text-caption">
                  {k} <IdText id={v ?? ''} />
                </span>
              ))}
            </span>
          </Row>
          <Row label={t.assumptions}>
            <IdText id={d.assumptions_version} />
          </Row>
          <Row label={t.generation}>{d.generation_id ? <IdText id={d.generation_id} /> : t.none}</Row>
        </dl>
      </section>
      <Link to={`/audit?decision_id=${d.decision_id}`} className="self-start text-accent underline underline-offset-2">
        {t.openAudit}
      </Link>
    </div>
  );
}

export default function DecisionRecordPage() {
  const { returnId = '', decisionId = '' } = useParams();
  const navigate = useNavigate();
  const record = useDecisionRecord(decisionId);
  const t = OPERATOR.record;
  return (
    <>
      <CasePage />
      <Sheet
        name="record"
        width="record"
        modal={false}
        open
        onOpenChange={(open) => !open && void navigate(`/returns/${returnId}`)}
        title={
          <span className="inline-flex items-center gap-2">
            <History aria-hidden="true" size={18} />
            {t.title} · <IdText id={decisionId} />
          </span>
        }
      >
        {record.isPending && <LoadingBlock rows={8} />}
        {record.isError && <ErrorState error={record.error} onRetry={() => void record.refetch()} />}
        {record.data && <RecordBody record={record.data} returnId={returnId} />}
      </Sheet>
    </>
  );
}
