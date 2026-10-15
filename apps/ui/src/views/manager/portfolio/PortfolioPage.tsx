// Portfolio matrix /portfolio (?quadrant=, ?cluster=, ?demo=0, ?view=table; US-601,
// docs/ui-design.md 8.1): counts and euros per quadrant, the value legend always visible, a click
// to the customer list. Positions, not verdicts: no quadrant has a colour.
import { CircleDashed, Grid2x2, List, Sparkles } from 'lucide-react';
import { useMemo } from 'react';
import { useSearchParams } from 'react-router';
import type { PortfolioMatrix, QuadrantId } from '@/api/models';
import { useDemoCases, useMatrix } from '@/api/queries';
import { QuadrantGlyph } from '@/components/decision';
import { DateText, Figure, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button } from '@/components/ui';
import { QUADRANT_ORDER } from '@/sim';
import { highlights as demoHighlights } from '@/domain/demoCases';
import { MANAGER } from '../copy';
import { sumMetrics } from '../metrics';
import { Loadable, ManagerPage, SideDrawer } from '../ui';
import { CustomerList } from './CustomerList';
import { valueDomain } from './geometry';
import { MatrixCanvas } from './MatrixCanvas';
import { QuadrantPolicyCard } from './QuadrantPolicyCard';

const P = MANAGER.portfolio;
/** A pressed toggle reads as selected: accent background and border (interaction colour). */
const PRESSED = 'aria-pressed:border-accent aria-pressed:bg-accent-bg';
/** Tiles in matrix order: Q1 top-left, Q3 top-right, Q2 bottom-left, Q4 bottom-right. */
const TILE_ORDER: QuadrantId[] = ['Q1', 'Q3', 'Q2', 'Q4'];
const isQuadrant = (q: string | null): q is QuadrantId => q !== null && (QUADRANT_ORDER as readonly string[]).includes(q);

function QuadrantTile({ summary, onOpen }: { summary: PortfolioMatrix['quadrants'][number]; onOpen(): void }) {
  const q = summary.quadrant;
  return (
    <li className="flex flex-col gap-2 rounded-card border border-border bg-surface p-3" data-quadrant-tile={q}>
      <p className="flex flex-wrap items-center gap-1.5">
        <QuadrantGlyph quadrant={q} />
        <span className="font-semibold" data-id>
          {q}
        </span>
        <span className="text-caption text-ink-3">{P.quadrantTitle[q]}</span>
      </p>
      <dl className="flex flex-col gap-1 text-dense">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <dd className="order-1 text-h3">
            <Figure metric={summary.customers} label={`${q} ${P.customers}`} />
          </dd>
          <dt className="order-2 text-ink-3">{P.customers}</dt>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-2">
          <dd className="order-1 font-medium">
            <Figure metric={summary.value_eur} label={`${q} ${P.value}`} />
          </dd>
          <dt className="order-2 text-ink-3">{P.value}</dt>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-2">
          <dd className="order-1 font-medium">
            <Figure metric={summary.returns_12m} label={`${q} ${P.returns}`} />
          </dd>
          <dt className="order-2 text-ink-3">{P.returns}</dt>
        </div>
      </dl>
      <Button className="mt-auto self-start" onClick={onOpen} data-open-quadrant={q}>
        <List aria-hidden="true" size={14} />
        {P.openList}
      </Button>
    </li>
  );
}

function QuadrantTable({ matrix }: { matrix: PortfolioMatrix }) {
  const t = P.table;
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full border-collapse text-dense" data-quadrant-table>
        <caption className="sr-only">{P.tableCaption}</caption>
        <thead>
          <tr className="border-b border-border-strong text-left text-caption text-ink-2">
            <th scope="col" className="px-2 py-1.5 font-medium">
              {t.quadrant}
            </th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">
              {t.customers}
            </th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">
              {t.value}
            </th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">
              {t.returns}
            </th>
          </tr>
        </thead>
        <tbody>
          {matrix.quadrants.map((q) => (
            <tr key={q.quadrant} className="border-b border-border">
              <th scope="row" className="px-2 py-1.5 text-left font-normal">
                <span className="inline-flex items-center gap-1.5">
                  <QuadrantGlyph quadrant={q.quadrant} />
                  <span data-id>{q.quadrant}</span>
                  <span className="text-caption text-ink-3">{P.quadrantTitle[q.quadrant]}</span>
                </span>
              </th>
              <td className="px-2 py-1.5 text-right">
                <Figure metric={q.customers} />
              </td>
              <td className="px-2 py-1.5 text-right">
                <Figure metric={q.value_eur} />
              </td>
              <td className="px-2 py-1.5 text-right">
                <Figure metric={q.returns_12m} />
              </td>
            </tr>
          ))}
          <tr className="border-b border-border">
            <th scope="row" className="px-2 py-1.5 text-left font-normal">
              {t.notPlaced}
            </th>
            <td className="px-2 py-1.5 text-right">
              <Figure metric={matrix.insufficient_evidence} />
            </td>
            <td className="px-2 py-1.5 text-right text-ink-3">—</td>
            <td className="px-2 py-1.5 text-right text-ink-3">—</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function Portfolio({ matrix }: { matrix: PortfolioMatrix }) {
  const [params, setParams] = useSearchParams();
  const quadrant = params.get('quadrant');
  const openQuadrant = isQuadrant(quadrant) ? quadrant : null;
  const notPlacedOpen = params.get('list') === 'not-placed';
  const cluster = params.get('cluster');
  const demo = params.get('demo') !== '0';
  // The golden cases of the demo reset: none before a reset (404), so nothing is annotated.
  const cases = useDemoCases();
  const highlights = useMemo(() => demoHighlights(cases.data), [cases.data]);
  const tableView = params.get('view') === 'table';
  const set = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value === null) next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: key !== 'quadrant' && key !== 'list' },
    );

  const clusters = useMemo(
    () => [...new Set(matrix.points.map((p) => p.cluster_id).filter((c): c is string => c !== null))].sort(),
    [matrix.points],
  );
  const scoredTotal = sumMetrics(matrix.quadrants.map((q) => q.customers));
  const domain = useMemo(
    () => valueDomain(matrix.points.filter((p) => p.risk !== null).map((p) => p.value_eur), matrix.thresholds.value_eur.value),
    [matrix],
  );
  const quadrantPoints = openQuadrant ? matrix.points.filter((p) => p.quadrant === openQuadrant) : [];
  const notPlaced = matrix.points.filter((p) => p.risk === null);

  return (
    <ManagerPage
      screen="portfolio"
      title={MANAGER.titles.portfolio}
      intro={
        <>
          <p className="flex flex-wrap items-baseline gap-x-2">
            {scoredTotal && <Figure metric={scoredTotal} label={P.scored} />}
            <span>{P.scored}</span>
            <span>·</span>
            <span>
              {MANAGER.asOf} <DateText value={matrix.as_of} />
            </span>
          </p>
          <p className="mt-1">{P.intro}</p>
        </>
      }
      actions={
        <>
          <Button aria-pressed={demo} className={PRESSED} onClick={() => set('demo', demo ? '0' : null)} title={P.demoCasesHint}>
            <Sparkles aria-hidden="true" size={14} />
            {P.demoCases}
          </Button>
          <label className="inline-flex items-center gap-2 text-body">
            <span>{P.highlight}</span>
            <select
              value={cluster ?? ''}
              onChange={(event) => set('cluster', event.target.value || null)}
              className="min-h-8 rounded-control border border-border-strong bg-surface px-2 text-body"
            >
              <option value="">{P.highlightNone}</option>
              {clusters.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <Button aria-pressed={tableView} className={PRESSED} onClick={() => set('view', tableView ? null : 'table')}>
            {tableView ? <Grid2x2 aria-hidden="true" size={14} /> : <List aria-hidden="true" size={14} />}
            {tableView ? P.chartView : P.tableView}
          </Button>
        </>
      }
    >
      <div className="grid gap-6 xl:grid-cols-12">
        <ProvenanceScope
          provenance={matrix.points_provenance}
          covers="the matrix points: risk, value, cluster, returns and coupons of each scored customer"
          className="flex min-w-0 flex-col gap-3 rounded-card border border-border bg-surface p-4 xl:col-span-8"
        >
          <div className="flex flex-col gap-1">
            <p className="text-caption font-medium text-ink-2">{P.yAxis}</p>
            <p className="max-w-prose text-caption text-ink-2" data-verbatim data-value-legend>
              {matrix.legend}
            </p>
          </div>
          {tableView ? <QuadrantTable matrix={matrix} /> : <MatrixCanvas matrix={matrix} demoCases={demo} highlights={highlights} highlightCluster={cluster} />}
          <dl className="flex flex-col gap-0.5 text-caption text-ink-2">
            <div className="flex flex-wrap gap-x-1">
              <dt>{P.riskThreshold}</dt>
              <dd className="flex flex-wrap gap-x-1">
                <Figure metric={matrix.thresholds.risk} label={P.riskThreshold} />
                <span data-verbatim>· {matrix.thresholds.risk_basis}</span>
              </dd>
            </div>
            <div className="flex flex-wrap gap-x-1">
              <dt>{P.valueThreshold}</dt>
              <dd className="flex flex-wrap gap-x-1">
                <Figure metric={matrix.thresholds.value_eur} label={P.valueThreshold} />
                <span data-verbatim>· {matrix.thresholds.value_basis}</span>
              </dd>
            </div>
            {domain.outside > 0 && !tableView && (
              <div>
                <PlainNumber value={domain.outside} unit="count" /> {P.outside}
              </div>
            )}
          </dl>
          <div className="flex flex-wrap items-center gap-2 rounded-control border border-dashed border-ink-3 px-3 py-2" data-not-placed>
            <CircleDashed aria-hidden="true" size={16} className="text-ink-2" />
            <span>{P.insufficientLead}</span>
            <Figure metric={matrix.insufficient_evidence} label={P.insufficient} />
            <span>{P.insufficient}</span>
            <Button variant="quiet" onClick={() => set('list', 'not-placed')}>
              {P.insufficientList}
            </Button>
          </div>
          <ProvenanceFooter />
        </ProvenanceScope>
        <ul className="grid min-w-0 grid-cols-1 content-start gap-3 sm:grid-cols-2 xl:col-span-4" aria-label={P.tableCaption}>
          {TILE_ORDER.map((q) => {
            const summary = matrix.quadrants.find((s) => s.quadrant === q);
            return summary ? <QuadrantTile key={q} summary={summary} onOpen={() => set('quadrant', q)} /> : null;
          })}
        </ul>
      </div>
      <QuadrantPolicyCard />
      <SideDrawer
        open={openQuadrant !== null}
        onOpenChange={(open) => !open && set('quadrant', null)}
        title={
          openQuadrant ? (
            <>
              <span data-id>{openQuadrant}</span> {P.list.titleTail}
            </>
          ) : (
            ''
          )
        }
        description={P.list.description}
      >
        {openQuadrant && <CustomerList points={quadrantPoints} provenance={matrix.points_provenance} />}
      </SideDrawer>
      <SideDrawer open={notPlacedOpen} onOpenChange={(open) => !open && set('list', null)} title={P.insufficientTitle} description={P.insufficientNote}>
        {notPlacedOpen && <CustomerList points={notPlaced} provenance={matrix.points_provenance} showRisk={false} defaultSort="value" />}
      </SideDrawer>
      {demo && highlights.customers.length > 0 && (
        <p className="sr-only" data-demo-note>
          {highlights.customers.map((c) => P.demoCustomers[c.key as keyof typeof P.demoCustomers]?.note).filter(Boolean).join('. ')}
        </p>
      )}
    </ManagerPage>
  );
}

export default function PortfolioPage() {
  const matrix = useMatrix();
  if (!matrix.data) {
    return (
      <ManagerPage screen="portfolio" title={MANAGER.titles.portfolio}>
        <Loadable query={matrix} label="GET /api/portfolio/matrix" rows={8}>
          {() => null}
        </Loadable>
      </ManagerPage>
    );
  }
  return <Portfolio matrix={matrix.data} />;
}
