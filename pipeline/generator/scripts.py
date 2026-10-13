"""Scripted golden cases (US-104, DATA-05): their orders, outcomes and identifiers.

Dates, outcomes and sharing patterns are fixed, so the signatures in pipeline/generator/golden.py
hold at any --scale and seed; only the catalogue variants they buy are drawn. Each golden case also
has one pending return in the last week of the period (requested, nothing else yet), the case the
demo opens in the operator's queue.

An order is written as a list of line specs (Keep, Back, Pair, Fixed) that Picker.order resolves
in turn. The items of one order are distinct: an item appears twice only as a bracketing Pair (two
adjacent sizes), so no signature depends on the luck of the draw.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt

from pipeline.generator import golden
from pipeline.generator.accounts import Address, IdentityFactory, Plan
from pipeline.generator.calibration import CalibrationError
from pipeline.generator.catalogue import Catalogue, SizeKind, size_codes
from pipeline.generator.config import START_US, US_PER_DAY, to_us
from pipeline.generator.orders import KEEP, RETURN, ScriptedLine, ScriptedOrder
from pipeline.generator.population import Population

I64 = npt.NDArray[np.int64]


def at(year: int, month: int, day: int, hour: int = 20, minute: int = 0) -> int:
    return to_us(datetime(year, month, day, hour, minute, tzinfo=UTC))


# ---- Line specs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Keep:
    """An item from `pool` that the customer keeps."""

    pool: str = "any"


@dataclass(frozen=True)
class Back:
    """An item from `pool` returned for `reason`, `days` after delivery."""

    reason: str
    days: int
    pool: str = "any"
    pending: bool = False  # requested, nothing else yet (the demo queue)


@dataclass(frozen=True)
class Pair:
    """Two adjacent sizes of one sized item: the first goes back for size, the second is kept."""

    days: int
    pending: bool = False


@dataclass(frozen=True)
class Fixed:
    """A line decided by the script itself (the defective jacket)."""

    line: ScriptedLine


Spec = Keep | Back | Pair | Fixed

# When a pool has no item left that is not already in the order, the next pool is tried.
_FALLBACK = {"dear": ("dear", "sized", "any"), "sized": ("sized", "any"), "any": ("any",)}


class Picker:
    """Variants for scripted orders: listed before the period, not defective, popular."""

    def __init__(self, cat: Catalogue, rng: np.random.Generator) -> None:
        self.cat = cat
        self.rng = rng
        safe = (cat.listed_at < START_US) & ~cat.defective
        for vid in (golden.DEFECTIVE_JACKET, golden.JACKET_SIBLING):
            if vid in cat.variant_id:
                safe[cat.index_of(vid)] = False
        safe &= cat.popularity > 0
        sized = safe & (cat.size_kind != int(SizeKind.ONE))
        if not sized.any():
            raise CalibrationError(
                [
                    "the golden cases need a sized item listed before the period (Giulia "
                    "brackets two sizes) and this catalogue has none: use a larger --scale or "
                    "a calibration with more sized product types"
                ]
            )
        price = cat.price_cents
        median = float(np.median(price[sized]))
        self.pools = {
            "any": np.flatnonzero(safe),
            "sized": np.flatnonzero(sized),
            "dear": np.flatnonzero(sized & (price >= median)),
        }

    def variant(self, pool: str, taken: set[int], sized: bool = False) -> int:
        """A popular item of `pool` that is not in `taken` (the order so far)."""
        for name in _FALLBACK[pool]:
            if sized and name == "any":
                break
            candidates = self.pools[name]
            candidates = candidates[~np.isin(candidates, np.fromiter(taken, np.int64))]
            if len(candidates):
                weights = self.cat.popularity[candidates]
                return int(self.rng.choice(candidates, p=weights / weights.sum()))
        raise CalibrationError([f"no distinct {pool} item left for a golden order: scale too low"])

    def size(self, variant: int) -> int:
        kind = SizeKind(int(self.cat.size_kind[variant]))
        if kind is SizeKind.ONE:
            return -1
        codes, weights = size_codes(kind)
        return int(self.rng.choice(codes, p=np.asarray(weights) / sum(weights)))

    def lines(self, spec: Spec, taken: set[int]) -> tuple[ScriptedLine, ...]:
        if isinstance(spec, Fixed):
            return (spec.line,)
        if isinstance(spec, Pair):
            v = self.variant("dear", taken, sized=True)
            codes, _ = size_codes(SizeKind(int(self.cat.size_kind[v])))
            i = int(self.rng.integers(1, len(codes) - 2)) if len(codes) > 3 else 0
            return (
                ScriptedLine(v, codes[i], RETURN, "size", spec.days, spec.pending),
                ScriptedLine(v, codes[i + 1], KEEP),
            )
        v = self.variant(spec.pool, taken)
        if isinstance(spec, Keep):
            return (ScriptedLine(v, self.size(v), KEEP),)
        return (ScriptedLine(v, self.size(v), RETURN, spec.reason, spec.days, spec.pending),)

    def order(
        self, customer: int, placed_at: int, delivery_days: int, *specs: Spec, welcome: bool = False
    ) -> ScriptedOrder:
        """A scripted order whose lines follow `specs`, each item at most once (pairs aside)."""
        taken: set[int] = set()
        lines: list[ScriptedLine] = []
        for spec in specs:
            drawn = self.lines(spec, taken)
            taken.update(line.variant for line in drawn)
            lines.extend(drawn)
        return ScriptedOrder(customer, placed_at, delivery_days, tuple(lines), welcome)


def script_golden(
    pop: Population,
    cat: Catalogue,
    factory: IdentityFactory,
    ids: Plan,
    pickups: list[Address],
    rng: np.random.Generator,
) -> list[ScriptedOrder]:
    pick = Picker(cat, rng)
    orders: list[ScriptedOrder] = []
    orders += _giulia(pop, pick, factory, ids)
    orders += _marco(pop, pick, factory, ids, pickups)
    orders += _rossi(pop, pick, factory, ids)
    orders += _elena(pop, cat, pick, factory, ids)
    orders += _jacket_returners(pop, cat, pick, rng)
    return orders


def _own(ids: Plan, factory: IdentityFactory, c: int, email: str, address: Address) -> None:
    rng = factory.rng
    ids.add(c, "email", factory.reserve_email(email), "created")
    ids.add(c, "phone", factory.phone(), "created")
    ids.add(c, "device", factory.token("dev"), "created")
    ids.add(c, "card", factory.token("card_fp"), "first_order")
    ids.add(c, "address", address.render(rng), "first_order")
    ids.add(c, "ip_prefix", factory.home_ip(), "first_order")


def _giulia(
    pop: Population, pick: Picker, factory: IdentityFactory, ids: Plan
) -> list[ScriptedOrder]:
    g = pop.index(golden.GIULIA)
    _own(ids, factory, g, "Giulia.Bianchi@gmail.com", factory.address())
    ids.add(g, "device", factory.token("dev"), at(2026, 1, 10, 12, 40))  # a new laptop
    ids.add(g, "ip_prefix", factory.mobile_ip(), at(2026, 5, 9, 13, 10))
    o = pick.order
    return [
        o(g, at(2025, 10, 18, 20, 15), 2, Pair(3), Keep("dear")),
        o(g, at(2025, 11, 28, 21, 5), 3, Keep("dear"), Keep()),
        o(g, at(2026, 1, 10, 12, 40), 2, Pair(2), Keep("dear")),
        o(g, at(2026, 3, 21, 19, 30), 2, Keep("dear"), Back("size", 4)),
        o(g, at(2026, 5, 9, 13, 10), 2, Keep("dear"), Keep()),
        o(g, at(2026, 7, 4, 10, 45), 3, Keep("dear")),
        # Monday 21 September, delivered Wednesday, size return requested Saturday: pending.
        o(g, at(2026, 9, 21, 20, 30), 2, Pair(3, pending=True), Keep()),
    ]


_RING_REASONS = ("changed_mind", "not_as_expected", "size")


def _marco(
    pop: Population, pick: Picker, factory: IdentityFactory, ids: Plan, pickups: list[Address]
) -> list[ScriptedOrder]:
    """Seven new accounts; device, flat and cards shared with noise; one welcome coupon each."""
    rng = factory.rng
    m = [pop.index(c) for c in golden.MARCO_ACCOUNTS]
    device, flat, other_flat = factory.token("dev"), factory.address(), factory.address()
    card_a, card_b, phone = factory.token("card_fp"), factory.token("card_fp"), factory.phone()
    home_ip = factory.home_ip().rsplit(".", 1)[0]
    emails = (
        "marco.ferri+1@gmail.com",
        "m.arco.ferri+shop@gmail.com",  # the same mailbox as account 1 once normalised
        "marcoferri+3@gmail.com",
        "ferri.m84@libero.it",
        "mf.store2026@outlook.com",
        "lucia.ferraro@hotmail.it",
        "marco.f.2026@yahoo.it",
    )
    for k, c in enumerate(m):
        ids.add(c, "email", factory.reserve_email(emails[k]), "created")
    for c in m[:6]:
        ids.add(c, "device", device, "created")
    ids.add(m[1], "device", factory.token("dev"), "later_order")
    ids.add(m[6], "device", factory.token("dev"), "created")
    for c in (m[0], m[1], m[2], m[4], m[5]):
        ids.add(c, "address", flat.render(rng), "first_order")
    ids.add(
        m[3], "address", (pickups[0] if pickups else factory.address()).render(rng), "first_order"
    )
    ids.add(m[6], "address", other_flat.render(rng), "first_order")
    for c in (m[0], m[1], m[3], m[5]):
        ids.add(c, "card", card_a, "first_order")
    for c in (m[2], m[4], m[6]):
        ids.add(c, "card", card_b, "first_order")
    ids.add(m[5], "card", factory.token("card_fp"), "later_order")
    for c in (m[0], m[1]):
        ids.add(c, "phone", phone, "created")
    for c in (m[2], m[3], m[5], m[6]):  # account 5 gave no phone
        ids.add(c, "phone", factory.phone(), "created")
    for c in m[:5]:
        ids.add(c, "ip_prefix", factory.ip(home_ip), "first_order")
    ids.add(m[5], "ip_prefix", factory.home_ip(), "first_order")
    ids.add(m[6], "ip_prefix", factory.mobile_ip(), "first_order")

    orders: list[ScriptedOrder] = []
    for k, c in enumerate(m):
        created = to_us(golden.ACCOUNTS[golden.MARCO_ACCOUNTS[k]][0])
        backs = [Back(_RING_REASONS[(k + j) % 3], 2 + (k + j) % 4) for j in range(3)]
        orders.append(pick.order(c, created + 22 * 3600 * 10**6, 2, *backs, welcome=True))
        if k % 2 == 0:  # accounts 1, 3, 5 and 7 order again five weeks later
            second: Spec = Keep() if k in (0, 4) else Back("not_as_expected", 4)
            again = (Back("changed_mind", 3), second)
            orders.append(pick.order(c, created + 35 * US_PER_DAY, 2, *again))
    # Account 7, Tuesday 22 September: a return requested on Sunday 27, pending.
    late = (Back("changed_mind", 3, pending=True), Keep())
    orders.append(pick.order(m[6], at(2026, 9, 22, 20, 10), 2, *late))
    return orders


def _family(
    factory: IdentityFactory,
    ids: Plan,
    members: list[int],
    emails: tuple[str, ...],
    shared_card: bool,
) -> None:
    """Same flat, household device and home connection; own e-mail, phone, device and card (two
    accounts share one card when shared_card)."""
    rng = factory.rng
    home = factory.address()
    household = factory.token("dev")
    home_ip = factory.home_ip().rsplit(".", 1)[0]
    card = factory.token("card_fp")
    for k, c in enumerate(members):
        ids.add(c, "email", factory.reserve_email(emails[k]), "created")
        ids.add(c, "phone", factory.phone(), "created")
        ids.add(c, "device", factory.token("dev"), "created")
        ids.add(c, "device", household, "first_order")
        ids.add(c, "address", home.render(rng), "first_order")
        ids.add(c, "ip_prefix", factory.ip(home_ip), "first_order")
        if shared_card and k < 2:
            ids.add(c, "card", card, "first_order")
        else:
            ids.add(c, "card", factory.token("card_fp"), "first_order")


ROSSI_EMAILS = ("anna.rossi@libero.it", "paolo.rossi71@gmail.com", "sofia.rossi@icloud.com")
ROSSI_CARD_EMAILS = ("marta.rossi@tiscali.it", "giorgio.rossi@alice.it", "chiara.rossi02@gmail.com")


def _rossi(
    pop: Population, pick: Picker, factory: IdentityFactory, ids: Plan
) -> list[ScriptedOrder]:
    r = [pop.index(c) for c in golden.ROSSI_ACCOUNTS]
    rc = [pop.index(c) for c in golden.ROSSI_CARD_ACCOUNTS]
    _family(factory, ids, r, ROSSI_EMAILS, shared_card=False)
    _family(factory, ids, rc, ROSSI_CARD_EMAILS, shared_card=True)
    o, k, b = pick.order, Keep(), Back
    return [
        o(r[0], at(2025, 10, 25, 18, 30), 2, k, k),
        o(r[0], at(2026, 1, 17, 11, 0), 3, k, b("changed_mind", 5)),
        o(r[0], at(2026, 4, 18, 16, 20), 2, k, k),
        o(r[0], at(2026, 9, 22, 19, 0), 2, b("size", 3, pending=True), k),
        o(r[1], at(2025, 12, 6, 10, 30), 2, k),
        o(r[1], at(2026, 3, 7, 15, 45), 2, k, b("size", 4)),
        o(r[1], at(2026, 6, 13, 12, 0), 3, k, k),
        o(r[2], at(2025, 11, 15, 21, 10), 2, k, b("not_as_expected", 6)),
        o(r[2], at(2026, 2, 21, 17, 30), 2, k),
        o(r[2], at(2026, 8, 29, 20, 0), 2, k, k),
        o(rc[0], at(2025, 10, 11, 10, 0), 2, k, k),
        o(rc[0], at(2026, 2, 7, 18, 0), 2, k, b("size", 4)),
        o(rc[0], at(2026, 5, 23, 11, 30), 2, k),
        o(rc[1], at(2025, 12, 13, 20, 0), 2, k, b("changed_mind", 6)),
        o(rc[1], at(2026, 4, 11, 13, 0), 3, k, k),
        o(rc[1], at(2026, 9, 23, 21, 0), 2, b("size", 2, pending=True), k),
        o(rc[2], at(2025, 10, 30, 19, 0), 2, k),
        o(rc[2], at(2026, 1, 24, 15, 0), 2, k, k),
        o(rc[2], at(2026, 7, 11, 10, 0), 3, k, b("not_as_expected", 5)),
    ]


def _elena(
    pop: Population, cat: Catalogue, pick: Picker, factory: IdentityFactory, ids: Plan
) -> list[ScriptedOrder]:
    e = pop.index(golden.ELENA)
    _own(ids, factory, e, "elena.conti@libero.it", factory.address())
    jacket = cat.index_of(golden.DEFECTIVE_JACKET)
    sizes = jacket_sizes(cat)
    medium = sizes[len(sizes) // 2]  # M for an apparel jacket
    fit = ScriptedLine(jacket, medium, RETURN, golden.JACKET_DEFECT_REASON, 3, pending=True)
    return [
        pick.order(e, at(2025, 11, 20, 19, 0), 2, Keep(), Keep()),
        pick.order(e, at(2026, 3, 5, 12, 30), 2, Keep(), Back("size", 5)),
        # Monday 21 September, in the jacket's defect window: delivered Wednesday, returned for
        # the fit defect on Saturday (pending).
        pick.order(e, at(2026, 9, 21, 19, 42), 2, Fixed(fit), Keep()),
    ]


def jacket_sizes(cat: Catalogue) -> list[int]:
    """Size codes of the golden jacket: those of its category (catalogue.jacket_type)."""
    kind = SizeKind(int(cat.size_kind[cat.index_of(golden.DEFECTIVE_JACKET)]))
    return size_codes(kind)[0]


def _jacket_returners(
    pop: Population, cat: Catalogue, pick: Picker, rng: np.random.Generator
) -> list[ScriptedOrder]:
    """Unrelated customers who return the jacket for the same reason in its window."""
    jacket = cat.index_of(golden.DEFECTIVE_JACKET)
    window_start = to_us(golden.JACKET_DEFECT_START)
    eligible = np.flatnonzero(
        ~pop.scripted
        & np.asarray([b == "normal" for b in pop.behaviour], dtype=np.bool_)
        & (pop.pickup_of < 0)
        & (pop.collective_of < 0)
        & (pop.created_at < window_start - US_PER_DAY)
    )
    k = golden.INDEPENDENT_JACKET_RETURNERS
    chosen = np.sort(rng.choice(eligible, size=min(k, len(eligible)), replace=False))
    sizes = jacket_sizes(cat)
    orders = []
    for j, c in enumerate(chosen):
        placed = window_start + (1 + 5 * j) * US_PER_DAY + int(rng.integers(17, 22)) * 3600 * 10**6
        fit = ScriptedLine(
            jacket,
            sizes[int(rng.integers(1, len(sizes) - 1))],  # S, M or L for an apparel jacket
            RETURN,
            golden.JACKET_DEFECT_REASON,
            int(rng.integers(2, 9)),
        )
        specs: tuple[Spec, ...] = (Fixed(fit), Keep()) if rng.random() < 0.5 else (Fixed(fit),)
        orders.append(pick.order(int(c), placed, int(rng.integers(2, 4)), *specs))
    return orders
