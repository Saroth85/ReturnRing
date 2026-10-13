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
  and similar.

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
event table with `available_at <= as_of`. On any other table it refuses `as_of`.

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

- **Spec 19.B `welcome_coupon` is `coupons_enabled`**: the customer's own coupons on future orders.
  The cluster-wide half of the same row of 8.6 is `cluster_coupons_disabled`.
- **`all_orders` is used only for the size exchange**, which the engine never restricts. A lever
  that makes terms worse applies only to future orders (rule 3, `policy/legal_guard.py`).
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
- Infra follow-up, outside this change: offline training jobs need a read-only mount of
  `data/truth/`. Today `tests/test_infra.py` allows only the `pipeline` service to mount `data/`.
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
