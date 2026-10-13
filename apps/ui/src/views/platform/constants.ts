// Named constants of the platform area (docs/ui-design.md 10.2: no number literals in copy). Each
// one names its source. Values the contract states come from src/api/contract.gen.ts instead.

/** Debounce of POST /api/checkout/evaluate after a cart change (docs/ui-design.md 9.1). */
export const EVALUATE_DEBOUNCE_MS = 300;

/** US-403: the checkout answers in less than one second (spec section 5, E4). */
export const CHECKOUT_TARGET_MS = 1000;

/** Client limit on an inspection photo, base64 in JSON (docs/ui-design.md 9.2, open question O-26). */
export const PHOTO_MAX_MB = 8;
export const PHOTO_MAX_BYTES = PHOTO_MAX_MB * 1024 * 1024;

/** The media types POST /api/inspections accepts (PhotoUpload.media_type, ADR-15). */
export const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp'] as const;

/** One model on one GPU (spec 9, architecture): what runs on the MI300X and what on the CPU. */
export const ARCHITECTURE = {
  gpu: ['vLLM · Gemma 4 31B-it', 'XGBoost (AMD build)', 'GraphSAGE'],
  cpu: ['API', 'UI', 'PostgreSQL'],
} as const;

/** The art. 14(2) reference of the guarantee-path note (Directive 2011/83/EU, docs/ui-design.md 9.2). */
export const DIMINISHED_VALUE_ARTICLE = 'art. 14(2)';
