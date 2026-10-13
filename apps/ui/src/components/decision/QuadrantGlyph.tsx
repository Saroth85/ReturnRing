import type { QuadrantId } from '@/api/models';

// Cell positions as on the matrix (docs/ui-design.md 5.3): Q1 top-left (higher value, lower risk
// score), Q3 top-right, Q2 bottom-left, Q4 bottom-right.
const CELLS: Record<QuadrantId, { x: number; y: number }> = {
  Q1: { x: 0, y: 0 },
  Q3: { x: 7, y: 0 },
  Q2: { x: 0, y: 7 },
  Q4: { x: 7, y: 7 },
};

/** A 14 × 14 position glyph in neutral ink: a position, never a verdict (no hue, T-NEUTRAL). */
export function QuadrantGlyph({ quadrant }: { quadrant: QuadrantId }) {
  return (
    <svg aria-hidden="true" focusable="false" width="14" height="14" viewBox="0 0 14 14" data-quadrant-glyph={quadrant}>
      {(Object.keys(CELLS) as QuadrantId[]).map((q) => {
        const { x, y } = CELLS[q];
        const active = q === quadrant;
        return (
          <rect
            key={q}
            x={x + 0.5}
            y={y + 0.5}
            width="6"
            height="6"
            rx="1"
            fill={active ? 'var(--ink-2)' : 'none'}
            stroke={active ? 'var(--ink-2)' : 'var(--border-strong)'}
            strokeWidth="1"
          />
        );
      })}
    </svg>
  );
}
