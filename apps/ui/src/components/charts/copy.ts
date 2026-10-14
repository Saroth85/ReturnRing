// Strings of the chart components (docs/ui-design.md 4.8, 5.6). English, no number literals.

export const CHARTS = {
  viewTable: 'View as table',
  viewChart: 'View as chart',
  week: 'Week',
  sales: 'Sales',
  returns: 'Returns',
  returnRate: 'Return rate',
  expected: 'Expected in the flagged week',
  flaggedWeek: 'Flagged week',
  correction: 'Correction done',
  beforeAfter: 'Before and after the correction',
  before: 'before',
  after: 'after',
  returnsTitle: 'Returns per week',
  rateTitle: 'Return rate per week',
  legendReturns: 'Bars: returns of the variant for this reason',
  legendExpected: 'Dashed marker: returns expected from the category base in the flagged week',
  legendCorrection: 'Shaded: weeks after the catalogue correction',
  noTrend: 'No weekly series for this variant yet.',
  trendLabel: (name: string) => `Trend of ${name}`,
};
