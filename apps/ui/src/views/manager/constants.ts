// Named values the manager screens interpolate (docs/ui-design.md 10.2: no number literals in copy).
// Each names its source; they render inside `data-constant` so the provenance scan knows them.
import { LEGAL_MINIMUM_WINDOW_DAYS } from '@/api/contract.gen';
import { BASE_RETURN_WINDOW_DAYS } from '@/components/records';

/** Return window of Q1 (spec 8.6 lever table: "60 giorni"). */
export const Q1_WINDOW_DAYS = 60;
/** Return window of Q2 and Q3: the Navigli base (spec 4, spec 8.6). */
export const STANDARD_WINDOW_DAYS = BASE_RETURN_WINDOW_DAYS;
/** Return window of Q4: the legal minimum (Directive 2011/83/EU art. 9; contract lever schema). */
export const Q4_WINDOW_DAYS = LEGAL_MINIMUM_WINDOW_DAYS;

/**
 * The demo cases with fixed ids (spec 16, docs/ui-design.md 12): never accuracy, only the customers
 * the pitch talks about, annotated on the matrix when "Demo cases" is on.
 */
export const DEMO_CASES = {
  customers: [
    { id: 'C-GIULIA', name: 'Giulia', note: 'Giulia: same return rate as Marco, no linked accounts' },
    {
      id: 'C-ELENA',
      name: 'Elena',
      note: 'Elena: her open return follows the guarantee path and is not scored; this is her position as a customer',
    },
  ],
  clusters: ['CL-000417', 'CL-000233', 'CL-000588'],
} as const;

/** Sort keys of the quadrant customer list. */
export type CustomerSort = 'name' | 'risk' | 'value' | 'returns' | 'coupons';

/** Percentile labels of the decision time (KpiResponse decision_time_p50, decision_time_p95). */
export const P50_LABEL = 'p50';
export const P95_LABEL = 'p95';
