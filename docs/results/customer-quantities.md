# Customer features, residual, value and evidence: measured results

Stories US-205, US-206 and US-209 (tests UNIT-02, UNIT-03, MOD-06, MOD-08, MOD-09, MOD-12; decision D-15). Design and definitions: docs/customer-features.md.

Rewritten by `python -m ml.reference.report --data data --results docs/results/customer-quantities.md` from the data alone (no wall-clock value), so the same store gives the same file. Every number below is synthetic: measured on the Navigli Fashion store, whose behaviours and rules are design hypotheses calibrated on real ASOS aggregates. None is a measure on real customers.

- Main store origin: Navigli Fashion generator, calibrated on ASOS GraphReturns aggregates (calibration: ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0; split periods per the paper: train 2021-09-01..2021-10-31, test 2021-10-01..2021-11-30; calibration: aggregates of the train split by pipeline/ingest/asos_profile.py. Rates, counts and purchases per customer come from the train events (2021-09-01..2021-10-31); product types, prices, return rates and reason shares come from the train product table, whose aggregates equal the test table's for every shared variant, so their window is the test window too (ADR-10); hence the period 2021-09-01..2021-11-30; calibration nature real; sha256 f49ce3711edd; seed 42, scale 1, scenario main; store period 2025-10-01..2026-09-30); period 2025-10-01 to 2026-09-30; nature synthetic.
- OOD store origin: Navigli Fashion generator, calibrated on ASOS GraphReturns aggregates (calibration: ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0; split periods per the paper: train 2021-09-01..2021-10-31, test 2021-10-01..2021-11-30; calibration: aggregates of the train split by pipeline/ingest/asos_profile.py. Rates, counts and purchases per customer come from the train events (2021-09-01..2021-10-31); product types, prices, return rates and reason shares come from the train product table, whose aggregates equal the test table's for every shared variant, so their window is the test window too (ADR-10); hence the period 2021-09-01..2021-11-30; calibration nature real; sha256 f49ce3711edd; seed 42, scale 1, scenario ood; store period 2025-10-01..2026-09-30); nature synthetic.
- Code: features-v1, reference model v1, value value-v1-2026-10-13, evidence evidence-v1-2026-10-13.

## A. Reference model: calibration on the last three months (US-206, MOD-06)

Origin: main store, lines ordered 2026-07-01 to 2026-09-30 and settled by 2026-10-01 00:00Z. Period: July to September 2026. Nature: synthetic. Trained at 2026-07-01 00:00Z on the 125,156 lines settled by then (each priced with what was known when it was ordered); validation 10% of the customers; k = 20 for the smoothed rates; reference early-stopped at 52 rounds, personalized at 101.

| Predictor | Lines | Observed rate | Mean p | Log loss | Brier | ECE | AUC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Smoothed variant rate (baseline) | 34,168 | 0.264 | 0.266 | 0.5310 | 0.1763 | 0.0078 | 0.693 |
| Reference, XGBoost before isotonic | 34,168 | 0.264 | 0.265 | 0.5187 | 0.1720 | 0.0100 | 0.718 |
| **Reference, XGBoost + isotonic (used by the residual)** | 34,168 | 0.264 | 0.267 | 0.5198 | 0.1723 | 0.0082 | 0.717 |
| Personalized, XGBoost before isotonic | 34,168 | 0.264 | 0.259 | 0.5060 | 0.1670 | 0.0073 | 0.737 |
| Personalized, XGBoost + isotonic (checkout) | 34,168 | 0.264 | 0.261 | 0.5067 | 0.1672 | 0.0107 | 0.736 |

Out-of-distribution store (US-105, MOD-12), a separate row whatever the result: the models trained on the main store at the train end price the OOD lines; the OOD history counts come from the OOD store itself; nothing is trained on it. Same period and nature (synthetic).

| Predictor | Lines | Observed rate | Mean p | Log loss | Brier | ECE | AUC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OOD: smoothed variant rate | 34,733 | 0.266 | 0.268 | 0.5338 | 0.1776 | 0.0074 | 0.690 |
| OOD: reference, XGBoost + isotonic | 34,733 | 0.266 | 0.267 | 0.5225 | 0.1735 | 0.0049 | 0.714 |
| OOD: personalized, XGBoost + isotonic | 34,733 | 0.266 | 0.264 | 0.5092 | 0.1684 | 0.0097 | 0.735 |

Reliability of the reference (main store, last three months, equal-width bins of p):

| p bin | Lines | Mean p | Observed rate |
| --- | --- | --- | --- |
| 0.0-0.1 | 2,773 | 0.054 | 0.064 |
| 0.1-0.2 | 12,129 | 0.151 | 0.142 |
| 0.2-0.3 | 5,390 | 0.237 | 0.238 |
| 0.3-0.4 | 6,061 | 0.328 | 0.330 |
| 0.4-0.5 | 2,829 | 0.433 | 0.443 |
| 0.5-0.6 | 4,079 | 0.516 | 0.499 |
| 0.6-0.7 | 872 | 0.605 | 0.587 |
| 0.7-0.8 | 1 | 0.800 | 1.000 |
| 0.8-0.9 | 15 | 0.824 | 0.533 |
| 0.9-1.0 | 19 | 0.974 | 0.632 |

MOD-06 asks an expected calibration error of at most 0.05; ECE here uses 10 equal-width bins weighted by lines.

## B. Residual with two references (US-206, MOD-09)

z_c of spec 8.2 over each customer's settled lines, with p_l from the product/cohort reference (the residual of features/customer_features) and, for comparison only, from the personalized predictor. Both are cross-fitted by customer at the decision time. Behaviours come from the ground truth (offline evaluation, rule 12). Abusive = wardrober or promo_ring. The residual is an indicator, not a probability of abuse.

Main store at 2026-10-01 00:00Z, reference (origin: main store; period October 2025 to the decision time; nature: synthetic):

| Behaviour | Customers with z | Mean z | Median z | z >= 2 |
| --- | --- | --- | --- | --- |
| normal | 15,720 | -0.11 | -0.28 | 4.9% |
| size | 2,364 | -0.23 | -0.27 | 0.4% |
| shared_benign | 688 | -0.09 | -0.25 | 5.4% |
| wardrober | 579 | 2.52 | 2.46 | 63.2% |
| promo_ring | 300 | 4.42 | 4.52 | 98.0% |

Main store at 2026-10-01 00:00Z, personalized (origin: main store; period October 2025 to the decision time; nature: synthetic):

| Behaviour | Customers with z | Mean z | Median z | z >= 2 |
| --- | --- | --- | --- | --- |
| normal | 15,720 | -0.10 | -0.23 | 3.3% |
| size | 2,364 | -0.24 | -0.26 | 0.0% |
| shared_benign | 688 | -0.07 | -0.19 | 4.7% |
| wardrober | 579 | 1.88 | 1.91 | 44.9% |
| promo_ring | 300 | 3.76 | 3.71 | 95.7% |

Out-of-distribution store at 2026-10-01 00:00Z, reference trained on the main store (separate row, never trained on; nature: synthetic):

| Behaviour | Customers with z | Mean z | Median z | z >= 2 |
| --- | --- | --- | --- | --- |
| normal | 15,703 | -0.03 | -0.20 | 5.8% |
| size | 2,366 | -0.13 | -0.19 | 0.4% |
| shared_benign | 692 | -0.04 | -0.24 | 6.1% |
| wardrober | 591 | 2.53 | 2.54 | 65.3% |
| promo_ring | 300 | 3.48 | 3.56 | 86.3% |

AUC of z for abusive against every other behaviour (customers with at least one settled line; a description of the synthetic store, not a risk model result):

| Decision time | Reference | Customers | AUC of z |
| --- | --- | --- | --- |
| 2026-07-01 00:00Z | reference | 17,574 | 0.937 |
| 2026-07-01 00:00Z | personalized | 17,574 | 0.933 |
| 2026-10-01 00:00Z | reference | 19,651 | 0.952 |
| 2026-10-01 00:00Z | personalized | 19,651 | 0.944 |
| OOD 2026-10-01 00:00Z | reference | 19,652 | 0.935 |
| OOD 2026-10-01 00:00Z | personalized | 19,652 | 0.922 |

Golden cases at the end of the data (demo cases, never a measure of accuracy, US-906):

| Customer | z reference | z personalized | Settled lines | Evidence | V (EUR) |
| --- | --- | --- | --- | --- | --- |
| C-GIULIA | -1.11 | -0.95 | 13 | sufficient | 239.16 |
| C-MARCO-1 | 2.94 | 2.15 | 5 | sufficient | -34.60 |
| C-MARCO-2 | 2.15 | 1.99 | 3 | insufficient | -32.52 |
| C-MARCO-3 | 5.38 | 3.45 | 5 | sufficient | -51.88 |
| C-MARCO-4 | 3.44 | 3.14 | 3 | insufficient | -34.04 |
| C-MARCO-5 | 2.53 | 1.74 | 5 | sufficient | -43.21 |
| C-MARCO-6 | 2.34 | 2.29 | 3 | insufficient | -33.43 |
| C-MARCO-7 | 3.25 | 2.45 | 5 | sufficient | -51.45 |
| C-ROSSI-1 | 0.01 | 0.25 | 6 | sufficient | 70.07 |
| C-ROSSI-2 | -0.41 | -0.32 | 5 | sufficient | 74.60 |
| C-ROSSI-3 | 0.35 | 0.33 | 3 | insufficient | 53.67 |
| C-ROSSI-CARD-1 | -0.11 | -0.07 | 5 | sufficient | 76.05 |
| C-ROSSI-CARD-2 | 0.08 | 0.05 | 4 | sufficient | 27.92 |
| C-ROSSI-CARD-3 | -0.18 | 0.08 | 5 | sufficient | 66.49 |
| C-ELENA | -0.06 | -0.01 | 4 | sufficient | 42.25 |

## C. Customer value (US-205, UNIT-03)

V_c = 0.55 x kept line prices - 8.00 EUR x returns - coupon discounts, over the 365 days up to the decision time (assumptions value-v1-2026-10-13: docs/spec.md section 4, Navigli Fashion hypotheses to calibrate with interviews (D-09)). Origin: main store; nature: synthetic; amounts in EUR. Mismatches: customers whose kept + returned - discounts differs from the sum of their order totals by at least one cent.

| Decision time | Customers | Mismatches | Order totals | Sum of V | p10 | p30 | p50 | p70 | p90 | V < 0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-01 00:00Z | 18,636 | 0 | 4,677,863.57 | 1,382,500.29 | 0.00 | 30.26 | 59.22 | 96.65 | 171.75 | 9.8% |
| 2026-08-01 00:00Z | 19,238 | 0 | 5,385,000.75 | 1,605,976.55 | 2.09 | 35.92 | 67.70 | 107.48 | 189.68 | 9.0% |
| 2026-09-01 00:00Z | 19,829 | 0 | 5,845,582.20 | 1,732,400.37 | 3.12 | 37.87 | 70.91 | 112.33 | 198.33 | 8.7% |
| 2026-10-01 00:00Z | 20,000 | 0 | 6,329,352.46 | 1,876,310.83 | 4.26 | 42.27 | 76.83 | 119.49 | 210.52 | 8.4% |

By behaviour at 2026-10-01 00:00Z (truth, offline). Value orients benefits; it is never a sign of abuse and never feeds the risk score (rule 6). The last column is the share above the store's 70th percentile of value, the Q1/Q3 side of spec 8.6.

| Behaviour | Customers | Median V (EUR) | Above p70 |
| --- | --- | --- | --- |
| normal | 16,000 | 79.21 | 31.0% |
| size | 2,400 | 85.72 | 32.8% |
| shared_benign | 700 | 85.45 | 32.9% |
| wardrober | 600 | -4.23 | 3.3% |
| promo_ring | 300 | -64.67 | 0.0% |

## D. Evidence quality (US-209, D-15)

Thresholds evidence-v1-2026-10-13: at least 4 settled lines (own, or with linked accounts when the account graph is given), identifier completeness at least 0.6, reason coherence at least 0.5 from 2 withdrawal returns on. Origin: main store at the decision times of the risk split, and the OOD store at the end (separate row); nature: synthetic. The account graph (features/clusters) was not available, so these use own observations only. Columns 4-6: share of customers missing that piece of evidence.

| Decision time | Customers | Insufficient | Observations | Identifiers | Reasons | Insufficient, abusive | Insufficient, other |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-01 00:00Z | 18,636 | 29.5% | 29.2% | 0.1% | 0.3% | 38.6% | 29.0% |
| 2026-08-01 00:00Z | 19,238 | 25.3% | 25.0% | 0.2% | 0.4% | 33.0% | 24.9% |
| 2026-09-01 00:00Z | 19,829 | 20.1% | 19.7% | 0.2% | 0.4% | 23.5% | 20.0% |
| 2026-10-01 00:00Z | 20,000 | 17.1% | 16.7% | 0.0% | 0.4% | 20.7% | 16.9% |
| OOD 2026-10-01 00:00Z | 20,000 | 24.3% | 16.8% | 8.6% | 0.5% | 24.3% | 24.3% |

D-15, train period only (2026-07-01 00:00Z, main store): the observations threshold against the share of customers it sends to insufficient evidence and the AUC of the reference z for abusive customers among those it keeps (truth, offline).

| Min observations | Insufficient | Abusive | Other | AUC of z, kept |
| --- | --- | --- | --- | --- |
| 1 | 5.7% | 9.8% | 5.5% | 0.937 |
| 2 | 10.5% | 15.8% | 10.2% | 0.946 |
| 3 | 19.2% | 26.7% | 18.9% | 0.960 |
| 4 | 29.2% | 38.6% | 28.7% | 0.974 |
| 5 | 39.2% | 49.4% | 38.7% | 0.979 |
| 6 | 48.9% | 61.1% | 48.4% | 0.982 |
| 8 | 65.7% | 78.5% | 65.1% | 0.989 |
| 10 | 77.0% | 89.9% | 76.4% | 0.992 |

AUC of z by number of settled lines (same instant):

| Settled lines | Customers | Abusive | AUC of z |
| --- | --- | --- | --- |
| 1 | 893 | 50 | 0.771 |
| 2 | 1,623 | 90 | 0.852 |
| 3 | 1,858 | 99 | 0.891 |
| 4 | 1,861 | 89 | 0.957 |
| 5 | 1,821 | 97 | 0.972 |
| 6 | 1,666 | 72 | 0.979 |
| 7 | 1,455 | 72 | 0.971 |
| 8 | 1,169 | 50 | 0.980 |
| 9+ | 5,228 | 128 | 0.993 |

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
| 0.5 | 0.3% |
| 0.7 | 5.7% |

## E. Customer features by behaviour

Medians at the train end 2026-07-01 00:00Z (main store, truth by behaviour, offline; nature: synthetic). Inputs for the risk model (US-204), not results.

| Feature | normal | size | shared_benign | wardrober | promo_ring |
| --- | --- | --- | --- | --- | --- |
| n_observations | 5.00 | 7.00 | 6.00 | 4.00 | 5.00 |
| return_rate | 0.20 | 0.31 | 0.20 | 0.75 | 1.00 |
| return_rate_to_date | 0.20 | 0.31 | 0.20 | 0.75 | 1.00 |
| changed_mind_share | 0.00 | 0.00 | 0.00 | 0.83 | 0.44 |
| size_share | 0.33 | 1.00 | 0.33 | 0.00 | 0.20 |
| median_days_to_return | 5.00 | 2.50 | 5.00 | 4.50 | 5.00 |
| tenure_days | 702.88 | 700.19 | 860.60 | 637.09 | 116.85 |
| n_welcome_coupons_used | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 |
| weekend_order_share | 0.29 | 0.25 | 0.33 | 0.00 | 0.33 |
| mean_line_price_eur | 29.55 | 29.33 | 29.86 | 59.43 | 28.02 |
| residual_z | -0.26 | -0.26 | -0.28 | 2.25 | 4.30 |
