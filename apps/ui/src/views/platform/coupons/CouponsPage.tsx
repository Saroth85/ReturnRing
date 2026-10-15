// Coupon rules /coupons (US-402, US-607; docs/ui-design.md 9.3): the general rule the manager
// approves and versions, its history, a new version (manager only, Ctrl+Enter), and the
// applications to clusters with their reversal (the shared CouponApplicationsTable).
import { useId, useState } from 'react';
import type { CouponRule, CouponRuleApplication, CouponRulesResponse } from '@/api/models';
import { useCreateCouponRule } from '@/api/mutations';
import { useCouponRules } from '@/api/queries';
import { usePersona } from '@/app/persona';
import { ErrorState, InlineNotice, LoadingBlock, useReadOnly } from '@/components/feedback';
import { DateText, IdText } from '@/components/provenance';
import { CouponApplicationsTable } from '@/components/records';
import { Button } from '@/components/ui';
import { PLATFORM } from '../copy';
import { PageHeader, Panel } from '../shared/layout';
import { WriteGate } from '../shared/WriteGate';

const t = PLATFORM.coupons;

function CurrentRule({ rule }: { rule: CouponRule }) {
  return (
    <Panel title={t.current}>
      <p className="flex flex-wrap items-center gap-x-1 text-caption text-ink-2" data-current-rule={rule.rule_version}>
        <IdText id={rule.rule_version} className="text-caption" /> · {t.approvedBy} <span className="font-medium text-ink-1">{rule.approved_by}</span>{' '}
        {t.on} <DateText value={rule.approved_at} />
        {rule.supersedes && (
          <>
            {' '}
            · {t.supersedes} <IdText id={rule.supersedes} className="text-caption" />
          </>
        )}
      </p>
      <blockquote className="border-l-2 border-border-strong pl-3 text-body text-ink-1" data-verbatim>
        {rule.text}
      </blockquote>
    </Panel>
  );
}

function NewVersion({ current }: { current: CouponRule }) {
  const persona = usePersona();
  const create = useCreateCouponRule();
  const [text, setText] = useState('');
  const [done, setDone] = useState<CouponRule | null>(null);
  const fieldId = useId();
  const hintId = useId();
  const empty = text.trim().length === 0;

  const submit = () => {
    if (empty || create.isPending) return;
    create.mutate(
      { text: text.trim(), approved_by: persona.id },
      {
        onSuccess: (result: CouponRulesResponse) => {
          setDone(result.current);
          setText('');
        },
      },
    );
  };

  return (
    <Panel title={t.newVersion}>
      <p className="text-body text-ink-2">{t.newVersionHint}</p>
      {done && (
        <InlineNotice tone="info">
          <span data-rule-created={done.rule_version}>{t.created(done.rule_version, done.approved_by)}</span>
        </InlineNotice>
      )}
      <WriteGate action="POST /api/coupon-rules">
        {(gate) => (
          <div className="flex w-full flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor={fieldId} className="text-caption text-ink-2">
                {t.textLabel}
              </label>
              <button
                type="button"
                disabled={!gate.allowed || create.isPending}
                onClick={() => setText(current.text)}
                className="text-caption text-accent underline underline-offset-2 disabled:text-ink-3 disabled:no-underline"
              >
                {t.startFromCurrent}
              </button>
            </div>
            <textarea
              id={fieldId}
              value={text}
              disabled={!gate.allowed || create.isPending}
              aria-describedby={hintId}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  submit();
                }
              }}
              rows={4}
              className="w-full rounded-control border border-field-border bg-field-bg px-3 py-2 text-body text-ink-1 placeholder:text-ink-3 disabled:opacity-60"
            />
            <p id={hintId} className="text-caption text-ink-3">
              {t.confirmHint}
            </p>
            {create.isError && <ErrorState error={create.error} />}
            <div>
              <Button variant="primary" disabled={!gate.allowed || empty || create.isPending} onClick={submit}>
                {create.isPending ? t.approving : t.approve}
              </Button>
            </div>
          </div>
        )}
      </WriteGate>
    </Panel>
  );
}

function History({ rules }: { rules: CouponRule[] }) {
  return (
    <Panel title={t.history}>
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[32rem] text-dense" data-rule-history>
          <thead className="text-left text-caption text-ink-2">
            <tr className="border-b border-border">
              <th scope="col" className="py-1 pr-3 font-medium">
                {t.version}
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                {t.approver}
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                {t.approvedAt}
              </th>
              <th scope="col" className="py-1 pr-3 font-medium">
                {t.supersedesHeader}
              </th>
              <th scope="col" className="py-1 font-medium">
                {t.state}
              </th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.rule_version} className="border-b border-border align-top last:border-b-0">
                <td className="py-1.5 pr-3">
                  <IdText id={rule.rule_version} />
                </td>
                <td className="py-1.5 pr-3">{rule.approved_by}</td>
                <td className="py-1.5 pr-3">
                  <DateText value={rule.approved_at} />
                </td>
                <td className="py-1.5 pr-3">{rule.supersedes ? <IdText id={rule.supersedes} /> : '—'}</td>
                <td className="py-1.5">{rule.active ? t.active : t.inactive}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export default function CouponsPage() {
  const rules = useCouponRules();
  const [reversed, setReversed] = useState<CouponRuleApplication | null>(null);
  const readOnly = useReadOnly();
  const data = rules.data;
  return (
    <div className="mx-auto flex max-w-content flex-col gap-4" data-page="coupons">
      <PageHeader title={PLATFORM.titles.coupons} intro={t.intro} stories={t.stories} />
      {rules.isError && <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />}
      {!data && !rules.isError && <LoadingBlock rows={6} />}
      {data && (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            <CurrentRule rule={data.current} />
            <NewVersion current={data.current} />
          </div>
          <Panel title={t.applications}>
            <p className="text-body text-ink-2">{t.applicationsNote}</p>
            {reversed && (
              <InlineNotice tone="info">
                <span data-reversed={reversed.application_id}>{t.reversed(reversed.application_id, reversed.reversed_by ?? '')}</span>
              </InlineNotice>
            )}
            {/* The read-only public demo refuses a reversal (403): none is offered, and the reason says why. */}
            {readOnly && (
              <p className="text-caption text-ink-3" data-reversal-read-only>
                {t.reversalReadOnly}
              </p>
            )}
            <CouponApplicationsTable applications={data.applications} reversible={!readOnly} onReversed={setReversed} />
          </Panel>
          <History rules={data.history} />
        </>
      )}
    </div>
  );
}
