import { Info } from 'lucide-react';
import { useLayoutEffect } from 'react';
import { NATURE_WORD, PROVENANCE } from '@/copy/common';
import { formatPeriod } from '@/format';
import { displayNature } from './nature';
import { NatureMark } from './NatureMark';
import { ProvenancePopover } from './ProvenancePopover';
import { useProvenanceScope } from './scope';

interface ProvenanceFooterProps {
  /**
   * One short inline mark for a dense header or line ("◇ Synthetic ⓘ"): the nature word, and the
   * origin and period behind a popover button. The default is the full caption line.
   */
  compact?: boolean;
}

/**
 * One caption line under a section: "Numbers in this section: SYNTHETIC · <origin> · <period>"
 * (docs/ui-design.md 5.1), or its compact inline form. Renders nothing outside a ProvenanceScope.
 */
export function ProvenanceFooter({ compact = false }: ProvenanceFooterProps) {
  const scope = useProvenanceScope();
  const register = scope?.registerFooter;
  useLayoutEffect(() => register?.(), [register]);
  if (!scope) return null;
  const { provenance, covers } = scope;
  const nature = displayNature(provenance);
  if (compact) {
    return (
      <span className="inline-flex items-center gap-1 text-caption text-ink-3" data-provenance-footer="compact">
        <span className="inline-flex items-center gap-1 text-micro uppercase text-ink-2">
          <NatureMark nature={nature} decorative />
          {NATURE_WORD[nature]}
        </span>
        <ProvenancePopover provenance={provenance} label={PROVENANCE.compactLabel(covers)}>
          <button
            type="button"
            aria-label={PROVENANCE.compactButton(covers)}
            className="inline-flex h-6 w-6 items-center justify-center rounded-control text-ink-3 hover:bg-surface-2 hover:text-ink-1"
          >
            <Info aria-hidden="true" size={14} />
          </button>
        </ProvenancePopover>
      </span>
    );
  }
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-1 text-caption text-ink-3" data-provenance-footer>
      <span>{PROVENANCE.footerPrefix}</span>
      <span className="inline-flex items-center gap-1 text-micro uppercase text-ink-2">
        <NatureMark nature={nature} decorative />
        {NATURE_WORD[nature]}
      </span>
      <span>· {provenance.origin}</span>
      <span>· {formatPeriod(provenance.period_start, provenance.period_end)}</span>
    </p>
  );
}
