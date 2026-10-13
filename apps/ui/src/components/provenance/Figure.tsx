import clsx from 'clsx';
import { useEffect, useId } from 'react';
import type { Metric } from '@/api/models';
import { NATURE_WORD, PROVENANCE } from '@/copy/common';
import { formatPeriod, formatValue } from '@/format';
import { displayNature } from './nature';
import { ProvenanceBadge } from './ProvenanceBadge';
import { ProvenancePopover } from './ProvenancePopover';
import { noteOrigin } from './stores';

export interface FigureProps {
  metric: Metric;
  /** What the number is; shown in the popover and read by screen readers. */
  label?: string;
  /** "+€123.50" and "−€4.40" for gains and costs. */
  signed?: boolean;
  /** A threshold: prints "0.87 (threshold 0.60)". */
  compareTo?: Metric;
  size?: 'default' | 'hero';
  /** A benchmark status; `illustrative` marks the figure illustrative. */
  status?: string | null;
  className?: string;
}

/**
 * Every number the UI shows goes through Figure or PlainNumber (rule 11, US-906): the value
 * formatted by unit, its nature glyph, and the origin and period one hover or focus away. The
 * description is always in the DOM for screen readers.
 */
export function Figure({ metric, label, signed = false, compareTo, size = 'default', status, className }: FigureProps) {
  const descriptionId = useId();
  const { provenance } = metric;
  const nature = displayNature(provenance, status);
  const valueText = formatValue(metric.value, metric.unit, { signed });
  const compareText = compareTo ? formatValue(compareTo.value, compareTo.unit) : null;
  const period = formatPeriod(provenance.period_start, provenance.period_end);

  useEffect(() => noteOrigin(provenance.origin), [provenance.origin]);

  return (
    <span data-figure data-nature={nature} data-unit={metric.unit} className={clsx('inline-flex items-baseline', className)}>
      <ProvenancePopover provenance={provenance} label={label} valueText={valueText} nature={nature}>
        <button
          type="button"
          aria-describedby={descriptionId}
          className={clsx(
            'inline-flex items-baseline gap-1 rounded-chip tabular-nums text-inherit hover:bg-surface-2',
            size === 'hero' ? 'text-hero' : '',
          )}
        >
          <span>{valueText}</span>
          {compareText && (
            <span className="text-ink-3">
              ({PROVENANCE.thresholdWord} {compareText})
            </span>
          )}
          <ProvenanceBadge provenance={provenance} status={status} />
        </button>
      </ProvenancePopover>
      <span id={descriptionId} className="sr-only">
        {label ? `${label}. ` : ''}
        {`${PROVENANCE.nature}: ${NATURE_WORD[nature]}. ${PROVENANCE.origin}: ${provenance.origin}. ${PROVENANCE.period}: ${period}.`}
      </span>
    </span>
  );
}
