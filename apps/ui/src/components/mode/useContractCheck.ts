import { compareContract, contractVersionOf, type ContractCheck } from '@/api/contractVersion';
import { useHealthz, useReadyz } from '@/api/queries';

/** The contract the API reports (readyz, or any health body that carries it) against the UI's (UI1). */
export function useContractCheck(): ContractCheck {
  const readyz = useReadyz();
  const healthz = useHealthz();
  return compareContract(contractVersionOf(readyz.data) ?? contractVersionOf(healthz.data));
}
