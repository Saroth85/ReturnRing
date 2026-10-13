# Synthetic store: Navigli Fashion generator

Stories US-102 (store calibrated on ASOS), US-103 (separate ground truth), US-104 (golden cases) and
US-105 (out-of-distribution scenario); tests DATA-01 to DATA-05 and MOD-12 (spec sections 7 and 12).
Code: `pipeline/generator/`. Every parameter not taken from the calibration file is a design
hypothesis of the project, listed in `pipeline/generator/config.py`.

```bash
python -m pipeline.generator --calibration pipeline/calibration/asos_calibration.json \
  --out data --seed 42 [--scale 1.0] [--scenario main|ood]
make generate        # main scenario, CALIBRATION, SEED and SCALE overridable
make generate-ood    # out-of-distribution scenario
make pipeline        # both (phase 1; features join in phase 2)
```

The command prints a JSON summary on stdout, with counts, measured return rates against the
calibration, the fitted factors, the weekly sales of the defective variants against the spec 8.9
minimum, and provenance. Exit code 1 means the calibration is invalid, or valid but unusable (every
product type one-size, see below); exit code 2 means a usage error. The `pipeline` compose service
runs the same two commands on the committed real calibration.

## Output and rules

| Path | Content | Who may read it |
| --- | --- | --- |
| `<out>/store/*.parquet` | The 10 `store/` contracts | Everyone: services, features, training |
| `<out>/truth/labels.parquet`, `<out>/truth/variants.parquet` | Behaviour, ring, family, scenario; latent rate and defects | Offline training and evaluation jobs only. Running services never read them (rule 12) |
| `<out>/ood/store/*.parquet` | The out-of-distribution store | Evaluation only, never training (see below) |
| `<out>/truth/ood/truth/*.parquet` | The out-of-distribution ground truth | Offline evaluation only. It sits under `<out>/truth`, so a mount that leaves out `data/truth` leaves it out too (rule 12) |

The generator writes these files and nothing else:

- **Contracts**: every table is written with `contracts.io.write_table`, so it is validated against
  contract v2 (ADR-13).
- **Provenance**: every file has nature `synthetic` and an origin that starts with "Navigli Fashion
  generator, calibrated on ASOS GraphReturns aggregates". The origin then names the calibration's
  own origin and nature, its sha256, the seed, the scale, the scenario and the store period
  (2025-10-01..2026-09-30). A store built from the illustrative `contracts/examples/calibration.json`
  therefore says so in every file. The provenance period of a file covers its rows, as
  `contracts.io.write_table` requires: it ends on 2026-09-30 and starts on 2025-10-01, or earlier for
  `store/customers` (accounts opened up to four years before the store, C-ROSSI-1 in 2019) and
  `store/variants` (products listed before it).
- **Determinism** (DATA-04, NFR-10): the generator uses numpy `Generator(PCG64)` streams only. Each
  component (catalogue, population, identities, orders, coupons, deliveries, returns, states) has its
  own stream, derived from the seed with `SeedSequence(seed, spawn_key=(scenario, component))`.
- **No wall clock**: no wall-clock value is read, and every table is sorted by its key. The test
  runs the CLI in two processes with different `PYTHONHASHSEED` and compares sha256 of every file.
- **No labels in the store** (DATA-03): no store table has a label column, ring and family ids never
  appear in a store value, and customer ids follow account creation, not behaviour.

## What the calibration drives

| Calibration field (ASOS aggregates) | Use |
| --- | --- |
| `product_types.*.variant_share` | Number of variants per type (largest remainder; a type with sales gets at least one) |
| `product_types.*.sales_share` | Popularity of each type: the share of order lines |
| `product_types.*.price_quantiles_gbp` | Product prices, stratified draws, converted at **1 GBP = 1.17 EUR** (`GBP_TO_EUR`, about the ECB rate of the ASOS period, Sep to Nov 2021) |
| `product_types.*.return_rate_quantiles` | Latent `base_return_rate` of each variant, to `truth/variants` only. The quantiles are sales-weighted, so rates are assigned in popularity-weighted strata: the store's sales-weighted rate distribution equals the calibration's |
| `overall.product_sales_weighted_return_rate` | Target of the store's overall return rate (DATA-02). Per-type targets are the means of the type distributions, rescaled by one factor so that their sales-weighted sum is this rate |
| `purchases_per_customer` | Shape of the purchases per customer, rescaled to the spec volume (10 lines per customer per year) |
| `reason_shares`, `join_coverage`, `event_label_rate`, `splits` | Reported in the summary, not used: ASOS reason codes are masked (A..L) and cannot be mapped to `policy.types.ReturnReason` |

Distributions known by p10..p90 are linear between the knots. Each tail extends the slope of the
nearest inner segment, clipped to [0, 1] for rates and at 0 for prices.

The generator checks the calibration itself (`pipeline/generator/calibration.py`), because the
pipeline image has no JSON Schema validator. It rejects at least every document the schema rejects
(the tests compare it with `jsonschema`), then applies `contracts.calibration.check_calibration`.

## The store

Period: 1 October 2025 to 30 September 2026. The data is extracted at 2026-10-01T00:00Z, so a row
exists only if it was available before that instant. Deliveries, returns and states that come later
are not in the data yet, and the last orders have no delivery yet. Return rates are measured on
**mature lines**: delivered lines ordered at least 60 days before the end, whose returns are all in
the data.

| Table | Content |
| --- | --- |
| store/variants | About 5,000 variants at scale 1.0. A product has 1 to 3 colour variants; the size is on the order line (XS-XL, 26-36 for jeans and trousers, 36-45 for shoes, none for one-size types). About 75% are listed before the period |
| store/customers | 20,000 accounts at scale 1.0. About 35% are opened during the period, the others up to 4 years earlier. `display_name` is for the UI only (forbidden feature) |
| store/identifiers | One row per (account, kind, value) at its first use. See below |
| store/orders, order_lines | About 4 orders per customer and 2.5 lines per order; seasonal calendar (Black Friday, sales, mid-August). An order holds distinct items, except bracketing pairs |
| store/terms_snapshots | The Navigli base terms (`policy.engine.NAVIGLI_BASE_TERMS`), `source` "base", shown 1 to 20 minutes before each order |
| store/deliveries | 1 to 5 days after the order (1: 15%, 2: 40%, 3: 28%, 4: 12%, 5: 5%), the same for every customer, never on Sunday; 0.3% of parcels are lost |
| store/returns | Reason (`ReturnReason`) and `days_since_delivery`, under 30 days on the withdrawal path |
| store/return_events | `notified`, `shipped_or_proof`, then `refunded` minutes later (instant refund at the carrier scan, base terms) and `received`. Non-conformity reasons follow the guarantee path: `received`, then `refunded` after inspection. Every refund comes within 14 days of the notice (rule 3). No `rejected` state is generated |
| store/coupons | A welcome coupon (15%, issued at account creation, `discount_eur` 0.00 until redeemed) for every new account; a few referral and Black Friday codes (10 EUR). One row at issue, one at redemption |

### Identifiers

Identifiers are stored only as salted SHA-256 hashes of the normalised value (`identity.py`):

| Kind | Normalisation |
| --- | --- |
| e-mail | Lower case; dots and the `+alias` removed from the local part; `googlemail.com` becomes `gmail.com` |
| address | Normalised as in UNIT-01: no accents, case or punctuation; abbreviations expanded; interior details dropped |
| phone | E.164 form, +39 added to Italian mobile numbers |
| ip_prefix | The /24 prefix of an IPv4 address |
| device, card | The device fingerprint and card fingerprint tokens |

Raw values are generated the way a shop sees them (aliases, addresses written in several ways) and
never leave the generator. The salt derives from the seed, so the synthetic store is reproducible;
a real deployment needs a secret salt.

### Behaviours (ground truth in truth/labels)

| Behaviour | Share | What the generator does |
| --- | --- | --- |
| normal | 80% | Line returned with probability `k_type x base_return_rate x propensity`. The propensity is drawn from Gamma(6, 1/6), with mean 1; the probability is capped at 0.95. Reasons are mixed: size 36%, changed mind 24%, not as expected 24%, other 8%, non-conformity 8% |
| size | 12% | About half of the orders, and at least one, hold two adjacent sizes of one item: exactly one goes back, for size. Other lines behave like normal lines with mostly size reasons. The customer keeps at least half of the spend (enforced on the extracted store) |
| wardrober | 3% | Buys dear items (popularity x price^2). Leans to Tuesday-Thursday orders, so the items are in hand for the weekend: 55% of the orders move to the Tuesday, Wednesday or Thursday of their week, the others keep their calendar day, and every delivery has the shop's lag. A tendency, not a rule (review G2). Returns 80% of the lines priced at or above the catalogue median, 3 to 6 days after delivery, 78% "changed mind" |
| promo_ring | about 1.5%: 40 rings of 5 to 9 accounts (sizes adjusted to 1.5%) | New accounts opened within 4 to 14 days, one welcome coupon each, redeemed on a first order within 2 days. 2 or 3 orders in 60 days, 95% of lines returned. A pool of shared values per ring, used by each account with a probability: device 0.8, address 0.7, card 0.65, e-mail alias 0.45, phone 0.3, IP prefix 0.8. Every account shares a card, e-mail or device with another one |
| shared_benign | 3.5%: families of 2 to 4 accounts | Same address, household device and home IP prefix; own e-mail, phone and card; mostly old accounts; normal return probability. In 15% of the families two accounts share a card (Rossi-like) |
| defective variant | about 2% of the variants | Drawn among the best sellers (about the top 6%) of the types that sell at least 56 lines a week on average (every type of the real calibration at scale 1.0). Window of 4 to 12 weeks starting on a Monday, not fixed in 20% of the cases. A defective batch sells strongly at first: in each of the first 4 weeks of the window, plain lines of the same product type switch to the variant until it sells about 28 that week, at most half of the type's lines (review G6), so type sales shares do not move and the spec 8.9 detector (20 sales a week) can see it. In the window, 35% to 65% of the buyers return the line for one reason (`defective` or `not_as_described`), whoever they are |

`k_type` is fitted per product type by bisection, so that the expected return rate of the mature
lines equals the type target once wardrobers, rings, bracketing and defects are included. With the
real calibration at scale 1.0 it is 0.67 to 0.98 (main scenario).

**Sizes are required.** Size customers bracket two sizes, and the golden cases buy sized items, so a
calibration whose product types are all one-size (bags, accessories, jewellery) is refused with a
`CalibrationError` that says so (exit code 1), never an `IndexError` (review G3). The golden jacket
takes its sizes from its category: it is an apparel type (XS-XL), named like outerwear if there is
one, else the dearest apparel type (review G4).

Hard situations (spec section 7):

- **Pickup points**: 10 pickup points (lockers, post offices), each shared by 50 to 250 unrelated
  customers at scale 1.0.
- **Collective addresses**: 30 collective addresses (student halls, offices), each with 4 to 14
  unrelated customers.
- **Shared mobile prefixes**: mobile carrier IP prefixes, each shared by about 24 customers.
- **Card-sharing families**: families where two accounts share a card.

### Golden cases (US-104, DATA-05)

The golden cases are scripted in `scripts.py`: dates, outcomes and sharing are fixed, and only the
catalogue items they buy are drawn. An order is a list of line specs (`Keep`, `Back`, `Pair`,
`Fixed`) resolved in turn, and the items of one order are distinct: an item appears twice only as
a bracketing pair (review G1; before, an item drawn twice by chance broke Giulia's signature at
scale 0.005 seed 5 and scale 0.25 seed 3). Their signatures therefore hold at any scale and seed;
DATA-05 checks them over seeds 1 to 10 at scale 0.005. Each has a **pending return** requested in
the last week of September (state `notified` only), for the demo queue.

| Id | Signature |
| --- | --- |
| C-GIULIA (size) | Account from 2023. 7 orders and 16 lines; 3 orders with two adjacent sizes of one item, one returned for size each, plus one other size return. Keeps more than half of the spend. Pending size return requested on 26 Sep 2026 |
| R-MARCO, C-MARCO-1..7 (promo_ring) | Accounts opened 2 to 13 Mar 2026, one welcome coupon each, redeemed on the first order. Device shared by 6 accounts; flat by 5 (account 4 uses a pickup point); two cards shared by 4 and 3 accounts; three e-mail spellings of one gmail mailbox; one phone shared by 2 accounts. 28 of 31 lines returned. Pending return of C-MARCO-7 |
| F-ROSSI, C-ROSSI-1..3 (shared_benign) | Accounts from 2019, 2021 and 2023; same flat, household device and home connection; three distinct cards; no coupons; 4 of 18 lines returned. Pending return of C-ROSSI-1 |
| F-ROSSI-CARD, C-ROSSI-CARD-1..3 (shared_benign) | The same situation in another home, where accounts 1 and 2 pay with one card; 4 of 16 lines returned. Pending return of C-ROSSI-CARD-2 |
| C-ELENA (normal), V-JKT-DEF | V-JKT-DEF (product P-JKT, sibling colour V-JKT-OK not defective) is an apparel jacket (XS-XL, in productType_C with the real calibration) with a fit defect, `not_as_described`, from 3 Aug 2026, not fixed by the end of the period. Elena buys it on 21 Sep and returns it for that reason (pending; guarantee path). 8 scripted unrelated customers return it for the same reason in the window, besides the organic buyers. The jacket sells about 0.7% of all lines |

### Out-of-distribution scenario (US-105, MOD-12)

`--scenario ood` builds a second store with the same shares and calibration but different
generator rules. Changing only the seed would not be enough, because a model would still learn
the generator's rules.

| | main | ood |
| --- | --- | --- |
| Rings | 40 rings of 5 to 9 accounts, opened within 14 days, active 60 days | 75 rings of 3 to 5 accounts, opened over 45 to 120 days, active 240 days |
| Ring returns, welcome coupon | 95% of lines, every account redeems | 72%, 70% of accounts |
| Ring sharing | Device 0.8, address 0.7, card 0.65, e-mail 0.45 | Device 0.45, address 0.4, card 0.3, e-mail 0.1; no guaranteed strong link |
| Missing identifiers | Phone 12% | Phone 40%, device 30%, card 25%, IP prefix 20% |
| Families | 15% share a card | 60% share a card; 15% opened together with a welcome coupon each; plus F-OOD-HARD: five accounts opened within 10 days, each with a welcome coupon, sharing address, device and one card, returning at most 40% of lines |
| Ids | Golden cases; plain ids | No golden case; every id carries `OOD-` (C-OOD-000001, R-OOD-001, ...) |

**Rule: the OOD store is never used in training, tuning or threshold selection.** Training jobs read
`<out>/store` and `<out>/truth/*.parquet` only. `<out>/ood/store` and `<out>/truth/ood` are read
once, by the evaluation, which reports its metrics on a separate row whatever they are (rule 13).

## Measured on the generated store

Origin of every number in this section: Navigli Fashion generator (`make pipeline`), seed 42,
scale 1.0, built from `pipeline/calibration/asos_calibration.json` (sha256 f49ce3711edd), the
aggregates of the real ASOS GraphReturns train split (calibration nature: real, period
2021-09-01..2021-11-30). Nature of the numbers: **synthetic**, measured on the generated store, period
of the store October 2025 to September 2026. Measured on 13 Oct 2026 on the development container
(CPU). The calibration targets are real ASOS aggregates (origin: ASOS GraphReturns, OSF c793h).

| | main | ood |
| --- | --- | --- |
| Customers / variants | 20,000 / 5,000 | 20,000 / 5,000 |
| Orders / lines | 80,356 / 205,462 | 80,103 / 204,081 |
| Lines per order / orders per customer | 2.56 / 4.02 | 2.55 / 4.01 |
| Deliveries / returns / return states | 79,636 / 60,986 / 241,673 | 79,346 / 60,803 / 240,967 |
| Identifier rows / coupon rows | 138,932 / 12,429 | 113,863 / 12,785 |
| Behaviour shares: normal, size, benign, wardrober, ring | 80.0%, 12.0%, 3.5%, 3.0%, 1.5% | same |
| Rings / families / card-sharing families | 40 / 261 / 40 | 75 / 252 / 177 |
| Return rate of mature lines vs calibration 0.3096 (real, sales-weighted) | 0.3069 (-0.9%) | 0.3081 (-0.5%) |
| Return rate by behaviour (delivered lines): normal, benign, size, wardrober, ring | 0.275, 0.278, 0.328, 0.741, 0.948 | 0.276, 0.267, 0.335, 0.739, 0.730 |
| Abusive share of returned value (wardrobers and rings; spec target about 12%) | 13.1% | 13.5% |
| Accounts with a device / card / phone / IP prefix | 100% / 100% / 88.1% / 100% | 70.9% / 75.7% / 60.1% / 81.0% |
| Run time (CPU) | 8.4 s | 8.4 s |

Return rate of mature lines per product type against its target (main; target = mean of the real
type distribution, rescaled to the real overall rate; every type within the DATA-02 band of 10%):

| Type | Mature lines | Rate | Target | Relative error | k |
| --- | --- | --- | --- | --- | --- |
| Jeans | 12,793 | 0.4473 | 0.4519 | -1.0% | 0.96 |
| productType_A | 3,446 | 0.3546 | 0.3459 | +2.5% | 0.91 |
| productType_B | 20,485 | 0.5079 | 0.5148 | -1.3% | 0.98 |
| productType_C | 6,396 | 0.3573 | 0.3656 | -2.3% | 0.81 |
| productType_D | 7,374 | 0.3036 | 0.3057 | -0.7% | 0.83 |
| productType_E | 4,959 | 0.3378 | 0.3411 | -1.0% | 0.92 |
| productType_F | 3,216 | 0.2248 | 0.2366 | -5.0% | 0.85 |
| productType_G | 5,533 | 0.2507 | 0.2489 | +0.7% | 0.68 |
| productType_H | 15,504 | 0.1990 | 0.2027 | -1.8% | 0.67 |
| productType_I | 6,603 | 0.4334 | 0.4378 | -1.0% | 0.96 |
| productType_J | 88,832 | 0.2493 | 0.2492 | +0.0% | 0.69 |

### Review findings of 13 Oct 2026: before and after

Same origin and nature as above (synthetic, seed 42, scale 1.0, real calibration, main scenario).
"Before" is the code at commit aab4d61, measured on the same day with the same seed and calibration.

| Check | Before | After |
| --- | --- | --- |
| Wardrober orders placed Tuesday-Thursday (normal customers: 41.9%) | 100% | 72.3% |
| Wardrober deliveries 2 days after the order (normal customers: 36.1%) | 99.8% | 37.8% |
| Label-free rule "at least 2 orders, all on Tuesday or Wednesday, all delivered 2 days later": customers flagged, precision for wardrobers, recall | 550, 0.931, 0.853 | 43, 0.116, 0.008 |
| Defective variants with at least 20 sales in a full week of their window (spec 8.9 minimum) | 5 of 100 | 100 of 100 |
| Full window weeks with at least 20 sales | 33 of 1,238 | 398 of 1,238 (378 of the 400 lifted weeks) |
| Lines of defective variants inside their window, share of all lines | 3.2% | 7.5% |
| Returns for the variant's defect reason inside its window, share of all returns | 4.8% | 12.1% |
| Non-conformity reasons, share of all returns (guarantee path) | 11.2% | 17.8% |
| V-JKT-DEF weekly sales in the 8 full weeks of its window | 30, 19, 20, 24, 23, 23, 22, 20 | 30, 32, 27, 34, 24, 33, 18, 19 |

The label-free rule's precision after the fix is 0.116 (OOD: 0.174), against a base rate of 3%
wardrobers: the weekday is a weak signal, as intended. With the illustrative example calibration
(same seed and scale, numbers not measured on ASOS) the rule falls from 0.877 to 0.095, every one
of the 100 defective variants reaches the minimum (3 before), and the jacket moves from the Jeans
type (sized XS-XL, a mismatch) to productType_J.

The lift is a trade-off, reported as it is (rule 13): defects now make 12.1% of the returns instead
of 4.8%, and the guarantee path 17.8% instead of 11.2%. The overall and per-type return rates still
match the calibration, because `k_type` is fitted with the defect probabilities included.

## Known limits and open points

- **Anomaly volume** (US-208): every defective variant reaches the spec 8.9 minimum of 20 sales in
  the first weeks of its window at scale 1.0 (see above); after those 4 weeks a variant sells at its
  usual level, so 840 of the 1,238 window weeks stay under the minimum. At small scales (0.1 and
  below) small types cannot lift a variant to 20 sales a week: defects are then drawn only from types
  that can, and none can below about scale 0.03 (the largest type then sells under 56 lines a week).
- **Graph volume** (US-203): with the real calibration, addresses shared at pickup points and
  collective addresses link 115,258 account pairs, and shared IP prefixes 92,805 pairs (main). This
  is intended, so that the d_k decay of 8.3 is exercised; the graph should prune by d_k before
  building pairs.
- **Reasons**: the reason mix is a hypothesis, because ASOS reason codes are masked.
- **Generated events**: no checkout sessions without an order are generated, since no contract
  holds them; no return is rejected.
- **Pickup-point ground truth**: no truth table marks pickup points. An evaluation can recognise
  them as addresses with many sharers and no common ring or family.
