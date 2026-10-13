# Customer features, residual, value and evidence quality

Stories US-205 (value), US-206 (residual against a product/cohort reference) and US-209 (evidence
quality), plus the request-level features of spec 8.4 and the product/cohort reference model of
spec 8.1 and 8.2. Tests UNIT-02, UNIT-03, MOD-06, MOD-08, MOD-09 and FAIR-01. Code:
`pipeline/features/` and `ml/reference/`. Contracts: ADR-13 amendment 2. Measured numbers:
[`docs/results/customer-quantities.md`](results/customer-quantities.md), rewritten from the data by
one command.

```bash
# The three as-of tables, at the month ends of the store (or --as-of ...): reads store/ only.
python -m pipeline.features --store data --out data
# The out-of-distribution store, priced by a reference model trained on the main store.
python -m pipeline.features --store data/ood --out data/ood --source data
# Offline evaluation (reads truth/, rule 12): rewrites docs/results/customer-quantities.md.
python -m ml.reference.report --data data --results docs/results/customer-quantities.md
```

| Output | Content | Who reads it |
| --- | --- | --- |
| `features/customer_features` | Behaviour features and the return residual per customer and decision time | The risk model (US-204), the case card |
| `features/customer_value` | V_c and its components | The quadrant (spec 8.6), the simulator; never the risk model (rule 6) |
| `features/evidence_quality` | Sufficient or insufficient, the components, the missing evidence | The policy (US-307), the case card |

`pipeline.features` never reads `data/truth/` (rule 12); only `ml.reference.report`, an offline
evaluation job, does.

## Decision time

Every quantity is computed at a decision time T from a **snapshot**: the store rows whose
`available_at` is at or before T, read with `contracts.io.read_table(name, root, as_of=T)` (rule 7).
Display names and identifier hashes are never loaded (rule 6). A snapshot can be narrowed to an
earlier instant with the same filter, so one read serves several decision times.

**Settlement.** A line's withdrawal outcome is final when its return window has closed: the window
of the order's terms snapshot (ADR-05), plus one day for the request to be recorded,

    settled_at = max(delivered_at + return_window_days + 1 day, delivery available_at)

and the outcome is read at `settled_at` from the facts available by then. A line is an
**observation** at T when it is settled by T and was not returned for non-conformity. A
non-conformity return (defective, damaged in shipping, wrong item, not as described) follows the
guarantee path and is not customer behaviour (rule 4), so it is never an observation, a return, a
reason share or an evidence item of the abuse features. A line delivered yesterday is not an
observation yet: counted as kept, it would bias return rates and residuals downwards.

**Point in time.** A line is priced with what was known **when it was ordered**: history counts of
the lines settled before its order instant, leaving its customer out. Its own outcome settles at
least a month later, so it can never be inside its own features. The first implementation computed
leave-one-out encodings at the training instant instead; that leaks the label (within a variant, a
returned line gets a lower rate than a kept one) and the trees decoded it: in-sample AUC 0.92,
out-of-time AUC 0.44, measured on the generated store on 13 Oct 2026 (synthetic). The checkout and
the request features use the same semantics.

**MOD-08 tests.** The feature tables at T are identical whether computed from the full store, from
the rows available at T only, or from the full store plus late facts (events before T recorded
after T: a delivery, a return, an identifier); request features computed from a later snapshot equal
those computed when the request arrived (`tests/test_features_generated.py`,
`tests/test_features_lines.py`).

## Customer features (`features/customer_features`)

One row per account known at T. Rates and shares are null, not zero, when there is nothing to
divide by: a new account is not a customer who never returns.

| Column | Definition at T |
| --- | --- |
| n_orders, n_lines, n_delivered_lines | Known at T |
| n_observations | Settled lines, not returned for non-conformity |
| return_rate | Withdrawal-path returns among the settled lines |
| n_returns, return_rate_to_date | Withdrawal-path returns known at T, over the delivered lines (minus non-conformity returns): a lower bound available before the windows close |
| size_share, changed_mind_share, not_as_expected_share, other_reason_share | Reason shares of the n_returns |
| median_days_to_return | Median days since delivery of the n_returns |
| tenure_days | Days since the account was opened (not "age": rule 6 forbids the word, ADR-11) |
| n_welcome_coupons_used, n_promo_coupons_used | Coupon redemptions (promo and referral in the second) |
| coupon_order_share, weekend_order_share | Orders with a coupon; orders placed on Saturday or Sunday (UTC) |
| mean_line_price_eur | Mean price of the lines ordered |
| expected_returns, residual, residual_z | sum p_l, R_c and z_c of spec 8.2 over the observations |
| reference_model_version | The reference model that priced the lines |

## Product/cohort reference and personalized predictor (`ml/reference`)

| | Reference (residual, rule 8) | Personalized (checkout M1, MOD-09) |
| --- | --- | --- |
| Variant and product withdrawal-return rates, shrunk to the category rate, and their line counts | yes | yes |
| Category rate and code, price, size range | yes | yes |
| Order context known at checkout: lines in the order, other sizes or colours of the same product | yes | yes |
| The customer's own settled return rate and line count | **never** | yes |

Every history count leaves the line's customer out, so the reference never reads the customer's
own history; a test flips a customer's returns and checks that the reference inputs of its lines do
not move while the personalized ones do. The order context is the cohort part: a bracketed pair is
known at checkout and is not the customer's history, and it is how a size customer such as Giulia
keeps a low residual (spec 7).

Training (spec 8.1): XGBoost on CPU (`xgboost-cpu`, hist), depth 4, learning rate 0.1, gamma 1,
lambda 2, early stopping after 5 rounds on 10% of the customers, isotonic calibration on the same
customers, probabilities kept in [0.01, 0.99]. One thread: on a shared CPU OpenMP threads stall at
every barrier (13 Oct 2026: 0.04 s with one thread, 25 s with four for 50 rounds on 7,046 lines).
The shrinkage pseudo-count k of the variant and product rates is chosen on the training lines from
a fixed grid. With fewer than 1,000 lines or a single outcome the model falls back to the smoothed
variant rate.

Calibration is evaluated with the split of spec 8.4: trained at 2026-07-01T00:00Z on the lines
settled by then, evaluated on the lines ordered from July to September and settled by the
extraction (MOD-06).

**Residual at a decision time.** The feature tables use the reference trained at T itself and
**cross-fitted by customer** (5 folds): each customer's lines are priced by a model trained on the
other customers only. The out-of-distribution store is priced by a model trained on the main store
at the same instant (`--source`), never on itself (US-105).

## Residual (spec 8.2, US-206, UNIT-02)

    R_c = sum r_l - sum p_l        z_c = R_c / sqrt(sum p_l (1 - p_l))

over the customer's observations at T, r_l = 1 for a withdrawal-path return. z_c is null, never
infinite, when the variance is zero (no observation). UNIT-02 checks five customers against a hand
computation. The residual is an indicator, not a probability of abuse. MOD-09 compares it with the
personalized predictor in the results file: the personalized residual absorbs part of the anomaly
it should measure.

## Value (spec 8.5, US-205, UNIT-03)

    V_c = m * sum(price of kept lines) - L * #returns - sum(coupon discounts)

over the orders placed in the 365 days up to T, with what is known at T: a line is returned when a
return request for it is known at T, whatever the reason (the logistics cost is paid for a defect
return too, as spec 8.5 counts every return), and kept otherwise. m = 0.55 and L = 8 EUR are the
merchant hypotheses of spec section 4, versioned in `VALUE_ASSUMPTIONS` (`value-v1-2026-10-13`).
Amounts are Decimal cents, rounded half up once.

UNIT-03: kept + returned - discounts equals the sum of the order totals to the cent, because an
order total is its line prices minus its coupon discount; `reconcile()` lists any customer where it
does not hold, and the results file reports the count. Value never feeds risk: it has its own table
and no customer feature reads it (rule 6, tested).

## Evidence quality (US-209, US-307, D-15)

| Component | Definition | Threshold (`evidence-v1-2026-10-13`) |
| --- | --- | --- |
| Observations | The account's own settled lines; the linked accounts' are reported (`n_observations_linked`, with the account graph) and never counted (review F14) | At least 4 |
| Identifier completeness | Share of the spec 8.3 identifier weight recorded (card 1.0, e-mail 0.9, device 0.8, address 0.6, phone 0.6, IP prefix 0.2) | At least 0.6: missing any two of card, e-mail and device is insufficient; missing the phone alone is not |
| Reason coherence | Share of withdrawal returns whose reason is specific (size, changed mind, not as expected) and fits the item (a size reason on a sized item) | At least 0.5, judged from 2 withdrawal returns on |

Below any threshold the outcome is **insufficient**, with one plain sentence per missing piece, for
example "observations: 2 order lines with a settled outcome up to 2026-07-01 00:00 UTC; at least 4
needed". The policy then gives standard terms: an insufficient case is never safe and never
suspicious (US-307).

The thresholds are design values, versioned; the results file measures what they do on the
generated store (share insufficient per component, by behaviour, OOD on a separate row) and sweeps
the observation threshold against the AUC of the residual among the customers it keeps, on the
training period only. A new value is a new version.

## Request-level and checkout features

`pipeline.features.requests.request_features(snapshot)` gives, per return request, the features at
the instant the request became known: days since delivery, reason, price, the variant's withdrawal
return rate (shrunk, customer left out) and line count, the category rate and, with a reference
model, p_l. Non-conformity requests are left out unless asked: they follow the guarantee path before
any scoring (rule 4, ADR-09).

`ml.reference.checkout.checkout_probabilities(snapshot, model, customer_id, cart)` prices the lines
of a cart at checkout with the personalized predictor (M1, US-401 input).

## Known limits

- The first month of the store has no settled history (no line settles before about 1 November
  2025), so its lines are priced from the price, category and order context only.
- Evidence counts the account's own observations only (review F14). Counting the linked
  accounts' lines let an account with no settled line of its own into Q4 (C-ROSSI-2 at 2026-01-01
  in the review), and the FAIR-03 counterfactual, which keeps the evidence fixed, could not see it.
  So a new account in a ring is often insufficient on its own: at the end of the data, C-MARCO-2,
  -4 and -6 have 3 settled lines each and are insufficient alone, while C-MARCO-7, whose return is
  the demo case, has 5 (results file, golden cases; synthetic). The Q4 proposal on their cluster
  (coupons disabled) still covers them. `pipeline.features --clusters` fills
  `n_observations_linked` for display; the pipeline does not pass it.
- Evidence on own observations sends more abusive than other customers to insufficient evidence
  at the train end (rings are new accounts, wardrobers buy fewer lines): standard terms for them,
  never a restriction. The results file reports the gap at every decision time.
- Reasons are the generator's hypotheses (ASOS reason codes are masked), so reason coherence is
  rarely below its threshold on the synthetic store.
- `weekend_order_share` uses UTC days, as the generator does; the shop's local time would shift a
  few late-evening orders.
