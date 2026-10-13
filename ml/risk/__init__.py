"""Abuse risk per customer and per return request (US-204, spec 8.4; tests MOD-04 to MOD-08,
FAIR-01 to FAIR-03).

- features: the feature sets of the ablation A0..A4 and the design matrices (no customer value,
  no forbidden feature, every input known at the decision time);
- split: the grouped, time-ordered split shared by every model (MOD-07);
- rules: A0, the returnguard-like rule baseline;
- model: XGBoost with isotonic calibration and native TreeSHAP contributions;
- explain: the fixed feature -> phrase table (Italian and English) of the top 3 contributions;
- metrics: AUPRC, precision at k, false-positive rates, the threshold at a target precision;
- fairness: FAIR-02 and FAIR-03 (offline: reads the ground truth);
- run and report: the offline training and evaluation job behind docs/results/us-204-risk.md.

Labels come from the synthetic ground truth (truth/labels): wardrober and promo_ring are 1,
normal, size and shared_benign are 0. Only offline jobs read them (rule 12 as amended by ADR-13).
"""
