import { TriangleAlert } from 'lucide-react';
import { MODE } from '@/copy/common';
import { useContractCheck } from './useContractCheck';

/**
 * A line under the mode banner when the API serves another major or minor contract version than
 * the one the UI types were generated from (UI1): fields may have moved, so a screen can fail.
 */
export function ContractVersionNotice() {
  const check = useContractCheck();
  if (check.status !== 'mismatch' || !check.server) return null;
  return (
    <div
      role="alert"
      data-contract-mismatch={check.server}
      className="flex flex-wrap items-center gap-x-2 border-t border-attn-border bg-attn-bg px-4 py-1 text-caption text-attn-fg"
    >
      <TriangleAlert aria-hidden="true" size={14} />
      <strong className="font-semibold">{MODE.contractMismatchTitle}</strong>
      <span data-constant>{MODE.contractMismatch(check.server, check.ui)}</span>
    </div>
  );
}
