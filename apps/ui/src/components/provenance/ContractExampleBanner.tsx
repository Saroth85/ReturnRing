import { Info } from 'lucide-react';
import { API_MODE } from '@/api/adapters';
import { PROVENANCE } from '@/copy/common';
import { useContractOriginSeen } from './stores';

/**
 * A thin line under the mode banner whenever the figures come from the contract examples
 * (origins starting "Contract example:"), so nobody takes them for results (US-906).
 */
export function ContractExampleBanner() {
  const seen = useContractOriginSeen();
  if (!seen && API_MODE !== 'mock') return null;
  return (
    <p
      className="flex items-center gap-2 border-b border-border bg-surface-2 px-4 py-1 text-caption text-ink-2"
      data-contract-example-banner
    >
      <Info aria-hidden="true" size={14} />
      {PROVENANCE.contractExamples}
    </p>
  );
}
