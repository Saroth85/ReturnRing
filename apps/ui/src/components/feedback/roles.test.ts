import { describe, expect, it } from 'vitest';
import { FEEDBACK } from '@/copy/common';
import { gateFor } from './roles';

describe('gateFor', () => {
  it('follows the roles of endpoints.json', () => {
    expect(gateFor('POST /api/returns/{return_id}/decision', 'operator', false)).toEqual({ allowed: true, reason: null });
    expect(gateFor('POST /api/returns/{return_id}/decision', 'warehouse', false).allowed).toBe(false);
  });

  it('disables every write in the read-only public demo, with the reason', () => {
    expect(gateFor('POST /api/returns/{return_id}/decision', 'operator', true)).toEqual({
      allowed: false,
      reason: FEEDBACK.readOnlyDemo,
    });
    expect(gateFor('PUT /api/simulator/assumptions', 'manager', true).allowed).toBe(false);
  });

  it('keeps the read-only agent and the checkout evaluation in the public demo, still by role', () => {
    expect(gateFor('POST /api/agent/ask', 'operator', true)).toEqual({ allowed: true, reason: null });
    expect(gateFor('POST /api/agent/ask', 'warehouse', true).allowed).toBe(false);
    expect(gateFor('POST /api/checkout/evaluate', 'operator', true)).toEqual({ allowed: true, reason: null });
    expect(gateFor('POST /api/checkout/confirm', 'operator', true).allowed).toBe(false);
  });
});
