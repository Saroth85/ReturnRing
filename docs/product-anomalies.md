# Product anomalies: detector, catalogue task and follow-up

Stories US-208 (product anomalies separated from customer behaviour) and the data side of US-606
(an anomaly opens a catalogue task, followed after the change); tests MOD-10, MOD-08, MOD-12 and the
data side of E2E-04; decision D-15 (spec sections 5, 8.9 and 12). Code: `pipeline/anomalies/`.
Measured results: `docs/results/us-208-product-anomalies.md`.

```bash
python -m pipeline.anomalies --store data --out data            # features/product_anomalies
python -m pipeline.anomalies --store data/ood --out data/ood    # the OOD store, for evaluation
python -m pipeline.anomalies.evaluate --data data --results docs/results/us-208-product-anomalies.md
```

The first two read `<store>/store` only, never `data/truth` (rule 12), and write one file with a
snapshot per decision time (default: every Monday 00:00 UTC of the store period and the instant
after its last day). The third is an offline evaluation job: it reads `truth/variants` to score the
flags and rewrites the results file from the data alone.

## The unit: a weekly sales cohort

A cell is (variant, return reason, sales week): the order lines of one variant sold in one
Monday-to-Sunday week (UTC), and their returns for one reason. Counting by sales week, not by the
week of the return request, keeps O and n about the same lines (O is at most n, and E = n times a
rate is meaningful), and ties the anomaly to what was shipped that week, which is where a faulty lot
or a wrong product page acts. A count by request week would mix the sales of several weeks.

At the decision time T, for each cell:

| Quantity | Definition |
| --- | --- |
| n | Lines of the variant sold in the week and known at T (order line `available_at <= T`) |
| O, observed | Lines of the cohort with a return for the reason known at T (return `available_at <= T`) |
| a_l | Age of line l at T: T minus its sale instant |
| S | Lines of the same category sold in the weeks before the cohort's week, other variants only |
| h_l | Lines among S with a return for the reason known within a_l of their own sale |
| E, expected | Sum over the cohort of p_l = (h_l + 1) / (S + 2); null when S = 0 |
| z | (O - E) / sqrt(E), spec 8.9 |

Three choices in E, each for a reason measured or stated:

- **Category baseline, other variants, earlier weeks** (spec 8.9, rule 7): the baseline never sees
  the cohort's week or a later one (no future data) and never the variant itself, so a defect does
  not raise its own expectation.
- **Same age.** A weekly run reads cohorts whose returns have not all come in. Each line is compared
  with the earlier lines at the same age, so a two-week-old cohort is not held against mature weeks
  (which would hide it) and a mature cohort is not held against young ones. Every return h_l counts
  was known before T: it belongs to a line sold before the cohort and was known within a_l of that
  sale.
- **Rule of succession.** (h_l + 1) / (S + 2) is Laplace's estimate of "returned for this reason
  within a_l". With the plain share h_l / S, a reason the category has never seen gives E = 0 and no
  z at all, however many lines come back: in the main store three defective variants sold 20 to 38
  lines a week, a third to two thirds of them returned for the defect reason, and could not be
  flagged because their category had no earlier return for that reason in the first weeks of the
  store (results, section B, step 1 against step 2).

## The flag (D-15)

`pipeline.anomalies.detect.ANOMALY_THRESHOLDS`, version `anomalies-v1-2026-10-13`:

- at least **20 sales** in the week and **z > 3**, the thresholds of spec 8.9, unchanged;
- **exact tail**: P(X >= O) for X ~ Poisson(E) is at most the false alarm rate that z > 3
  promises, the one-sided normal tail of 3 (0.00135). The normal approximation behind z needs E of
  about 5 or more, and most cells have E under 1: with E = 0.08, one return gives z = 3.25, which
  Poisson(0.08) produces once in 13 tests;
- **family-wise level**: the 8 reasons of a variant-week are tested together, and they share that
  rate (Bonferroni): each reason is tested at 0.00135 / 8. A variant-week is one catalogue decision,
  so the 3-sigma promise is kept for the decision, not for each of its 8 tests.

No constant was tuned on the store: 20 and 3 are the spec's, 8 is the number of reasons of
`policy.types.ReturnReason`, and the rule of succession has no parameter. **The rule itself was
chosen on the main store**, after measuring the literal rule of spec 8.9 there (precision 0.386 at
commit 88c370e): the MOD-10 figures of the main store are in-sample for that choice, and the OOD
store changes the customers, not the defect mechanism, so its rows do not test the rule on another
defect mechanism (review F10). The results file reports the literal rule and each step on the same
cells, regressions included (rule 13). Spec 8.9 and decision D-15 record the rule as implemented.

The **multi-week window** variant (pooling weeks under 20 sales) was not built. It is needed only
if the single-week rule cannot work: every defective variant of both stores reaches 20 sales in at
least one week of its defect window, and the single-week rule meets the MOD-10 precision of 0.80
(results, sections A to C).

## Output: features/product_anomalies (contract v2, ADR-13)

One row per flagged cell and decision time: `variant_id`, `reason`, `week` (the Monday), `observed`,
`expected`, `z`, `n`, `status`, `as_of`. No customer column. The pipeline writes `status = open`;
`task_opened` and `resolved` belong to the catalogue workflow (US-606, API), because a fresh
computation cannot know that a task was opened, and a decrease of returns alone does not resolve an
anomaly. A cell can appear in many snapshots, and can leave a later one when its returns develop
like the category's.

Provenance (rule 11): origin `ReturnRing anomalies-v1 (pipeline.anomalies; thresholds ...)`,
computed from the store's origin; period from the store's first day to the last decision time;
nature of the store (synthetic).

## Decision time (rule 7, MOD-08)

`build.load_sales` reads `store/variants`, `store/order_lines` and `store/returns` with
`contracts.io.read_table(as_of=T)`, and `StoreSnapshot.restrict` applies the same filter for the
earlier decision times of a run. Lines and returns are each filtered on their own `available_at`,
so a line recorded late or a return known after T does not exist at T. Tests: hand-built frames
with rows sold, recorded or returned after T (`tests/test_anomalies_detect.py`); stores written
with and without the later rows (`tests/test_anomalies_build.py`); a brute-force count of S and h_l
on a random store. The results file (section H) recomputes five snapshots of the generated main
store (synthetic) from the store read at T.

## Separate from customer behaviour (spec 8.9, E2E-04)

An anomaly is a catalogue matter. The table and the catalogue task name no customer, and no module
that scores customers (`pipeline/features`, `ml/`) reads them. Elena's return of V-JKT-DEF is
`not_as_described`, a non-conformity reason, so `policy.engine.choose_path` sends it to the
guarantee path, where no restrictive lever applies whatever the risk score (results, section E).

Returns for a withdrawal reason (size, changed mind, not as expected, other) that fall in an open
anomaly still count in the customer features today; showing "consistent with an open product
anomaly" as a favourable element on the case card (US-302) is an open point for the API.

## Catalogue task (US-606)

`catalogue.catalogue_task(rows, product_id=..., category=..., provenance=...)` turns the rows of one
variant, reason and decision time into a JSON-ready payload: a deterministic `task_id`
(`CAT-<variant>-<reason>-<first week>`), the flagged weeks with n, O, E and z, their totals, the three
checks of spec 8.9, a fixed note (the task concerns the product, names no customer and changes no
customer's risk, terms or messages), the follow-up caveat, the thresholds version and the provenance
(origin, period, nature). The checks are ordered by what the reason points to:

| Reason | Checks, first to last |
| --- | --- |
| size | size guide, description, lot check |
| not_as_described, not_as_expected, changed_mind, other | description, size guide, lot check |
| defective, damaged_in_shipping, wrong_item | lot check, description, size guide |

## Follow-up after a change (US-606)

`catalogue.before_after(sales, variant, reason, change_at, as_of, exclude=...)` compares the
variant's return rate for the reason in the 8 calendar weeks before and after the change
(`FOLLOW_WEEKS`, a design default) with the rest of its category in the same weeks, read at the same
decision time, so recent weeks are equally immature on both sides. The week that contains the change
belongs to neither period. The result holds the weekly series, both periods, the difference in
differences with an approximate 95% interval (normal, independent lines), and a plain conclusion;
with fewer than 20 sales on either side it is "not assessable yet". The variants with their own
anomaly for the reason (`exclude`, read from features/product_anomalies) leave the comparison: a
defective variant fixed in the same weeks would otherwise make every other variant of its category
look worse (results, section F). Every result carries the fixed caveat: a decrease alone does not
prove the effect of the change.

## Known limits

- The baseline counts other defective variants of the category, so the defect reasons are expected
  more often than normal variants return them (results, section G). It makes those reasons harder
  to flag on any variant; excluding flagged cells from the baseline is a possible next step.
- Variants returned much more than their category for every reason make most of the remaining false
  flags (results, section D): the category baseline cannot see a variant's own level, by design.
- The detector counts returns, not returners: linked accounts returning one variant in one week
  could look like a product anomaly. The synthetic rings spread their purchases, so this is not
  measured.
- Every synthetic defect starts with a 4-week sales surge, shared by matched non-defective controls
  (docs/synthetic-store.md). The OOD store changes the customers, not the defect mechanism.
