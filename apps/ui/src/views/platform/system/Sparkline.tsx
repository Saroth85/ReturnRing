// A small line on a fixed domain (docs/ui-design.md 4.8: 2 px line, an 8 px end marker with a
// surface ring, no rescaling between polls). Decorative: the values are in the table next to it.
import { sparkline } from './telemetry';

const WIDTH = 120;
const HEIGHT = 32;

export function Sparkline({ values, min, max, className }: { values: readonly number[]; min: number; max: number; className?: string }) {
  const line = sparkline(values, { width: WIDTH, height: HEIGHT, min, max });
  if (!line.last) return null;
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox={`-5 -5 ${WIDTH + 10} ${HEIGHT + 10}`}
      className={className ?? 'h-10 w-36'}
      data-sparkline
    >
      <line x1="0" y1={HEIGHT} x2={WIDTH} y2={HEIGHT} stroke="var(--grid)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      <polyline
        points={line.points}
        fill="none"
        stroke="var(--chart-pos)"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={line.last.x} cy={line.last.y} r="4" fill="var(--chart-pos)" stroke="var(--surface)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
