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
make pipeline        # both, then graph, features, anomalies, risk and serving (data/README.md)
```

The command prints a JSON summary on stdout, with counts, measured return rates against the
calibration, the fitted factors, the weekly sales of the defective variants and of their matched
controls against the spec 8.9 minimum (`defect_detectability`), whether sales alone point to a
defect (`sales_gate`), and provenance. Exit code 1 means the calibration is invalid, valid but
unusable (every product type one-size, or no sized variant listed before the period, see below), or
the output folder cannot be written; exit code 2 means a usage error. The `pipeline` compose service
(`make demo-data`) runs the whole chain of `make pipeline` on the committed real calibration, without
rewriting `docs/results/`, as the owner of the checkout (`HOST_UID` and `HOST_GID`, which the
Makefile sets from `id -u` and `id -g`), so it can write `data/`, `artifacts/` and `serving/` in a
clone made as root (review P5).

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
| defective variant | about 2% of the variants | Window of 4 to 12 weeks starting on a Monday, not fixed in 20% of the cases. In the window, 35% to 65% of the buyers return the line for one reason (`defective` or `not_as_described`), whoever they are. A defective batch sells strongly at first, so the spec 8.9 detector (20 sales a week) can see it: the first 4 weeks of the window are a **sales surge** (below, reviews G6 and P1) |

`k_type` is fitted per product type by bisection, so that the expected return rate of the mature
lines equals the type target once wardrobers, rings, bracketing and defects are included. With the
real calibration at scale 1.0 it is 0.65 to 0.99 (main scenario).

**Sales surges and matched controls** (reviews G6 and P1). The surging variants are drawn at random
from one pool: the best sellers (twice as many as the surges, at least the top 5%) of the types that
sell at least 56 lines a week on average (every type of the real calibration at scale 1.0). There
are 5 surges per generic defect. Each surge starts on a random Monday, and the starts are spread so
that the surges of a type rarely overlap more than the type can carry. Only then is one surge in five
drawn to be a defect, whose window starts with its surge. The other four are **matched controls**:
non-defective best sellers with the same types, popularity and dates. In each of the 4 weeks of a
surge, plain lines of the same product type switch to the variant until it sells about 28 that week.
At most half of the type's free lines switch to one variant, and the variants are taken in random
order. Type sales shares do not move. The lift does not look at the defect, so a defect and its
controls surge alike, and neither a surge nor reaching 20 sales a week identifies a defect: only the
returns do. Without the controls, 94% of the variants that ever sold 20 in a week were defective
(see the round 2 table below).

**Sizes are required.** Size customers bracket two sizes, and the golden cases buy sized items, so a
calibration whose product types are all one-size (bags, accessories, jewellery) is refused with a
`CalibrationError` that says so (exit code 1), never an `IndexError` (review G3). So is a catalogue
with no sized variant that sells and is listed before the period: an order placed before an item
is listed falls back to those variants. A sized type with a tiny share gives such a catalogue at
small scales, in either scenario. Before review P2, the OOD scenario then crashed with a
`ValueError`. The golden jacket takes its sizes from its category: it is an apparel type (XS-XL),
named like outerwear if there is one, else the dearest apparel type (review G4).

Hard situations (spec section 7):

- **Pickup points**: 10 pickup points (lockers, post offices), each shared by 50 to 250 unrelated
  customers at scale 1.0.
- **Collective addresses**: 30 collective addresses (student halls, offices), each with 4 to 14
  unrelated customers.
- **Shared mobile prefixes**: mobile carrier IP prefixes, each shared by about 24 customers.
- **Card-sharing families**: families where two accounts share a card.
- **Unrelated accounts sharing a strong identifier** (OOD only, review F3): pairs of unrelated
  accounts of any behaviour that come to use one device (a second-hand phone, a shared laptop; 100
  pairs at scale 1.0), and flatmates who pay with one card at half the collective addresses. In the
  main store no card, e-mail or device is shared across rings, families and unrelated accounts, so
  the graph's precision at the 0.8 threshold holds by construction there.

Identifier counts never tell the behaviour (review F5): every account, ring and family members
included, draws a second device (35%), a second card (25%) and a mobile IP prefix (40%) with the
same probabilities; ring and family members keep their shared values on top. Ring accounts get
referral and Black Friday codes like everyone else. Before the fix, ring and family accounts never
had a mobile prefix, family accounts always had two devices, and ring accounts never held a
referral or Black Friday code: label proxies for a graph model (A4).

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
| Unrelated accounts | Share no card, e-mail or device | 100 pairs per 20,000 accounts share a device (second-hand or shared); flatmates share a card at half the collective addresses |
| Wardrobers, size, defects | Same rules in both stores: the OOD wardrober rows are in distribution (review F4) | Same |
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
(CPU); the identifier and coupon rows and the OOD identifier coverage after the phase 2 review fixes
F3 and F5, which change no other row of this table. The calibration targets are real ASOS aggregates (origin: ASOS GraphReturns, OSF c793h).

| | main | ood |
| --- | --- | --- |
| Customers / variants | 20,000 / 5,000 | 20,000 / 5,000 |
| Orders / lines | 80,356 / 205,462 | 80,103 / 204,081 |
| Lines per order / orders per customer | 2.56 / 4.02 | 2.55 / 4.01 |
| Deliveries / returns / return states | 79,636 / 60,916 / 241,399 | 79,346 / 60,678 / 240,523 |
| Identifier rows / coupon rows | 139,086 / 12,453 | 114,569 / 12,819 |
| Behaviour shares: normal, size, benign, wardrober, ring | 80.0%, 12.0%, 3.5%, 3.0%, 1.5% | same |
| Rings / families / card-sharing families | 40 / 261 / 40 | 75 / 252 / 177 |
| Return rate of mature lines vs calibration 0.3096 (real, sales-weighted) | 0.3063 (-1.1%) | 0.3077 (-0.6%) |
| Return rate by behaviour (delivered lines): normal, benign, size, wardrober, ring | 0.275, 0.278, 0.326, 0.741, 0.947 | 0.276, 0.264, 0.333, 0.738, 0.728 |
| Abusive share of returned value (wardrobers and rings; spec target about 12%) | 13.2% | 13.4% |
| Accounts with a device / card / phone / IP prefix | 100% / 100% / 88.1% / 100% | 71.1% / 75.8% / 60.1% / 81.0% |
| Surging variants: defects + matched controls; lines switched to them | 100 + 396; 48,572 (23.6% of lines) | 100 + 400; 49,646 (24.3%) |
| Variants that ever sell 20 in a week (spec 8.9 minimum): all, defective share | 495, 0.202 | 502, 0.199 |
| Run time (CPU; the code at commit 8aaf686 took 16.8 s on the same container that day) | 16.4 s | 16.5 s |

Return rate of mature lines per product type against its target (main; target = mean of the real
type distribution, rescaled to the real overall rate; every type within the DATA-02 band of 10%):

| Type | Mature lines | Rate | Target | Relative error | k |
| --- | --- | --- | --- | --- | --- |
| Jeans | 12,795 | 0.4462 | 0.4519 | -1.3% | 0.99 |
| productType_A | 3,451 | 0.3512 | 0.3459 | +1.5% | 0.95 |
| productType_B | 20,481 | 0.5089 | 0.5148 | -1.1% | 0.98 |
| productType_C | 6,396 | 0.3563 | 0.3656 | -2.5% | 0.83 |
| productType_D | 7,375 | 0.3056 | 0.3057 | -0.0% | 0.82 |
| productType_E | 4,960 | 0.3387 | 0.3411 | -0.7% | 0.83 |
| productType_F | 3,212 | 0.2235 | 0.2366 | -5.5% | 0.74 |
| productType_G | 5,531 | 0.2450 | 0.2489 | -1.6% | 0.65 |
| productType_H | 15,504 | 0.2007 | 0.2027 | -1.0% | 0.75 |
| productType_I | 6,604 | 0.4370 | 0.4378 | -0.2% | 0.92 |
| productType_J | 88,832 | 0.2478 | 0.2492 | -0.6% | 0.73 |

### Review findings of 13 Oct 2026: before and after

Same origin and nature as above (synthetic, seed 42, scale 1.0, real calibration, main scenario).
"Before" is the code at commit 8dc3c45, "after" the code at commit 8aaf686, measured on the same day
with the same seed and calibration. The defect rows changed again with the matched controls of
round 2 (next table).

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

### Review findings of 13 Oct 2026, round 2: before and after

Same origin and nature as above (synthetic, seed 42, scale 1.0, real calibration). "Before" is the
code at commit 8aaf686, "after" this change (matched controls, review P1), both measured on 13 Oct
2026 with the same seed and calibration. Weekly sales are counted from `store/order_lines` in
Monday-to-Sunday weeks, from the week a variant is listed; no return and no label is read, except
the ground truth to score the result. Three runs of the new code (two string-hash seeds) gave
byte-identical files for both scenarios (24 files). For reference, the code at commit 8dc3c45 (no lift at all)
gives 14 variants that ever sell 20 in a week (7 defective) and 3 flagged by the surge rule (1
defective).

| Check | Before (main) | After (main) | Before (ood) | After (ood) |
| --- | --- | --- | --- | --- |
| Variants that ever sell at least 20 in a week (spec 8.9 minimum); defective among them | 106; 100 (0.943) | 495; 100 (0.202) | 106; 100 (0.943) | 502; 100 (0.199) |
| Variant-weeks with at least 20 sales (assessable by the detector); defective share | 469; 0.951 | 1,948; 0.215 | 484; 0.783 | 1,931; 0.193 |
| Label-free surge rule "best week >= 15 sales and >= 4 times the median week": flagged, precision, recall | 92, 0.989, 0.910 | 484, 0.200, 0.970 | 96, 1.000, 0.960 | 496, 0.202, 1.000 |
| Weekly sales of non-defective variants, best week: p99, p99.9 | 12, 20 | 36, 41 | 12, 20 | 37, 43 |
| Defective variants with at least 20 sales in a full week of their window | 100 of 100 | 100 of 100 | 100 of 100 | 100 of 100 |
| Lifted weeks of the defects with at least 20 sales | 378 of 400 | 376 of 400 | 377 of 400 | 370 of 400 |
| Matched controls with at least 20 sales in a surge week; surge weeks with at least 20 sales | none | 395 of 396; 1,480 of 1,584 | none | 398 of 400; 1,492 of 1,600 |
| Generic lines switched to a surging variant | 9,322 (4.5%) | 48,572 (23.6%) | 9,766 (4.8%) | 49,646 (24.3%) |
| Lines of defective variants inside their window, share of all lines | 7.5% | 6.4% | 6.9% | 6.3% |
| Returns for the variant's defect reason inside its window, share of all returns | 12.1% | 10.6% | 11.0% | 10.3% |
| Non-conformity reasons, share of all returns (guarantee path) | 17.8% | 16.4% | 16.9% | 16.3% |
| V-JKT-DEF weekly sales in the 8 full weeks of its window | 30, 32, 27, 34, 24, 33, 18, 19 | 31, 37, 33, 34, 24, 33, 18, 19 | no golden case | no golden case |

Before, the lift went to defective variants only: sales volume alone, or a surge, was a defect
label, and the detector could be evaluated almost only on defects (23 non-defective variant-weeks
in a year at 20 sales or more). After, the defects and their controls surge alike (94.0% and 93.4%
of their surge weeks reach the minimum in main; 92.5% and 93.3% in OOD). Reaching the minimum or
surging now points to a defect with a precision of about 0.2, the share of defects among the
surging variants (1 in 5). The surge rule's recall stays high, because every defect still surges:
it is the returns, not the sales, that must tell a defect from its controls.

The trade-off, reported as it is (rule 13): about 10% of the variants now surge, and 23.6% of the
order lines move to a surging variant of the same type (4.5% before). Type sales shares and return
rates still match the calibration (tables above), but the weekly sales of the best sellers are much
peakier than before (best week p99 36 instead of 12). The defects are now drawn from about the top
20% of sellers instead of the top 6%, so they sell a little less outside their surge: defect returns
are 10.6% of the returns instead of 12.1%.

The golden cases keep their orders, dates, returns and signatures, and V-JKT-DEF keeps its defect,
its window and Elena's return. Only the other items they buy change (84 of their 87 lines): those
items are drawn among the variants that are not defective, and the defective variants are new.

The tests check the same two numbers on smaller stores: the example calibration at scale 0.1 (main
and OOD) and the real one at scale 0.25 (main) and 0.1 (OOD). The defective share among the
variants that reach the minimum, and the precision of the surge rule, are both at most 0.3. With
the example calibration at scale 1.0 (same seed, numbers not measured on ASOS), 501 variants
reach the minimum, the defective share is 0.200, and the surge rule flags 493 variants with
precision 0.201.

## Known limits and open points

- **Anomaly volume** (US-208): every defective variant reaches the spec 8.9 minimum of 20 sales in
  the first weeks of its window at scale 1.0 (see above); after those 4 weeks a variant sells at its
  usual level, so 1,014 of the 1,392 full weeks inside the windows of the 100 defective variants
  (main store; `window_weeks` of `defect_detectability`) stay under the minimum. At small scales
  (0.1 and below) small types cannot lift a variant to 20 sales a week: defects are then drawn only
  from types that can, and none can below about scale 0.03 (the largest type then sells under 56
  lines a week).
  Where few types can carry the surges, they overlap and fewer weeks reach the minimum, for the
  defects and their controls alike: at scale 0.1 OOD with the real calibration (one type), about
  60% of the surge weeks reach it.
- **Surges are a design artifact** (review P1): matched controls keep the sales from labelling the
  defects, but every defect surges, and the surges are 4 weeks long and start on a Monday. A model
  that sees sales volume learns that surges exist, not which surges are defects.
- **Graph volume** (US-203): with the real calibration, addresses shared at pickup points and
  collective addresses link 115,258 account pairs, and shared IP prefixes 92,805 pairs (main). This
  is intended, so that the d_k decay of 8.3 is exercised. The graph prunes these hubs before
  building pairs (docs/account-graph.md, "Hub pruning"): on the main store it writes 19,344 links
  instead of the 216,320 pairs of the brute force, with identical clusters and every pair left out
  below the 0.8 threshold (docs/results/account-graph.md, "Hub pruning against the brute force";
  synthetic, as of 2026-10-01).
- **Reasons**: the reason mix is a hypothesis, because ASOS reason codes are masked.
- **Generated events**: no checkout sessions without an order are generated, since no contract
  holds them; no return is rejected.
- **Pickup-point ground truth**: no truth table marks pickup points. An evaluation can recognise
  them as addresses with many sharers and no common ring or family.
