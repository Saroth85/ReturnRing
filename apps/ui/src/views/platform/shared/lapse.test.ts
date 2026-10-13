// T-SERVER-STATE for the platform screens: a lapse comes from the API's fields only, never from the
// client clock (docs/ui-design.md P9, 11.4).
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CheckoutEvaluateResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { serverLapse } from './lapse';

const marco = readExample<CheckoutEvaluateResponse>('checkout_evaluate_response.marco.json');

afterEach(() => {
  vi.useRealTimers();
});

describe('serverLapse', () => {
  it('is unknown while the contract carries no lapse fields (contract 1.1.0)', () => {
    expect(serverLapse(marco)).toEqual({ kind: 'unknown' });
  });

  it('never derives a lapse from the client clock', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2027-06-01T00:00:00Z'));
    expect(serverLapse(marco)).toEqual({ kind: 'unknown' });
  });

  it('reads in_force and review_due once the API sends them', () => {
    expect(serverLapse({ ...marco, in_force: true, review_due: false })).toEqual({ kind: 'in_force' });
    expect(serverLapse({ ...marco, in_force: false })).toEqual({ kind: 'lapsed' });
    expect(serverLapse({ ...marco, review_due: true })).toEqual({ kind: 'lapsed' });
  });

  it('ignores fields of the wrong type', () => {
    expect(serverLapse({ ...marco, in_force: 'no', review_due: 'yes' })).toEqual({ kind: 'unknown' });
  });
});
