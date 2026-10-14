// Lever labels for records (`lever_ids` in the "id:value" form): the table of docs/ui-design.md
// 5.3, mirroring contracts/levers.py. The operator area's src/domain/levers.ts is the owner of the
// full LeverChip; this is the read-only text the audit log and the reviews list need.
import { LEGAL_MINIMUM_WINDOW_DAYS } from '@/api/contract.gen';

/** The return window of the Navigli base policy (spec 4); a shorter window is less generous. */
export const BASE_RETURN_WINDOW_DAYS = 30;

export interface LeverText {
  id: string;
  label: string;
  /** A less generous term: what needs a person's attention (rule 5). */
  restrictive: boolean;
}

const FIXED: Record<string, { label: string; restrictive: boolean }> = {
  'free_return_shipping:false': { label: 'Return shipping paid by the customer', restrictive: true },
  'free_return_shipping:true': { label: 'Free return shipping', restrictive: false },
  'instant_refund:false': { label: 'Refund within the legal terms', restrictive: true },
  'instant_refund:true': { label: 'Instant refund', restrictive: false },
  'free_size_exchange:true': { label: 'Free size exchange', restrictive: false },
  'coupons_enabled:false': { label: 'Coupons not available', restrictive: true },
  'coupons_enabled:true': { label: 'Coupons available', restrictive: false },
  'cluster_coupons_disabled:true': { label: 'Coupons paused for the linked accounts', restrictive: true },
  'transparency_notice:true': { label: 'Return-rate notice in the message', restrictive: true },
  'size_advice:true': { label: 'Size advice at checkout', restrictive: false },
  'priority_size_exchange:true': { label: 'Priority size exchange', restrictive: false },
};

/** "Return window 14 days (legal minimum)", "Refund within the legal terms", or the raw id. */
export function leverText(leverId: string): LeverText {
  const fixed = FIXED[leverId];
  if (fixed) return { id: leverId, ...fixed };
  const [name, value] = leverId.split(':');
  if (name === 'return_window_days' && value && /^\d+$/.test(value)) {
    const days = Number(value);
    const minimum = days === LEGAL_MINIMUM_WINDOW_DAYS ? ' (legal minimum)' : '';
    return { id: leverId, label: `Return window ${days} days${minimum}`, restrictive: days < BASE_RETURN_WINDOW_DAYS };
  }
  return { id: leverId, label: leverId, restrictive: false };
}
