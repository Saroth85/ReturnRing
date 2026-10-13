"""Orders, lines, coupons and deliveries of Navigli Fashion (US-102).

Volumes (spec section 7): about 10 lines per customer per year in about 4 orders, so about 80,000
orders and 200,000 lines at scale 1.0. The shape of the purchases per customer comes from the
calibration (ASOS purchases_per_customer quantiles), rescaled to that volume.

Behaviour signatures in the orders:

- size customers bracket: in about half of their orders they buy two adjacent sizes of one item
  (MODE_PAIR lines), and later return one of the two for size;
- wardrobers buy high-value items (popularity x price^2) and lean to Tuesday-Thursday orders, so the
  items are in hand for the weekend: a tendency (WARDROBE_MIDWEEK_SHARE of their orders), not a
  rule, so the calendar alone never gives the label away;
- ring accounts place their first order within two days of opening, with the welcome coupon.

Defective variants sell about DEFECT_WEEKLY_SALES a week in the first DEFECT_BOOST_WEEKS weeks of
their defect window (boost_defect_windows), so the spec 8.9 detector can evaluate them.

Every order is delivered 1 to 5 days later (no Sunday deliveries), with the same lag distribution
for every customer, except 0.3% of lost parcels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from pipeline.generator.calibration import Calibration, QuantileDistribution
from pipeline.generator.catalogue import Catalogue, SizeKind, size_codes
from pipeline.generator.config import (
    BLACK_FRIDAY_CODES_AT,
    BLACK_FRIDAY_DAYS,
    BRACKETING_ORDER_SHARE,
    DEFECT_BOOST_WEEKS,
    DEFECT_MAX_TYPE_SHARE,
    DEFECT_WEEKLY_SALES,
    END_US,
    EXTRA_LINES_MEAN,
    FIXED_COUPON_CENTS,
    LINES_PER_CUSTOMER_YEAR,
    LOST_PARCEL_SHARE,
    MAX_LINES_PER_ORDER,
    PROMO_SHARE,
    REFERRAL_SHARE,
    START_US,
    US_PER_DAY,
    US_PER_HOUR,
    US_PER_MINUTE,
    US_PER_SECOND,
    WARDROBE_MIDWEEK_SHARE,
    WELCOME_PERCENT,
    WELCOME_REDEEM_PROBABILITY,
    ScenarioConfig,
    to_us,
)
from pipeline.generator.population import Population
from pipeline.generator.timeline import Calendar, midnight, minutes, uniform_us, weekday

I64 = npt.NDArray[np.int64]
F64 = npt.NDArray[np.float64]
B = npt.NDArray[np.bool_]

MODE_NORMAL, MODE_SIZE, MODE_PAIR, MODE_WARDROBE, MODE_RING, MODE_SCRIPTED = range(6)
KEEP, RETURN, RANDOM = 0, 1, -1
DELIVERY_DAYS = ((1, 0.15), (2, 0.40), (3, 0.28), (4, 0.12), (5, 0.05))
# No prefix ends in R or F: a code like "REF-0001..." would contain a family id ("F-0001").
COUPON_PREFIXES = {"welcome": "WLC", "referral": "RFL", "promo": "PRM"}
RING_FIRST_ORDER_DAYS = 2
RING_ORDER_GAP_DAYS = 5
NEW_FIRST_ORDER_MEAN_DAYS = 1.5


@dataclass(frozen=True)
class ScriptedLine:
    variant: int
    size: int  # code in catalogue.SIZE_LABELS, -1 for one size
    outcome: int  # KEEP or RETURN
    reason: str | None = None
    days: int = 0  # days after delivery for a return
    pending: bool = False  # requested, nothing else yet (the demo queue)


@dataclass(frozen=True)
class ScriptedOrder:
    customer: int
    placed_at: int
    delivery_days: int
    lines: tuple[ScriptedLine, ...]
    welcome: bool = False  # redeems the account's welcome coupon


@dataclass
class OrderPlan:
    customer: I64
    placed_at: I64
    delivery_days: I64  # -1: drawn
    scripted: B
    welcome: B  # scripted welcome redemption
    line_order: I64
    line_variant: I64
    line_size: I64
    line_mode: I64
    line_outcome: I64  # RANDOM, KEEP or RETURN
    line_reason: list[str | None]
    line_days: I64
    line_pending: B
    line_pair: I64  # bracketing pair id, -1 otherwise

    @property
    def n_orders(self) -> int:
        return len(self.customer)

    @property
    def n_lines(self) -> int:
        return len(self.line_order)


@dataclass
class Coupons:
    code: list[str] = field(default_factory=list)
    customer: list[int] = field(default_factory=list)
    kind: list[str] = field(default_factory=list)
    issued_at: list[int] = field(default_factory=list)
    nominal_cents: list[int] = field(default_factory=list)  # 0 for the percentage welcome coupon
    order: list[int] = field(default_factory=list)  # redeeming order, -1 when not redeemed
    discount_cents: list[int] = field(default_factory=list)


@dataclass
class Deliveries:
    delivered_at: I64  # -1: lost
    available_at: I64


class VariantSampler:
    """Draws variants by weight among those listed before the order."""

    def __init__(self, cat: Catalogue, weights: F64) -> None:
        self.listed = cat.listed_at
        self.cum = np.cumsum(weights)
        early = np.where(cat.listed_at < START_US, weights, 0.0)
        self.early_cum = np.cumsum(early)

    def draw(self, rng: np.random.Generator, placed: I64) -> I64:
        n = len(placed)
        v = np.searchsorted(self.cum, rng.random(n) * self.cum[-1], side="right")
        v = np.minimum(v, len(self.cum) - 1)
        for _ in range(20):
            bad = np.flatnonzero(self.listed[v] > placed)
            if len(bad) == 0:
                return np.asarray(v, dtype=np.int64)
            redraw = np.searchsorted(self.cum, rng.random(len(bad)) * self.cum[-1], side="right")
            v[bad] = np.minimum(redraw, len(self.cum) - 1)
        bad = np.flatnonzero(self.listed[v] > placed)
        early = np.searchsorted(
            self.early_cum, rng.random(len(bad)) * self.early_cum[-1], side="right"
        )
        v[bad] = np.minimum(early, len(self.cum) - 1)
        return np.asarray(v, dtype=np.int64)


def draw_sizes(rng: np.random.Generator, cat: Catalogue, variants: I64) -> I64:
    sizes = np.full(len(variants), -1, dtype=np.int64)
    kinds = cat.size_kind[variants]
    for kind in SizeKind:
        if kind is SizeKind.ONE:
            continue
        idx = np.flatnonzero(kinds == int(kind))
        codes, weights = size_codes(kind)
        sizes[idx] = rng.choice(codes, size=len(idx), p=np.asarray(weights) / sum(weights))
    return sizes


def adjacent_size(rng: np.random.Generator, cat: Catalogue, variant: int, size: int) -> int:
    codes, _ = size_codes(SizeKind(int(cat.size_kind[variant])))
    i = codes.index(size)
    if i == 0:
        return codes[1]
    if i == len(codes) - 1:
        return codes[-2]
    return codes[i + 1] if rng.random() < 0.5 else codes[i - 1]


def _order_counts(
    pop: Population, cal: Calibration, cfg: ScenarioConfig, rng: np.random.Generator
) -> I64:
    dist = QuantileDistribution.from_quantiles(cal.purchases_per_customer, 0.0, None)
    mean_q = max(dist.mean(), 1e-9)
    active_from = np.maximum(pop.created_at, START_US)
    active = (END_US - active_from) / (END_US - START_US)
    regular = ~pop.scripted & (pop.ring_of < 0)
    norm = float(active[regular].mean()) if regular.any() else 1.0
    lines = dist.sample(rng.random(pop.n)) / mean_q * LINES_PER_CUSTOMER_YEAR * active / norm
    counts = np.floor(lines / (1 + EXTRA_LINES_MEAN) + rng.random(pop.n)).astype(np.int64)
    counts = np.maximum(counts, 1)
    ring = pop.ring_of >= 0
    counts[ring] = rng.integers(cfg.ring_orders[0], cfg.ring_orders[1] + 1, int(ring.sum()))
    counts[pop.scripted] = 0
    return counts


def plan_generic_orders(
    pop: Population,
    cat: Catalogue,
    cal: Calibration,
    cfg: ScenarioConfig,
    rng: np.random.Generator,
    calendar: Calendar,
) -> OrderPlan:
    counts = _order_counts(pop, cal, cfg, rng)
    cust = np.repeat(np.arange(pop.n, dtype=np.int64), counts)
    n = len(cust)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    pos = np.arange(n) - np.repeat(starts, counts)
    behaviour = np.asarray(pop.behaviour, dtype=object)[cust]
    created = pop.created_at[cust]
    ring = pop.ring_of[cust] >= 0
    family = pop.family_of[cust]
    hard = np.asarray([pop.families[f].hard if f >= 0 else False for f in family], dtype=np.bool_)
    new = created >= START_US

    first = created + minutes(rng, 10, 60, n)
    first += np.where(
        ring,
        uniform_us(rng, 0, RING_FIRST_ORDER_DAYS * US_PER_DAY, n),
        np.where(
            hard,
            uniform_us(rng, 0, 3 * US_PER_DAY, n),
            np.minimum(rng.exponential(NEW_FIRST_ORDER_MEAN_DAYS, n), 10.0) * US_PER_DAY,
        ).astype(np.int64),
    )
    first = np.minimum(first, END_US - 2 * US_PER_DAY)
    anchored = new | ring
    gap = np.where(ring, RING_ORDER_GAP_DAYS, 1) * US_PER_DAY
    low = np.where(anchored, first + gap, START_US)
    high = np.where(ring, np.minimum(first + cfg.ring_activity_days * US_PER_DAY, END_US), END_US)
    placed = calendar.sample(rng, low, np.maximum(high, low + US_PER_MINUTE))
    placed = np.where(anchored & (pos == 0), first, placed)

    wardrobe = behaviour == "wardrober"
    placed = _wardrobe_days(rng, placed, np.maximum(created, START_US), wardrobe)
    order = np.lexsort((placed, cust))
    cust, placed, wardrobe = cust[order], placed[order], wardrobe[order]
    behaviour, ring = behaviour[order], ring[order]

    n_lines = 1 + np.minimum(rng.poisson(EXTRA_LINES_MEAN, n), MAX_LINES_PER_ORDER - 1)
    n_lines = np.where(wardrobe, 1 + np.minimum(rng.poisson(0.8, n), 3), n_lines)
    n_lines = np.where(ring, 2 + np.minimum(rng.poisson(1.0, n), 4), n_lines)
    line_order = np.repeat(np.arange(n, dtype=np.int64), n_lines)
    line_placed = placed[line_order]
    popular = VariantSampler(cat, cat.popularity)
    dear = VariantSampler(cat, cat.popularity * (cat.price_cents.astype(np.float64) / 100) ** 2)
    variant = popular.draw(rng, line_placed)
    w_lines = np.flatnonzero(wardrobe[line_order])
    variant[w_lines] = dear.draw(rng, line_placed[w_lines])
    _distinct_in_order(rng, line_order, line_placed, variant, wardrobe[line_order], popular, dear)
    size = draw_sizes(rng, cat, variant)

    mode_of = {"normal": MODE_NORMAL, "shared_benign": MODE_NORMAL, "size": MODE_SIZE}
    mode_of |= {"wardrober": MODE_WARDROBE, "promo_ring": MODE_RING}
    order_mode = np.asarray([mode_of[str(b)] for b in behaviour], dtype=np.int64)
    plan = OrderPlan(
        customer=cust,
        placed_at=placed,
        delivery_days=np.full(n, -1, dtype=np.int64),  # drawn for everyone
        scripted=np.zeros(n, dtype=np.bool_),
        welcome=np.zeros(n, dtype=np.bool_),
        line_order=line_order,
        line_variant=variant,
        line_size=size,
        line_mode=order_mode[line_order],
        line_outcome=np.full(len(line_order), RANDOM, dtype=np.int64),
        line_reason=[None] * len(line_order),
        line_days=np.zeros(len(line_order), dtype=np.int64),
        line_pending=np.zeros(len(line_order), dtype=np.bool_),
        line_pair=np.full(len(line_order), -1, dtype=np.int64),
    )
    _bracket(plan, cat, rng)
    return plan


def _distinct_in_order(
    rng: np.random.Generator,
    line_order: I64,
    placed: I64,
    variant: I64,
    wardrobe: B,
    popular: VariantSampler,
    dear: VariantSampler,
) -> None:
    """Redraw a variant that is already in the same order: the only item bought twice in an order
    is a bracketing pair, added on purpose."""
    for _ in range(20):
        by_pair = np.lexsort((np.arange(len(variant)), variant, line_order))
        same = (np.diff(line_order[by_pair]) == 0) & (np.diff(variant[by_pair]) == 0)
        repeat = by_pair[1:][same]
        if len(repeat) == 0:
            return
        plain = repeat[~wardrobe[repeat]]
        variant[plain] = popular.draw(rng, placed[plain])
        fancy = repeat[wardrobe[repeat]]
        variant[fancy] = dear.draw(rng, placed[fancy])


def _wardrobe_days(rng: np.random.Generator, placed: I64, earliest: I64, wardrobe: B) -> I64:
    """WARDROBE_MIDWEEK_SHARE of the wardrober orders move to the Tuesday, Wednesday or Thursday
    of their week, so the items tend to arrive before the weekend; the others stay as drawn."""
    n = len(placed)
    target = rng.integers(1, 4, n)  # Tuesday, Wednesday or Thursday
    move = wardrobe & (rng.random(n) < WARDROBE_MIDWEEK_SHARE)
    shift = ((weekday(placed) - target) % 7) * US_PER_DAY
    moved = placed - shift
    moved = np.where(moved < earliest, moved + 7 * US_PER_DAY, moved)
    moved = np.where(moved >= END_US - 3 * US_PER_DAY, moved - 7 * US_PER_DAY, moved)
    return np.where(move & (moved >= earliest), moved, placed)


def boost_defect_windows(plan: OrderPlan, cat: Catalogue, rng: np.random.Generator) -> int:
    """A defective batch sells strongly at first: in each of the first DEFECT_BOOST_WEEKS weeks of
    its defect window, ordinary lines of the same product type switch to the defective variant
    until it sells about DEFECT_WEEKLY_SALES that week. Type sales shares stay as calibrated.

    Only plain lines move (normal and size modes, no bracketing pair, no scripted line), never a
    line of another defective variant, at most DEFECT_MAX_TYPE_SHARE of the type's lines of the
    week, and at most one line per order, never into an order that already holds the variant.
    Returns the number of lines switched."""
    week = 7 * US_PER_DAY
    placed = plan.placed_at[plan.line_order]
    movable = (
        np.isin(plan.line_mode, (MODE_NORMAL, MODE_SIZE))
        & (plan.line_pair < 0)
        & ~cat.defective[plan.line_variant]
    )
    line_type = cat.type_index[plan.line_variant]
    switched = 0
    for v in np.flatnonzero(cat.defective):
        start = int(cat.defect_start[v])
        end = END_US if cat.defect_end[v] < 0 else int(cat.defect_end[v])
        holding = np.unique(plan.line_order[plan.line_variant == v])
        free = movable & (line_type == cat.type_index[v]) & ~np.isin(plan.line_order, holding)
        for w in range(DEFECT_BOOST_WEEKS):
            lo, hi = start + w * week, min(start + (w + 1) * week, end)
            if lo >= hi:
                break
            in_week = (placed >= lo) & (placed < hi)
            sold = int(np.count_nonzero(in_week & (plan.line_variant == v)))
            want = DEFECT_WEEKLY_SALES * (hi - lo) / week - sold
            candidates = np.flatnonzero(free & in_week)
            if want <= 0 or len(candidates) == 0:
                continue
            share = min(DEFECT_MAX_TYPE_SHARE, want / len(candidates))
            chosen = candidates[rng.random(len(candidates)) < share]
            _, first = np.unique(plan.line_order[chosen], return_index=True)
            chosen = chosen[np.sort(first)]
            plan.line_variant[chosen] = v  # same type, so the same sizes
            free[np.isin(plan.line_order, plan.line_order[chosen])] = False
            switched += len(chosen)
    return switched


def _bracket(plan: OrderPlan, cat: Catalogue, rng: np.random.Generator) -> None:
    """Size customers: two adjacent sizes of one item in about half of their orders, at least one
    order each (US-103 signature)."""
    sized = np.flatnonzero(cat.size_kind != int(SizeKind.ONE))
    sized_weights = np.zeros(cat.n)
    sized_weights[sized] = cat.popularity[sized]
    sized_sampler = VariantSampler(cat, sized_weights)
    first_line = np.searchsorted(plan.line_order, np.arange(plan.n_orders), side="left")
    last_line = np.searchsorted(plan.line_order, np.arange(plan.n_orders), side="right")
    size_orders = np.flatnonzero(plan.line_mode[first_line] == MODE_SIZE)
    if len(size_orders) == 0:
        return
    customers = plan.customer[size_orders]
    chosen = rng.random(len(size_orders)) < BRACKETING_ORDER_SHARE
    # At least one bracketing order per size customer.
    boundaries = np.flatnonzero(np.diff(np.concatenate([[-1], customers])))
    for b, end in zip(boundaries, [*boundaries[1:], len(customers)], strict=True):
        if not chosen[b:end].any():
            chosen[b + int(rng.integers(0, end - b))] = True
    extra_order: list[int] = []
    extra_variant: list[int] = []
    extra_size: list[int] = []
    for pair_id, o in enumerate(size_orders[chosen]):
        lines = range(int(first_line[o]), int(last_line[o]))
        line = next(
            (i for i in lines if cat.size_kind[plan.line_variant[i]] != int(SizeKind.ONE)), None
        )
        if line is None:  # only one-size items: the first becomes a sized one, new to the order
            line = int(first_line[o])
            in_order = set(plan.line_variant[lines].tolist())
            v = sized_sampler.draw(rng, plan.placed_at[o : o + 1])
            for _ in range(20):
                if int(v[0]) not in in_order:
                    break
                v = sized_sampler.draw(rng, plan.placed_at[o : o + 1])
            plan.line_variant[line] = v[0]
            plan.line_size[line] = draw_sizes(rng, cat, v)[0]
        variant = int(plan.line_variant[line])
        plan.line_mode[line] = MODE_PAIR
        plan.line_pair[line] = pair_id
        extra_order.append(int(o))
        extra_variant.append(variant)
        extra_size.append(adjacent_size(rng, cat, variant, int(plan.line_size[line])))
    k = len(extra_order)
    _append_lines(
        plan,
        order=np.asarray(extra_order, dtype=np.int64),
        variant=np.asarray(extra_variant, dtype=np.int64),
        size=np.asarray(extra_size, dtype=np.int64),
        mode=np.full(k, MODE_PAIR, dtype=np.int64),
        outcome=np.full(k, RANDOM, dtype=np.int64),
        reason=[None] * k,
        days=np.zeros(k, dtype=np.int64),
        pending=np.zeros(k, dtype=np.bool_),
        pair=np.arange(k, dtype=np.int64),
    )


def _append_lines(
    plan: OrderPlan,
    order: I64,
    variant: I64,
    size: I64,
    mode: I64,
    outcome: I64,
    reason: list[str | None],
    days: I64,
    pending: B,
    pair: I64,
) -> None:
    keys = np.concatenate([plan.line_order, order])
    sort = np.argsort(keys, kind="stable")
    plan.line_order = keys[sort]
    plan.line_variant = np.concatenate([plan.line_variant, variant])[sort]
    plan.line_size = np.concatenate([plan.line_size, size])[sort]
    plan.line_mode = np.concatenate([plan.line_mode, mode])[sort]
    plan.line_outcome = np.concatenate([plan.line_outcome, outcome])[sort]
    reasons = plan.line_reason + reason
    plan.line_reason = [reasons[int(i)] for i in sort]
    plan.line_days = np.concatenate([plan.line_days, days])[sort]
    plan.line_pending = np.concatenate([plan.line_pending, pending])[sort]
    plan.line_pair = np.concatenate([plan.line_pair, pair])[sort]


def add_scripted(plan: OrderPlan, scripted: list[ScriptedOrder]) -> None:
    """Append scripted orders (golden cases, independent jacket returners)."""
    first = plan.n_orders
    k = len(scripted)
    plan.customer = np.concatenate([plan.customer, [s.customer for s in scripted]]).astype(np.int64)
    plan.placed_at = np.concatenate([plan.placed_at, [s.placed_at for s in scripted]]).astype(
        np.int64
    )
    plan.delivery_days = np.concatenate(
        [plan.delivery_days, [s.delivery_days for s in scripted]]
    ).astype(np.int64)
    plan.scripted = np.concatenate([plan.scripted, np.ones(k, dtype=np.bool_)])
    plan.welcome = np.concatenate([plan.welcome, [s.welcome for s in scripted]]).astype(np.bool_)
    lines = [(first + j, line) for j, s in enumerate(scripted) for line in s.lines]
    m = len(lines)
    _append_lines(
        plan,
        order=np.asarray([o for o, _ in lines], dtype=np.int64),
        variant=np.asarray([ln.variant for _, ln in lines], dtype=np.int64),
        size=np.asarray([ln.size for _, ln in lines], dtype=np.int64),
        mode=np.full(m, MODE_SCRIPTED, dtype=np.int64),
        outcome=np.asarray([ln.outcome for _, ln in lines], dtype=np.int64),
        reason=[ln.reason for _, ln in lines],
        days=np.asarray([ln.days for _, ln in lines], dtype=np.int64),
        pending=np.asarray([ln.pending for _, ln in lines], dtype=np.bool_),
        pair=np.full(m, -1, dtype=np.int64),
    )


def orders_by_customer(plan: OrderPlan, n_customers: int) -> list[list[int]]:
    """Order indices of each customer, by time."""
    out: list[list[int]] = [[] for _ in range(n_customers)]
    for o in np.lexsort((np.arange(plan.n_orders), plan.placed_at, plan.customer)):
        out[int(plan.customer[o])].append(int(o))
    return out


def subtotals(plan: OrderPlan, cat: Catalogue) -> I64:
    prices = cat.price_cents[plan.line_variant]
    return np.bincount(plan.line_order, weights=prices, minlength=plan.n_orders).astype(np.int64)


def plan_coupons(
    plan: OrderPlan,
    pop: Population,
    cat: Catalogue,
    cfg: ScenarioConfig,
    rng: np.random.Generator,
) -> tuple[Coupons, I64]:
    """Welcome coupons for every new account (15% on the first order, spec section 4), a few
    referral and Black Friday codes. Returns the coupon log and each order's coupon (-1: none)."""
    coupons = Coupons()
    order_coupon = np.full(plan.n_orders, -1, dtype=np.int64)
    by_customer = orders_by_customer(plan, pop.n)
    subtotal = subtotals(plan, cat)
    codes: set[str] = set()

    def code(prefix: str) -> str:
        while True:
            value = f"{prefix}-{rng.bytes(5).hex().upper()}"
            if value not in codes:
                codes.add(value)
                return value

    def issue(customer: int, kind: str, at: int, nominal: int) -> int:
        coupons.code.append(code(COUPON_PREFIXES[kind]))
        coupons.customer.append(customer)
        coupons.kind.append(kind)
        coupons.issued_at.append(at)
        coupons.nominal_cents.append(nominal)
        coupons.order.append(-1)
        coupons.discount_cents.append(0)
        return len(coupons.code) - 1

    def redeem(c: int, o: int) -> None:
        coupons.order[c] = o
        if coupons.kind[c] == "welcome":
            coupons.discount_cents[c] = (int(subtotal[o]) * WELCOME_PERCENT + 50) // 100
        else:
            coupons.discount_cents[c] = min(coupons.nominal_cents[c], int(subtotal[o]))
        order_coupon[o] = c

    draws = rng.random((pop.n, 4))
    for i in range(pop.n):
        created = int(pop.created_at[i])
        mine = by_customer[i]
        if created >= START_US:
            c = issue(i, "welcome", created + US_PER_SECOND, 0)
            if pop.scripted[i]:
                forced = [o for o in mine if plan.welcome[o]]
                if forced:
                    redeem(c, forced[0])
            elif mine:
                family = pop.family_of[i]
                if pop.ring_of[i] >= 0:
                    p = cfg.ring_welcome_redeem
                elif family >= 0 and pop.families[family].hard:
                    p = 1.0
                else:
                    p = WELCOME_REDEEM_PROBABILITY
                if draws[i, 0] < p:
                    redeem(c, mine[0])
        if pop.scripted[i] or pop.ring_of[i] >= 0:
            continue
        if created >= START_US and draws[i, 1] < REFERRAL_SHARE:
            c = issue(i, "referral", created + 2 * US_PER_SECOND, FIXED_COUPON_CENTS)
            if len(mine) > 1 and order_coupon[mine[1]] < 0 and draws[i, 2] < 0.6:
                redeem(c, mine[1])
        black_friday = to_us(BLACK_FRIDAY_CODES_AT)
        if created < black_friday and draws[i, 3] < PROMO_SHARE:
            c = issue(i, "promo", black_friday, FIXED_COUPON_CENTS)
            window = [
                o
                for o in mine
                if black_friday <= plan.placed_at[o] < black_friday + BLACK_FRIDAY_DAYS * US_PER_DAY
                and order_coupon[o] < 0
            ]
            if window and rng.random() < 0.8:
                redeem(c, window[0])
    return coupons, order_coupon


def plan_deliveries(plan: OrderPlan, rng: np.random.Generator) -> Deliveries:
    n = plan.n_orders
    values = [d for d, _ in DELIVERY_DAYS]
    probs = np.asarray([p for _, p in DELIVERY_DAYS])
    drawn = rng.choice(values, size=n, p=probs / probs.sum())
    lag = np.where(plan.delivery_days >= 0, plan.delivery_days, drawn).astype(np.int64)
    day = midnight(plan.placed_at) + lag * US_PER_DAY
    day = np.where(weekday(day) == 6, day + US_PER_DAY, day)  # no Sunday deliveries
    delivered = day + uniform_us(rng, 9 * US_PER_HOUR, 19 * US_PER_HOUR, n)
    lost = (rng.random(n) < LOST_PARCEL_SHARE) & ~plan.scripted
    delivered = np.where(lost, -1, delivered)
    available = np.where(lost, -1, delivered + minutes(rng, 5, 360, n))
    return Deliveries(
        delivered_at=delivered.astype(np.int64), available_at=available.astype(np.int64)
    )
