// Named limits of the agent area (docs/ui-design.md 10.2: no number literals in copy). Each one
// names its source. The step limit and the tool list come from the contract (AGENT_MAX_STEPS,
// AGENT_TOOLS in src/api/contract.gen.ts).

/** Seconds the question input stays disabled after a 429 rate_limited (docs/ui-design.md 7, 10.1). */
export const RATE_LIMIT_COOLDOWN_SECONDS = 10;

/**
 * AgentAskRequest.question maxLength (contracts/json/api/agent_ask_request.schema.json); a test
 * compares it with the schema, since contract.gen.ts does not export string limits yet.
 */
export const QUESTION_MAX_LENGTH = 500;

/**
 * The width from which the case card has two columns and the evidence column stays visible next
 * to the 440 px drawer (docs/ui-design.md 4.5). Narrower, a cited ref closes the drawer first.
 */
export const WIDE_LAYOUT_QUERY = '(min-width: 1280px)';

/** From this width the drawer also starts under the top bar (docs/ui-design.md 4.5 breakpoints). */
export const HEADER_OFFSET_QUERY = '(min-width: 1024px)';

/** Phone-sized viewports (docs/ui-design.md 4.5: under 640 px, readable, not optimised). */
export const COMPACT_LAYOUT_QUERY = '(max-width: 639px)';
