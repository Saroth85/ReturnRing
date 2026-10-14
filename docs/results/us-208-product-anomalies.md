# Product anomalies: measured results

Stories US-208 and US-606, data side (tests MOD-10, MOD-08, MOD-12, E2E-04; decision D-15). Design and definitions: docs/product-anomalies.md.

Rewritten by `python -m pipeline.anomalies.evaluate --data data --results docs/results/us-208-product-anomalies.md` from the data alone (no wall-clock value), so the same store gives the same file. Every number below is synthetic: measured on the Navigli Fashion store, whose defects and their sales surges are design hypotheses calibrated on real ASOS aggregates. None is a measure on real products.

- Main store origin: Navigli Fashion generator, calibrated on ASOS GraphReturns aggregates (seed 42, scale 1, scenario main; calibration real, sha256 f49ce3711edd: ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0); store period 2025-10-01..2026-09-30; calibration: ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0; split periods per the paper: train 2021-09-01..2021-10-31, test 2021-10-01..2021-11-30; calibration: aggregates of the train split by pipeline/ingest/asos_profile.py. Rates, counts and purchases per customer come from the train events (2021-09-01..2021-10-31); product types, prices, return rates and reason shares come from the train product table, whose aggregates equal the test table's for every shared variant, so their window is the test window too (ADR-10); hence the period 2021-09-01..2021-11-30; calibration nature real; sha256 f49ce3711edd0edf1a7fd30bcbfed790a0a60ea334545c5c1b5a107c01e9f0af; nature synthetic.
- OOD store origin: Navigli Fashion generator, calibrated on ASOS GraphReturns aggregates (seed 42, scale 1, scenario ood; calibration real, sha256 f49ce3711edd: ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0); store period 2025-10-01..2026-09-30; calibration: ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0; split periods per the paper: train 2021-09-01..2021-10-31, test 2021-10-01..2021-11-30; calibration: aggregates of the train split by pipeline/ingest/asos_profile.py. Rates, counts and purchases per customer come from the train events (2021-09-01..2021-10-31); product types, prices, return rates and reason shares come from the train product table, whose aggregates equal the test table's for every shared variant, so their window is the test window too (ADR-10); hence the period 2021-09-01..2021-11-30; calibration nature real; sha256 f49ce3711edd0edf1a7fd30bcbfed790a0a60ea334545c5c1b5a107c01e9f0af; nature synthetic.
- Code: anomalies-v1; product thresholds anomalies-v1-2026-10-13: at least 20 sales in the week, z > 3, and P(X >= O) <= 0.000169 for X ~ Poisson(E) (the normal tail of z = 3 shared by the 8 reasons).

**In-sample for the choice of rule** (review F10): no constant was tuned, but steps 2 to 4 were adopted after the literal rule of spec 8.9 (step 1) was measured on this main store, so the MOD-10 figures of section B are in-sample for that choice. The OOD store changes the customers, not the defect mechanism: section C does not test the rule on another defect mechanism. Spec 8.9 and D-15 record the rule as implemented.

## A. Volume: who reaches the minimum of 20 sales in a week

Weekly sales from store/order_lines known at the extraction instant, in Monday-to-Sunday sales weeks (UTC); no return and no label is used for the counts, the ground truth only tells which variants are defective and when (offline evaluation). Nature: synthetic.

| Store | Variant-weeks with >= 20 sales | Tests (x 8 reasons) | Variants that ever reach 20 | Defective among them | Defective variants with >= 20 sales in a window week | Window weeks with >= 20 sales (of window weeks with a sale) |
| --- | --- | --- | --- | --- | --- | --- |
| main | 1,948 | 15,584 | 495 | 100 of 100 | 100 of 100 | 378 of 1,211 |
| out-of-distribution | 1,931 | 15,448 | 502 | 100 of 100 | 100 of 100 | 370 of 1,114 |

When every defective variant reaches the minimum in at least one week of its defect window, the single-week rule of spec 8.9 can see each of them. The multi-week window variant (pooling weeks under the minimum) is needed only if the single-week rule cannot work; it was not built, and sections B and C measure the single-week rule. The window weeks under 20 sales are mostly the weeks after each defect's 4-week sales surge (docs/synthetic-store.md, known limits).

## B. MOD-10 on the main store

Origin: main store, sales weeks 2025-10-01 to 2026-10-01 00:00Z; nature: synthetic. Truth: truth/variants (offline evaluation only). Each step adds one change to the one above; step 4 is what python -m pipeline.anomalies writes.

At the extraction instant 2026-10-01 00:00Z (every sales week of the year, each cohort read with all its returns known by then):

| Rule | Variants flagged | True anomalies | Precision (MOD-10) | Precision, any defect | Recall | Cells flagged | Cell precision |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1. Spec 8.9 read literally: E = h / S, z > 3 | 251 | 97 | 0.386 | 0.394 | 0.970 (97 of 100) | 658 | 0.553 |
| 2. + rule of succession: E = (h + 1) / (S + 2) per line | 246 | 100 | 0.407 | 0.407 | 1.000 (100 of 100) | 654 | 0.575 |
| 3. + exact Poisson tail at the level of z > 3 | 122 | 100 | 0.820 | 0.820 | 1.000 (100 of 100) | 400 | 0.940 |
| **4. + level shared by the 8 reasons of a variant-week (anomalies-v1-2026-10-13, the product rule)** | 103 | 100 | 0.971 | 0.971 | 1.000 (100 of 100) | 374 | 0.992 |
| 4 without step 2 (E = h / S) | 101 | 97 | 0.960 | 0.960 | 0.970 (97 of 100) | 363 | 0.989 |

Weekly operation: one run every Monday 00:00 UTC (52 runs) and one at the extraction instant, each with what was known then. A variant flagged in any run would have opened a catalogue task for each flagged reason (one task per variant and reason, pipeline.anomalies.catalogue). Precision per variant is the MOD-10 measure; precision per task counts a defective variant flagged for a reason that is not its defect as a false task. The delay runs from the defect start to the first run with a matching flag:

| Rule | Variants flagged in any run | Precision per variant (MOD-10) | Catalogue tasks (variant, reason) | Task precision | Recall | Delay median (days) | Delay p90 | Delay max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1. Spec 8.9 read literally: E = h / S, z > 3 | 347 | 0.280 | 607 | 0.160 | 0.970 (97 of 100) | 14 | 14 | 21 |
| 2. + rule of succession: E = (h + 1) / (S + 2) per line | 346 | 0.289 | 596 | 0.168 | 1.000 (100 of 100) | 14 | 14 | 21 |
| 3. + exact Poisson tail at the level of z > 3 | 125 | 0.800 | 129 | 0.775 | 1.000 (100 of 100) | 14 | 21 | 21 |
| **4. + level shared by the 8 reasons of a variant-week (anomalies-v1-2026-10-13, the product rule)** | 105 | 0.952 | 106 | 0.943 | 1.000 (100 of 100) | 14 | 21 | 28 |
| 4 without step 2 (E = h / S) | 104 | 0.933 | 104 | 0.933 | 0.970 (97 of 100) | 14 | 21 | 28 |

## C. MOD-12: the out-of-distribution store, a separate row whatever the result

Origin: out-of-distribution store, sales weeks 2025-10-01 to 2026-10-01 00:00Z; nature: synthetic. Truth: truth/variants (offline evaluation only). Each step adds one change to the one above; step 4 is what python -m pipeline.anomalies writes.

At the extraction instant 2026-10-01 00:00Z (every sales week of the year, each cohort read with all its returns known by then):

| Rule | Variants flagged | True anomalies | Precision (MOD-10) | Precision, any defect | Recall | Cells flagged | Cell precision |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1. Spec 8.9 read literally: E = h / S, z > 3 | 249 | 99 | 0.398 | 0.398 | 0.990 (99 of 100) | 643 | 0.568 |
| 2. + rule of succession: E = (h + 1) / (S + 2) per line | 244 | 100 | 0.410 | 0.410 | 1.000 (100 of 100) | 634 | 0.584 |
| 3. + exact Poisson tail at the level of z > 3 | 115 | 100 | 0.870 | 0.870 | 1.000 (100 of 100) | 385 | 0.958 |
| **4. + level shared by the 8 reasons of a variant-week (anomalies-v1-2026-10-13, the product rule)** | 100 | 100 | 1.000 | 1.000 | 1.000 (100 of 100) | 368 | 0.997 |
| 4 without step 2 (E = h / S) | 99 | 99 | 1.000 | 1.000 | 0.990 (99 of 100) | 363 | 0.997 |

Weekly operation: one run every Monday 00:00 UTC (52 runs) and one at the extraction instant, each with what was known then. A variant flagged in any run would have opened a catalogue task for each flagged reason (one task per variant and reason, pipeline.anomalies.catalogue). Precision per variant is the MOD-10 measure; precision per task counts a defective variant flagged for a reason that is not its defect as a false task. The delay runs from the defect start to the first run with a matching flag:

| Rule | Variants flagged in any run | Precision per variant (MOD-10) | Catalogue tasks (variant, reason) | Task precision | Recall | Delay median (days) | Delay p90 | Delay max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1. Spec 8.9 read literally: E = h / S, z > 3 | 362 | 0.273 | 664 | 0.149 | 0.990 (99 of 100) | 14 | 14 | 28 |
| 2. + rule of succession: E = (h + 1) / (S + 2) per line | 360 | 0.278 | 651 | 0.154 | 1.000 (100 of 100) | 14 | 14 | 28 |
| 3. + exact Poisson tail at the level of z > 3 | 124 | 0.806 | 125 | 0.800 | 1.000 (100 of 100) | 14 | 15 | 28 |
| **4. + level shared by the 8 reasons of a variant-week (anomalies-v1-2026-10-13, the product rule)** | 100 | 1.000 | 101 | 0.990 | 1.000 (100 of 100) | 14 | 21 | 28 |
| 4 without step 2 (E = h / S) | 99 | 1.000 | 100 | 0.990 | 0.990 (99 of 100) | 14 | 21 | 28 |

## D. Flagged cells that match no defect window (product rule, every run)

Each cell flagged by step 4 in any run that does not match a defect window, at its first run. The latent return rate relative to the mean of the category's variants that sold comes from the ground truth (offline only): the store draws each variant's base rate from the real ASOS distribution of its product type, so some variants are returned much more than their category for every reason, and their returns are measured against the category.

| Store | Variant | Category | Reason | Sales week | First run | n | O | E | z | P(X >= O) | Defective | Latent rate / category mean |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| main | V-000296 | productType_G | other | 2025-11-10 | 2025-11-17 | 32 | 3 | 0.08 | 10.2 | 8.7e-05 | yes | 1.80 |
| main | V-003520 | productType_J | not_as_expected | 2025-10-27 | 2025-11-17 | 27 | 7 | 1.08 | 5.7 | 1.4e-04 | no | 2.39 |
| main | V-000459 | productType_J | not_as_expected | 2025-11-17 | 2025-12-08 | 29 | 8 | 1.09 | 6.6 | 1.9e-05 | no | 2.20 |
| main | V-002344 | productType_J | other | 2025-12-29 | 2026-01-19 | 24 | 5 | 0.29 | 8.7 | 1.4e-05 | no | 1.97 |
| main | V-000518 | productType_J | not_as_expected | 2026-02-09 | 2026-02-16 | 26 | 4 | 0.10 | 12.5 | 3.6e-06 | no | 1.95 |
| main | V-002598 | productType_B | changed_mind | 2026-05-04 | 2026-05-18 | 24 | 9 | 1.82 | 5.3 | 1.2e-04 | no | 1.39 |
| out-of-distribution | V-OOD-000110 | productType_J | other | 2026-01-19 | 2026-02-02 | 20 | 4 | 0.20 | 8.4 | 6.2e-05 | yes | 2.33 |

## E. Golden case: Elena and the jacket V-JKT-DEF (US-208, US-606, E2E-04 data side)

V-JKT-DEF has a fit defect (not as described) from 2026-08-03, not fixed in the period. Its weekly series at the extraction instant 2026-10-01 00:00Z for that reason (main store, nature synthetic; the comparison is the rest of its category in the same sales week, US-606):

| Sales week | n | O | E | z | Flagged | Category: returned / sold |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-08-03 | 31 | 12 | 0.10 | 38.5 | yes | 0 / 64 |
| 2026-08-10 | 37 | 20 | 0.11 | 59.3 | yes | 0 / 51 |
| 2026-08-17 | 33 | 18 | 0.10 | 56.8 | yes | 0 / 76 |
| 2026-08-24 | 34 | 18 | 0.10 | 56.4 | yes | 0 / 119 |
| 2026-08-31 | 24 | 16 | 0.07 | 60.3 | yes | 1 / 118 |
| 2026-09-07 | 33 | 18 | 0.10 | 56.7 | yes | 1 / 131 |
| 2026-09-14 | 18 | 8 | 0.05 | 34.0 | under 20 sales | 0 / 102 |
| 2026-09-21 | 19 | 2 | 0.03 | 11.3 | under 20 sales | 0 / 104 |
| 2026-09-28 | 10 | 0 | 0.00 | 0.0 | under 20 sales | 0 / 36 |

First flagged by the weekly run of 2026-08-17 00:00Z, 14 days after the defect start. Demo case: it shows the mechanism and is never a measure of accuracy (US-906).

The catalogue task at the extraction instant (pipeline.anomalies.catalogue.catalogue_task; every number has the origin, period and nature of its provenance):

```json
{
  "task_id": "CAT-V-JKT-DEF-not_as_described-20260803",
  "kind": "catalogue_check",
  "variant_id": "V-JKT-DEF",
  "product_id": "P-JKT",
  "category": "productType_C",
  "reason": "not_as_described",
  "as_of": "2026-10-01T00:00:00+00:00",
  "weeks": [
    {
      "week": "2026-08-03",
      "n": 31,
      "observed": 12,
      "expected": 0.095,
      "z": 38.55
    },
    {
      "week": "2026-08-10",
      "n": 37,
      "observed": 20,
      "expected": 0.112,
      "z": 59.31
    },
    {
      "week": "2026-08-17",
      "n": 33,
      "observed": 18,
      "expected": 0.099,
      "z": 56.8
    },
    {
      "week": "2026-08-24",
      "n": 34,
      "observed": 18,
      "expected": 0.101,
      "z": 56.35
    },
    {
      "week": "2026-08-31",
      "n": 24,
      "observed": 16,
      "expected": 0.07,
      "z": 60.35
    },
    {
      "week": "2026-09-07",
      "n": 33,
      "observed": 18,
      "expected": 0.1,
      "z": 56.7
    }
  ],
  "totals": {
    "n": 192,
    "observed": 102,
    "expected": 0.577
  },
  "checks": [
    {
      "id": "description",
      "title": "Description and photos",
      "instruction": "Compare the product page (text, photos, material, colour) with items returned for this reason, and correct the page where it differs from the goods."
    },
    {
      "id": "size_guide",
      "title": "Size guide",
      "instruction": "Measure returned items against the size chart of the product page, size by size, and correct the chart or add a fit note where they differ."
    },
    {
      "id": "lot_check",
      "title": "Lot check",
      "instruction": "Inspect the stock and the supplier lot shipped in the flagged weeks (labels, finishing, packaging) and hold the lot if the fault is confirmed."
    }
  ],
  "note": "Product check: this task concerns the variant, not the customers who returned it. It names no customer and changes no customer's risk, terms or messages.",
  "follow_up": "After a change, follow the weekly returns of the variant against the rest of its category over the same weeks: a decrease alone does not prove the effect of the change.",
  "thresholds_version": "anomalies-v1-2026-10-13",
  "provenance": {
    "origin": "ReturnRing anomalies-v1 (thresholds anomalies-v1-2026-10-13), computed from the main Navigli Fashion store (origin at the top of this file)",
    "period_start": "2025-10-01",
    "period_end": "2026-09-30",
    "nature": "synthetic"
  }
}
```

Elena's return stays regular. What the data side guarantees (E2E-04 is completed by the API and UI tests):

| Check | Result |
| --- | --- |
| Her return of the jacket | RET-0060361, reason not_as_described, requested 2026-09-27, known 2026-09-27 05:25Z |
| Path (policy.engine.choose_path) | guarantee |
| Engine decision with the highest possible risk score (1.0) | quadrant none, approval required no, restrictive levers none |
| Customer columns in features/product_anomalies | none |
| Customer fields in the catalogue task | none |

## F. Follow-up after a change (US-606): a decrease alone does not prove the effect

For every defect that ended at least 9 weeks before the extraction instant (in the generator the defective batch stops at defect_end; here that date stands for the catalogue fix), before_after compares the 8 sales weeks before and after the change for the defect reason, against the rest of the category in the same weeks, read at 2026-10-01 00:00Z; the comparison leaves out the variants flagged for the reason at that instant (detector output). Placebo: the same comparison, at the same date and for the same reason, on the 5 best-selling non-defective variants of the same category in those weeks, where nothing changed. Origin: main store; nature: synthetic; the change dates come from the ground truth (offline evaluation).

| Variants | Follow-ups | Assessable (>= 20 sales each side) | Fewer returns after (raw count) | Rate fell vs category (95% interval below 0) | Rate rose vs category | Median difference (points) |
| --- | --- | --- | --- | --- | --- | --- |
| Defects at their fix | 74 | 27 | 27 (1.00) | 27 (1.00) | 0 | -49.1 |
| Placebos, same dates | 370 | 319 | 66 (0.21) | 4 (0.01) | 2 | 0.0 |

A raw decrease of the returns count also happens where nothing was fixed (sales move, surges end), so the follow-up states the difference against the rest of the category over the same weeks, with its interval, and the fixed caveat of pipeline.anomalies.catalogue.FOLLOW_UP_NOTE. The interval is approximate (normal, independent lines).

## G. Known limits, measured

- **The baseline includes other defects.** The category baseline counts every other variant of the category, defective or not, so the expected returns for the defect reasons are inflated where other defects sold. Observed against expected over the non-defective cohorts tested at the extraction instant (main store, nature synthetic):

| Reason | Cells | O | E | O / E |
| --- | --- | --- | --- | --- |
| size | 1,529 | 3,898 | 4,681.2 | 0.83 |
| changed_mind | 1,529 | 2,363 | 2,751.5 | 0.86 |
| not_as_expected | 1,529 | 2,229 | 2,260.1 | 0.99 |
| defective | 1,529 | 394 | 1,053.7 | 0.37 |
| damaged_in_shipping | 1,529 | 172 | 177.7 | 0.97 |
| wrong_item | 1,529 | 107 | 95.0 | 1.13 |
| not_as_described | 1,529 | 100 | 877.9 | 0.11 |
| other | 1,529 | 757 | 745.0 | 1.02 |

  This makes the defect reasons harder to flag on any variant (fewer false alarms, later detection), and it is reported rather than corrected: excluding flagged cells from the baseline is a possible next step.
- **Variants returned more than their category** for every reason produce most of the remaining false flags (section D). The category baseline cannot see a variant's own level, by design of spec 8.9; such a variant may still deserve a look at its page.
- **Who returns.** The detector counts returns, not returners: a group of linked accounts returning one variant in one week could look like a product anomaly. The synthetic rings spread their purchases over the catalogue, so this case is not measured here.
- **Synthetic mechanism.** Every defect starts with a 4-week sales surge, and matched non-defective controls surge alike (docs/synthetic-store.md). The OOD store changes the customer rules, not the defect mechanism, so its rows test the detector on another population of customers, not on another kind of defect.

## H. Decision-time availability (MOD-08)

Rows of features/product_anomalies at 5 decision times of the main store, computed from the store read at T (contracts.io.read_table(as_of=T)) and from the store read at the extraction instant and cut at T: identical, although the later store holds the order lines and returns known after T counted below. Nature: synthetic.

| Decision time | Rows | Identical | Order lines known after T | Returns known after T | Every week <= T |
| --- | --- | --- | --- | --- | --- |
| 2025-12-29 00:00Z | 71 | yes | 157,656 | 47,911 | yes |
| 2026-03-30 00:00Z | 250 | yes | 104,284 | 31,278 | yes |
| 2026-06-29 00:00Z | 358 | yes | 54,522 | 15,920 | yes |
| 2026-09-28 00:00Z | 374 | yes | 1,440 | 446 | yes |
| 2026-10-01 00:00Z | 374 | yes | 0 | 0 | yes |
