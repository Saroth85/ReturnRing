// Checkout presets (US-403, docs/ui-design.md 9.1): the carts of the demo reset, from GET
// /api/demo/cases (ADR-15 section 15). `giulia` holds the lines of Giulia's order, `ring` the
// returned item of the Marco case's account with an unredeemed coupon of the account when it has
// one; each request is sent as the API gives it. `linked` is the same item for another account of
// the case's linked accounts (its card's linked_accounts): once Marco is approved, the coupon pause
// reaches it with the neutral text that gives no cause (ADR-15 section 19). `?preset=marco` is the
// old name of the ring cart.
import { DEMO_CART_KEYS } from '@/api/contract.gen';
import type { CaseCard, DemoCart, DemoCartKey, Lang } from '@/api/models';
import type { Cart } from './checkout';

export type PresetId = DemoCartKey | 'linked';
export const PRESET_IDS: readonly PresetId[] = [...DEMO_CART_KEYS, 'linked'];

/** The old preset names that still open a cart: `marco` is the ring cart. */
const ALIASES: Record<string, PresetId> = { marco: 'ring' };

export function presetId(value: string | null): PresetId {
  if (value && ALIASES[value]) return ALIASES[value];
  return PRESET_IDS.find((id) => id === value) ?? 'giulia';
}

/** A fresh copy of a demo cart (the presenter edits it): a missing quantity is one, no coupon is null. */
export function presetCart(cart: DemoCart): Cart {
  const { request } = cart;
  return {
    customer_id: request.customer_id,
    lines: request.lines.map((l) => ({ variant_id: l.variant_id, size: l.size, quantity: l.quantity ?? 1 })),
    coupon_code: request.coupon_code ?? null,
  };
}

/**
 * The ring cart for another account of the case's linked accounts: the first account of the card
 * that is not the case customer, with no coupon (the code belongs to the case account). Null when
 * the card has no other account.
 */
export function linkedCart(ring: DemoCart, card: CaseCard | undefined): Cart | null {
  const other = card?.linked_accounts?.accounts.find((a) => !a.is_case_customer && a.customer_id !== ring.request.customer_id);
  if (!other) return null;
  return { ...presetCart(ring), customer_id: other.customer_id, coupon_code: null };
}

/** The language the shop shows the customer texts in (CustomerText, rule 14): Italian unless asked. */
export const SHOP_LANGS: readonly Lang[] = ['it', 'en'];
export function shopLang(value: string | null): Lang {
  return SHOP_LANGS.find((lang) => lang === value) ?? 'it';
}
