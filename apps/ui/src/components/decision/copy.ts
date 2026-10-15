// Strings of the decision components (docs/ui-design.md 5.3), in English. No number literals:
// numbers come from data or from named constants (docs/ui-design.md 10.2).
import type { LeverId, LeverScope } from '@/domain/levers';

type ByValue = { true: string; false: string };

export const LEVER_COPY = {
  window: (days: number) => `Return window ${days} days`,
  legalMinimum: 'legal minimum',
  label: {
    free_return_shipping: { true: 'Free return shipping', false: 'Return shipping paid by the customer' },
    instant_refund: { true: 'Instant refund', false: 'Refund within the legal terms' },
    free_size_exchange: { true: 'Free size exchange', false: 'No free size exchange' },
    coupons_enabled: { true: 'Coupons available', false: 'Coupons not available' },
    cluster_coupons_disabled: { true: 'Coupons paused for the linked accounts', false: 'Coupons not paused for the linked accounts' },
    transparency_notice: { true: 'Return-rate notice in the message', false: 'No return-rate notice' },
    size_advice: { true: 'Size advice at checkout', false: 'No size advice at checkout' },
    priority_size_exchange: { true: 'Priority size exchange', false: 'No priority size exchange' },
  } as Record<Exclude<LeverId, 'return_window_days'>, ByValue>,
  scope: {
    future_orders: 'Future orders',
    future_orders_cluster: 'Future orders of the linked accounts',
    all_orders: 'All orders (a concession)',
    current_order: 'This order at checkout',
    communication: 'Message',
  } as Record<LeverScope, string>,
  restrictive: 'restrictive',
  moreGenerous: 'more generous',
  noLevers: 'No change to terms',
  listLabel: 'Levers',
};

export const TERMS_COPY = {
  row: {
    return_window_days: 'Return window',
    free_return_shipping: 'Return shipping',
    instant_refund: 'Instant refund',
    free_size_exchange: 'Size exchange',
    coupons_enabled: 'Coupons',
    cluster_coupons_disabled: 'Cluster coupons',
    transparency_notice: 'Return-rate notice',
    size_advice: 'Size advice',
    priority_size_exchange: 'Priority size exchange',
  } as Record<LeverId, string>,
  days: (days: number) => `${days} days`,
  value: {
    free_return_shipping: { true: 'free', false: 'paid by the customer' },
    instant_refund: { true: 'yes', false: 'no, refund within the legal terms' },
    free_size_exchange: { true: 'free', false: 'not free' },
    coupons_enabled: { true: 'on', false: 'off' },
    cluster_coupons_disabled: { true: 'paused for the linked accounts', false: 'not paused' },
    transparency_notice: { true: 'in this message', false: 'none' },
    size_advice: { true: 'at checkout', false: 'none' },
    priority_size_exchange: { true: 'yes', false: 'no' },
  } as Record<Exclude<LeverId, 'return_window_days'>, ByValue>,
  /** Around the count of linked accounts (LinkedAccountsCount, with its provenance). */
  pausedForBefore: 'paused for',
  pausedForAfter: 'linked accounts',
  change: { less: 'less generous', more: 'more generous', same: 'unchanged' },
  term: 'Term',
  thisOrder: 'This order',
  snapshot: 'snapshot',
  futureOrders: 'Future orders',
  changeHeader: 'Change',
  caption: 'Terms of this order against the terms for future orders',
};

export const STATE_COPY = {
  proposed: 'Proposed',
  versionOf: (version: number, total: number) => `v${version} of ${total}`,
  version: (version: number) => `v${version}`,
  approvedBy: 'Approved by',
  overriddenBy: 'Overridden by',
  rejectedBy: 'Rejected by',
  liftedBy: 'Lifted by',
  rejected: 'Proposal rejected (the return proceeds)',
  supersededBy: 'Superseded by',
};

export const RESTRICTION_COPY = {
  needsApproval: "Restrictive · needs a person's approval · review",
  inForce: 'In force for future orders · review',
  lapsedOn: 'Lapsed on',
  lapsedTail: (canRenew: boolean) =>
    canRenew ? ': no longer applies to new orders. Renew or lift.' : ': no longer applies to new orders. A person reviews it.',
  supersededBy: 'Superseded by',
  liftedOn: 'Restriction lifted on',
  by: 'by',
  appliesItself: 'Applies by itself · logged',
  benefitInForce: 'In force for future orders · logged',
  notInForce: 'Not in force',
  rejected: 'Proposal rejected: no change to terms',
  noChange: 'No change to terms',
};

export const STATEMENT_COPY = {
  label: { OBSERVED: 'Observed', SCORE: 'Score contribution', HYPOTHESIS: 'To verify' } as Record<
    'OBSERVED' | 'SCORE' | 'HYPOTHESIS',
    string
  >,
  tip: {
    OBSERVED: 'Observed data of the synthetic shop',
    SCORE: 'A contribution to the score, not a cause',
    HYPOTHESIS: 'A hypothesis for a person to verify',
  } as Record<'OBSERVED' | 'SCORE' | 'HYPOTHESIS', string>,
};

export const CHAIN_COPY = {
  label: 'How this decision was made',
  models: 'Models score',
  notScoredGuarantee: 'Not scored (guarantee path)',
  notScoredInsufficient: 'Not scored (not enough evidence)',
  rules: 'Rules decide',
  guard: 'Legal guard checked',
  person: 'A person approves',
  pending: 'pending',
  notRequired: 'not required: logged',
  by: (word: string, who: string) => `${word} by ${who}`,
  words: { approved: 'approved', overridden: 'overridden', rejected: 'rejected' } as Record<string, string>,
  versions: 'Versions',
  rulesVersion: 'rules',
};

export const APPROVER_COPY = {
  at: '·',
};
