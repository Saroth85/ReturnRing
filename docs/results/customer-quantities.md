# Customer features, residual, value and evidence: measured results

Stories US-205, US-206 and US-209 (tests UNIT-02, UNIT-03, MOD-06, MOD-08, MOD-09, MOD-12; decision D-15). Design and definitions: docs/customer-features.md.

Rewritten by `python -m ml.reference.report --data data --results docs/results/customer-quantities.md` from the data alone (no wall-clock value), so the same store gives the same file. Every number below is synthetic: measured on the Navigli Fashion store, whose behaviours and rules are design hypotheses calibrated on real ASOS aggregates. None is a measure on real customers.

- Main store origin: Navigli Fashion generator, calibrated on ASOS GraphReturns aggregates (calibration: ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0; split periods per the paper: train 2021-09-01..2021-10-31, test 2021-10-01..2021-11-30; calibration: aggregates of the train split by pipeline/ingest/asos_profile.py. Rates, counts and purchases per customer come from the train events (2021-09-01..2021-10-31); product types, prices, return rates and reason shares come from the train product table, whose aggregates equal the test table's for every shared variant, so their window is the test window too (ADR-10); hence the period 2021-09-01..2021-11-30; calibration nature real; sha256 f49ce3711edd; seed 42, scale 1, scenario main; store period 2025-10-01..2026-09-30); period 2025-10-01 to 2026-09-30; nature synthetic.
- OOD store origin: Navigli Fashion generator, calibrated on ASOS GraphReturns aggregates (calibration: ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0; split periods per the paper: train 2021-09-01..2021-10-31, test 2021-10-01..2021-11-30; calibration: aggregates of the train split by pipeline/ingest/asos_profile.py. Rates, counts and purchases per customer come from the train events (2021-09-01..2021-10-31); product types, prices, return rates and reason shares come from the train product table, whose aggregates equal the test table's for every shared variant, so their window is the test window too (ADR-10); hence the period 2021-09-01..2021-11-30; calibration nature real; sha256 f49ce3711edd; seed 42, scale 1, scenario ood; store period 2025-10-01..2026-09-30); nature synthetic.
- Code: features-v1, reference model v1, value value-v1-2026-10-13, evidence evidence-v1-2026-10-13.

## A. Reference model: calibration on the last three months (US-206, MOD-06)

Origin: main store, lines ordered 2026-07-01 to 2026-09-30 and settled by 2026-10-01 00:00Z. Period: July to September 2026. Nature: synthetic. Trained at 2026-07-01 00:00Z on the 125,381 lines settled by then (each priced with what was known when it was ordered); validation 10% of the customers; k = 20 for the smoothed rates; reference early-stopped at 90 rounds, personalized at 112.

| Predictor | Lines | Observed rate | Mean p | Log loss | Brier | ECE | AUC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Smoothed variant rate (baseline) | 34,360 | 0.268 | 0.275 | 0.5398 | 0.1797 | 0.0106 | 0.684 |
| Reference, XGBoost before isotonic | 34,360 | 0.268 | 0.277 | 0.5292 | 0.1759 | 0.0137 | 0.707 |
| **Reference, XGBoost + isotonic (used by the residual)** | 34,360 | 0.268 | 0.283 | 0.5308 | 0.1765 | 0.0191 | 0.706 |
| Personalized, XGBoost before isotonic | 34,360 | 0.268 | 0.268 | 0.5169 | 0.1712 | 0.0085 | 0.726 |
| Personalized, XGBoost + isotonic (checkout) | 34,360 | 0.268 | 0.271 | 0.5180 | 0.1714 | 0.0120 | 0.725 |

Out-of-distribution store (US-105, MOD-12), a separate row whatever the result: the models trained on the main store at the train end price the OOD lines; the OOD history counts come from the OOD store itself; nothing is trained on it. Same period and nature (synthetic).

| Predictor | Lines | Observed rate | Mean p | Log loss | Brier | ECE | AUC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OOD: smoothed variant rate | 34,858 | 0.265 | 0.273 | 0.5404 | 0.1800 | 0.0110 | 0.676 |
| OOD: reference, XGBoost + isotonic | 34,858 | 0.265 | 0.277 | 0.5304 | 0.1764 | 0.0159 | 0.700 |
| OOD: personalized, XGBoost + isotonic | 34,858 | 0.265 | 0.271 | 0.5155 | 0.1705 | 0.0087 | 0.724 |

Reliability of the reference (main store, last three months, equal-width bins of p):

| p bin | Lines | Mean p | Observed rate |
| --- | --- | --- | --- |
| 0.0-0.1 | 3,623 | 0.067 | 0.085 |
| 0.1-0.2 | 6,673 | 0.155 | 0.136 |
| 0.2-0.3 | 8,898 | 0.221 | 0.205 |
| 0.3-0.4 | 7,095 | 0.341 | 0.323 |
| 0.4-0.5 | 4,285 | 0.463 | 0.440 |
| 0.5-0.6 | 3,070 | 0.521 | 0.516 |
| 0.6-0.7 | 651 | 0.658 | 0.568 |
| 0.7-0.8 | 60 | 0.786 | 0.533 |
| 0.9-1.0 | 5 | 0.990 | 1.000 |

MOD-06 asks an expected calibration error of at most 0.05; ECE here uses 10 equal-width bins weighted by lines.

## B. Residual with two references (US-206, MOD-09)

z_c of spec 8.2 over each customer's settled lines, with p_l from the product/cohort reference (the residual of features/customer_features) and, for comparison only, from the personalized predictor. Both are cross-fitted by customer at the decision time. Behaviours come from the ground truth (offline evaluation, rule 12). Abusive = wardrober or promo_ring. The residual is an indicator, not a probability of abuse.

Main store at 2026-10-01 00:00Z, reference (origin: main store; period October 2025 to the decision time; nature: synthetic):

| Behaviour | Customers with z | Mean z | Median z | z >= 2 |
| --- | --- | --- | --- | --- |
| normal | 15,719 | -0.12 | -0.28 | 4.8% |
| size | 2,364 | -0.25 | -0.29 | 0.2% |
| shared_benign | 687 | -0.09 | -0.29 | 5.2% |
| wardrober | 581 | 2.47 | 2.40 | 62.1% |
| promo_ring | 300 | 4.29 | 4.30 | 97.7% |

Main store at 2026-10-01 00:00Z, personalized (origin: main store; period October 2025 to the decision time; nature: synthetic):

| Behaviour | Customers with z | Mean z | Median z | z >= 2 |
| --- | --- | --- | --- | --- |
| normal | 15,719 | -0.12 | -0.23 | 2.9% |
| size | 2,364 | -0.26 | -0.30 | 0.1% |
| shared_benign | 687 | -0.08 | -0.20 | 3.8% |
| wardrober | 581 | 1.85 | 1.88 | 43.7% |
| promo_ring | 300 | 3.66 | 3.64 | 96.3% |

Out-of-distribution store at 2026-10-01 00:00Z, reference trained on the main store (separate row, never trained on; nature: synthetic):

| Behaviour | Customers with z | Mean z | Median z | z >= 2 |
| --- | --- | --- | --- | --- |
| normal | 15,722 | -0.14 | -0.30 | 5.1% |
| size | 2,365 | -0.29 | -0.36 | 0.2% |
| shared_benign | 693 | -0.12 | -0.30 | 6.2% |
| wardrober | 591 | 2.35 | 2.38 | 61.1% |
| promo_ring | 300 | 3.30 | 3.34 | 84.3% |

AUC of z for abusive against every other behaviour (customers with at least one settled line; a description of the synthetic store, not a risk model result):

| Decision time | Reference | Customers | AUC of z |
| --- | --- | --- | --- |
| 2026-07-01 00:00Z | reference | 17,588 | 0.938 |
| 2026-07-01 00:00Z | personalized | 17,588 | 0.932 |
| 2026-10-01 00:00Z | reference | 19,651 | 0.951 |
| 2026-10-01 00:00Z | personalized | 19,651 | 0.944 |
| OOD 2026-10-01 00:00Z | reference | 19,671 | 0.936 |
| OOD 2026-10-01 00:00Z | personalized | 19,671 | 0.918 |

Golden cases at the end of the data (demo cases, never a measure of accuracy, US-906):

| Customer | z reference | z personalized | Settled lines | Evidence | V (EUR) |
| --- | --- | --- | --- | --- | --- |
| C-GIULIA | -0.96 | -0.91 | 13 | sufficient | 245.27 |
| C-MARCO-1 | 4.52 | 2.81 | 5 | sufficient | -28.68 |
| C-MARCO-2 | 3.68 | 3.81 | 3 | insufficient | -32.49 |
| C-MARCO-3 | 3.16 | 2.66 | 5 | sufficient | -59.87 |
| C-MARCO-4 | 4.26 | 4.01 | 3 | insufficient | -35.21 |
| C-MARCO-5 | 2.93 | 2.10 | 5 | sufficient | -32.02 |
| C-MARCO-6 | 4.62 | 4.25 | 3 | insufficient | -37.45 |
| C-MARCO-7 | 3.67 | 2.59 | 5 | sufficient | -34.82 |
| C-ROSSI-1 | -0.39 | -0.27 | 6 | sufficient | 52.54 |
| C-ROSSI-2 | -0.49 | -0.47 | 5 | sufficient | 73.16 |
| C-ROSSI-3 | 0.26 | 0.14 | 3 | insufficient | 45.64 |
| C-ROSSI-CARD-1 | -0.31 | -0.29 | 5 | sufficient | 61.88 |
| C-ROSSI-CARD-2 | -0.15 | -0.17 | 4 | sufficient | 58.55 |
| C-ROSSI-CARD-3 | 0.42 | 0.60 | 5 | sufficient | 34.99 |
| C-ELENA | -0.38 | -0.30 | 4 | sufficient | 35.94 |

## C. Customer value (US-205, UNIT-03)

V_c = 0.55 x kept line prices - 8.00 EUR x returns - coupon discounts, over the 365 days up to the decision time (assumptions value-v1-2026-10-13: docs/spec.md section 4, Navigli Fashion hypotheses to calibrate with interviews (D-09)). Origin: main store; nature: synthetic; amounts in EUR. Mismatches: customers whose kept + returned - discounts differs from the sum of their order totals by at least one cent.

| Decision time | Customers | Mismatches | Order totals | Sum of V | p10 | p30 | p50 | p70 | p90 | V < 0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-01 00:00Z | 18,636 | 0 | 4,656,821.53 | 1,381,581.74 | -0.02 | 29.14 | 59.06 | 97.48 | 172.41 | 10.0% |
| 2026-08-01 00:00Z | 19,238 | 0 | 5,363,460.89 | 1,604,631.89 | 1.76 | 35.79 | 67.31 | 107.88 | 189.25 | 9.0% |
| 2026-09-01 00:00Z | 19,829 | 0 | 5,824,226.26 | 1,730,911.60 | 3.30 | 37.17 | 70.59 | 112.80 | 197.83 | 8.7% |
| 2026-10-01 00:00Z | 20,000 | 0 | 6,307,945.09 | 1,874,554.53 | 4.38 | 41.46 | 76.27 | 120.05 | 211.07 | 8.5% |

By behaviour at 2026-10-01 00:00Z (truth, offline). Value orients benefits; it is never a sign of abuse and never feeds the risk score (rule 6). The last column is the share above the store's 70th percentile of value, the Q1/Q3 side of spec 8.6.

| Behaviour | Customers | Median V (EUR) | Above p70 |
| --- | --- | --- | --- |
| normal | 16,000 | 79.17 | 31.0% |
| size | 2,400 | 85.91 | 33.6% |
| shared_benign | 700 | 84.38 | 30.7% |
| wardrober | 600 | -4.58 | 3.2% |
| promo_ring | 300 | -64.62 | 0.0% |

## D. Evidence quality (US-209, D-15)

Thresholds evidence-v1-2026-10-13: at least 4 settled lines (own, or with linked accounts when the account graph is given), identifier completeness at least 0.6, reason coherence at least 0.5 from 2 withdrawal returns on. Origin: main store at the decision times of the risk split, and the OOD store at the end (separate row); nature: synthetic. The account graph (features/clusters) was not available, so these use own observations only. Columns 4-6: share of customers missing that piece of evidence.

| Decision time | Customers | Insufficient | Observations | Identifiers | Reasons | Insufficient, abusive | Insufficient, other |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-01 00:00Z | 18,636 | 29.3% | 28.9% | 0.1% | 0.4% | 37.7% | 28.9% |
| 2026-08-01 00:00Z | 19,238 | 25.0% | 24.6% | 0.2% | 0.4% | 31.7% | 24.7% |
| 2026-09-01 00:00Z | 19,829 | 19.8% | 19.4% | 0.2% | 0.4% | 22.8% | 19.7% |
| 2026-10-01 00:00Z | 20,000 | 16.7% | 16.3% | 0.0% | 0.5% | 19.9% | 16.6% |
| OOD 2026-10-01 00:00Z | 20,000 | 24.1% | 16.6% | 8.6% | 0.5% | 24.3% | 24.1% |

D-15, train period only (2026-07-01 00:00Z, main store): the observations threshold against the share of customers it sends to insufficient evidence and the AUC of the reference z for abusive customers among those it keeps (truth, offline).

| Min observations | Insufficient | Abusive | Other | AUC of z, kept |
| --- | --- | --- | --- | --- |
| 1 | 5.6% | 9.7% | 5.4% | 0.938 |
| 2 | 10.5% | 15.7% | 10.3% | 0.947 |
| 3 | 19.1% | 26.3% | 18.8% | 0.961 |
| 4 | 28.9% | 37.4% | 28.5% | 0.974 |
| 5 | 38.9% | 49.0% | 38.4% | 0.978 |
| 6 | 49.0% | 60.5% | 48.5% | 0.981 |
| 8 | 65.5% | 78.6% | 64.9% | 0.988 |
| 10 | 77.0% | 89.1% | 76.5% | 0.992 |

AUC of z by number of settled lines (same instant):

| Settled lines | Customers | Abusive | AUC of z |
| --- | --- | --- | --- |
| 1 | 917 | 50 | 0.762 |
| 2 | 1,597 | 88 | 0.840 |
| 3 | 1,831 | 92 | 0.892 |
| 4 | 1,859 | 96 | 0.958 |
| 5 | 1,887 | 95 | 0.973 |
| 6 | 1,637 | 70 | 0.973 |
| 7 | 1,439 | 80 | 0.972 |
| 8 | 1,205 | 54 | 0.977 |
| 9+ | 5,216 | 123 | 0.993 |

D-15 rule: the smallest settled-line count from which the AUC of z is at least 0.9 at that count and at every larger one. On this store it gives 4, which agrees with the versioned threshold 4. A different value is a decision for a new version, never an automatic change.

Identifier completeness below each threshold (main at the train end; OOD at the end):

| Threshold | Main | OOD |
| --- | --- | --- |
| 0.4 | 0.0% | 0.6% |
| 0.5 | 0.0% | 3.1% |
| 0.6 | 0.1% | 8.6% |
| 0.7 | 0.1% | 23.0% |
| 0.8 | 0.2% | 35.8% |

Reason coherence below each threshold, customers with at least 2 withdrawal returns (main, train end):

| Threshold | Customers below |
| --- | --- |
| 0.3 | 0.1% |
| 0.5 | 0.4% |
| 0.7 | 5.6% |

## E. Customer features by behaviour

Medians at the train end 2026-07-01 00:00Z (main store, truth by behaviour, offline; nature: synthetic). Inputs for the risk model (US-204), not results.

| Feature | normal | size | shared_benign | wardrober | promo_ring |
| --- | --- | --- | --- | --- | --- |
| n_observations | 5.00 | 7.00 | 6.00 | 4.00 | 5.00 |
| return_rate | 0.20 | 0.32 | 0.20 | 0.75 | 1.00 |
| return_rate_to_date | 0.20 | 0.30 | 0.20 | 0.75 | 1.00 |
| changed_mind_share | 0.00 | 0.00 | 0.00 | 0.80 | 0.43 |
| size_share | 0.33 | 1.00 | 0.33 | 0.00 | 0.20 |
| median_days_to_return | 4.50 | 2.50 | 5.00 | 4.50 | 5.00 |
| tenure_days | 702.88 | 700.19 | 860.60 | 637.09 | 116.85 |
| n_welcome_coupons_used | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 |
| weekend_order_share | 0.29 | 0.25 | 0.33 | 0.00 | 0.33 |
| mean_line_price_eur | 29.34 | 29.13 | 29.52 | 59.43 | 28.02 |
| residual_z | -0.28 | -0.27 | -0.30 | 2.17 | 4.21 |
