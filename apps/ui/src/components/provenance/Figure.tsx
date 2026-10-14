import clsx from 'clsx';
import { useEffect, useId } from 'react';
import type { Metric } from '@/api/models';
import { NATURE_WORD, PROVENANCE } from '@/copy/common';
import { formatDistinct, formatPeriod } from '@/format';
import { displayNature } from './nature';
import { ProvenanceBadge } from './ProvenanceBadge';
import { ProvenancePopover } from './ProvenancePopover';
import { noteOrigin, useProvenanceMode } from './stores';

export interface FigureProps {
  metric: Metric;
  /** What the number is; shown in the popover and read by screen readers. */
  label?: string;
  /** "+€123.50" and "−€4.40" for gains and costs. */
  signed?: boolean;
  /**
   * A threshold: prints "0.87 (threshold 0.60)", the threshold as its own figure with its own
   * provenance (UI6), both with enough digits to tell them apart (UI5).
   */
  compareTo?: Metric;
  /** A value this one is compared with elsewhere on screen: print enough digits to differ (UI5). */
  distinctFrom?: number;
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
export function Figure({ metric, label, signed = false, compareTo, distinctFrom, size = 'default', status, className }: FigureProps) {
  const descriptionId = useId();
  const provenanceMode = useProvenanceMode();
  const { provenance } = metric;
  const nature = displayNature(provenance, status);
  const against = compareTo?.value ?? distinctFrom;
  const valueText = formatDistinct(metric.value, against, metric.unit, { signed });
  const period = formatPeriod(provenance.period_start, provenance.period_end);

  useEffect(() => noteOrigin(provenance.origin), [provenance.origin]);

  return (
    <span
      data-figure
      data-nature={nature}
      data-unit={metric.unit}
      className={clsx('relative inline-flex flex-wrap items-baseline gap-x-1', className)}
    >
      <ProvenancePopover provenance={provenance} label={label} valueText={valueText} nature={nature}>
        <button
          type="button"
          aria-describedby={descriptionId}
          className={clsx(
            'rounded-chip tabular-nums text-inherit hover:bg-surface-2',
            // Provenance mode puts the nature and the period under the value, so a figure never
            // squeezes its neighbours or scrolls the page sideways (UI21).
            provenanceMode ? 'inline-flex flex-col items-start' : 'inline-flex items-baseline gap-1',
            size === 'hero' ? 'text-hero' : '',
          )}
        >
          {/* The sign and the amount never break apart (UI21). */}
          <span className="whitespace-nowrap">{valueText}</span>
          <ProvenanceBadge provenance={provenance} status={status} />
        </button>
      </ProvenancePopover>
      {compareTo && (
        <span className="inline-flex items-baseline whitespace-nowrap text-ink-3" data-compare>
          ({PROVENANCE.thresholdWord}&nbsp;
          <Figure metric={compareTo} label={label ? `${label} · ${PROVENANCE.thresholdWord}` : PROVENANCE.thresholdWord} distinctFrom={metric.value} />)
        </span>
      )}
      <span id={descriptionId} className="sr-only">
        {label ? `${label}. ` : ''}
        {`${PROVENANCE.nature}: ${NATURE_WORD[nature]}. ${PROVENANCE.origin}: ${provenance.origin}. ${PROVENANCE.period}: ${period}.`}
      </span>
    </span>
  );
}
