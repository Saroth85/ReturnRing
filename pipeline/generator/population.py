"""Customers of Navigli Fashion: behaviours, rings, families and account creation (US-102, US-103).

Shares of spec section 7: normal 80%, size 12%, wardrober 3%, promo rings about 1.5% (40 rings of
5 to 9 accounts at scale 1.0), shared benign 3.5% (families of 2 to 4 accounts). The behaviour is
ground truth: it is written to truth/labels only. Customer ids are numbered by account creation,
like a real shop, so an id says when the account was opened and nothing about its behaviour.

Hard situations: pickup points and collective addresses are shared by many unrelated customers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from pipeline.generator import golden
from pipeline.generator.config import (
    BEHAVIOUR_SHARES,
    COLLECTIVE_ADDRESSES_AT_SCALE_1,
    COLLECTIVE_SIZE,
    END_US,
    NEW_ACCOUNT_SHARE,
    OLD_ACCOUNT_YEARS,
    PICKUP_MIN_SIZE,
    PICKUP_POINTS_AT_SCALE_1,
    PICKUP_SIZE_SHARE,
    PROPENSITY_SHAPE,
    START_US,
    US_PER_DAY,
    ScenarioConfig,
    customers_at,
    to_us,
)
from pipeline.generator.timeline import uniform_us

I64 = npt.NDArray[np.int64]
F64 = npt.NDArray[np.float64]
B = npt.NDArray[np.bool_]

FIRST_NAMES = (
    "Alessandro", "Alessia", "Andrea", "Angela", "Antonio", "Arianna", "Beatrice", "Camilla",
    "Carlo", "Caterina", "Chiara", "Claudio", "Cristina", "Daniele", "Davide", "Elisa",
    "Emma", "Enrico", "Federica", "Federico", "Filippo", "Francesca", "Francesco", "Gabriele",
    "Giacomo", "Gianluca", "Giorgia", "Giovanni", "Giuseppe", "Greta", "Ilaria", "Irene",
    "Laura", "Leonardo", "Lorenzo", "Luca", "Lucia", "Ludovica", "Marco", "Maria",
    "Martina", "Matteo", "Michela", "Michele", "Nicola", "Noemi", "Paola", "Pietro",
    "Riccardo", "Roberta", "Roberto", "Samuele", "Sara", "Serena", "Silvia", "Simone",
    "Stefano", "Tommaso", "Valentina", "Valeria", "Vittoria", "Aisha", "Wei", "Ahmed",
    "Yasmine", "Ioana", "Mateo", "Fatima",
)  # fmt: skip
LAST_NAMES = (
    "Rossi", "Russo", "Ferrari", "Esposito", "Bianchi", "Romano", "Colombo", "Ricci",
    "Marino", "Greco", "Bruno", "Gallo", "Conti", "De Luca", "Mancini", "Costa",
    "Giordano", "Rizzo", "Lombardi", "Moretti", "Barbieri", "Fontana", "Santoro", "Mariani",
    "Rinaldi", "Caruso", "Ferrara", "Galli", "Martini", "Leone", "Longo", "Gentile",
    "Martinelli", "Vitale", "Lombardo", "Serra", "Coppola", "De Santis", "D'Angelo", "Marchetti",
    "Parisi", "Villa", "Conte", "Ferraro", "Ferri", "Fabbri", "Bianco", "Marini",
    "Grasso", "Valentini", "Messina", "Sala", "De Angelis", "Gatti", "Pellegrini", "Palumbo",
    "Sanna", "Farina", "Rizzi", "Monti", "Cattaneo", "Morelli", "Amato", "Silvestri",
    "Mazza", "Testa", "Grassi", "Pellegrino", "Carbone", "Giuliani", "Benedetti", "Barone",
    "Rossetti", "Caputo", "Montanari", "Guerra", "Palmieri", "Bernardi", "Martino", "Fiore",
    "Popescu", "Chen", "Hassan", "Fernandez", "Kaur", "Nguyen",
)  # fmt: skip
FAMILY_SIZES = ((2, 0.45), (3, 0.35), (4, 0.20))
FAMILY_NEW_ACCOUNT_SHARE = 0.2  # benign families are mostly long-standing customers
HARD_FAMILY_SPREAD_DAYS = 10


@dataclass
class Ring:
    ring_id: str
    members: list[int]


@dataclass
class Family:
    family_id: str
    members: list[int]
    card_pair: tuple[int, int] | None  # (owner, sharer) of a shared card
    hard: bool  # created together, welcome coupon each, one card for all (OOD)


@dataclass
class Population:
    customer_id: list[str]
    behaviour: list[str]
    display_name: list[str]
    first_name: list[str]
    last_name: list[str]
    created_at: I64
    propensity: F64
    ring_of: I64
    family_of: I64
    pickup_of: I64
    collective_of: I64
    scripted: B  # golden accounts: orders and identifiers come from scripts.py
    rings: list[Ring]
    families: list[Family]
    n_pickup: int
    n_collective: int
    _index: dict[str, int] = field(default_factory=dict, repr=False)

    @property
    def n(self) -> int:
        return len(self.customer_id)

    def index(self, customer_id: str) -> int:
        if not self._index:
            self._index = {c: i for i, c in enumerate(self.customer_id)}
        return self._index[customer_id]


def _ring_sizes(n: int, n_rings: int, cfg: ScenarioConfig, rng: np.random.Generator) -> list[int]:
    lo, hi = cfg.ring_size
    fixed = [len(golden.MARCO_ACCOUNTS)] if cfg.golden else []
    k = n_rings - len(fixed)
    sizes = [int(s) for s in rng.integers(lo, hi + 1, k)]
    target = round(BEHAVIOUR_SHARES["promo_ring"] * n) - sum(fixed)
    target = min(max(target, lo * k), hi * k)
    while sum(sizes) != target:
        grow = sum(sizes) < target
        candidates = [i for i, s in enumerate(sizes) if (s < hi if grow else s > lo)]
        i = candidates[int(rng.integers(0, len(candidates)))]
        sizes[i] += 1 if grow else -1
    return fixed + sizes


def _family_sizes(total: int, rng: np.random.Generator) -> list[int]:
    sizes: list[int] = []
    remaining = total
    values = [s for s, _ in FAMILY_SIZES]
    probs = [p for _, p in FAMILY_SIZES]
    while remaining > 0:
        if remaining <= 4:
            sizes.append(remaining)
            break
        size = int(rng.choice(values, p=probs))
        if remaining - size == 1:
            size = size + 1 if size < 4 else size - 1
        sizes.append(size)
        remaining -= size
    return sizes


def _created(rng: np.random.Generator, n: int, new_share: float) -> I64:
    new = rng.random(n) < new_share
    old = uniform_us(rng, START_US - OLD_ACCOUNT_YEARS * 365 * US_PER_DAY, START_US - US_PER_DAY, n)
    fresh = uniform_us(rng, START_US, END_US - 21 * US_PER_DAY, n)
    return np.where(new, fresh, old)


def build_population(scale: float, cfg: ScenarioConfig, rng: np.random.Generator) -> Population:
    n = customers_at(scale)
    n_rings = max(1, round(cfg.rings_at_scale_1 * scale))
    ring_sizes = _ring_sizes(n, n_rings, cfg, rng)

    fixed_families: list[tuple[str, int, bool]] = []
    if cfg.golden:
        fixed_families += [(golden.ROSSI_FAMILY, 3, False), (golden.ROSSI_CARD_FAMILY, 3, False)]
    if cfg.hard_family:
        fixed_families.append((golden.OOD_HARD_FAMILY, golden.OOD_HARD_FAMILY_SIZE, True))
    fixed_members = sum(size for _, size, _ in fixed_families)
    generic_members = max(0, round(BEHAVIOUR_SHARES["shared_benign"] * n) - fixed_members)
    if generic_members == 1:
        generic_members = 2
    family_sizes = _family_sizes(generic_members, rng)

    n_wardrober = round(BEHAVIOUR_SHARES["wardrober"] * n)
    n_size = round(BEHAVIOUR_SHARES["size"] * n)
    n_normal = n - sum(ring_sizes) - fixed_members - generic_members - n_wardrober - n_size
    if n_normal < 1:
        raise ValueError(f"scale {scale} leaves no normal customer")

    behaviour: list[str] = []
    fixed_id: list[str | None] = []
    created = np.zeros(n, dtype=np.int64)
    ring_of = np.full(n, -1, dtype=np.int64)
    family_of = np.full(n, -1, dtype=np.int64)
    rings: list[Ring] = []
    families: list[Family] = []

    def add(kind: str, count: int, customer_ids: tuple[str, ...] = ()) -> list[int]:
        first = len(behaviour)
        behaviour.extend([kind] * count)
        fixed_id.extend(customer_ids or [None] * count)
        return list(range(first, first + count))

    # Golden accounts first: their creation and names are fixed (US-104).
    if cfg.golden:
        add("size", 1, (golden.GIULIA,))
        marco = add("promo_ring", 7, golden.MARCO_ACCOUNTS)
        rings.append(Ring(golden.MARCO_RING, marco))
        rossi = add("shared_benign", 3, golden.ROSSI_ACCOUNTS)
        families.append(Family(golden.ROSSI_FAMILY, rossi, None, False))
        rossi_card = add("shared_benign", 3, golden.ROSSI_CARD_ACCOUNTS)
        families.append(
            Family(golden.ROSSI_CARD_FAMILY, rossi_card, (rossi_card[0], rossi_card[1]), False)
        )
        add("normal", 1, (golden.ELENA,))
        for i, customer in enumerate(fixed_id):
            assert customer is not None
            created[i] = to_us(golden.ACCOUNTS[customer][0])
    n_scripted = len(behaviour)

    # Rings: new accounts opened in a burst (main) or slowly (OOD).
    lo_start = START_US + 14 * US_PER_DAY
    hi_start = max(lo_start + US_PER_DAY, END_US - (cfg.ring_activity_days + 14) * US_PER_DAY)
    generic_rings: list[list[int]] = []
    for size in ring_sizes[len(rings) :]:
        members = add("promo_ring", size)
        start = int(uniform_us(rng, lo_start, hi_start, 1)[0])
        spread = int(
            rng.integers(cfg.ring_creation_spread_days[0], cfg.ring_creation_spread_days[1] + 1)
        )
        created[members] = np.sort(uniform_us(rng, start, start + spread * US_PER_DAY, size))
        generic_rings.append(members)

    # Families: mostly old accounts; hard families are opened together.
    generic_families: list[tuple[list[int], bool]] = []
    for family_id, size, hard in fixed_families[2 if cfg.golden else 0 :]:
        members = add("shared_benign", size)
        families.append(Family(family_id, members, (members[0], members[1]), hard))
    hard_flags = rng.random(len(family_sizes)) < cfg.hard_family_share
    for size, hard in zip(family_sizes, hard_flags, strict=True):
        generic_families.append((add("shared_benign", size), bool(hard)))
    for family in families:
        if family.hard:
            _hard_created(rng, created, family.members)
    for members, hard in generic_families:
        if hard:
            _hard_created(rng, created, members)
        else:
            created[members] = _created(rng, len(members), FAMILY_NEW_ACCOUNT_SHARE)

    others = add("wardrober", n_wardrober) + add("size", n_size - (1 if cfg.golden else 0))
    others += add("normal", n_normal - (1 if cfg.golden else 0))
    created[others] = _created(rng, len(others), NEW_ACCOUNT_SHARE)
    assert len(behaviour) == n

    # Ids: golden fixed; the others numbered by creation time, ties in random order.
    tiebreak = rng.random(n)
    generic = np.arange(n_scripted, n)
    ordered = generic[np.lexsort((tiebreak[generic], created[generic]))]
    customer_id: list[str] = [c or "" for c in fixed_id]
    for number, i in enumerate(ordered, start=1):
        customer_id[int(i)] = f"C-{cfg.id_prefix}{number:06d}"

    # Ring and family ids: numbered by their first account.
    for number, members in enumerate(sorted(generic_rings, key=lambda m: int(created[m].min())), 1):
        rings.append(Ring(f"R-{cfg.id_prefix}{number:03d}", members))
    card_pairs = _card_sharing(cfg, len(generic_families), rng)
    by_creation = sorted(
        range(len(generic_families)), key=lambda j: int(created[generic_families[j][0]].min())
    )
    for number, j in enumerate(by_creation, 1):
        members, hard = generic_families[j]
        pair = (members[0], members[1]) if (hard or j in card_pairs) else None
        families.append(Family(f"F-{cfg.id_prefix}{number:04d}", members, pair, hard))
    for r, ring in enumerate(rings):
        ring_of[ring.members] = r
    for f, family in enumerate(families):
        family_of[family.members] = f

    first, last, display = _names(rng, behaviour, families, fixed_id)
    scripted = np.zeros(n, dtype=np.bool_)
    scripted[:n_scripted] = True
    pop = Population(
        customer_id=customer_id,
        behaviour=behaviour,
        display_name=display,
        first_name=first,
        last_name=last,
        created_at=created,
        propensity=rng.gamma(PROPENSITY_SHAPE, 1.0 / PROPENSITY_SHAPE, n),
        ring_of=ring_of,
        family_of=family_of,
        pickup_of=np.full(n, -1, dtype=np.int64),
        collective_of=np.full(n, -1, dtype=np.int64),
        scripted=scripted,
        rings=rings,
        families=families,
        n_pickup=0,
        n_collective=0,
    )
    _assign_shared_places(pop, scale, rng)
    return pop


def _hard_created(rng: np.random.Generator, created: I64, members: list[int]) -> None:
    start = int(uniform_us(rng, START_US + 30 * US_PER_DAY, END_US - 150 * US_PER_DAY, 1)[0])
    spread = HARD_FAMILY_SPREAD_DAYS * US_PER_DAY
    created[members] = np.sort(uniform_us(rng, start, start + spread, len(members)))


def _card_sharing(cfg: ScenarioConfig, n_families: int, rng: np.random.Generator) -> set[int]:
    """Generic families where two accounts share a card: the Rossi-like hard situation."""
    if n_families == 0 or cfg.family_card_share <= 0:
        return set()
    k = min(n_families, max(1, round(cfg.family_card_share * n_families)))
    return {int(j) for j in rng.choice(n_families, size=k, replace=False)}


def _names(
    rng: np.random.Generator,
    behaviour: list[str],
    families: list[Family],
    fixed_id: list[str | None],
) -> tuple[list[str], list[str], list[str]]:
    n = len(behaviour)
    first = [FIRST_NAMES[int(i)] for i in rng.integers(0, len(FIRST_NAMES), n)]
    last = [LAST_NAMES[int(i)] for i in rng.integers(0, len(LAST_NAMES), n)]
    for family in families:
        surname = last[family.members[0]]
        for m in family.members:
            last[m] = surname
    display = [f"{a} {b}" for a, b in zip(first, last, strict=True)]
    reserved = {name for _, name in golden.ACCOUNTS.values()}
    for i, customer in enumerate(fixed_id):
        if customer is not None:
            name = golden.ACCOUNTS[customer][1]
            display[i] = name
            first[i], _, last[i] = name.partition(" ")
        elif display[i] in reserved:  # no stranger shares a golden name in the UI
            display[i] = f"{first[i]} {last[i]} Jr"
    return first, last, display


def _assign_shared_places(pop: Population, scale: float, rng: np.random.Generator) -> None:
    """Pickup points (lockers, post offices) and collective addresses (student halls, offices)."""
    n = pop.n
    free = np.flatnonzero(~pop.scripted)
    n_pickup = max(1, round(PICKUP_POINTS_AT_SCALE_1 * scale))
    lo = max(PICKUP_MIN_SIZE[0], round(PICKUP_SIZE_SHARE[0] * n))
    hi = max(PICKUP_MIN_SIZE[1], round(PICKUP_SIZE_SHARE[1] * n))
    available = rng.permutation(free)
    cursor = 0
    budget = int(0.3 * len(free))
    for p in range(n_pickup):
        size = int(rng.integers(lo, hi + 1))
        if cursor + size > budget:
            break
        pop.pickup_of[available[cursor : cursor + size]] = p
        cursor += size
        pop.n_pickup = p + 1
    eligible = np.flatnonzero(
        ~pop.scripted & (pop.family_of < 0) & (pop.ring_of < 0) & (pop.pickup_of < 0)
    )
    available = rng.permutation(eligible)
    cursor = 0
    n_collective = max(1, round(COLLECTIVE_ADDRESSES_AT_SCALE_1 * scale))
    for c in range(n_collective):
        size = int(rng.integers(COLLECTIVE_SIZE[0], COLLECTIVE_SIZE[1] + 1))
        if cursor + size > len(available) // 4:
            break
        pop.collective_of[available[cursor : cursor + size]] = c
        cursor += size
        pop.n_collective = c + 1
