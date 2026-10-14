// Checkout presets (US-403, docs/ui-design.md 9.1): the carts of the contract's request examples
// (checkout_evaluate_request.giulia.json and .marco.json), which the mock routes on customer_id.
// Giulia is a Q1 customer; C-MARCO-1 is one account of the linked accounts of cluster CL-000417.
import type { Cart } from './checkout';

export const PRESETS = {
  giulia: {
    customer_id: 'C-GIULIA',
    lines: [
      { variant_id: 'V-DRS-2500', size: 'M', quantity: 1 },
      { variant_id: 'V-DRS-2500', size: 'L', quantity: 1 },
    ],
    coupon_code: null,
  },
  marco: {
    customer_id: 'C-MARCO-1',
    lines: [{ variant_id: 'V-JKT-2207', size: 'M', quantity: 1 }],
    coupon_code: 'WELCOME-Z9K3',
  },
} satisfies Record<string, Cart>;

export type PresetId = keyof typeof PRESETS;
export const PRESET_IDS = Object.keys(PRESETS) as PresetId[];

export function presetId(value: string | null): PresetId {
  return PRESET_IDS.find((id) => id === value) ?? 'giulia';
}

/** A fresh copy of a preset cart (the presenter edits it). */
export function presetCart(id: PresetId): Cart {
  const preset: Cart = PRESETS[id];
  return { ...preset, lines: preset.lines.map((l) => ({ ...l })) };
}
