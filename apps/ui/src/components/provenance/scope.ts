import { createContext, useContext } from 'react';
import type { Provenance } from '@/api/models';

export interface ProvenanceScopeValue {
  provenance: Provenance;
  /** What the section's provenance covers (ADR-15 section 2 table), e.g. "orders and lines". */
  covers: string;
  /**
   * A ProvenanceFooter announces itself, so the scope can mark `data-has-footer`: only a scope with
   * a footer makes its plain numbers reachable (PROV-UI, UI11). Returns the unregister function.
   */
  registerFooter: () => () => void;
}

export const ProvenanceScopeContext = createContext<ProvenanceScopeValue | null>(null);

/** The enclosing ProvenanceScope, or null outside one. */
export const useProvenanceScope = () => useContext(ProvenanceScopeContext);
