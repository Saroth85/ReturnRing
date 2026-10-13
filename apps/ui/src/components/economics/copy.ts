// Strings of the economics components (docs/ui-design.md 5.6, 10.2). English, no number literals;
// "net benefit (simulated), under explicit assumptions", never savings.
import type { ComponentId, SensitivityId } from '@/sim';

export const ECONOMICS = {
  component: {
    logistics_costs_avoided: 'Logistics costs avoided',
    undue_discounts_avoided: 'Undue discounts avoided',
    time_saved_value: 'Value of the time saved',
    incremental_margin: 'Incremental margin',
    incentives: 'Incentives and extra benefits',
    system_cost: 'System and operations',
    error_cost: 'Errors and repairs',
    abandonment_margin_lost: 'Margin lost to abandonment',
  } satisfies Record<ComponentId, string>,
  /** The letter of each component in the formula of spec 8.10. */
  formulaLetter: {
    logistics_costs_avoided: 'C',
    undue_discounts_avoided: 'C',
    time_saved_value: 'T',
    incremental_margin: 'M',
    incentives: 'I',
    system_cost: 'S',
    error_cost: 'E',
    abandonment_margin_lost: 'A',
  } satisfies Record<ComponentId, string>,
  total: 'Net benefit',
  refundNote: 'The refund owed to the customer is never counted as a saving.',
  negative: 'Net benefit is negative under these assumptions',
  increase: 'adds to the net benefit',
  decrease: 'subtracts from the net benefit',
  zero: 'no effect',
  waterfallLabel: 'Net benefit, component by component',
  scenarioShort: {
    base: 'Base',
    logistics_halved: 'Logistics halved',
    abandonment_doubled: 'Abandonment doubled',
    both: 'Both',
  } satisfies Record<SensitivityId, string>,
  scenarioLong: {
    base: 'the stored assumptions',
    logistics_halved: 'logistics cost per return halved',
    abandonment_doubled: 'abandonment margin doubled',
    both: 'logistics halved and abandonment doubled',
  } satisfies Record<SensitivityId, string>,
  label: {
    scenario: 'Scenario:',
    from: 'from',
    decision: 'Decision',
    alternative: 'alternative:',
    kpiWeek: 'KPI week of',
    stored: 'Assumptions',
    basedOn: 'based on',
  },
  alternative: {
    proposal: 'proposal',
    current_terms: 'current terms',
    size_exchange: 'size exchange',
    verification: 'verification',
    product_report: 'product report',
  } as Record<string, string>,
};
