# Synthetic store: Navigli Fashion generator

Stories US-102 (store calibrated on ASOS), US-103 (separate ground truth), US-104 (golden cases) and
US-105 (out-of-distribution scenario); tests DATA-01 to DATA-05 and MOD-12 (spec sections 7 and 12).
Code: `pipeline/generator/`. Every parameter not taken from the calibration file is a design
hypothesis of the project, listed in `pipeline/generator/config.py`.

```bash
python -m pipeline.generator --calibration pipeline/calibration/asos_calibration.json \
  --out data --seed 42 [--scale 1.0] [--scenario main|ood]
```

The command prints a JSON summary on stdout, with counts, measured return rates against the
calibration, the fitted factors and provenance. Exit code 1 means the calibration is invalid; exit
code 2 means a usage error.

## Output and rules

| Path | Content | Who may read it |
| --- | --- | --- |
| `<out>/store/*.parquet` | The 10 `store/` contracts | Everyone: services, features, training |
| `<out>/truth/labels.parquet`, `<out>/truth/variants.parquet` | Behaviour, ring, family, scenario; latent rate and defects | Offline training and evaluation jobs only. Running services never read them (rule 12) |
| `<out>/ood/store`, `<out>/ood/truth` | The out-of-distribution scenario | Evaluation only, never training (see below) |

The generator writes these files and nothing else:

- **Contracts**: every table is written with `contracts.io.write_table`, so it is validated against
  contract v2 (ADR-13).
- **Provenance**: every file has nature `synthetic`, period 2025-10-01 to 2026-09-30, and an origin
  that starts with "Navigli Fashion generator, calibrated on ASOS GraphReturns aggregates". The
  origin then names the calibration's own origin and nature, its sha256, the seed, the scale and
  the scenario. A store built from the illustrative `contracts/examples/calibration.json` therefore
  says so in every file.
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
| store/deliveries | 1 to 5 days after the order, never on Sunday; 0.3% of parcels are lost |
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
| wardrober | 3% | Buys dear items (popularity x price^2). Orders on Tuesday or Wednesday, receives on Thursday or Friday. Returns 80% of the lines priced at or above the catalogue median, 3 to 6 days after delivery, 78% "changed mind" |
| promo_ring | about 1.5%: 40 rings of 5 to 9 accounts (sizes adjusted to 1.5%) | New accounts opened within 4 to 14 days, one welcome coupon each, redeemed on a first order within 2 days. 2 or 3 orders in 60 days, 95% of lines returned. A pool of shared values per ring, used by each account with a probability: device 0.8, address 0.7, card 0.65, e-mail alias 0.45, phone 0.3, IP prefix 0.8. Every account shares a card, e-mail or device with another one |
| shared_benign | 3.5%: families of 2 to 4 accounts | Same address, household device and home IP prefix; own e-mail, phone and card; mostly old accounts; normal return probability. In 15% of the families two accounts share a card (Rossi-like) |
| defective variant | about 2% of the variants | Drawn among the best sellers (about the top 6%). Window of 4 to 12 weeks starting on a Monday, not fixed in 20% of the cases. In the window, 35% to 65% of the buyers return the line for one reason (`defective` or `not_as_described`), whoever they are |

`k_type` is fitted per product type by bisection, so that the expected return rate of the mature
lines equals the type target once wardrobers, rings, bracketing and defects are included. With the
example calibration it is 0.79 to 0.91.

Hard situations (spec section 7):

- **Pickup points**: 10 pickup points (lockers, post offices), each shared by 50 to 250 unrelated
  customers at scale 1.0.
- **Collective addresses**: 30 collective addresses (student halls, offices), each with 4 to 14
  unrelated customers.
- **Shared mobile prefixes**: mobile carrier IP prefixes, each shared by about 24 customers.
- **Card-sharing families**: families where two accounts share a card.

### Golden cases (US-104, DATA-05)

The golden cases are scripted in `scripts.py`: dates, outcomes and sharing are fixed, and only the
catalogue items they buy are drawn. Their signatures therefore hold at any scale and seed. Each has
a **pending return** requested in the last week of September (state `notified` only), for the demo
queue.

| Id | Signature |
| --- | --- |
| C-GIULIA (size) | Account from 2023. 7 orders and 16 lines; 3 orders with two adjacent sizes of one item, one returned for size each, plus one other size return. Keeps more than half of the spend. Pending size return requested on 26 Sep 2026 |
| R-MARCO, C-MARCO-1..7 (promo_ring) | Accounts opened 2 to 13 Mar 2026, one welcome coupon each, redeemed on the first order. Device shared by 6 accounts; flat by 5 (account 4 uses a pickup point); two cards shared by 4 and 3 accounts; three e-mail spellings of one gmail mailbox; one phone shared by 2 accounts. 28 of 31 lines returned. Pending return of C-MARCO-7 |
| F-ROSSI, C-ROSSI-1..3 (shared_benign) | Accounts from 2019, 2021 and 2023; same flat, household device and home connection; three distinct cards; no coupons; 4 of 18 lines returned. Pending return of C-ROSSI-1 |
| F-ROSSI-CARD, C-ROSSI-CARD-1..3 (shared_benign) | The same situation in another home, where accounts 1 and 2 pay with one card; 4 of 16 lines returned. Pending return of C-ROSSI-CARD-2 |
| C-ELENA (normal), V-JKT-DEF | V-JKT-DEF (product P-JKT, sibling colour V-JKT-OK not defective) has a fit defect, `not_as_described`, from 3 Aug 2026, not fixed by the end of the period. Elena buys it on 21 Sep and returns it for that reason (pending; guarantee path). 8 scripted unrelated customers return it for the same reason in the window, besides the organic buyers. The jacket sells about 0.6% of all lines |

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
`<out>/store` and `<out>/truth` only. `<out>/ood` is read once, by the evaluation, which reports its
metrics on a separate row whatever they are (rule 13).

## Measured on the generated store

All numbers below are synthetic. Origin: Navigli Fashion generator, seed 42, scale 1.0, built from
the **illustrative** `contracts/examples/calibration.json` (its values are not measured on ASOS).
Period: Oct 2025 to Sep 2026. Measured on 13 Oct 2026. They must be measured again once the US-101
profiler writes the real calibration.

| | main | ood |
| --- | --- | --- |
| Customers / variants | 20,000 / 5,000 | 20,000 / 5,000 |
| Orders / lines | 80,936 / 206,678 | 80,280 / 204,887 |
| Deliveries / returns / return states | 80,179 / 60,079 / 237,869 | 79,505 / 59,294 / 234,903 |
| Identifier rows / coupon rows | 138,932 / 12,403 | 113,863 / 12,760 |
| Rings / families / card-sharing families | 40 / 261 / 40 | 75 / 252 / 177 |
| Return rate of mature lines vs calibration 0.30 | 0.2985 (-0.5%) | 0.2971 (-1.0%) |
| Same, per type, vs target (Jeans 0.398, B 0.209, J 0.327) | 0.391 / 0.209 / 0.325 | 0.395 / 0.205 / 0.323 |
| Return rate by behaviour: normal, benign, size, wardrober, ring | 0.269, 0.258, 0.324, 0.742, 0.947 | 0.267, 0.247, 0.328, 0.754, 0.729 |
| Abusive share of returned value (wardrobers and rings; spec target about 12%) | 11.9% | 12.8% |
| Accounts with a device / card / phone / IP prefix | 100% / 100% / 88% / 100% | 71% / 76% / 60% / 81% |
| Run time on the development container (CPU) | 8.7 s | 7.7 s |

## Known limits and open points

- **Anomaly volume** (US-208): a defective variant sells a median 3.2 lines a week in its window.
  Only V-JKT-DEF (about 22 a week) reaches the spec 8.9 minimum of 20 sales a week. The detector
  needs a lower minimum or a wider window at this store size.
- **Graph volume** (US-203): pickup points link about 115,000 account pairs on addresses, and
  mobile prefixes about 93,000 pairs on IP prefixes. This is intended, so that the d_k decay of 8.3
  is exercised; the graph should prune by d_k before building pairs.
- **Reasons**: the reason mix is a hypothesis, because ASOS reason codes are masked.
- **Generated events**: no checkout sessions without an order are generated, since no contract
  holds them; no return is rejected.
- **Pickup-point ground truth**: no truth table marks pickup points. An evaluation can recognise
  them as addresses with many sharers and no common ring or family.
