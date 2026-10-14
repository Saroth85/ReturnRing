import type { QuadrantId } from '@/api/models';
import { DECISION } from '@/copy/common';
import { Tooltip } from '@/components/ui/Tooltip';
import { quadrantRule, type QuadrantThresholds } from './quadrant';
import { QuadrantGlyph } from './QuadrantGlyph';

/**
 * Glyph + "Q4", the same style for every quadrant (T-NEUTRAL): the position in the matrix,
 * never a judgement of the customer. Thresholds come from the card or the matrix data.
 */
export function QuadrantBadge({ quadrant, thresholds }: { quadrant: QuadrantId; thresholds?: QuadrantThresholds }) {
  return (
    <Tooltip content={DECISION.quadrantTip(quadrant, quadrantRule(quadrant, thresholds))}>
      <button
        type="button"
        data-quadrant-badge={quadrant}
        data-id
        className="inline-flex items-center gap-1 rounded-chip border border-border-strong bg-surface px-1.5 py-0.5 text-dense font-medium text-ink-1"
      >
        <QuadrantGlyph quadrant={quadrant} />
        {quadrant}
      </button>
    </Tooltip>
  );
}
