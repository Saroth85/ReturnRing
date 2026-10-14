import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { Provenance } from '@/api/models';
import { displayNature } from './nature';
import { ProvenanceScopeContext } from './scope';
import { noteOrigin } from './stores';

interface ProvenanceScopeProps {
  provenance: Provenance;
  /** What the section's provenance covers (ADR-15 section 2 table). */
  covers: string;
  children: ReactNode;
  className?: string;
}

/**
 * A section whose provenance covers its plain numbers (a price, a link weight, a SHAP value):
 * PlainNumber and Money work only inside one, and ProvenanceBadge prints the word when a figure
 * differs from it (docs/ui-design.md 5.1). The scope needs a ProvenanceFooter (full or compact)
 * for its numbers to carry a reachable origin, period and nature: `data-has-footer` says it has one.
 */
export function ProvenanceScope({ provenance, covers, children, className }: ProvenanceScopeProps) {
  const [footers, setFooters] = useState(0);
  const registerFooter = useCallback(() => {
    setFooters((n) => n + 1);
    return () => setFooters((n) => n - 1);
  }, []);
  const value = useMemo(() => ({ provenance, covers, registerFooter }), [provenance, covers, registerFooter]);
  useEffect(() => noteOrigin(provenance.origin), [provenance.origin]);
  return (
    <ProvenanceScopeContext.Provider value={value}>
      <div
        data-provenance-scope
        data-has-footer={footers > 0 ? '' : undefined}
        data-nature={displayNature(provenance)}
        data-covers={covers}
        className={className}
      >
        {children}
      </div>
    </ProvenanceScopeContext.Provider>
  );
}
