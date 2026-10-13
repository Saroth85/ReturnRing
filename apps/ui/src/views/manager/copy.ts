// Manager area strings (docs/ui-design.md 8). English; no number literals.

export const MANAGER = {
  nav: {
    portfolio: 'Portfolio matrix',
    simulator: 'Simulator',
    kpi: 'Weekly KPIs',
    fairness: 'Fairness',
    products: 'Product issues',
    reviews: 'Reviews due',
    audit: 'Audit log',
  },
  titles: {
    portfolio: 'Risk score × customer value',
    simulator: 'Economic simulator',
    kpi: 'Weekly KPIs',
    fairness: 'Fairness report',
    products: 'Product issues',
    anomaly: 'Product issue',
    tasks: 'Catalogue tasks',
    reviews: 'Restrictions under review',
    audit: 'Audit log',
  },
  portfolio: { insufficient: 'Not placed: customers with not enough evidence (no risk score)' },
  simulator: {
    total: 'Net benefit (simulated)',
    refundNote: 'The refund owed to the customer is never counted as a saving.',
  },
  kpi: { netBenefit: 'Net benefit, simulated', realised: 'Not observed yet: realised benefit needs observed outcomes' },
  fairness: { threshold: 'Maximum gap threshold' },
  reviews: {
    note: 'A restriction lapses at its review date and stops applying to new orders. A person renews it with a new decision or lifts it. No label is permanent.',
    review: 'Review',
    empty: 'No restriction is due for review.',
  },
  audit: { banner: 'Append-only log. Human actions are never recorded as the system; every restrictive approval names a person and a review date.' },
};
