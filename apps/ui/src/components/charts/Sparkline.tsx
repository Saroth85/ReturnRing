interface SparklineProps {
  values: number[];
  /** What the line shows, for screen readers ("Weekly returns of V-JKT-DEF"). */
  label: string;
  /** Index of the point to mark (the flagged week). */
  highlight?: number;
  width?: number;
  height?: number;
}

/**
 * A small trend line (docs/ui-design.md 5.6): 2 px in neutral ink, one marked point, no text, so it
 * never prints a number of its own. The table next to it carries the values.
 */
export function Sparkline({ values, label, highlight, width = 88, height = 24 }: SparklineProps) {
  if (values.length === 0) return null;
  const pad = 3;
  const max = Math.max(...values);
  const min = Math.min(0, ...values);
  const span = max - min || 1;
  const x = (i: number) => (values.length === 1 ? width / 2 : pad + (i * (width - 2 * pad)) / (values.length - 1));
  const y = (v: number) => height - pad - ((v - min) / span) * (height - 2 * pad);
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const marked = highlight !== undefined ? values[highlight] : undefined;
  return (
    <svg role="img" aria-label={label} width={width} height={height} viewBox={`0 0 ${width} ${height}`} className="shrink-0" data-sparkline>
      <line x1={pad} x2={width - pad} y1={y(min)} y2={y(min)} stroke="var(--grid)" strokeWidth={1} />
      <polyline points={points} fill="none" stroke="var(--ink-2)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {highlight !== undefined && marked !== undefined && (
        <circle cx={x(highlight)} cy={y(marked)} r={4} fill="var(--ink-1)" stroke="var(--surface)" strokeWidth={2} />
      )}
    </svg>
  );
}
