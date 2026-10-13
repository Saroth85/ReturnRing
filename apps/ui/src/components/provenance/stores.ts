// Provenance mode (`P`, docs/ui-design.md 3.4) and the contract-example detector behind
// ContractExampleBanner (5.1).
import { createStore, useStore } from '@/api/store';

export const provenanceModeStore = createStore<boolean>(false);
export const useProvenanceMode = () => useStore(provenanceModeStore);
export const toggleProvenanceMode = () => provenanceModeStore.set((on) => !on);

export const CONTRACT_EXAMPLE_PREFIX = 'Contract example:';
export const contractOriginStore = createStore<boolean>(false);
export const useContractOriginSeen = () => useStore(contractOriginStore);

/** Called by provenance components: a figure from the contract examples was shown. */
export function noteOrigin(origin: string): void {
  if (origin.startsWith(CONTRACT_EXAMPLE_PREFIX) && !contractOriginStore.get()) contractOriginStore.set(true);
}
