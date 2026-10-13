# ADR-13: Data contracts v2

Status: accepted, 13 Oct 2026. Contract version `2.0.0`. Resolves review findings CON-01, CON-03,
CON-05, CON-08, CON-12, POL-7 and ASOS-7, and covers part of CON-02, CON-09, CON-10 and CON-11.
Changes `contracts/`, `policy/types.py` (`Levers.to_contract`), CLAUDE.md rule 12 and spec section 7.

## Context

The v1 contracts had five problems.

- **Late facts on early rows (CON-01).** `store/orders.delivered_at` and `store/returns.status`
  sat on rows whose `available_at` is the order or request time. The rule 7 filter
  `available_at <= decision_time` let a delivery date or a refund that happened after the decision
  through.
- **No place for some data.** There was no table for account links (US-203, US-302, US-310), the
  catalogue, defective variants or the out-of-distribution scenario (US-105). There were no ASOS raw
  contracts and no calibration schema. Rule 12 said the ground truth is "for evaluation only", but
  spec 8.4 trains the risk model on synthetic labels (CON-08).
- **Terms snapshots without context (CON-12).** A snapshot did not say which order it belonged to,
  or which policy and quadrant produced it (ADR-05).
- **Three lever vocabularies (POL-7, CON-05).** The engine used `"instant_refund:false"`, the JSON
  examples used objects with free scopes, and spec 19.B used `welcome_coupon`. The Q4 examples were
  not something the engine can produce: 2 lever objects and 1 echoed id, where the engine gives 6.
- **Checks on names only (ASOS-7, CON-09, CON-10).** The forbidden-feature check was an exact-name
  set that missed every real ASOS protected column (`isMale`, `shippingCountry`, `Country_*`,
  `yearOfBirth`, `hash(...)`). Files had no value checks and no provenance.

## Decision

### 1. A row holds only facts known at its available_at

Three kinds of table:

- **Event tables**: `store/*`, with `event_at` and `available_at`.
- **As-of tables**: `features/*` and `scores/*`, with `as_of`.
- **Static tables**: `raw/*` and `truth/*`, with no decision time.

A fact learnt later is a row of its own. This is checked on every write:

- in an event table, `event_at <= available_at`, and every other timestamp of a row is
  `<= available_at`;
- in an as-of table, every timestamp is `<= as_of`.

| Table | v2 content | Change |
| --- | --- | --- |
| store/orders | order_id, customer_id, terms_snapshot_id, coupon_code, total_eur; event_at = placed | `delivered_at` removed |
| store/deliveries | order_id; event_at = delivered | new |
| store/returns | return_id, line_id, reason (`policy.types.ReturnReason`), days_since_delivery; event_at = requested | `status` removed |
| store/return_events | return_id, state in {notified, shipped_or_proof, received, refunded, rejected} | new: the per-path states of NFR-17 and spec 8.6 |
| store/identifiers | customer_id, kind in {card, email, device, address, phone, ip_prefix}, value_hash; event_at = first use by the customer | `ip` renamed `ip_prefix`; one row per (customer, kind, value) |
| store/terms_snapshots | + order_id (not null), source ("base" or a decision id), quadrant_at_purchase (nullable) | policy context of ADR-05 |
| store/coupons | coupon_code, customer_id, kind in {welcome, referral, promo}, discount_eur, order_id (nullable) | an event log: one row when a coupon is issued (order_id null), one when it is redeemed (order_id set, event_at = redemption) |
| store/variants | variant_id, product_id, category, brand, price_eur, size_range; event_at = listed | new; observable catalogue attributes only |

A terms snapshot belongs to the order it applies to, so `order_id` is not null. A checkout that ends
without an order is not in the store. Where the API stores live checkout snapshots (PostgreSQL, with
the same columns) is decided with E4, the second half of CON-12.

### 2. Ground truth and rule 12

- `truth/labels`: customer_id, behaviour in {normal, size, wardrober, promo_ring, shared_benign},
  ring_id (set exactly for promo_ring), family_id (set for every shared_benign account),
  scenario in {main, ood}.
- `truth/variants` (new): variant_id, base_return_rate, defective, defect_reason (set exactly when
  defective), defect_start (set exactly when defective), defect_end (null if the defect is never
  fixed in the period). The latent return rate and the defect flags are ground truth, not catalogue
  data, so they are not in `store/variants`.

Rule 12 now reads: "Running services (api, ui, demo) never read data/truth/; only offline training
and evaluation jobs do." Spec 8.4 trains the risk model on synthetic labels, which is an offline job.
Truth never reaches a service, a feature table or a score.

### 3. Features

- `features/links` (new) serves US-203, US-302 and US-310: customer_a < customer_b, kinds
  (list of identifier kinds), weight (w_ij of 8.3), weight_decayed (with time and d_k decay,
  <= weight), first_shared_at, last_shared_at, n_shared_events, max_sharers (largest d_k), disputed,
  as_of. Identifiers are recorded at first use, so a shared event is one identifier value used by
  both accounts:
  - first_shared_at and last_shared_at are the earliest and latest instants at which the two
    accounts came to share a value;
  - the decay of 8.3 runs from last_shared_at to as_of.
- `features/clusters` gains n_distinct_cards, creation_spread_days, welcome_coupons_per_account,
  cluster_return_rate (null before any delivered line) and strong_id_share. These are the 8.3
  features that keep the Rossi family out of Q3 and Q4.
- `features/product_anomalies`: status in {open, task_opened, resolved}, plus as_of.

### 4. Raw ASOS contracts (facts of ADR-10)

The ingest renames the source columns, drops the one-hot columns that repeat another column
(`Country_*`, `Brand_*`, `productType_*`) and adds `split` in {train, test}. The source repeats the
reason label `customerId_level_return_code_D` (and `variantID_level_..._D`): the first is
`reason_share_D` and the second, which comes after E, is `reason_share_D2`.

| Contract | Source columns → contract columns |
| --- | --- |
| raw/asos_events | hash(variantID) → variant_hash (int64), hash(customerId) → customer_hash (int64), isReturned → is_returned (bool) |
| raw/asos_customers | hash(customerId) → customer_hash, salesPerCustomer → sales, returnsPerCustomer → returns, customerReturnRate → return_rate, 13 reason codes → reason_share_{A,B,C,D,E,D2,F..L}, premier (bool); descriptive only: isMale → is_male, shippingCountry → shipping_country, yearOfBirth → year_of_birth |
| raw/asos_variants | hash(variantID) → variant_hash, hash(productID) → product_hash, hash(supplierRef) → supplier_hash, productType → product_type, brandDesc → brand, avgGbpPrice → avg_price_gbp, avgDiscountValue → avg_discount, salesPerProduct → sales, returnsPerProduct → returns, productReturnRate → return_rate, 13 reason codes → reason_share_* |

`DESCRIPTIVE_ONLY = {is_male, shipping_country, year_of_birth}` are kept only for descriptive
statistics (rule 6, ADR-11). The source year of birth holds the placeholder 1900 and values after the
data period; the contract does not range-check it, because it never feeds a model. These column names
were read from the pickle headers (origin: ASOS GraphReturns, OSF c793h; nature: real; 12 Oct 2026).

### 5. Calibration contract

The generator reads `contracts/json/calibration.schema.json`. The file holds only aggregates, never
row-level data, because the dataset is not redistributed. It contains:

- provenance {origin, period {start, end}, nature} and the ASOS splits used;
- overall rates: the event label rate and the product sales-weighted return rate (sum of returns
  over sum of sales), with event, customer and variant counts;
- per product type: variant_share, sales_share, price quantiles p10..p90 in GBP, and sales-weighted
  return-rate quantiles p10..p90;
- reason shares by masked code (A..L with D2);
- purchases per customer, quantiles p10..p90;
- join coverage: events with a customer row, a variant row, or both.

`contracts/calibration.check_calibration` adds the checks a JSON Schema cannot express: ordered
quantiles, shares that sum to 1 (tolerance 0.001) and a consistent period. The example
`contracts/examples/calibration.json` is illustrative and not measured. Its provenance says so, and
its numbers must never be shown as ASOS results.

### 6. Forbidden features

`is_forbidden_feature(name)` replaces the exact-name set:

- **How it splits a name**: case-insensitive. A name is split into words, so `yearOfBirth` gives
  year, of, birth and `hash(customerId)` gives hash, customer, id.
- **Forbidden words**: gender, sex, (fe)male, ismale, country, age, dob, birth, name and its
  variants, hash.
- **Forbidden fragments inside a word**: gender, country, ismale, yearofbirth, birth, hash.
- **Raw identifiers by whole name**: email, phone, ip, ip_prefix, address, device_id, card_number
  and similar; since amendment 1 also any name with an identifier stem that is not an aggregate
  (more stems and spellings in amendment 2).

Keys such as `customer_id` and counts such as `n_distinct_cards` stay allowed.
`forbidden_features(names)` is the call for every model's feature list (FAIR-01). The tests run it
on every `features/` and `scores/` schema and on the 74 real ASOS column names.
`FORBIDDEN_FEATURE_FIELDS` stays as a compatibility set of canonical names.

### 7. File I/O (DATA-01, DATA-04, NFR-16)

`contracts.io.write_table(name, table, root, provenance)` writes to `<root>/<name>.parquet`:

- **Structure checks**: column names (in any order), exact types (list element names may differ),
  nulls in non-null columns, and null list elements.
- **Value checks**: NaN is rejected everywhere (missing values are null), plus the temporal rules
  above and the checks in `contracts/checks.py`. These cover enums bound to the policy StrEnums,
  ranges (risk in [0, 1], `return_window_days >= 14`, `days_since_delivery >= 0`), keys and row
  rules.
- **Metadata**: the Parquet metadata gets `returnring.contract`, `returnring.contract_version` and
  `returnring.provenance` (canonical JSON {origin, period_start, period_end, nature in {real,
  synthetic, simulated}}).
- **Deterministic writing**: zstd level 3, fixed row group size, no wall-clock value, and an atomic
  rename. The same input gives byte-identical files, whatever its chunking.

`read_table(name, root, as_of=None)` checks the contract name, the version and the schema, and
returns `(table, provenance)`. With `as_of`, which must be timezone-aware, it keeps the rows of an
event table with `available_at <= as_of`, refuses an as-of table that holds a row computed after
`as_of`, and refuses a static table (amendment 1).

### 8. Lever catalogue (POL-7, CON-05)

`contracts/levers.py` has one entry per `policy.types.Levers` field, with `future_terms` expanded
into the five ReturnTerms fields. The scopes follow the lever table of spec 8.6.

| Lever id | Value | Scope |
| --- | --- | --- |
| return_window_days | integer >= 14 | future_orders |
| free_return_shipping | boolean | future_orders |
| instant_refund | boolean | future_orders |
| free_size_exchange | boolean | all_orders |
| coupons_enabled | boolean | future_orders |
| cluster_coupons_disabled | boolean | future_orders_cluster |
| transparency_notice | boolean | communication |
| size_advice | boolean | current_order |
| priority_size_exchange | boolean | all_orders |

A lever has two forms:

- **Object** `{id, value, scope}`, in JSON contracts. `Levers.to_contract(base)` returns the
  objects that differ from the standard levers on the base, in catalogue order.
- **String** `"id:value"`, the engine's format, wherever one identifier is needed: `lever_ids`,
  the LLM decision echo, the validator and the audit log. `format_lever_id` and `parse_lever_id`
  convert between the forms and reject unknown ids and values of the wrong type.

The catalogue also sets these rules:

- **Spec 19.B `welcome_coupon` is `cluster_coupons_disabled`**: the old example carried
  `{"id": "welcome_coupon", "value": false, "scope": "future_orders_cluster"}`, so by its scope it is
  the cluster-wide block, `cluster_coupons_disabled:true`. The customer's own coupons on future
  orders, the other half of the same row of 8.6, are `coupons_enabled`. Spec 19.B now shows the six
  levers of `contracts/examples/llm_case.json`.
- **`all_orders` is used only for the size exchange**, which the engine never restricts. A lever
  that makes terms worse applies only to future orders (rule 3, `policy/legal_guard.py`). An
  `all_orders` lever can only be granted: its value is always `true` (schema `const: true`,
  `contracts.levers._check_value`), and when the engine grants the free size exchange it also holds
  for the order being returned, even if its snapshot lacked it (`policy.engine.with_all_orders_levers`).
- **One value per lever**: the `levers` array has `uniqueItems` and at most one object per lever id
  (`maxContains: 1`); `contracts.levers.check_lever_objects` is the same check in Python.
- **`size_advice:false` is now a visible difference.** It is standard on, and no quadrant turns it
  off today.

The lever object schema in `llm_case.schema.json` and `decision_request.schema.json` is generated
from the catalogue, and a test keeps the files equal to it. It rejects unknown ids, wrong scopes,
wrong value types and windows below 14 days. `llm_case` also gains these rules:

- `missing_evidence` (US-307), a list of strings, is required and non-empty when evidence is
  insufficient;
- an insufficient-evidence case and a guarantee case have no quadrant and no lever.

The Q4 examples are now exactly what `policy.engine.decide` produces for the Navigli base with a
cluster: 6 levers in `llm_case.json` and the same 6 ids in `llm_output.json`.

## Consequences

- The generator, the ingest, the graph and the features write through `contracts.io.write_table`,
  and read store tables through `read_table(..., as_of=...)`.
- The ingest (US-101) writes `raw/asos_*` with the mapping above, and the profiler writes
  `calibration.json`.
- Infra follow-up, done in amendment 1: the training jobs mount `data/` read-only, and
  `tests/test_infra.py` keeps `data/truth/` away from every other service.
- `jsonschema` is a development dependency. A runtime check of JSON contracts would need it in
  `requirements/base.txt` (MIT).
- Still open from the review:
  - `display_name` stays in `store/customers`: it is display-only and forbidden as a feature
    (CON-04 suggested a separate table);
  - the example `cluster.id` "R-MARCO" is a truth ring id, and the graph should produce opaque
    cluster ids (CON-08);
  - "last shared use" in the sense of 8.3 would need an identifier observation log
    (`store/identifier_uses`) if first-use rows prove too coarse.
- Files written under contract v1 cannot be read. No v1 file exists.

## Amendment 1 (13 Oct 2026): review findings C1 to C14, G5 and F2

The contract version stays `2.0.0`. The only schema change is the nullability of
`features/links.kinds`, and no `features/` or `scores/` file had been written yet. Every other change
tightens a check on write or on read.

**File I/O (`contracts/io.py`)**

- **C1, determinism.** `write_table` combines the chunks of every column before writing. Equal
  rows now give equal bytes at any chunking. Before, 200,000 order lines in 1 chunk and in 64 chunks
  gave different files.
- **C3, read guard.** `read_table(name, root, as_of=T)` now works on as-of tables too. It raises
  `ContractError` if a row has `as_of > T`, because those rows were computed after the decision. It
  refuses `as_of` on static tables (`raw/`, `truth/`).
- **C3, keys.** Every key of an as-of table ends with `as_of`, so one file may hold several
  snapshots.
- **C10, floats.** NaN and +/-inf are rejected in every float column; missing values are null.
- **C11, provenance and family.** The nature must fit the table family: `real` for `raw/`;
  `synthetic` for `store/` and `truth/`; `synthetic` or `simulated` for `features/` and `scores/`.
  A caller writes another nature only by naming it in `write_table(..., nature_override=...)`, for
  example a synthetic test fixture shaped like the ASOS tables.
- **C11, provenance and period.** In an event table, every `event_at` (UTC day) lies in the
  provenance period. The generator therefore gives `store/customers` and `store/variants` a period
  that starts with their oldest account or listing. Its origin still names the store period.
- **C12, missing provenance.** A file without provenance metadata raises `ContractError`, not
  `KeyError`.

**Value checks (`contracts/checks.py`)**

- **C2.** In an as-of table, a date column is also checked against the UTC day of `as_of`, so a
  future `features/product_anomalies.week` is rejected.
- **C7.** `features/links.kinds` is not null. It holds at least one kind, and each kind once.
- **C8.** `store/coupons` is an issue-and-use log. Each `coupon_code` has exactly one issue row
  (`order_id` null) and at most one redemption, and the redemption comes no earlier than the issue.
- **C9.** `truth/variants.defect_end` is null when `defective` is false.

**Forbidden features (C4)**

Identifier stems are forbidden unless the name is clearly an aggregate (`n_*`, `*_count`, `shared_*`,
`*_share`). The stems are e-mail, address, IP, phone, device, postcode or zip, and fingerprint.
Nationality and citizenship are forbidden like country, in any form. So `email_normalized`,
`shipping_address`, `ip_prefix_24` and `zip_code` are forbidden, and `n_shared_devices` and
`email_share` are allowed.

**Levers (C5, C6, C13)**

See section 8. An `all_orders` lever carries only `true`, and the engine applies a granted free size
exchange to the current order. Lever arrays have one object per lever id. Spec 19.B
`welcome_coupon` is `cluster_coupons_disabled`. The JSON contracts were regenerated from
`contracts.levers.lever_list_json_schema`.

**Layout and rule 12 (G5, F2)**

- The out-of-distribution truth is written under `<out>/truth/ood/` (files
  `truth/ood/truth/*.parquet`), next to the main truth. Its store is in `<out>/ood/store/`.
  `data/truth/` therefore holds every ground truth.
- In `infra/docker-compose.yml`, the offline jobs `trainer-gbm` and `trainer-gnn` mount `data/`
  read-only, the generator job `pipeline` writes it, and no other service mounts `data/` or any path
  with a `truth` part. `tests/test_infra.py` checks this for every compose file.

## Amendment 2 (13 Oct 2026): review findings P3 and P4

The contract version stays `2.0.0`. No schema changes. One write path changes, and one check is
tightened.

**Atomic writes (P3, `contracts/files.py`)**

`write_table`, the ASOS calibration and profile report (`pipeline/ingest/asos_profile.py`) and the
ASOS downloader now use one helper. It writes to a temporary file, `.<name>.part`, in the target's
folder, then renames that file over the target. The temporary file is always created anew: the
helper first removes whatever is at that name, then opens it with
`O_CREAT | O_EXCL | O_NOFOLLOW`. A symlink planted at that name is therefore never followed, and a
symlink at the target is replaced, not written through. If anything fails, for example a directory
in the way of the target, nothing is left at the temporary name. Before, `write_table` and
`write_calibration` wrote `<name>.tmp` through a planted symlink, and `write_table` left the
`.tmp` file behind on failure. Writing through the file descriptor gives the same Parquet bytes
as writing to a path (checked on the 205,462 order lines of the scale 1.0 store).

**Forbidden features (P4)**

Identifier names written without separators or abbreviated are forbidden too, unless they are
aggregates:

- **More stems**: `addr`, `mobile`, `msisdn`, `imei`, `mac`, `card`, `cards` and `pan` (a card
  number).
- **Fragments inside a word**: `ipprefix`, `ipaddr`, `ipv4`, `ipv6`, `msisdn`, `imei`, `cardlast`,
  `cardbin`, `cardnumber`, `creditcard`, `debitcard` and `paymentcard`.
- **Word forms**: `ip24`, `customerip`, `remoteip`, `shippingaddr`, `shippingzip`, `zip5` and
  similar.

`ipprefix24`, `remote_addr`, `card_last4`, `msisdn` and `mobile_number` are therefore forbidden.
`n_distinct_cards`, `card_count`, `free_return_shipping`, `zipper_defect_rate` and `discard_rate`
stay allowed. The verdict changes for no column of any contract schema and for no name in the
spec.

Round 3 of the same review made the verdict independent of separators and removed two false
positives:

- A fragment counts inside one word, or across words only where a word starts, so
  `time_in_transit` ("timein") no longer reads as `imei`.
- `mobile`, `card` and `cards` name an identifier only as the head noun (`customer_mobile`,
  `credit_card`, `card`), not as a modifier or after a preposition (`is_mobile`,
  `mobile_checkout`, `paid_by_card`, `gift_card_used`).
- More fragments and word forms: `mobilenumber`, `postalcode`, `cardnum`, `cardno`, `cardid`,
  `cardtoken`, `cclast`, `ccnum`, `ipblock`, `iprange`, `ipsubnet`, `useragent`, `advertisingid`,
  `androidid`, `idfa`, `addr1`, `ip24prefix`, and IP names after signup, lastlogin, visitor, buyer,
  registration, account and request.

## Amendment 3 (13 Oct 2026): customer features, value and evidence (US-205, US-206, US-209)

The contract version stays `2.0.0`: no `features/` file had been written. Definitions are in
`docs/customer-features.md`; the code is `pipeline/features/` and `ml/reference/`.

| Table | Change |
| --- | --- |
| features/customer_features | Extended to the decision-time behaviour features: n_orders, n_lines, n_delivered_lines, n_observations (settled lines), n_returns (withdrawal path), return_rate, return_rate_to_date, size, changed_mind, not_as_expected and other_reason shares, median_days_to_return, tenure_days, n_welcome_coupons_used, n_promo_coupons_used, coupon_order_share, weekend_order_share, mean_line_price_eur, and the residual of spec 8.2: expected_returns, residual, residual_z, reference_model_version. return_rate, size_share and changed_mind_share become nullable: a customer without a settled line has no rate, which is not a rate of zero |
| features/customer_value | New. V_c of spec 8.5 with its components (kept, returned, discount, order totals), counts, window_start and assumptions_version. A table of its own, so no risk model reads value by accident (rule 6) |
| features/evidence_quality | New. evidence_quality (sufficient or insufficient), n_observations, n_observations_linked (null without the account graph), identifier_completeness, missing_identifier_kinds, n_reasons, reason_coherence, missing_evidence (plain sentences, US-307) and thresholds_version |

The checks add ranges, `n_observations <= n_delivered_lines <= n_lines`, `n_returns <= n_lines`,
`n_observations <= n_observations_linked`, the EvidenceQuality enum and the identifier kinds. No
new column is a forbidden feature (`tenure_days`, not "account age": the word age is forbidden).

## Amendment 4 (13 Oct 2026): scores of the risk model (US-204)

The contract version stays `2.0.0`: no `scores/` file had been written. Code: `ml/risk/` and
`ml/scoring/`; method in `docs/risk-model.md`.

| Table | Change |
| --- | --- |
| scores/scores | Unchanged columns. Two checks: `quadrant` is null unless `evidence_quality` is sufficient (an insufficient case has no quadrant, as in `llm_case`), and `model_version` is not blank. `shap_top3` holds a JSON list of three {feature, value, contribution, direction, phrase_en, phrase_it}: contributions to the score, not causes |
| scores/request_scores | New. One row per return request scored at its own decision time: return_id, customer_id, decided_at (when the request became known), path (`policy.types.Path`), context_as_of (the customer snapshot used, the last one strictly before decided_at; null when the account had none), risk, evidence_quality and value_eur (at context_as_of), quadrant, shap_top3, model_version, as_of |

Checks of `scores/request_scores`: unique (return_id, as_of); risk and shap_top3 present exactly on
the withdrawal path, so a defect or non-conformity request is listed and never scored (rule 4);
risk in [0, 1]; quadrant null on the guarantee path and without sufficient evidence;
`context_as_of < decided_at` (MOD-08: a snapshot taken at the request instant already counts the
request); every timestamp `<= as_of`, as in every as-of table.

## Amendment 5 (13 Oct 2026): phase 2 review F1, F6, F14, F15, F17 to F20, F22

The contract version stays `2.0.0`. Files written before this amendment fail `read_table` with a
`ContractError` that names the reason (a scores/ schema that differs, or an origin longer than 300
characters), and `make pipeline` rewrites them all. Code: `contracts/`, `ml/scoring/`, `ml/risk/`,
`pipeline/*/build.py`, `pipeline/generator/core.py`.

| Change | What and why |
| --- | --- |
| Short origin, lineage key (F17) | `Provenance.origin` holds at most 300 characters, the limit of the API's `Provenance.origin` (ADR-15), checked when a provenance is made and when a file is read. The full chain of origins of a derived file goes to its own metadata key, `returnring.lineage`: this file's origin and details, then ` <- ` and the chain of the file it was computed from (`Provenance.derive`). `returnring.provenance` keeps the four fields of the API. A source file (store, truth, raw) has its long details (the calibration's origin) in its lineage too. |
| Joins on the same data (F22) | `contracts.io.require_derived(child, parent, ...)`: a features/ or scores/ table is joined to a store only when its lineage ends with the store's whole chain. Customer ids are the same at any seed, so a table left over from another store would otherwise join silently. Checked by `ml.scoring.read_inputs`, `ml.risk.data.GraphSnapshots` and `pipeline.features.build.clusters_at`. |
| Thresholds with every quadrant (F15) | `scores/scores` gains `risk_threshold` (float64, not null) and `value_threshold_eur` (EUR, not null); `scores/request_scores` gains both, nullable: `risk_threshold` with `risk`, `value_threshold_eur` with `value_eur` (the 70th percentile at `context_as_of`). New check `QuadrantRule`: where the evidence is sufficient, the quadrant is `policy.engine.quadrant_for` of the risk, the value and the two thresholds on the same row. The API reproduces and shows every quadrant without reading features/ or the models. |
| Direction words (F18) | `shap_top3[].direction` is `raises`, `lowers` or `unchanged` (a zero contribution), the words of the API's `ScoreContribution.direction` plus `unchanged`; the API maps `unchanged` or leaves such a factor out. |
| No snapshot before a request (F19) | A withdrawal-path request whose account has no snapshot before `decided_at` has `evidence_quality = insufficient` (never null), no value and no quadrant: standard terms with a missing-evidence reason (US-307). Check: `evidence_quality` present exactly on the withdrawal path. |
| Guarantee path has no model version (F20) | `scores/request_scores.model_version` is nullable: present exactly on the withdrawal path, null on the guarantee path, which is not scored (rule 4); the path column says why. |
| Decision time after training (F1) | `ml.scoring` refuses a decision time before the models' training end (rule 7: the scores would be in-sample, with labels from after the decision); `backtest=True` (`--backtest`) allows it for an offline check and writes "in-sample backtest" into the origin. |
| Evidence of the account only (F14) | `features/evidence_quality.n_observations_linked` stays a reported column: the outcome counts the account's own settled lines only. Counted, linked observations let an account with no line of its own into Q4, and the FAIR-03 counterfactual, which keeps the evidence fixed, could not see it. |
| Labels and keys are never features (F6) | `contracts.parquet.label_or_key_features`: every column of `truth/*`, the derived label `abusive`, and every row key (any name ending in `_id`, the link pair, `coupon_code`, `value_hash`). `ml.risk.features.check_feature_names`, `ml.reference.features.check_feature_names` and `ml.propensity.features.check_feature_names` refuse them, with the forbidden features of section 6. |

Offline evaluation outputs (graph `evaluation.json`, risk `metrics.json`, FAIR-02/03 of
`ml.scoring --truth`) are written under `artifacts/eval/` and hold counts and metrics, never a
customer or a cluster listed with its ground truth (review F2). A running service needs only
`artifacts/risk/models`; narrowing its mount is an infra change (`tests/test_infra.py` holds the
check as a strict expected failure until then).
