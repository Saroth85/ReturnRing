// Checkout presets (docs/ui-design.md 9.1): the carts of the contract's request examples
// (checkout_evaluate_request.giulia.json and .marco.json), which the mock routes on customer_id.
import type { CheckoutEvaluateRequest } from '@/api/models';

export const PRESETS: Record<string, CheckoutEvaluateRequest> = {
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
};
