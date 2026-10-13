import { NATURE_WORD, PROVENANCE } from '@/copy/common';
import { formatPeriod } from '@/format';
import { displayNature } from './nature';
import { NatureMark } from './NatureMark';
import { useProvenanceScope } from './scope';

/**
 * One caption line under a section: "Numbers in this section: SYNTHETIC · <origin> · <period>"
 * (docs/ui-design.md 5.1). Renders nothing outside a ProvenanceScope.
 */
export function ProvenanceFooter() {
  const scope = useProvenanceScope();
  if (!scope) return null;
  const { provenance } = scope;
  const nature = displayNature(provenance);
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
