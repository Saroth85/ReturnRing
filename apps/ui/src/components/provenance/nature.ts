import type { DisplayNature, Provenance } from '@/api/models';

const ILLUSTRATIVE = /^(contract example:\s*)?illustrative/i;

/**
 * The nature shown for a provenance: the contract's nature, or illustrative when the origin says
 * "Illustrative" or a benchmark is `status: illustrative` (docs/ui-design.md 5.1, O-23).
 */
export function displayNature(provenance: Provenance, status?: string | null): DisplayNature {
  if (status === 'illustrative' || ILLUSTRATIVE.test(provenance.origin)) return 'illustrative';
  return provenance.nature;
}
