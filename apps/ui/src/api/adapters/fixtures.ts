/**
 * Mock scenarios the contract does not ship, layered on its examples by the mock router
 * (docs/ui-design.md 11.5). Their bodies say "UI fixture" so nobody mistakes them for contract
 * examples. The contract's own scenarios (cached, conflict, empty, rate_limited, read_only, ...)
 * come from contracts/examples/api/index.json.
 */
export const UI_FIXTURE_SCENARIOS = [
  // The case card while its draft is still being written (draft null, draft_status pending).
  'draft_pending',
  // The agent stream ends with an error event instead of an answer.
  'agent_error',
  // /readyz reports another contract minor version: the shell's mismatch notice (UI1).
  'contract_mismatch',
  // The service's clock stands past the refund deadlines of the proposals: the queue flags them,
  // a case shows the overdue banner, and a rejection or the standard terms send no message. (The
  // contract's `refund_overdue` scenario keeps the clock before the deadline and answers the
  // approval with 409: the server's clock passed it first.)
  'refund_deadline_passed',
] as const;
