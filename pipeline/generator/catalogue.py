"""Catalogue of Navigli Fashion: products and variants sampled from the ASOS calibration (US-102).

A product has one to three colour variants; the size is chosen on the order line from the variant's
size range. Per product type of the calibration:

- the number of variants follows variant_share and the sales follow sales_share;
- each product's price is drawn from price_quantiles_gbp and converted with GBP_TO_EUR;
- each variant's latent base_return_rate is drawn from return_rate_quantiles. It goes to
  truth/variants only, never to store/variants (DATA-03).

Draws are stratified (one draw per equal-probability stratum, in random order), so even a small
store reproduces the calibration quantiles. Popularity is lognormal and independent of the rate, so
the sales-weighted rate distribution of the store matches the sales-weighted quantiles of ASOS.

About 2% of the variants are defective (spec section 7): during a window, a share of their buyers
return them for one non-conformity reason. The golden jacket V-JKT-DEF is one of them (US-104).
Their sales surge in the first weeks of the window (orders.boost_surges), so that the weekly
detector of spec 8.9 (at least 20 sales a week) can see them. Matched controls, non-defective best
sellers drawn from the same pool, get the same surge at random dates (review P1), so a surge is no
evidence of a defect: only the returns are.

A store needs at least one sized product type: size customers bracket two sizes and the golden
cases buy sized items (Giulia's pairs, the jacket's fit defect). A calibration whose types are all
one-size is refused with a CalibrationError (require_sizes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import numpy.typing as npt

from pipeline.generator import golden
from pipeline.generator.calibration import (
    Calibration,
    CalibrationError,
    ProductType,
    QuantileDistribution,
)
from pipeline.generator.config import (
    DEFECT_BOOST_WEEKS,
    DEFECT_MAX_TYPE_SHARE,
    DEFECT_WEEKLY_SALES,
    DEFECTIVE_VARIANT_SHARE,
    END_US,
    GBP_TO_EUR,
    JACKET_SALES_SHARE,
    MIN_PRICE_CENTS,
    MIN_VARIANTS,
    START_US,
    SURGE_CONTROLS_PER_DEFECT,
    US_PER_DAY,
    VARIANTS_AT_SCALE_1,
    ScenarioConfig,
    expected_weekly_lines,
    to_us,
)
from pipeline.generator.timeline import uniform_us, weekday

I64 = npt.NDArray[np.int64]
F64 = npt.NDArray[np.float64]
B = npt.NDArray[np.bool_]

BRANDS = (
    "Navigli Studio",
    "Brera Basics",
    "Darsena",
    "Porta Nuova",
    "Isola Denim",
    "Tortona Lab",
    "Sempione",
    "Ticinese",
    "Lambrate",
    "Bovisa Sport",
    "Corvetto",
    "Moscova",
    "Cadorna",
    "Magenta",
    "Garibaldi",
    "Città Studi",
)
COLOURS_PER_PRODUCT = ((1, 0.5), (2, 0.3), (3, 0.2))
PRE_PERIOD_LISTING_SHARE = 0.75
POPULARITY_SIGMA = 1.0
DEFECT_INTENSITY = (0.35, 0.65)  # share of in-window buyers who return for the defect
DEFECT_WEEKS = (4, 12)
SURGE_START_TRIES = 20  # random Mondays tried per surge before the least loaded one is taken
DEFECT_NEVER_FIXED_SHARE = 0.2
DEFECT_REASONS = ("defective", "not_as_described")


class SizeKind(IntEnum):
    ONE = 0  # one size: size and size_range are null
    APPAREL = 1
    WAIST = 2
    SHOES = 3


SIZE_LABELS = (
    "XS", "S", "M", "L", "XL",
    "26", "28", "30", "32", "34", "36",
    "37", "38", "39", "40", "41", "42", "43", "44", "45",
)  # fmt: skip
_SIZES: dict[SizeKind, tuple[tuple[str, ...], tuple[float, ...]]] = {
    SizeKind.ONE: ((), ()),
    SizeKind.APPAREL: (("XS", "S", "M", "L", "XL"), (0.10, 0.25, 0.32, 0.22, 0.11)),
    SizeKind.WAIST: (("26", "28", "30", "32", "34", "36"), (0.08, 0.18, 0.26, 0.24, 0.16, 0.08)),
    SizeKind.SHOES: (
        ("36", "37", "38", "39", "40", "41", "42", "43", "44", "45"),
        (0.05, 0.10, 0.14, 0.14, 0.12, 0.12, 0.12, 0.10, 0.07, 0.04),
    ),
}
SIZE_RANGE: dict[SizeKind, str | None] = {
    SizeKind.ONE: None,
    SizeKind.APPAREL: "XS-XL",
    SizeKind.WAIST: "26-36",
    SizeKind.SHOES: "36-45",
}
_KEYWORDS: tuple[tuple[SizeKind, tuple[str, ...]], ...] = (
    (SizeKind.ONE, ("bag", "accessor", "jewel", "hat", "cap", "scarf", "belt", "sunglass",
                    "watch", "wallet", "beauty", "fragrance")),
    (SizeKind.SHOES, ("shoe", "boot", "sneaker", "trainer", "sandal", "heel", "footwear")),
    (SizeKind.WAIST, ("jean", "trouser", "pant", "chino")),
)  # fmt: skip
_JACKET_WORDS = ("jacket", "coat", "outerwear", "blazer", "giacc", "cappott")


def size_kind_for(category: str) -> SizeKind:
    name = category.lower()
    for kind, words in _KEYWORDS:
        if any(w in name for w in words):
            return kind
    return SizeKind.APPAREL


def size_codes(kind: SizeKind) -> tuple[list[int], list[float]]:
    labels, weights = _SIZES[kind]
    return [SIZE_LABELS.index(s) for s in labels], list(weights)


@dataclass
class Catalogue:
    variant_id: list[str]
    product_id: list[str]
    type_index: I64  # index into Calibration.product_types
    brand: list[str]
    size_kind: I64
    price_cents: I64
    listed_at: I64
    base_rate: F64
    popularity: F64  # probability that a line is this variant; sums to 1
    defective: B
    defect_reason: list[str | None]
    defect_start: I64  # 0 when not defective
    defect_end: I64  # -1: not defective or never fixed in the period
    defect_intensity: F64
    surge_start: I64  # Monday a sales surge starts (defects and their controls), -1: none
    type_names: tuple[str, ...]

    @property
    def n(self) -> int:
        return len(self.variant_id)

    def index_of(self, variant_id: str) -> int:
        return self.variant_id.index(variant_id)

    def category(self, i: int) -> str:
        return self.type_names[int(self.type_index[i])]

    def defect_probability(self, variant: I64, placed_at: I64) -> F64:
        """Probability that a line is returned for the defect: the variant's intensity when the
        order falls in its defect window, 0 otherwise."""
        start = self.defect_start[variant]
        end = np.where(self.defect_end[variant] < 0, END_US, self.defect_end[variant])
        inside = self.defective[variant] & (placed_at >= start) & (placed_at < end)
        return np.where(inside, self.defect_intensity[variant], 0.0)


def _largest_remainder(total: int, shares: list[float]) -> list[int]:
    weight = sum(shares)
    if total <= 0 or weight <= 0:
        return [0] * len(shares)
    exact = [total * s / weight for s in shares]
    counts = [math.floor(x) for x in exact]
    order = sorted(range(len(shares)), key=lambda i: (-(exact[i] - counts[i]), i))
    for i in order[: total - sum(counts)]:
        counts[i] += 1
    # Every type with sales gets at least one variant when there is room.
    active = [i for i, s in enumerate(shares) if s > 0]
    if total >= len(active):
        for i in active:
            if counts[i] == 0:
                donor = max(range(len(counts)), key=lambda j: (counts[j], -j))
                counts[donor] -= 1
                counts[i] += 1
    return counts


def _stratified(rng: np.random.Generator, n: int) -> F64:
    """n probabilities, one in each of n equal strata, in random order."""
    return (rng.permutation(n) + rng.random(n)) / max(n, 1)


def _sales_weighted_rates(rng: np.random.Generator, t: ProductType, popularity: F64) -> F64:
    """Latent rates whose popularity-weighted distribution is the calibration's sales-weighted
    one: in random order, each variant takes the stratum of [0, 1] as wide as its share of the
    type's sales and the rate at the middle of it. The order is random, so the rate stays
    independent of popularity."""
    order = rng.permutation(len(popularity))
    share = popularity[order] / popularity.sum()
    middle = np.cumsum(share) - share / 2
    rates = np.empty(len(popularity))
    rates[order] = QuantileDistribution.from_quantiles(t.return_rate, 0.0, 1.0).sample(middle)
    return rates


def jacket_type(types: tuple[ProductType, ...]) -> int:
    """The type of the golden jacket, an apparel garment sized XS-XL: an apparel type named like
    outerwear, else the dearest apparel type, else (no apparel type) the dearest sized type."""
    apparel = [i for i, t in enumerate(types) if size_kind_for(t.name) is SizeKind.APPAREL]
    for i in apparel:
        if any(w in types[i].name.lower() for w in _JACKET_WORDS):
            return i
    sized = [i for i, t in enumerate(types) if size_kind_for(t.name) is not SizeKind.ONE]
    pool = apparel or sized
    if not pool:
        raise CalibrationError(["the golden jacket needs a sized product type: " + _NO_SIZES])
    return max(pool, key=lambda i: (types[i].price_gbp.p50, -i))


_NO_SIZES = (
    "every product type of the calibration is one-size (bags, accessories, jewellery...), so "
    "the size behaviour (12% of customers, spec section 7) and the golden cases, which buy "
    "sized items, cannot be generated"
)


def require_sizes(types: tuple[ProductType, ...]) -> None:
    """Refuse a calibration without any sized product type that sells (G3)."""
    if not any(size_kind_for(t.name) is not SizeKind.ONE and t.sales_share > 0 for t in types):
        raise CalibrationError([_NO_SIZES])


def require_early_sized_variant(cat: Catalogue) -> None:
    """Refuse a catalogue without a sized variant that sells from the first day (review P2).

    An order drawn before a variant is listed falls back to the variants listed before the
    period, and a bracketing pair needs a sized one among them; the golden jacket does not
    count, since its dates are scripted. A calibration whose sized types have tiny shares gives
    such a catalogue at small scales, in any scenario."""
    generic = np.asarray(
        [vid not in (golden.DEFECTIVE_JACKET, golden.JACKET_SIBLING) for vid in cat.variant_id],
        dtype=np.bool_,
    )
    sized = (cat.size_kind != int(SizeKind.ONE)) & (cat.popularity > 0)
    if not (generic & sized & (cat.listed_at < START_US)).any():
        raise CalibrationError(
            [
                "no sized variant is listed before the period in this catalogue, so size "
                "customers cannot bracket two sizes from the first day: use a larger --scale "
                "or a calibration with larger sized product types"
            ]
        )


def _price_cents(gbp: F64) -> I64:
    cents = np.round(gbp * GBP_TO_EUR * 100).astype(np.int64)
    return np.maximum(cents, MIN_PRICE_CENTS)


def build_catalogue(
    cal: Calibration, scale: float, cfg: ScenarioConfig, rng: np.random.Generator
) -> Catalogue:
    types = cal.product_types
    require_sizes(types)
    n_total = max(MIN_VARIANTS, round(VARIANTS_AT_SCALE_1 * scale))
    n_golden = 2 if cfg.golden else 0
    per_type = _largest_remainder(n_total - n_golden, [t.variant_share for t in types])

    product_type: list[int] = []
    product_price: list[int] = []
    variant_product: list[int] = []
    variant_type: list[int] = []
    base_rate: list[float] = []
    popularity_parts: list[F64] = []
    for ti, (t, count) in enumerate(zip(types, per_type, strict=True)):
        if count == 0:
            continue
        colours: list[int] = []
        while sum(colours) < count:
            colours.append(
                int(
                    rng.choice(
                        [c for c, _ in COLOURS_PER_PRODUCT], p=[p for _, p in COLOURS_PER_PRODUCT]
                    )
                )
            )
        colours[-1] -= sum(colours) - count
        prices = QuantileDistribution.from_quantiles(t.price_gbp, 0.0, None).sample(
            _stratified(rng, len(colours))
        )
        popularity = rng.lognormal(0.0, POPULARITY_SIGMA, count)
        rates = _sales_weighted_rates(rng, t, popularity)
        popularity_parts.append(popularity)
        base_rate.extend(float(r) for r in rates)
        for k, price in zip(colours, _price_cents(prices), strict=True):
            product_type.append(ti)
            product_price.append(int(price))
            variant_product.extend([len(product_type) - 1] * k)
            variant_type.extend([ti] * k)

    n_products = len(product_type)
    pre = rng.random(n_products) < PRE_PERIOD_LISTING_SHARE
    listed = np.where(
        pre,
        uniform_us(rng, START_US - 540 * US_PER_DAY, START_US - US_PER_DAY, n_products),
        uniform_us(rng, START_US, END_US - 45 * US_PER_DAY, n_products),
    )
    brands = [BRANDS[int(i)] for i in rng.integers(0, len(BRANDS), n_products)]
    popularity = np.concatenate(popularity_parts) if popularity_parts else np.zeros(0)

    # Number products by listing time (as a shop would), variants in product order.
    order = np.lexsort((rng.random(n_products), listed))
    rank = np.empty(n_products, dtype=np.int64)
    rank[order] = np.arange(n_products)
    vp = np.asarray(variant_product, dtype=np.int64)
    v_order = np.lexsort((np.arange(len(vp)), rank[vp]))
    prefix = cfg.id_prefix
    product_ids = [f"P-{prefix}{rank[p] + 1:06d}" for p in range(n_products)]

    cat = Catalogue(
        variant_id=[f"V-{prefix}{i + 1:06d}" for i in range(len(v_order))],
        product_id=[product_ids[vp[j]] for j in v_order],
        type_index=np.asarray(variant_type, dtype=np.int64)[v_order],
        brand=[brands[vp[j]] for j in v_order],
        size_kind=np.asarray(
            [size_kind_for(types[variant_type[j]].name) for j in v_order], dtype=np.int64
        ),
        price_cents=np.asarray(product_price, dtype=np.int64)[vp[v_order]],
        listed_at=listed[vp[v_order]],
        base_rate=np.asarray(base_rate, dtype=np.float64)[v_order],
        popularity=popularity[v_order],
        defective=np.zeros(len(v_order), dtype=np.bool_),
        defect_reason=[None] * len(v_order),
        defect_start=np.zeros(len(v_order), dtype=np.int64),
        defect_end=np.full(len(v_order), -1, dtype=np.int64),
        defect_intensity=np.zeros(len(v_order), dtype=np.float64),
        surge_start=np.full(len(v_order), -1, dtype=np.int64),
        type_names=tuple(t.name for t in types),
    )
    if cfg.golden:
        _add_golden_jacket(cat, types)
    _normalise_popularity(cat, types, cfg.golden)
    require_early_sized_variant(cat)
    type_weekly = np.asarray([t.sales_share for t in types]) * expected_weekly_lines(scale)
    _add_defects(cat, n_total, cfg.golden, type_weekly, rng)
    return cat


def _add_golden_jacket(cat: Catalogue, types: tuple[ProductType, ...]) -> None:
    ti = jacket_type(types)
    t = types[ti]
    kind = size_kind_for(t.name)  # its sizes are those of its category (XS-XL for apparel)
    price = max(MIN_PRICE_CENTS, math.ceil(t.price_gbp.p75 * GBP_TO_EUR) * 100 - 1)
    listed = to_us(golden.JACKET_LISTED_AT)
    for vid in (golden.DEFECTIVE_JACKET, golden.JACKET_SIBLING):
        cat.variant_id.append(vid)
        cat.product_id.append(golden.JACKET_PRODUCT)
        cat.brand.append(BRANDS[0])
        cat.defect_reason.append(None)
    k = 2
    cat.type_index = np.concatenate([cat.type_index, np.full(k, ti, dtype=np.int64)])
    cat.size_kind = np.concatenate([cat.size_kind, np.full(k, int(kind))])
    cat.price_cents = np.concatenate([cat.price_cents, np.full(k, price, dtype=np.int64)])
    cat.listed_at = np.concatenate([cat.listed_at, np.full(k, listed, dtype=np.int64)])
    cat.base_rate = np.concatenate([cat.base_rate, np.full(k, t.return_rate.p50)])
    cat.popularity = np.concatenate([cat.popularity, np.ones(k)])
    cat.defective = np.concatenate([cat.defective, np.array([True, False])])
    cat.defect_start = np.concatenate(
        [cat.defect_start, np.array([to_us(golden.JACKET_DEFECT_START), 0], dtype=np.int64)]
    )
    cat.defect_end = np.concatenate([cat.defect_end, np.array([-1, -1], dtype=np.int64)])
    cat.defect_intensity = np.concatenate(
        [cat.defect_intensity, np.array([golden.JACKET_DEFECT_INTENSITY, 0.0])]
    )
    cat.surge_start = np.concatenate([cat.surge_start, cat.defect_start[-2:-1], [-1]])
    cat.defect_reason[-2] = golden.JACKET_DEFECT_REASON


def _normalise_popularity(
    cat: Catalogue, types: tuple[ProductType, ...], with_jacket: bool
) -> None:
    """Scale popularity so that each type's sales share is the calibration's sales_share."""
    pop = cat.popularity.copy()
    jacket = cat.index_of(golden.DEFECTIVE_JACKET) if with_jacket else -1
    sibling = cat.index_of(golden.JACKET_SIBLING) if with_jacket else -1
    for ti, t in enumerate(types):
        members = np.flatnonzero(cat.type_index == ti)
        if len(members) == 0:
            continue
        share = t.sales_share
        fixed = np.isin(members, [jacket, sibling])
        free = members[~fixed]
        if with_jacket and fixed.any():
            jacket_share = min(JACKET_SALES_SHARE, share / 2)
            average = share / len(members)
            pop[jacket] = jacket_share
            pop[sibling] = min(average, (share - jacket_share) / 2)
            share -= pop[jacket] + pop[sibling]
        if len(free):
            total = pop[free].sum()
            pop[free] = pop[free] / total * share if total > 0 else share / len(free)
    total = pop.sum()
    cat.popularity = pop / total if total > 0 else np.full(cat.n, 1.0 / cat.n)


def _monday(t: I64) -> I64:
    day = np.floor_divide(t, US_PER_DAY)
    return np.asarray((day - weekday(t)) * US_PER_DAY, dtype=np.int64)


def _schedule_surges(
    cat: Catalogue, surging: I64, type_weekly: F64, latest_start: int, rng: np.random.Generator
) -> None:
    """A random Monday for each surge, spread so that the surges of a type rarely overlap more
    than the type can carry (DEFECT_MAX_TYPE_SHARE of its lines a week, DEFECT_WEEKLY_SALES per
    surge, at least one surge): in random order, each variant takes the first of SURGE_START_TRIES
    random Mondays whose weeks stay within that load, else the least loaded of them. The rule
    knows nothing of the defect, so defects and controls are spread alike."""
    week = 7 * US_PER_DAY
    first_monday = int(_monday(np.asarray([START_US], dtype=np.int64))[0])
    n_weeks = (END_US - first_monday) // week + DEFECT_BOOST_WEEKS + 1
    capacity = np.maximum(1.0, type_weekly * DEFECT_MAX_TYPE_SHARE / DEFECT_WEEKLY_SALES)
    load = np.zeros((len(type_weekly), n_weeks), dtype=np.int64)
    for v in np.flatnonzero(cat.surge_start >= 0).tolist():  # the golden jacket
        w = (int(cat.surge_start[v]) - first_monday) // week
        load[cat.type_index[v], w : w + DEFECT_BOOST_WEEKS] += 1
    for v in rng.permutation(surging).tolist():
        t = int(cat.type_index[v])
        earliest = max(START_US + 21 * US_PER_DAY, int(cat.listed_at[v]) + 14 * US_PER_DAY)
        tries = _monday(uniform_us(rng, earliest, latest_start, SURGE_START_TRIES)) + week
        offsets = (tries - first_monday) // week
        peaks = np.asarray([load[t, w : w + DEFECT_BOOST_WEEKS].max() for w in offsets.tolist()])
        fits = np.flatnonzero(peaks + 1 <= capacity[t])
        pick = int(fits[0]) if len(fits) else int(np.argmin(peaks))
        cat.surge_start[v] = tries[pick]
        load[t, offsets[pick] : offsets[pick] + DEFECT_BOOST_WEEKS] += 1


def _add_defects(
    cat: Catalogue,
    n_total: int,
    with_jacket: bool,
    type_weekly: F64,
    rng: np.random.Generator,
) -> None:
    """Defective variants and their matched controls (reviews G6 and P1).

    A defect is noticed on a variant that sells, and its batch sells strongly at first: the
    first DEFECT_BOOST_WEEKS weeks of the window are a sales surge (orders.boost_surges), so the
    weekly detector of spec 8.9 can assess it. So that a surge does not give the defect away,
    SURGE_CONTROLS_PER_DEFECT non-defective best sellers per defect get the same surge.

    Defects and controls are drawn together from one pool: the best sellers (twice as many as
    the surges, at least the top 5%) of the types that sell enough lines a week for the lift.
    Each gets a surge from a random Monday; only then are the defective ones drawn among them,
    so defects and controls share types, popularity and dates. When too few variants qualify
    (small scales), the pool is the best sellers of every type: the detector cannot see them at
    that size."""
    n_defective = max(1, round(DEFECTIVE_VARIANT_SHARE * n_total))
    n_generic = n_defective - (1 if with_jacket else 0)
    if n_generic <= 0:
        return
    latest_start = END_US - 120 * US_PER_DAY
    golden_ids = {golden.DEFECTIVE_JACKET, golden.JACKET_SIBLING}
    listed_in_time = np.asarray(
        [
            i
            for i in range(cat.n)
            if cat.variant_id[i] not in golden_ids
            and cat.listed_at[i] + 14 * US_PER_DAY < latest_start
        ],
        dtype=np.int64,
    )
    liftable = type_weekly[cat.type_index[listed_in_time]] * DEFECT_MAX_TYPE_SHARE
    eligible = listed_in_time[liftable >= DEFECT_WEEKLY_SALES]
    if len(eligible) < n_generic:
        eligible = listed_in_time
    n_surging = min(n_generic * (1 + SURGE_CONTROLS_PER_DEFECT), len(eligible))
    by_popularity = eligible[np.argsort(-cat.popularity[eligible], kind="stable")]
    pool = by_popularity[: max(2 * n_surging, len(eligible) // 20)]
    surging = np.sort(rng.choice(pool, size=n_surging, replace=False))
    _schedule_surges(cat, surging, type_weekly, latest_start, rng)
    # The defects: a random subset of the surging best sellers, the others are the controls.
    chosen = np.sort(rng.choice(surging, size=min(n_generic, n_surging), replace=False))
    k = len(chosen)
    start = cat.surge_start[chosen]
    weeks = rng.integers(DEFECT_WEEKS[0], DEFECT_WEEKS[1] + 1, k)
    end = start + weeks * 7 * US_PER_DAY
    never_fixed = (rng.random(k) < DEFECT_NEVER_FIXED_SHARE) | (end >= END_US)
    cat.defective[chosen] = True
    cat.defect_start[chosen] = start
    cat.defect_end[chosen] = np.where(never_fixed, -1, end)
    cat.defect_intensity[chosen] = rng.uniform(*DEFECT_INTENSITY, k)
    for i, reason in zip(chosen, rng.integers(0, len(DEFECT_REASONS), k), strict=True):
        cat.defect_reason[int(i)] = DEFECT_REASONS[int(reason)]
