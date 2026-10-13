# Abuse risk per customer and per return request

Story US-204 (spec 8.4 and 8.6); tests MOD-04 to MOD-08, MOD-12, FAIR-01 to FAIR-03 (spec 12).
Code: `ml/risk/` (training and evaluation, offline) and `ml/scoring/` (batch scoring). Contracts:
`scores/scores` and `scores/request_scores` (ADR-13, amendment 4). Measured numbers:
[`docs/results/us-204-risk.md`](results/us-204-risk.md), rewritten from the data by one command.

```bash
# Offline job (reads data/truth, rule 12 as amended by ADR-13): split, ablation A0..A3, request
# model, OOD rows, models in artifacts/risk/models, metrics in artifacts/eval/risk/metrics.json
# (offline outputs, never mounted by a service), batch scores in data/scores, FAIR-02/FAIR-03, the
# golden checks and FAIR-03 at every month end, and the results file. Exit 1 when a golden check
# fails. `make risk`.
python -m ml.risk --data data --out artifacts --scores data --results docs/results/us-204-risk.md

# Batch scoring with the saved models (never reads data/truth): every customer and every open return
# at the last snapshot of features/customer_features, or at --as-of. A decision time before the
# models' training end is refused (rule 7); --backtest allows it for an offline check and writes
# "in-sample backtest" into the origin. `make score AS_OF=...`.
python -m ml.scoring --data data --models artifacts/risk/models --out data [--as-of ISO]
# Offline evaluation run only: also FAIR-02 and FAIR-03 on the held-out customers.
python -m ml.scoring --data data --out data --truth data --fairness artifacts/eval/scores/fairness.json
```

Inputs: `features/customer_features`, `features/customer_value` and `features/evidence_quality`
(`python -m pipeline.features`, monthly snapshots), `features/clusters` (`python -m pipeline.graph`,
used when it holds the instant; other instants are built in memory by the same code from the store
known then), and the store for the request features (`pipeline.features.requests`). Every
features/ table must have been computed from the store it is joined to: its lineage ends with the
store's origin (ADR-13 amendment 5, review F22), otherwise the job stops.

## Labels and the two scoring units

Labels come from the synthetic ground truth: wardrober and promo_ring are 1; normal, size and
shared_benign are 0 (spec 8.4). Only offline jobs read them (`ml/risk/labels.py`).

| Unit | Rows | Features |
| --- | --- | --- |
| Customer (nightly) | One per account known at the decision time T | `features/customer_features` and `features/clusters` computed at exactly T |
| Request (at the moment) | One per withdrawal-path return request, at its `decided_at` (the instant it became known) | The customer row of the last snapshot computed strictly before `decided_at`, plus days since delivery, reason, item price, and the item's return rate and line count among other customers at `decided_at` |

A snapshot computed at `decided_at` or later would already count the request itself (n_returns,
return rate), so the request unit never uses one (MOD-08). With the monthly snapshots of
`pipeline.features`, a request sees customer features up to a month old, and an account opened
after the last snapshot has none: the nightly batch of a deployment would shorten that gap to a
day. Such a request is scored with missing customer features and gets insufficient evidence: no
quadrant, standard terms with a missing-evidence reason (US-307). Defect and non-conformity
requests follow the guarantee path and are never scored (rule 4): `scores/request_scores` lists
them with their path, a null risk, no evidence and no model version.

## Feature sets (ablation, MOD-04)

| Set | Features | Learner |
| --- | --- | --- |
| A0 | return rate to date, median days to return, mean line price | Three rules, `ml/risk/rules.py` |
| A1 | A0 + 15 individual behaviour features (counts, rates, reason shares, tenure, coupons, weekend orders) | XGBoost |
| A2 | A1 + the 8 cluster features of spec 8.3 | XGBoost |
| A3 | A2 + expected returns, residual R_c and z_c of spec 8.2 | XGBoost (the product's customer model) |
| A4 | A3 + GraphSAGE embeddings | Hook only (`EmbeddingSource` in `ml/risk/features.py`): phase 6, GPU |
| Request | A3 + days since delivery, item price, item return rate and lines, category return rate, reason (one-hot) | XGBoost |

**A0, the returnguard-like baseline**, implemented here (no dependency): a customer fires the rule
"return rate to date >= r", "median days to return <= d" (sent back soon after delivery) and
"mean line price >= v" (dear items). The score is the share of rules that fire. Each threshold is
the training quantile (5% to 95%) with the best single-rule F1; nothing is set by hand. "Value"
here is the price of the items, never the customer value V_c.

**Rule 6.** No feature set holds a forbidden feature (`contracts.parquet.forbidden_features`,
FAIR-01) or a column of `features/customer_value`; `ml/risk/features.py` checks both when it loads
and `ml/risk/model.py` before every training. Value is read only to choose the quadrant and, in
the evaluation, who counts as a high-value customer. A test triples every value and finds the same
risk scores.

## Split (MOD-07) and time order

- **Groups**: customers joined by a graph cluster at the evaluation time, or by a ground-truth
  ring or family, form one group (connected components), so a ring the graph does not link is
  still never split.
- **Sides**: the groups are stratified by their most specific behaviour (ring, wardrober, family,
  other) and shuffled with seed 42; 30% of each stratum is test, 20% of the rest validation.
- **Time**: the models learn from the rows computed at the train end (the first nine months of the
  store: 2026-07-01), validation uses the validation groups at the same instant, and the test
  groups are evaluated on the rows computed at the evaluation time (2026-10-01). For requests:
  train and validation requests arrived before the train end, test requests after it.
- Every model of the ablation and the request baselines use exactly these rows.

## Models, calibration and thresholds

- XGBoost on CPU (`xgboost-cpu`, hist, one thread, deterministic) with the spec 8.1 starting point:
  depth 4, learning rate 0.1, gamma 1, lambda 2, early stopping after 5 rounds on the validation log
  loss. The MI300X run of phase 6 uses the same parameters with `device="cuda"`.
- Isotonic calibration on the validation groups (`ml.propensity.calibration`, stored as JSON),
  probabilities kept in [0.0001, 0.9999]. MOD-06 asks ECE <= 0.05 on the held-out rows.
- **Risk threshold (spec 8.6)**: the lowest calibrated risk whose rule "risk >= t" has precision >= 0.8
  on the validation groups. Calibrated on the same rows, precision grows with t, so the lowest such
  t is well defined. When no t reaches 0.8, nobody is high risk.
- **Value threshold**: the 70th percentile of `value_eur` at the snapshot.
- Both thresholds are written on every score row (`risk_threshold`, `value_threshold_eur`, ADR-13
  amendment 5): a request row carries the value threshold of its own snapshot, and the contract
  checks that each quadrant is `quadrant_for` of its row.
- **Quadrant**: `policy.engine.quadrant_for`; null when the evidence is insufficient (standard terms,
  US-307). The engine decides; the models only estimate (rule 1). Q3 and Q4 need a human approver
  (rule 5).
- **Decision time**: never before the models' training end (rule 7, review F1). The models of a run
  are trained at the train end; scoring an earlier snapshot would score train-side customers
  in-sample with labels from after the decision.

## Explanations (MOD-05)

XGBoost's native TreeSHAP (`Booster.predict(pred_contribs=True)`): exact Shapley values of the
trees on the log-odds scale; calibration is monotone, so order and sign hold for the calibrated
risk. The top 3 by absolute contribution, among the features whose value is known (a factor shows
a value), go to `shap_top3` as JSON objects {feature, value, contribution, direction, phrase_en,
phrase_it}; direction is `raises`, `lowers` or `unchanged`, the words of the API (review F18). The phrases come from a fixed table (`ml/risk/explain.py`), say "a contribution to the
score, not a cause" / "un contributo al punteggio, non una causa", and hold no number, no verdict
word, no decision word and no quadrant (checked with `llm.text`). The API maps `phrase_en` or
`phrase_it` to the `phrase` of `llm_case.risk.top_factors`.

## Metrics

On the held-out test groups, and on the OOD store on rows of their own (MOD-12):

- AUPRC (average precision) and precision at 50 and 100, on the score before calibration (isotonic
  calibration merges scores into ties); ties at the k-th score are shared out;
- at the validation threshold, on the calibrated risk: customers flagged, precision, recall,
  false-positive rate, and above all the false-positive rate on high-value customers (the 30% of
  non-abusive customers with the highest value);
- ECE (ten equal-width bins).

**FAIR-02** (spec 12, US-605): false positives (non-abusive customers flagged by the model, and in
Q3/Q4) by segment of the held-out customers: new customers (account under 90 days), linked accounts
and families, low-data groups (fewer settled lines than the evidence threshold), tenure bands,
price bands (thirds of the mean line price) and main category. Per dimension, the gap is the
largest segment rate minus the smallest (spec 12: "scarto <= 5 punti tra" segments; the API's
`max_gap` is the same quantity); FAIR-02 fails when a gap exceeds 5 points for either measure. The
results print every segment's size next to its rate: segments of 20 to 60 customers are noisy, and
one false positive in 22 customers is already 4.5 points (review F8: the first version compared
each segment with the pooled rate and called a breach a warning).

**FAIR-03**: no shared_benign customer in Q3/Q4 because of the cluster alone. Counterfactual: the
cluster features computed on the account alone, same value, evidence and thresholds. Measured at
the evaluation time and at every month-end snapshot (review F21); before the train end the scores
are in-sample backtests, marked as such. The output holds counts, never a customer id (rule 12).

**OOD rows by behaviour** (review F4): the generator's wardrober rules are the same in both stores
(the OOD scenario changes rings, families and missing identifiers only), so every OOD row is also
reported per abusive behaviour, each against the non-abusive rows. The wardrober rows are in
distribution; the ring rows carry the shift.

## Known limits

- **Circularity.** The generator plants the behaviours the features look for; the main store
  measures consistency with the generator. The OOD store changes the ring, family and identifier
  rules, not the wardrober rules: its ring rows are the stress test (MOD-12), its wardrober rows are
  in distribution (review F4).
- **Labels are customers.** A request inherits its customer's label: an ordinary size return by a
  wardrober counts as abusive. Pilot data would need labels per request.
- **Snapshot cadence.** The request unit uses the monthly snapshots of `pipeline.features`; see
  above.
- **Evidence of the account only.** `features/evidence_quality` counts the account's own settled
  lines; the lines of linked accounts are at most reported (`n_observations_linked`, with
  `pipeline.features --clusters`) and never counted (review F14). Counted, they let an account
  with no settled line of its own into Q4 (C-ROSSI-2 at 2026-01-01 in the review), and FAIR-03,
  whose counterfactual keeps the evidence fixed, could not see it. So a new ring account with three
  settled lines stays insufficient: no quadrant, standard terms; the Q4 proposal on its cluster
  (coupons disabled) still covers it. If linked observations are ever counted, the FAIR-03
  counterfactual must recompute the evidence without the linked accounts too, and the F-ROSSI and
  FAIR-03 checks must hold at every month end (they are measured there already).
- **A4** waits for the GraphSAGE embeddings of phase 6.
- **Ground-truth groups.** The split joins ground-truth rings and families to the graph clusters.
  This is a stricter split than the graph alone, possible only offline.
