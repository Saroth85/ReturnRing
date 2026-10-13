"""Expected return probability of an order line (spec 8.1, 8.2; rule 8; US-206, MOD-09).

Two predictors on the same line features, with different roles:

- the product/cohort reference: p_l from the product, the category, the price, the size range and
  the order context only, never the customer's own history. It is the only model allowed in the
  return residual R_c, z_c (rule 8);
- the personalized predictor: the same features plus the customer's own past outcomes, for the
  checkout (M1) and for the MOD-09 comparison. It absorbs the very anomaly the residual measures.

features: the design matrices; model: XGBoost with isotonic calibration, a smoothed fallback,
cross-fitting by customer; metrics: calibration metrics; report: the offline evaluation that
rewrites docs/results/customer-quantities.md.
"""
