// The golden cases by key (GET /api/demo/cases): one source for every demo flow, tolerant of a
// key the store does not hold and of no reset at all.
import { describe, expect, it } from 'vitest';
import type { DemoCasesResponse } from '@/api/models';
import { readExample } from '@/test/examples';
import { cartOf, caseOf, disputeOf, disputePath, goldenReturnIds, highlights, PITCH_CASE_KEYS } from './demoCases';

const cases = readExample<DemoCasesResponse>('demo_cases.json');

describe('demo cases', () => {
  it('finds each case and cart of the example by key', () => {
    expect(caseOf(cases, 'marco')).toMatchObject({ return_id: 'RET-000102', decision_id: 'DEC-000102-1', cluster_id: 'CL-000417' });
    expect(caseOf(cases, 'insufficient')?.return_id).toBe('RET-000109');
    expect(cartOf(cases, 'giulia')?.request.lines.map((l) => l.size)).toEqual(['M', 'L']);
    expect(cartOf(cases, 'ring')?.request).toMatchObject({ customer_id: 'C-MARCO-1', coupon_code: 'WELCOME-Z9K3' });
    expect(disputeOf(cases)).toMatchObject({ link_id: 'C-10233~C-20871', changes_decision: true });
  });

  it('lists the return ids of the pitch in its order', () => {
    expect(PITCH_CASE_KEYS).toEqual(['giulia', 'marco', 'rossi', 'rossi_card', 'elena']);
    expect(goldenReturnIds(cases)).toEqual(['RET-000101', 'RET-000102', 'RET-000103', 'RET-000104', 'RET-000105']);
    expect(goldenReturnIds(cases, ['insufficient', 'giulia'])).toEqual(['RET-000109', 'RET-000101']);
  });

  it('highlights the customers of Giulia and Elena and the clusters of the Marco and Rossi cases', () => {
    expect(highlights(cases)).toEqual({
      customers: [
        { key: 'giulia', customerId: 'C-GIULIA' },
        { key: 'elena', customerId: 'C-ELENA' },
      ],
      clusters: [
        { key: 'marco', clusterId: 'CL-000417' },
        { key: 'rossi', clusterId: 'CL-000233' },
        { key: 'rossi_card', clusterId: 'CL-000588' },
      ],
    });
  });

  it('links the dispute to its case with the link selected', () => {
    expect(disputePath(cases)).toBe('/returns/RET-000106?link=C-10233~C-20871');
  });

  it('takes a missing key, a missing dispute and no reset in its stride', () => {
    const small: DemoCasesResponse = {
      ...cases,
      cases: cases.cases.filter((c) => c.key !== 'rossi' && c.key !== 'elena'),
      dispute: null,
      carts: [],
    };
    expect(caseOf(small, 'rossi')).toBeNull();
    expect(cartOf(small, 'ring')).toBeNull();
    expect(goldenReturnIds(small)).toEqual(['RET-000101', 'RET-000102', 'RET-000104']);
    expect(highlights(small).customers.map((c) => c.key)).toEqual(['giulia']);
    expect(disputePath(small)).toBeNull();
    for (const none of [undefined, null]) {
      expect(caseOf(none, 'marco')).toBeNull();
      expect(goldenReturnIds(none)).toEqual([]);
      expect(highlights(none)).toEqual({ customers: [], clusters: [] });
      expect(disputePath(none)).toBeNull();
    }
  });

  it('names a cluster shared by two cases once', () => {
    const shared: DemoCasesResponse = {
      ...cases,
      cases: cases.cases.map((c) => (c.key === 'rossi_card' ? { ...c, cluster_id: 'CL-000233' } : c)),
    };
    expect(highlights(shared).clusters.map((c) => c.clusterId)).toEqual(['CL-000417', 'CL-000233']);
  });
});
