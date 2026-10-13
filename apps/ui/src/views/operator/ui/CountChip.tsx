import clsx from 'clsx';
import type { Provenance } from '@/api/models';
import { NATURE_WORD } from '@/copy/common';
import { displayNature, NatureMark } from '@/components/provenance';

/**
 * A count of records (returns in a view, links of a kind) with the provenance of the list it
 * counts, like the navigation badges: `data-figure`, the nature glyph, and the origin for
 * screen readers. Used where a full Figure button cannot nest (inside a tab or a chip).
 */
export function CountChip({ value, provenance, className }: { value: number; provenance: Provenance; className?: string }) {
  const nature = displayNature(provenance);
  return (
    <span
      data-figure
      data-nature={nature}
      title={`${NATURE_WORD[nature]} · ${provenance.origin}`}
      className={clsx('inline-flex items-center gap-1 rounded-full bg-surface-2 px-1.5 text-caption tabular-nums text-ink-1', className)}
    >
      {value}
      <NatureMark nature={nature} />
      <span className="sr-only">{` (${provenance.origin})`}</span>
    </span>
  );
}
