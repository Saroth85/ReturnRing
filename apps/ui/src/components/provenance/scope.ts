import { createContext, useContext } from 'react';
import type { Provenance } from '@/api/models';

export interface ProvenanceScopeValue {
  provenance: Provenance;
  /** What the section's provenance covers (ADR-15 section 2 table), e.g. "orders and lines". */
  covers: string;
}

export const ProvenanceScopeContext = createContext<ProvenanceScopeValue | null>(null);

/** The enclosing ProvenanceScope, or null outside one. */
export const useProvenanceScope = () => useContext(ProvenanceScopeContext);
