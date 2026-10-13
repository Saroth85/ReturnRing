# ADR-10: ASOS GraphReturns is evaluated with two protocols

Status: accepted, 13 Oct 2026. Closes open question D-03. Affects US-101, US-201, US-202, US-206,
MOD-01, MOD-02, MOD-09 and benchmarks B1 and B2.

## Context

The ASOS customer and product features are aggregates computed over a window that contains the
labelled purchases. A feature built from the label cannot be known at decision time (CLAUDE.md rule 7,
NFR-15), so a model trained on it measures the dataset, not the product.

Measured on 12 Oct 2026 on the real dataset (origin: ASOS GraphReturns, OSF c793h; nature: real data;
read-only static analysis, no pickle executed):

| Check | Result |
| --- | --- |
| One-feature rule `customerReturnRate >= 0.5`, events with customer features | F1 0.759 train, 0.753 test |
| Same rule with the event's own label removed (leave-one-out), threshold tuned to 0.3 | F1 0.705 train, 0.699 test |
| Events of customers with `customerReturnRate == 1.0` that are returned | 99.99% (102,106 train events) |
| Product aggregates in the train and test product tables | Identical for all 405,505 shared variants |
| Test events of variants with `productReturnRate == 1.0` that are returned | 100% (2,378 events) |
| Paper XGBoost F1 (arXiv:2302.14096) | 0.774 |

The spec go/no-go for US-201 (F1 >= 0.75) is therefore met by a single leaky feature, and the paper
does not state the window its features cover.

## Decision

Every ASOS model runs under two protocols, on the same splits, and both results are reported (rule 13).

| Protocol | Features | Used for | Label shown next to every number |
| --- | --- | --- | --- |
| A, paper | The paper's node features, minus the forbidden ones (rule 6, ADR-11) | Benchmarks B1 and B2 (CPU vs MI300X timing) and the comparison with the paper | "Paper protocol: features computed over the labelled window, not available at decision time" |
| B, ReturnRing | Leave-one-out counts per event (customer and product sales and returns excluding the event itself), static product attributes (product type, brand, price, discount); no return rates, no reason shares | The return-probability model used by the product, the product/cohort reference model of the residual (rule 8) | "Decision-time protocol" |

Go/no-go for US-201 under protocol B: XGBoost beats the best single-feature leave-one-out rule on the
same split, on both F1 and cross-entropy, with calibrated probabilities (MOD-06). The baseline is
re-measured in US-201 and reported with the model. The F1 >= 0.75 threshold applies only to protocol A,
and a protocol A number is never presented as product performance (NFR-16).

Protocol B still sees other purchases from the same window, because ASOS events carry no timestamp.
This is a declared limitation, written in the README next to the results.

## Dataset facts for US-101

| File | Bytes | sha256 |
| --- | --- | --- |
| event_table_training.p | 32,859,910 | 6d61ce2fc645e6720aed243d45174bfc7fc139dbed70b9306798c648c6a7fed6 |
| event_table_testing.p | 35,049,502 | bc0ae494b0d95be651c988da8e83142a9371aedbf28a6264614a2656321602fa |
| product_nodes_training.p | 143,204,587 | eb731fc46ba6712fb986971d0af235822ebc2317e38877f5627ee6b6f2f8d9e0 |
| product_nodes_testing.p | 143,221,671 | aa0d960208fb159df53a45a395de3c4dd06cff55cc9f43bf164f8843538974ef |
| customer_nodes_training.p | 188,037,655 | 8517d56a4a27d38be28f2b0b5fb0d5281c1c74246ebc666e2bad49e8efccfcae |
| customer_nodes_testing.p | 199,798,243 | a927539fe661d037ae6fb78b7966c65a30cf1d2d9e44b3b0f0b415c57fea7d24 |

- Every file is one pickled pandas DataFrame (pickle protocol 4, written with pandas < 2 and numpy < 2).
  Unpickling runs code, so the ingest pins these hashes in code and fails closed before loading.
- Events: `hash(variantID)`, `hash(customerId)`, `isReturned`; no timestamp, order or reason.
  1,369,133 train and 1,460,366 test rows.
- Customers (30 columns) include `isMale`, `shippingCountry`, nine `Country_*` columns and `yearOfBirth`:
  descriptive statistics only (rule 6, ADR-11).
- About 35% of events have no product row and about 5% no customer row. Joins are left joins with
  explicit nulls, and every F1 is reported on the full test set and on the fully featured subset.

## Consequences

- US-101 converts the pickles to `raw/asos_{customers,variants,events}.parquet` after the hash check, and
  its report includes join coverage.
- US-201 reports protocols A and B side by side; B1 timings use protocol A data.
- The spec rows of US-201, MOD-01 and MOD-02 point to this ADR.

## Amendment 1 (13 Oct 2026): protocol B's decision-time population (US-201)

Found while building protocol B; measured numbers in `docs/results/us-201-propensity.md` (origin:
ASOS GraphReturns, OSF c793h; nature: real).

Every customer and variant row of the dataset has at least one return (the paper's selection). A
row whose only return is the event's own return exists because that event was returned: its
leave-one-out returns are 0, and the event is returned in practically every case (train 60,451
events, 99.77% returned; test 59,884, 100%). Such an event cannot occur at decision time, where the
event's outcome is unknown and cannot select the rows.

**Decision.** Protocol B is trained, calibrated and judged on the decision-time population: the
events except those whose customer row or variant row holds no other return
(`selected_by_own_return` in `ml.propensity.features`, the `decision_time` mask of
`ml.propensity.features.population_masks`). Left in, they teach the model that a customer or a
variant without another return returns this purchase, the opposite of what a store sees. The full
test set and the fully featured subset are still reported, and a diagnostic row trains the same
features on every event; it is never saved or used.

**Risk R-07, distribution shift.** ASOS holds only customers with at least one return, and the
decision-time population still has no customer with zero returns besides the event. The synthetic
store, and any real shop, has many customers with no return at all, and new customers with no
history. Protocol B has never seen them: its probabilities for those customers are an
extrapolation. The store-side models do not inherit this: the product/cohort reference of the
residual (rule 8) is trained on the store's own lines (`ml/reference`), and the checks of R-07
(calibration of the store models, ECE <= 0.05, MOD-06) are run there. A protocol B probability is
never used for a store customer without that calibration check.
