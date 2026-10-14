// Named constants of the platform area (docs/ui-design.md 10.2: no number literals in copy). Each
// one names its source. Values the contract states come from src/api/contract.gen.ts instead.
import { PHOTO_BASE64_MAX_LENGTH } from '@/api/contract.gen';

/** Debounce of POST /api/checkout/evaluate after a cart change (docs/ui-design.md 9.1). */
export const EVALUATE_DEBOUNCE_MS = 300;

/** US-403: the checkout answers in less than one second (spec section 5, E4). */
export const CHECKOUT_TARGET_MS = 1000;

/**
 * Client limit on an inspection photo, base64 in JSON (docs/ui-design.md 9.2, open question O-26):
 * the largest file whose base64 fits PhotoUpload.data_base64 maxLength. Base64 writes 4 characters
 * for every 3 bytes, so the limit is 3 bytes per whole group of 4 characters (5,250,000 bytes for
 * 7,000,000 characters).
 */
export const PHOTO_MAX_BYTES = Math.floor(PHOTO_BASE64_MAX_LENGTH / 4) * 3;
/** The same limit in megabytes (10^6 bytes) for the copy, rounded down to two decimals. */
export const PHOTO_MAX_MB = Math.floor(PHOTO_MAX_BYTES / 10_000) / 100;

/** The media types POST /api/inspections accepts (PhotoUpload.media_type, ADR-15). */
export const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp'] as const;

/** One model on one GPU (spec 9, architecture): what runs on the MI300X and what on the CPU. */
export const ARCHITECTURE = {
  gpu: ['vLLM · Gemma 4 31B-it', 'XGBoost (AMD build)', 'GraphSAGE'],
  cpu: ['API', 'UI', 'PostgreSQL'],
} as const;

/** The art. 14(2) reference of the guarantee-path note (Directive 2011/83/EU, docs/ui-design.md 9.2). */
export const DIMINISHED_VALUE_ARTICLE = 'art. 14(2)';

/** The golden cases of the pitch (spec 16, docs/ui-design.md 12): Giulia, Marco, Rossi twice, Elena. */
export const GOLDEN_RETURN_IDS = ['RET-000101', 'RET-000102', 'RET-000103', 'RET-000104', 'RET-000105'] as const;
/** Marco's case: proposed at its first version before the pitch (docs/ui-design.md 9.5). */
export const MARCO_RETURN_ID = 'RET-000102';
export const MARCO_FIRST_VERSION = 1;
/** Rossi's card variant: its latest decision is still the first one before the pitch. */
export const ROSSI_RETURN_ID = 'RET-000104';
export const ROSSI_FIRST_DECISION_ID = 'DEC-000104-1';
/** The assumption set of beat 5 (ECO-01: +€368, docs/ui-design.md 12). */
export const DEMO_ASSUMPTIONS_VERSION = 'assumptions-v1';
/** The golden cards and the matrix load in under two seconds (docs/ui-design.md 9.5). */
export const PREFLIGHT_LOAD_TARGET_MS = 2000;
/** The second origin that serves the cached demo (docs/ui-design.md 9.5, failover). */
export const CACHED_ORIGIN: string | null = import.meta.env.VITE_CACHED_ORIGIN || null;

/** The real dataset behind the return model and its licence (spec section 7, CLAUDE.md rule 15). */
export const ASOS_DATASET = 'ASOS GraphReturns, CC BY 4.0';
