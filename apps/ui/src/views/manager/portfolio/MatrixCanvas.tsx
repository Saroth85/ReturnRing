import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import type { PortfolioMatrix } from '@/api/models';
import { useWidth } from '@/components/charts';
import { Money, PlainNumber } from '@/components/provenance';
import { formatValue } from '@/format';
import { DEMO_CASES } from '../constants';
import { MANAGER } from '../copy';
import { convexHull, createScales, nearestIndex, placeLabels, textBox, ticks, valueDomain, type Pt } from './geometry';

type MatrixPoint = PortfolioMatrix['points'][number];

interface MatrixCanvasProps {
  matrix: PortfolioMatrix;
  demoCases: boolean;
  highlightCluster: string | null;
}

const P = MANAGER.portfolio;
const HOVER_RADIUS = 10;

/** Re-render when the theme changes, so the canvas redraws with the new token colours. */
function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1);
    const observer = typeof MutationObserver === 'undefined' ? null : new MutationObserver(bump);
    observer?.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-present'] });
    const media = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    media?.addEventListener?.('change', bump);
    return () => {
      observer?.disconnect();
      media?.removeEventListener?.('change', bump);
    };
  }, []);
  return version;
}

function token(el: Element, name: string, fallback: string): string {
  const value = getComputedStyle(el).getPropertyValue(name).trim();
  return value || fallback;
}

/**
 * The risk × value matrix (docs/ui-design.md 8.1): every scored customer as a 3 px dot in neutral
 * ink on a canvas (about 20,000 points stay fast), with an SVG overlay for the axes, the two
 * thresholds labelled with their basis, the quadrant positions, the demo annotations and the
 * highlighted cluster. No hue for a quadrant: positions, not verdicts. The whole matrix sits in the
 * points' ProvenanceScope (the caller's), so axis numbers carry their origin, period and nature.
 */
export function MatrixCanvas({ matrix, demoCases, highlightCluster }: MatrixCanvasProps) {
  const [measure, width] = useWidth<HTMLDivElement>();
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const theme = useThemeVersion();
  const [hover, setHover] = useState<number>(-1);
  const height = Math.max(300, Math.min(480, Math.round(width * 0.55)));
  /** Narrow screens: cluster labels without the account count, so they fit. */
  const compact = width < 560;

  const scored = useMemo(() => matrix.points.filter((p): p is MatrixPoint & { risk: number } => p.risk !== null), [matrix.points]);
  const riskT = matrix.thresholds.risk.value;
  const valueT = matrix.thresholds.value_eur.value;
  const domain = useMemo(() => valueDomain(scored.map((p) => p.value_eur), valueT), [scored, valueT]);
  const scales = useMemo(() => createScales({ width: Math.max(width, 1), height }, domain), [width, height, domain]);
  const drawn = useMemo(() => scored.filter((p) => p.value_eur >= domain.min && p.value_eur <= domain.max), [scored, domain]);
  const xs = useMemo(() => drawn.map((p) => scales.x(p.risk)), [drawn, scales]);
  const ys = useMemo(() => drawn.map((p) => scales.y(p.value_eur)), [drawn, scales]);

  useEffect(() => {
    const el = canvas.current;
    if (!el || width === 0) return;
    const ctx = typeof el.getContext === 'function' ? el.getContext('2d') : null;
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    el.width = Math.round(width * dpr);
    el.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = token(el, '--mark', '#3b4658');
    ctx.globalAlpha = 0.35;
    for (let i = 0; i < xs.length; i += 1) {
      ctx.beginPath();
      ctx.arc(xs[i] ?? 0, ys[i] ?? 0, 3, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }, [xs, ys, width, height, theme]);

  const onMove = (event: MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    setHover(nearestIndex(xs, ys, event.clientX - box.left, event.clientY - box.top, HOVER_RADIUS));
  };

  const { plot } = scales;
  const xT = scales.x(riskT);
  const yT = scales.y(valueT);
  const yTicks = ticks(domain.min, domain.max, 5);
  const xTicks = [0, 0.2, 0.4, 0.6, 0.8, 1];
  const clusterPts = (id: string): Pt[] =>
    drawn.flatMap((p, i) => (p.cluster_id === id ? [[xs[i] ?? 0, ys[i] ?? 0] as Pt] : []));
  const annotations = useMemo(() => {
    if (!demoCases) return [];
    const out: { key: string; kind: 'cluster' | 'customer'; x: number; y: number; text: string; hull: Pt[]; note?: string }[] = [];
    for (const id of DEMO_CASES.clusters) {
      const pts = drawn.flatMap((p, i) => (p.cluster_id === id ? [[xs[i] ?? 0, ys[i] ?? 0] as Pt] : []));
      if (pts.length === 0) continue;
      const top = pts.reduce((a, b) => (b[1] < a[1] ? b : a));
      const size = matrix.points.filter((p) => p.cluster_id === id).length;
      out.push({ key: id, kind: 'cluster', x: top[0], y: top[1] - 6, text: compact ? id : `${id} · ${size} ${P.linkedAccounts}`, hull: convexHull(pts) });
    }
    for (const c of DEMO_CASES.customers) {
      const i = drawn.findIndex((p) => p.customer_id === c.id);
      if (i < 0) continue;
      out.push({ key: c.id, kind: 'customer', x: xs[i] ?? 0, y: ys[i] ?? 0, text: c.name, hull: [], note: c.note });
    }
    return out;
  }, [demoCases, drawn, xs, ys, matrix.points, compact]);
  const hovered = hover >= 0 ? drawn[hover] : undefined;
  const corner = [
    { q: 'Q1', x: plot.left + 8, y: plot.top + 18, anchor: 'start' },
    { q: 'Q3', x: plot.left + plot.width - 8, y: plot.top + 18, anchor: 'end' },
    { q: 'Q2', x: plot.left + 8, y: plot.top + plot.height - 10, anchor: 'start' },
    { q: 'Q4', x: plot.left + plot.width - 8, y: plot.top + plot.height - 10, anchor: 'end' },
  ] as const;
  const riskLabel = `${P.riskThreshold} ${formatValue(riskT, 'probability')}`;
  const valueLabel = `${P.valueThreshold} ${formatValue(valueT, 'eur')}`;
  const riskLabelAt = { x: xT + 4, y: plot.top + plot.height - 24 };
  const valueLabelAt = { x: plot.left + plot.width - 6, y: yT - 6 };
  // Demo annotations keep clear of the threshold labels and the quadrant names.
  const reserved = [
    textBox(riskLabelAt.x, riskLabelAt.y, riskLabel, 'start'),
    textBox(valueLabelAt.x, valueLabelAt.y, valueLabel, 'end'),
    ...corner.map((c) => textBox(c.x, c.y, c.q, c.anchor)),
  ];
  // A handful of labels: placed on every render, no memo needed.
  const labels = placeLabels(annotations, scales.plot, reserved);

  return (
    <div ref={measure} className="relative w-full min-w-0" style={{ height }} data-matrix>
      {width > 0 && (
        <>
          <canvas ref={canvas} aria-hidden="true" className="absolute inset-0" style={{ width, height }} data-matrix-canvas />
          <svg
            role="img"
            aria-label={P.matrixLabel}
            width={width}
            height={height}
            className="absolute inset-0 overflow-visible"
            onMouseMove={onMove}
            onMouseLeave={() => setHover(-1)}
            onClick={onMove}
          >
            {/* grid and axes */}
            {yTicks.map((v) => (
              <g key={`y${v}`}>
                <line x1={plot.left} x2={plot.left + plot.width} y1={scales.y(v)} y2={scales.y(v)} stroke="var(--grid)" />
                <text x={plot.left - 6} y={scales.y(v) + 4} textAnchor="end" fontSize={12} fill="var(--ink-3)">
                  {formatValue(v, 'eur').replace(/\.00$/, '')}
                </text>
              </g>
            ))}
            {xTicks.map((v) => (
              <text key={`x${v}`} x={scales.x(v)} y={plot.top + plot.height + 16} textAnchor="middle" fontSize={12} fill="var(--ink-3)">
                {formatValue(v, 'probability')}
              </text>
            ))}
            <line x1={plot.left} x2={plot.left + plot.width} y1={plot.top + plot.height} y2={plot.top + plot.height} stroke="var(--border-strong)" />
            <line x1={plot.left} x2={plot.left} y1={plot.top} y2={plot.top + plot.height} stroke="var(--border-strong)" />
            <text x={plot.left + plot.width / 2} y={height - 4} textAnchor="middle" fontSize={12} fill="var(--ink-2)">
              {P.xAxis}
            </text>
            {/* thresholds */}
            <line x1={xT} x2={xT} y1={plot.top} y2={plot.top + plot.height} stroke="var(--border-strong)" strokeDasharray="6 4" strokeWidth={1.5} />
            <line x1={plot.left} x2={plot.left + plot.width} y1={yT} y2={yT} stroke="var(--border-strong)" strokeDasharray="6 4" strokeWidth={1.5} />
            <text
              x={riskLabelAt.x}
              y={riskLabelAt.y}
              fontSize={12}
              fill="var(--ink-2)"
              stroke="var(--surface)"
              strokeWidth={3}
              paintOrder="stroke"
              data-threshold="risk"
            >
              {riskLabel}
            </text>
            {/* At the right end, away from the dense low-risk points and the demo annotations. */}
            <text
              x={valueLabelAt.x}
              y={valueLabelAt.y}
              textAnchor="end"
              fontSize={12}
              fill="var(--ink-2)"
              stroke="var(--surface)"
              strokeWidth={3}
              paintOrder="stroke"
              data-threshold="value"
            >
              {valueLabel}
            </text>
            {corner.map((c) => (
              <text key={c.q} x={c.x} y={c.y} textAnchor={c.anchor} fontSize={13} fontWeight={600} fill="var(--ink-2)" data-id>
                {c.q}
              </text>
            ))}
            {/* highlighted cluster */}
            {highlightCluster &&
              clusterPts(highlightCluster).map(([x, y], i) => (
                <circle key={`h${i}`} cx={x} cy={y} r={6} fill="none" stroke="var(--accent)" strokeWidth={2} data-highlight={highlightCluster} />
              ))}
            {/* demo annotations */}
            {demoCases &&
              annotations.map((a, i) => {
                const label = labels[i];
                if (!label) return null;
                return (
                  <g key={a.key} data-demo-cluster={a.kind === 'cluster' ? a.key : undefined} data-demo-customer={a.kind === 'customer' ? a.key : undefined}>
                    {a.kind === 'cluster' &&
                      (a.hull.length >= 3 ? (
                        <polygon
                          points={a.hull.map((p) => p.join(',')).join(' ')}
                          fill="var(--ink-1)"
                          fillOpacity={0.1}
                          stroke="var(--ink-1)"
                          strokeOpacity={0.18}
                          strokeWidth={16}
                          strokeLinejoin="round"
                        />
                      ) : (
                        <polyline
                          points={a.hull.map((p) => p.join(',')).join(' ')}
                          fill="none"
                          stroke="var(--ink-1)"
                          strokeOpacity={0.18}
                          strokeWidth={16}
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      ))}
                    {a.kind === 'customer' && (
                      <>
                        <circle cx={a.x} cy={a.y} r={7} fill="none" stroke="var(--ink-1)" strokeWidth={2} />
                        <circle cx={a.x} cy={a.y} r={2.5} fill="var(--ink-1)" />
                      </>
                    )}
                    <text
                      x={label.x}
                      y={label.y}
                      textAnchor={label.anchor}
                      fontSize={12}
                      fontWeight={600}
                      fill="var(--ink-1)"
                      stroke="var(--surface)"
                      strokeWidth={3}
                      paintOrder="stroke"
                    >
                      {a.text}
                    </text>
                    {a.note && <title>{a.note}</title>}
                  </g>
                );
              })}
            {hovered && <circle cx={xs[hover]} cy={ys[hover]} r={6} fill="none" stroke="var(--ink-1)" strokeWidth={2} pointerEvents="none" />}
          </svg>
          {hovered && (
            <div
              className="pointer-events-none absolute z-10 w-60 rounded-control border border-border bg-surface p-2 text-caption text-ink-1 shadow-overlay"
              style={{
                left: Math.min(Math.max(0, (xs[hover] ?? 0) + 12), Math.max(0, width - 244)),
                top: Math.min(Math.max(0, (ys[hover] ?? 0) + 12), Math.max(0, height - 150)),
              }}
              role="status"
              data-matrix-tooltip
            >
              <p className="font-medium">
                {hovered.display_name ?? MANAGER.portfolio.list.unnamed}{' '}
                <span className="font-mono text-ink-3" data-id>
                  {hovered.customer_id}
                </span>
              </p>
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2">
                <dt className="text-ink-3">{P.tooltip.risk}</dt>
                <dd>
                  <PlainNumber value={hovered.risk} unit="probability" />
                </dd>
                <dt className="text-ink-3">{P.tooltip.value}</dt>
                <dd>
                  <Money value={hovered.value_eur} />
                </dd>
                <dt className="text-ink-3">{P.tooltip.quadrant}</dt>
                <dd data-id>{hovered.quadrant}</dd>
                <dt className="text-ink-3">{P.tooltip.cluster}</dt>
                <dd data-id>{hovered.cluster_id ?? P.tooltip.noCluster}</dd>
                <dt className="text-ink-3">{P.tooltip.returns}</dt>
                <dd>
                  <PlainNumber value={hovered.returns_12m} unit="count" />
                </dd>
                <dt className="text-ink-3">{P.tooltip.coupons}</dt>
                <dd>
                  <PlainNumber value={hovered.welcome_coupons} unit="count" />
                </dd>
              </dl>
              {DEMO_CASES.customers.find((c) => c.id === hovered.customer_id) && (
                <p className="mt-1 text-ink-2">{DEMO_CASES.customers.find((c) => c.id === hovered.customer_id)?.note}</p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
