// Named constants that copy interpolates (docs/ui-design.md 10.2: no number literals in copy).
// Each one names its source. Values the contract states come from src/api/contract.gen.ts
// instead (LEGAL_MINIMUM_WINDOW_DAYS, AGENT_MAX_STEPS).

/** The GPU of the hackathon (spec section 2, AMD Developer Hackathon ACT III). */
export const GPU_NAME = 'AMD Instinct MI300X';
/** The same GPU in short labels ("Precomputed on MI300X", docs/ui-design.md 5.4). */
export const GPU_SHORT_NAME = 'MI300X';
/** The language model served by vLLM (spec 8.7). */
export const LLM_NAME = 'Gemma 4';
/** The legal basis of the guarantee path (spec section 3, legal frame; CLAUDE.md rule 4). */
export const GUARANTEE_DIRECTIVE = 'Directive (EU) 2019/771';
/** The review horizon of a restriction: a project assumption (US-607, ADR-15 section 4). */
export const REVIEW_HORIZON_DAYS = 90;
/** Seconds before a retry after the language model was unavailable (docs/ui-design.md 5.8). */
export const LLM_RETRY_SECONDS = 10;
/** The synthetic shop's generator seed (spec section 7, CLAUDE.md SEED=42). */
export const SYNTHETIC_SEED = 42;
/** The window of the order history and the return rate (spec 8.4 and 8.5: 12 months). */
export const HISTORY_MONTHS = 12;
