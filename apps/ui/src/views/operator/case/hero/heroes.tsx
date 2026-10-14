// The heroes of the case card (docs/ui-design.md 6.2.2) other than the linked accounts: the
// guarantee banner (never scored, no restrictive lever), the product issue (Elena) and the
// not-enough-evidence panel (no risk number anywhere, US-307).
import { CircleDashed, ShieldCheck, Shirt } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import type { CaseCard } from '@/api/models';
import { GUARANTEE_DIRECTIVE } from '@/copy/constants';
import { DateText, Figure, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { refAnchorId } from '@/domain/refs';
import { OPERATOR } from '../../copy';
import { reasonLabel } from '../../format';
import { EvidenceBars } from './EvidenceBars';

export function GuaranteeBanner() {
  const h = OPERATOR.hero;
  return (
    <div className="flex gap-3 rounded-card border-2 border-ink-2 bg-surface p-4" data-guarantee-banner>
      <ShieldCheck aria-hidden="true" size={24} className="mt-0.5 shrink-0 text-ink-1" />
      <div className="flex flex-col gap-1">
        <h2 className="text-h3">{h.guaranteeTitle}</h2>
        <p className="text-body text-ink-1">
          {h.guaranteeBody} <span data-legal-ref>{GUARANTEE_DIRECTIVE}</span>.
        </p>
      </div>
    </div>
  );
}

type Anomaly = NonNullable<CaseCard['product_anomaly']>;
const CHART_HEIGHT = 96;
const BAR_GAP = 6;

function TrendChart({ anomaly, metric }: { anomaly: Anomaly; metric: 'returns' | 'return_rate' }) {
  const values = anomaly.trend.map((w) => w[metric]);
  const max = Math.max(...values) || 1;
  const width = 100 / anomaly.trend.length;
  return (
    <svg viewBox={`0 0 100 ${CHART_HEIGHT}`} preserveAspectRatio="none" className="h-24 w-full" aria-hidden="true">
      <line
        x1="0"
        x2="100"
        y1={CHART_HEIGHT - 0.5}
        y2={CHART_HEIGHT - 0.5}
        stroke="var(--grid)"
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
      {anomaly.trend.map((w, i) => {
        const h = (w[metric] / max) * (CHART_HEIGHT - BAR_GAP);
        const anomalyWeek = w.week === anomaly.week;
        return (
          <rect
            key={w.week}
            x={i * width + width * 0.15}
            y={CHART_HEIGHT - h}
            width={width * 0.7}
            height={Math.max(h, 0.5)}
            fill={anomalyWeek ? 'var(--surface)' : 'var(--chart-pos)'}
            stroke={anomalyWeek ? 'var(--ink-1)' : 'none'}
            strokeWidth={anomalyWeek ? 2 : 0}
            vectorEffect="non-scaling-stroke"
            data-anomaly-week={anomalyWeek || undefined}
          />
        );
      })}
    </svg>
  );
}

export function ProductAnomalyPanel({ card, anomaly }: { card: CaseCard; anomaly: Anomaly }) {
  const [asTable, setAsTable] = useState(false);
  const p = OPERATOR.product;
  const others = anomaly.return_ids.filter((id) => id !== card.return_id);
  return (
    <section
      className="flex flex-col gap-3 rounded-card border border-border bg-surface p-4"
      data-product-anomaly
      id={refAnchorId({ kind: 'anomaly', id: anomaly.anomaly_id })}
    >
      <h2 className="flex flex-wrap items-center gap-2 text-h3">
        <Shirt aria-hidden="true" size={18} />
        {p.onVariant} <IdText id={anomaly.variant_id} />
        <span className="text-body font-normal text-ink-2">
          ({anomaly.category}
          {anomaly.brand ? `, ${anomaly.brand}` : ''})
        </span>
      </h2>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        <div>
          <dt className="text-caption text-ink-3">
            {p.observed} {p.forReason} {reasonLabel(anomaly.reason).toLowerCase()}
          </dt>
          <dd className="text-title">
            <Figure metric={anomaly.observed} label={p.observed} />
          </dd>
        </div>
        <div>
          <dt className="text-caption text-ink-3">{p.expected}</dt>
          <dd className="text-title">
            <Figure metric={anomaly.expected} label={p.expected} />
          </dd>
        </div>
        <div>
          <dt className="text-caption text-ink-3">{p.z}</dt>
          <dd className="text-title">
            <Figure metric={anomaly.z} label={p.z} />
          </dd>
        </div>
        <div>
          <dt className="text-caption text-ink-3">{p.sales}</dt>
          <dd className="text-title">
            <Figure metric={anomaly.sales} label={p.sales} />
          </dd>
        </div>
      </dl>
      <p className="text-caption text-ink-3">
        {p.weekOf} <DateText value={anomaly.week} />
      </p>
      <ProvenanceScope provenance={card.provenance} covers="weekly sales and returns of the variant">
        <figure className="flex flex-col gap-2">
          <figcaption className="flex flex-wrap items-center justify-between gap-2 text-caption text-ink-2">
            <span>
              {p.trend} · <span className="text-ink-3">{p.anomalyWeek}: </span>
              <DateText value={anomaly.week} />
            </span>
            <Button variant="quiet" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
              {asTable ? p.viewChart : p.viewTable}
            </Button>
          </figcaption>
          {asTable ? (
            <table className="w-full text-dense">
              <thead className="text-left text-caption text-ink-3">
                <tr>
                  <th scope="col" className="font-normal">
                    {p.week}
                  </th>
                  <th scope="col" className="text-right font-normal">
                    {p.salesShort}
                  </th>
                  <th scope="col" className="text-right font-normal">
                    {p.returns}
                  </th>
                  <th scope="col" className="text-right font-normal">
                    {p.rateShort}
                  </th>
                </tr>
              </thead>
              <tbody>
                {anomaly.trend.map((w) => (
                  <tr key={w.week} className={w.week === anomaly.week ? 'font-semibold' : undefined}>
                    <td>
                      <DateText value={w.week} />
                    </td>
                    <td className="text-right">
                      <PlainNumber value={w.sales} unit="count" />
                    </td>
                    <td className="text-right">
                      <PlainNumber value={w.returns} unit="count" />
                    </td>
                    <td className="text-right">
                      <PlainNumber value={w.return_rate} unit="ratio" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
              <div>
                <TrendChart anomaly={anomaly} metric="returns" />
                <p className="flex justify-between text-caption text-ink-3">
                  <DateText value={anomaly.trend[0]?.week ?? anomaly.week} />
                  <DateText value={anomaly.week} />
                </p>
              </div>
              <div>
                <TrendChart anomaly={anomaly} metric="return_rate" />
                <p className="text-caption text-ink-3">{p.rate}</p>
              </div>
            </div>
          )}
        </figure>
        <ProvenanceFooter />
      </ProvenanceScope>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-body">
        <span>
          {p.status[anomaly.status] ?? anomaly.status}
          {anomaly.catalog_task_id && (
            <>
              {' · '}
              {p.task}{' '}
              <Link to="/products/tasks" className="font-mono text-dense text-accent underline underline-offset-2" data-id>
                {anomaly.catalog_task_id}
              </Link>
            </>
          )}
        </span>
        <Link to={`/products/${encodeURIComponent(anomaly.anomaly_id)}`} className="text-accent underline underline-offset-2">
          {p.openInProducts}
        </Link>
      </div>
      {others.length > 0 && (
        <p className="flex flex-wrap items-center gap-2 text-caption text-ink-3">
          {p.otherReturns}:
          {others.map((id) => (
            <Link
              key={id}
              to={`/returns/${id}`}
              className="rounded-chip border border-border px-1 font-mono text-dense text-accent"
              data-id
            >
              {id}
            </Link>
          ))}
        </p>
      )}
      <p className="text-body font-medium text-ink-1">{p.regular}</p>
    </section>
  );
}

export function InsufficientEvidencePanel({ card }: { card: CaseCard }) {
  const h = OPERATOR.hero;
  return (
    <section className="flex flex-col gap-3 rounded-card border-2 border-dashed border-ink-3 bg-surface p-4" data-insufficient-panel>
      <h2 className="flex items-center gap-2 text-h3">
        <CircleDashed aria-hidden="true" size={20} />
        {h.insufficientTitle}
      </h2>
      <EvidenceBars components={card.evidence.components} compact />
      {card.evidence.missing_evidence.length > 0 && (
        <div>
          <h3 className="text-caption font-medium text-ink-2">{h.missing}</h3>
          <ul className="list-disc pl-5 text-body" data-verbatim>
            {card.evidence.missing_evidence.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-body text-ink-1">{h.insufficientLine}</p>
    </section>
  );
}
