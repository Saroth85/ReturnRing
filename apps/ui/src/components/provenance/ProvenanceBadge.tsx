import type { Provenance } from '@/api/models';
import { NATURE_WORD } from '@/copy/common';
import { formatPeriod } from '@/format';
import { displayNature } from './nature';
import { NatureMark } from './NatureMark';
import { useProvenanceScope } from './scope';
import { useProvenanceMode } from './stores';

interface ProvenanceBadgeProps {
  provenance: Provenance;
  /** Always print the nature word. */
  showWord?: boolean;
  /** A benchmark status: `illustrative` shows the illustrative mark. */
  status?: string | null;
}

/**
 * The nature of a figure (docs/ui-design.md 5.1): the glyph always; the word when asked, when it
 * differs from the enclosing ProvenanceScope, or in provenance mode (`P`), which adds the period.
 */
export function ProvenanceBadge({ provenance, showWord = false, status }: ProvenanceBadgeProps) {
  const scope = useProvenanceScope();
  const provenanceMode = useProvenanceMode();
  const nature = displayNature(provenance, status);
  const differsFromScope = scope !== null && displayNature(scope.provenance) !== nature;
  const word = showWord || differsFromScope || provenanceMode;
  return (
    <span className="inline-flex items-center gap-1 text-micro uppercase text-ink-2" data-provenance-badge={nature}>
      <NatureMark nature={nature} decorative={word} />
      {word && <span>{NATURE_WORD[nature]}</span>}
      {provenanceMode && (
        <span className="normal-case tracking-normal font-normal text-ink-3">
          · {formatPeriod(provenance.period_start, provenance.period_end)}
        </span>
      )}
    </span>
  );
}
