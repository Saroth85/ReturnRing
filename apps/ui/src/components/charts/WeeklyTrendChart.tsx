import { useId } from 'react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Metric, Provenance } from '@/api/models';
import { DateText, PlainNumber } from '@/components/provenance';
import { formatDate, formatValue } from '@/format';
import { ChartFrame } from './ChartFrame';
import { CHARTS } from './copy';

const shortWeek = (week: string) => formatDate(week).replace(/ \d{4}$/, '');
import { firstWeekAfter, type TrendWeek } from './trend';
import { useWidth } from './useWidth';

interface WeeklyTrendChartProps {
  title: string;
  trend: TrendWeek[];
  /** The provenance that covers the trend (ProductAnomaliesResponse or CaseCard, ADR-15 section 2). */
  provenance: Provenance;
  /** The flagged week and its figures (spec 8.9). */
  flagged?: { week: string; expected: Metric; z: Metric } | null;
  /** When a catalogue task was done: weeks after it are shaded (US-606 follow-up). */
  correctionAt?: string | null;
  /** Height of the returns chart in px; the rate chart is smaller. */
  height?: number;
}

interface TickProps {
  x?: number | string;
  y?: number | string;
  index?: number;
  payload?: { value?: unknown };
}

/**
 * Axis ticks as plain SVG text, so the labels use the page's tokens and thin out on narrow charts.
 * (Recharts measures text in a hidden span appended to <body>; the PROV-UI scan exempts it.)
 */
function tick(format: (value: unknown) => string, axis: 'x' | 'y', every = 1) {
  return function AxisTick({ x, y, payload, index = 0 }: TickProps) {
    if (index % every !== 0) return <g />;
    return (
      <text
        x={Number(x)}
        y={Number(y)}
        dy={axis === 'x' ? 14 : 4}
        dx={axis === 'y' ? -4 : 0}
        textAnchor={axis === 'x' ? 'middle' : 'end'}
        fontSize={12}
        fill="var(--ink-3)"
      >
        {format(payload?.value)}
      </text>
    );
  };
}

const X_TICK = tick((v) => shortWeek(String(v)), 'x');
/** Every other week label on narrow charts, so labels never collide. */
const X_TICK_SPARSE = tick((v) => shortWeek(String(v)), 'x', 2);
const NARROW_CHART = 520;
const COUNT_TICK = tick((v) => String(v), 'y');
const RATE_TICK = tick((v) => formatValue(Number(v), 'ratio'), 'y');

function TrendTooltip({ active, payload }: { active?: boolean; payload?: readonly { payload?: unknown }[] }) {
  const row = payload?.[0]?.payload as TrendWeek | undefined;
  if (!active || !row) return null;
  return (
    <div className="rounded-control border border-border bg-surface px-2 py-1 text-caption text-ink-1 shadow-overlay">
      <p className="font-medium">
        {CHARTS.week} <DateText value={row.week} />
      </p>
      <p>
        {CHARTS.returns}: <PlainNumber value={row.returns} unit="count" /> · {CHARTS.sales}: <PlainNumber value={row.sales} unit="count" />
      </p>
      <p>
        {CHARTS.returnRate}: <PlainNumber value={row.return_rate} unit="ratio" />
      </p>
    </div>
  );
}

/**
 * Weekly returns of a variant for one reason (docs/ui-design.md 8.5): returns as bars with the
 * expected count of the flagged week as a dashed marker and "z = …" on that week, then the return
 * rate in its own small chart (never a second axis). Weeks after a catalogue correction are shaded,
 * so the before/after series of US-606 reads at a glance; the caveat stays with the caller.
 */
export function WeeklyTrendChart({ title, trend, provenance, flagged, correctionAt, height = 200 }: WeeklyTrendChartProps) {
  const descId = useId();
  const [measure, width] = useWidth<HTMLDivElement>();
  const rateHeight = Math.round(height * 0.6);
  const after = firstWeekAfter(trend, correctionAt);
  const last = trend[trend.length - 1]?.week;
  /** A correction in the second half of the series gets its label on the left of the line. */
  const lateCorrection = after !== null && trend.findIndex((w) => w.week === after) > trend.length / 2;
  const zText = flagged ? formatValue(flagged.z.value, flagged.z.unit) : null;
  const columns = [CHARTS.week, CHARTS.sales, CHARTS.returns, CHARTS.returnRate, ...(after ? [CHARTS.beforeAfter] : [])];

  if (trend.length === 0) {
    return <p className="text-body text-ink-2">{CHARTS.noTrend}</p>;
  }

  return (
    <ChartFrame
      title={title}
      provenance={provenance}
      covers="the weekly sales and returns of the variant"
      subtitle={
        <ul id={descId} className="flex flex-wrap gap-x-4">
          <li>{CHARTS.legendReturns}</li>
          {flagged && <li>{CHARTS.legendExpected}</li>}
          {after && <li>{CHARTS.legendCorrection}</li>}
        </ul>
      }
      table={{
        caption: title,
        columns,
        rows: trend.map((w) => ({
          key: w.week,
          cells: [
            <DateText key="w" value={w.week} />,
            <PlainNumber key="s" value={w.sales} unit="count" />,
            <PlainNumber key="r" value={w.returns} unit="count" />,
            <PlainNumber key="rr" value={w.return_rate} unit="ratio" />,
            ...(after ? [w.week >= after ? CHARTS.after : CHARTS.before] : []),
          ],
        })),
      }}
    >
      <div ref={measure} className="flex min-w-0 flex-col gap-3" aria-describedby={descId} data-trend-chart>
        <div role="img" aria-label={`${title}: ${CHARTS.returnsTitle}`}>
          <p className="text-caption text-ink-2" aria-hidden="true">
            {CHARTS.returnsTitle}
          </p>
          {width > 0 && (
            <ComposedChart width={width} height={height} data={trend} margin={{ top: 20, right: 12, bottom: 4, left: 0 }}>
              <XAxis dataKey="week" interval={0} tick={width < NARROW_CHART ? X_TICK_SPARSE : X_TICK} tickLine={false} axisLine={{ stroke: 'var(--border-strong)' }} />
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <YAxis allowDecimals={false} interval={0} tick={COUNT_TICK} tickLine={false} axisLine={false} width={32} />
              {after && last && <ReferenceArea x1={after} x2={last} fill="var(--surface-2)" fillOpacity={1} ifOverflow="extendDomain" />}
              <Tooltip content={(p) => <TrendTooltip active={p.active} payload={p.payload} />} cursor={{ fill: 'var(--surface-2)' }} isAnimationActive={false} />
              <Bar dataKey="returns" fill="var(--chart-pos)" barSize={18} radius={[4, 4, 0, 0]} isAnimationActive={false} />
              {flagged && (
                <ReferenceLine
                  x={flagged.week}
                  stroke="var(--ink-2)"
                  strokeDasharray="4 3"
                  label={{ value: zText ?? '', position: 'top', fill: 'var(--ink-1)', fontSize: 12 }}
                />
              )}
              {flagged && (
                <ReferenceDot
                  x={flagged.week}
                  y={flagged.expected.value}
                  r={5}
                  fill="var(--surface)"
                  stroke="var(--ink-1)"
                  strokeWidth={2}
                  strokeDasharray="2 2"
                  ifOverflow="extendDomain"
                />
              )}
              {after && <ReferenceLine x={after} stroke="var(--ink-2)" label={{ value: CHARTS.correction, position: lateCorrection ? 'insideTopRight' : 'insideTopLeft', fill: 'var(--ink-2)', fontSize: 12 }} />}
            </ComposedChart>
          )}
        </div>
        <div role="img" aria-label={`${title}: ${CHARTS.rateTitle}`}>
          <p className="text-caption text-ink-2" aria-hidden="true">
            {CHARTS.rateTitle}
          </p>
          {width > 0 && (
            <LineChart width={width} height={rateHeight} data={trend} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
              {/* Padding keeps the first and last week labels inside the chart. */}
              <XAxis
                dataKey="week"
                interval={0}
                padding={{ left: 20, right: 24 }}
                tick={width < NARROW_CHART ? X_TICK_SPARSE : X_TICK}
                tickLine={false}
                axisLine={{ stroke: 'var(--border-strong)' }}
              />
              <CartesianGrid vertical={false} stroke="var(--border)" />
              <YAxis interval={0} tickCount={3} tick={RATE_TICK} tickLine={false} axisLine={false} width={48} />
              {after && last && <ReferenceArea x1={after} x2={last} fill="var(--surface-2)" fillOpacity={1} />}
              <Tooltip content={(p) => <TrendTooltip active={p.active} payload={p.payload} />} isAnimationActive={false} />
              <Line
                dataKey="return_rate"
                stroke="var(--ink-2)"
                strokeWidth={2}
                dot={{ r: 4, fill: 'var(--ink-2)', stroke: 'var(--surface)', strokeWidth: 2 }}
                isAnimationActive={false}
              />
              {after && <ReferenceLine x={after} stroke="var(--ink-2)" />}
            </LineChart>
          )}
        </div>
      </div>
    </ChartFrame>
  );
}
