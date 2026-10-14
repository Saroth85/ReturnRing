"""Services of the manager's area (view 3; E6: US-601..US-607, US-208, US-402, US-604):

- matrix: every scored customer as a risk-value point, the thresholds and the quadrant totals;
- assumptions: the stored, append-only assumption versions of the economic simulator;
- evaluation: the offline evaluation report of the serving snapshot (FAIR-02), for the fairness
  report and the false positives of the KPIs (rule 12: the API never reads labels);
- kpi: the KPIs of a week from the decisions and the audit log;
- records: the audit log, its CSV export and the simulated outbox;
- catalog: the product anomalies, the catalogue tasks and their follow-up;
- coupons: the general coupon rule, its versions and the reversal of a cluster application.

Nothing here decides or predicts (rule 1): figures come from the serving snapshot, the
database and policy.simulator, each with its provenance (rule 11).
"""
