/**
 * Mock scenarios the contract does not ship yet (open question O-1). Their bodies use the
 * ErrorResponse shape and say "UI fixture" so nobody mistakes them for contract examples.
 */
export const UI_FIXTURE_SCENARIOS = [
  'forbidden_role',
  'llm_unavailable',
  'rate_limited',
  'conflict',
  'empty',
  'draft_pending',
  'agent_error',
  // /readyz reports another contract minor version: the shell's mismatch notice (UI1).
  'contract_mismatch',
] as const;
