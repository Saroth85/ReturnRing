import type { Metric, QuadrantId } from '@/api/models';
import { DECISION } from '@/copy/common';
import { formatValue } from '@/format';

export interface QuadrantThresholds {
  risk: Metric;
  value: Metric;
}

/** The tooltip line of a quadrant: from the thresholds when known (docs/ui-design.md 5.3). */
export function quadrantRule(quadrant: QuadrantId, thresholds?: QuadrantThresholds): string {
  if (!thresholds) return DECISION.quadrantPlain[quadrant];
  const rule = DECISION.quadrantRule[quadrant];
  const risk = formatValue(thresholds.risk.value, thresholds.risk.unit);
  const value = formatValue(thresholds.value.value, thresholds.value.unit);
  return `risk score ${rule.risk} ${risk} and value ${rule.value} ${value} (matrix thresholds)`;
}
