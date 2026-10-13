"""Identifiers of each account (store/identifiers): raw values, sharing, first use (spec 7, 8.3).

Raw values are generated as a shop would see them (e-mail aliases, addresses written in several
ways, phone formats), normalised with pipeline.generator.identity and kept only as salted hashes.
Who shares what:

- every account has its own e-mail, phone, device, card, home address and home IP prefix, with a
  second device, card or a mobile IP prefix for some;
- a promo ring has a pool of shared values (device, address, card, phone, IP prefix and one gmail
  base whose aliases normalise to one e-mail); each account uses each shared value with a
  probability ("with noise", ScenarioConfig.ring_sharing);
- a family shares the home address, a household device and the home IP prefix; accounts keep their
  own cards, except in card-sharing families (the Rossi-like hard situation);
- pickup points and collective addresses are shared by many unrelated customers; mobile carrier
  prefixes are shared by many customers too.

An identifier row records its first use by the account (ADR-13): at account creation (e-mail,
phone, personal device), at the first order (card, address, IP prefix) or at a later order (second
card or device, pickup point). For accounts older than the period, first orders happened before it.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from pipeline.generator.config import START_US, US_PER_DAY, US_PER_SECOND, ScenarioConfig
from pipeline.generator.identity import (
    Hasher,
    ip_prefix24,
    normalize_address,
    normalize_email,
    normalize_phone,
    normalize_token,
)
from pipeline.generator.population import Population

I64 = npt.NDArray[np.int64]
When = int | Literal["created", "first_order", "later_order"]

STRONG_KINDS = ("card", "email", "device")
SECOND_DEVICE_SHARE = 0.35
SECOND_CARD_SHARE = 0.25
MOBILE_IP_SHARE = 0.4
RING_EXTRA_DEVICE = 0.3
RING_EXTRA_CARD = 0.2

EMAIL_DOMAINS = (
    ("gmail.com", 0.42),
    ("libero.it", 0.14),
    ("hotmail.it", 0.1),
    ("outlook.com", 0.08),
    ("yahoo.it", 0.07),
    ("icloud.com", 0.07),
    ("virgilio.it", 0.05),
    ("tiscali.it", 0.04),
    ("alice.it", 0.03),
)
STREET_TYPES = (("Via", 0.7), ("Viale", 0.1), ("Corso", 0.08), ("Piazza", 0.07), ("Largo", 0.05))
ABBREVIATED = {
    "Via": ("V.",),
    "Viale": ("V.le",),
    "Corso": ("C.so",),
    "Piazza": ("P.za", "P.zza"),
    "Largo": ("L.go",),
}
STREETS = (
    "Roma", "Garibaldi", "Mazzini", "Dante", "Verdi", "Cavour", "Manzoni", "Marconi",
    "Matteotti", "Gramsci", "Vittorio Veneto", "XX Settembre", "IV Novembre", "Leopardi",
    "Carducci", "Pascoli", "Volta", "Galilei", "Colombo", "Kennedy", "della Repubblica",
    "dei Mille", "San Francesco", "Sant'Ambrogio", "Santa Maria", "della Libertà", "Nazionale",
    "Torino", "Milano", "Napoli", "Venezia", "Trieste", "Bologna", "Firenze", "Genova",
    "Lorenteggio", "Padova", "Monza", "Melzo", "Tortona", "Solari", "Savona", "Bergognone",
    "Sempione", "Washington", "Buenos Aires", "Lodi", "Ripamonti", "Ticinese", "Porta Romana",
    "Moscova", "Brera", "Fiori Chiari", "Montenapoleone", "Paolo Sarpi", "Canonica", "Procaccini",
    "Niccolò Tommaseo", "Foppa", "Morgagni", "Plinio", "Pergolesi", "Vitruvio", "Settembrini",
    "Lazzaretto", "Tadino", "Castelmorrone", "Ampère", "Pacini", "Celoria", "Bassini", "Golgi",
    "Nizza", "Po", "Cernaia", "Pietro Micca", "Cibrario", "Duca degli Abruzzi", "Arenella",
    "Toledo", "Chiaia", "Etnea", "Libertà", "Indipendenza", "Saragozza", "Mazzini Giuseppe",
)  # fmt: skip
CITIES = (
    ("Milano", 20121, 20162, 0.30),
    ("Roma", 118, 199, 0.12),
    ("Torino", 10121, 10156, 0.07),
    ("Napoli", 80121, 80147, 0.06),
    ("Bologna", 40121, 40141, 0.05),
    ("Firenze", 50121, 50145, 0.05),
    ("Genova", 16121, 16167, 0.04),
    ("Monza", 20900, 20900, 0.04),
    ("Bergamo", 24121, 24129, 0.04),
    ("Brescia", 25121, 25136, 0.03),
    ("Verona", 37121, 37142, 0.03),
    ("Padova", 35121, 35143, 0.03),
    ("Bari", 70121, 70132, 0.03),
    ("Palermo", 90121, 90151, 0.03),
    ("Como", 22100, 22100, 0.02),
    ("Pavia", 27100, 27100, 0.02),
    ("Parma", 43121, 43126, 0.02),
    ("Catania", 95121, 95131, 0.02),
)
PICKUP_NAMES = (
    "Locker InPost Stazione Centrale",
    "Punto Ritiro BRT",
    "Ufficio Postale",
    "Amazon Hub Locker",
    "Punto Poste Tabaccheria",
    "Locker Esselunga",
    "GLS Parcel Shop",
    "Mail Boxes Etc.",
)
COLLECTIVE_NAMES = (
    "Residenza Universitaria",
    "Collegio",
    "Coworking",
    "Condominio",
    "Uffici",
    "Studentato",
)
INTERIORS = ("int. {n}", "interno {n}", "scala {s}", "piano {p}", "scala {s} int. {n}")
ISP_FIRST_OCTETS = (2, 5, 31, 37, 79, 80, 82, 87, 93, 95, 151, 176, 185, 188, 212, 213, 217)
MOBILE_FIRST_OCTETS = (5, 37, 151, 176)


@dataclass(frozen=True)
class IdentifierUse:
    customer: int
    kind: str
    normalized: str
    when: When


@dataclass(frozen=True)
class Address:
    """An address as parts; render() writes it the way one customer types it."""

    street_type: str
    street: str
    number: int
    cap: int
    city: str
    name: str = ""  # pickup point or building name, part of the address

    def plain(self) -> str:
        head = f"{self.name}, " if self.name else ""
        return f"{head}{self.street_type} {self.street} {self.number}, {self.cap:05d} {self.city}"

    def normalized(self) -> str:
        return normalize_address(self.plain())

    def render(self, rng: np.random.Generator) -> str:
        street_type = self.street_type
        if rng.random() < 0.25:
            options = ABBREVIATED[street_type]
            street_type = options[int(rng.integers(0, len(options)))]
        number = f"n. {self.number}" if rng.random() < 0.1 else str(self.number)
        separator = (", ", " ", ", ")[int(rng.integers(0, 3))]
        head = f"{self.name}, " if self.name else ""
        text = f"{head}{street_type} {self.street}{separator}{number}, {self.cap:05d} {self.city}"
        if rng.random() < 0.3:
            interior = INTERIORS[int(rng.integers(0, len(INTERIORS)))]
            text += ", " + interior.format(
                n=int(rng.integers(1, 40)),
                s="ABCD"[int(rng.integers(0, 4))],
                p=int(rng.integers(1, 8)),
            )
        case = rng.random()
        if case < 0.2:
            return text.lower()
        if case < 0.35:
            return text.upper()
        return text


def _ascii(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in plain.lower() if c.isalnum())


class IdentityFactory:
    """Raw identifier values, unique unless sharing is intended."""

    def __init__(self, rng: np.random.Generator, n_customers: int) -> None:
        self.rng = rng
        self._emails: set[str] = set()
        self._addresses: set[str] = set()
        self._phones: set[str] = set()
        self.home_prefixes = self._prefixes(max(64, 2 * n_customers), ISP_FIRST_OCTETS)
        self.mobile_prefixes = self._prefixes(max(8, n_customers // 60), MOBILE_FIRST_OCTETS)

    def _pick(self, options: tuple[tuple[str, float], ...]) -> str:
        weights = np.asarray([w for _, w in options])
        return options[int(self.rng.choice(len(options), p=weights / weights.sum()))][0]

    def _prefixes(self, n: int, first_octets: tuple[int, ...]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        while len(out) < n:
            a = first_octets[int(self.rng.integers(0, len(first_octets)))]
            prefix = f"{a}.{int(self.rng.integers(0, 256))}.{int(self.rng.integers(0, 256))}"
            if prefix not in seen:
                seen.add(prefix)
                out.append(prefix)
        return out

    def email(self, first: str, last: str) -> str:
        f, s = _ascii(first) or "cliente", _ascii(last) or "navigli"
        styles = (f"{f}.{s}", f"{f}{s}", f"{f[0]}{s}", f"{f}_{s}", f"{s}.{f}", f"{f}.{s[0]}")
        while True:
            local = styles[int(self.rng.integers(0, len(styles)))]
            if self.rng.random() < 0.6:
                local += str(int(self.rng.integers(1, 99)))
            raw = f"{local}@{self._pick(EMAIL_DOMAINS)}"
            if self.rng.random() < 0.15:
                raw = raw.title()
            key = normalize_email(raw)
            if key not in self._emails:
                self._emails.add(key)
                return raw

    def reserve_email(self, raw: str) -> str:
        """A fixed e-mail (golden cases): no generated account will normalise to it."""
        self._emails.add(normalize_email(raw))
        return raw

    def gmail_base(self, first: str, last: str) -> str:
        """A gmail local part for a ring: its aliases normalise to one address."""
        while True:
            local = f"{_ascii(first)}.{_ascii(last)}{int(self.rng.integers(10, 99))}"
            if normalize_email(f"{local}@gmail.com") not in self._emails:
                self._emails.add(normalize_email(f"{local}@gmail.com"))
                return local

    def alias(self, local: str, k: int) -> str:
        """The k-th spelling of a gmail base: dots moved, a +tag added."""
        letters = local.replace(".", "")
        cut = 1 + k % max(1, len(letters) - 1)
        spelled = letters[:cut] + "." + letters[cut:] if k % 2 else letters
        return f"{spelled}+{('shop', 'promo', 'x', str(k))[k % 4]}@gmail.com"

    def phone(self) -> str:
        while True:
            digits = (
                f"3{int(self.rng.integers(20, 100))}{int(self.rng.integers(0, 10_000_000)):07d}"
            )
            if digits not in self._phones:
                self._phones.add(digits)
                break
        style = int(self.rng.integers(0, 3))
        if style == 0:
            return f"+39 {digits[:3]} {digits[3:6]} {digits[6:]}"
        if style == 1:
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        return f"0039{digits}"

    def token(self, prefix: str) -> str:
        raw = f"{prefix}-{self.rng.bytes(12).hex()}"
        return raw.upper() if self.rng.random() < 0.3 else raw

    def address(self, name: str = "") -> Address:
        while True:
            city, low, high, _ = CITIES[int(self.rng.choice(len(CITIES), p=_city_weights()))]
            parts = Address(
                street_type=self._pick(STREET_TYPES),
                street=STREETS[int(self.rng.integers(0, len(STREETS)))],
                number=int(self.rng.integers(1, 250)),
                cap=int(self.rng.integers(low, high + 1)),
                city=city,
                name=name,
            )
            key = parts.normalized()
            if key not in self._addresses:
                self._addresses.add(key)
                return parts

    def ip(self, prefix: str) -> str:
        return f"{prefix}.{int(self.rng.integers(1, 255))}"

    def home_ip(self) -> str:
        return self.ip(self.home_prefixes[int(self.rng.integers(0, len(self.home_prefixes)))])

    def mobile_ip(self) -> str:
        return self.ip(self.mobile_prefixes[int(self.rng.integers(0, len(self.mobile_prefixes)))])


def _city_weights() -> npt.NDArray[np.float64]:
    w = np.asarray([c[3] for c in CITIES], dtype=np.float64)
    return w / w.sum()


def normalize(kind: str, raw: str) -> str:
    if kind == "email":
        return normalize_email(raw)
    if kind == "phone":
        return normalize_phone(raw)
    if kind == "address":
        return normalize_address(raw)
    if kind == "ip_prefix":
        return ip_prefix24(raw)
    return normalize_token(raw)


class Plan:
    """Collects IdentifierUse rows, normalising raw values."""

    def __init__(self) -> None:
        self.uses: list[IdentifierUse] = []

    def add(self, customer: int, kind: str, raw: str, when: When) -> None:
        self.uses.append(IdentifierUse(customer, kind, normalize(kind, raw), when))


def plan_generic_identifiers(
    pop: Population,
    cfg: ScenarioConfig,
    factory: IdentityFactory,
    plan: Plan,
    pickups: list[Address],
    collectives: list[Address],
) -> None:
    """Identifiers of every account not scripted in scripts.py."""
    rng = factory.rng
    grouped = (pop.ring_of >= 0) | (pop.family_of >= 0)
    missing = {
        kind: rng.random(pop.n) < cfg.missing.get(kind, 0.0)
        for kind in ("phone", "device", "card", "ip_prefix")
    }
    for i in range(pop.n):
        if pop.scripted[i] or grouped[i]:
            continue
        _own(plan, factory, pop, i, missing, collectives)
        if pop.pickup_of[i] >= 0:
            plan.add(i, "address", pickups[pop.pickup_of[i]].render(rng), "later_order")
    for ring in pop.rings:
        if not pop.scripted[ring.members[0]]:
            _ring(plan, factory, pop, cfg, ring.members, missing)
    for family in pop.families:
        if not pop.scripted[family.members[0]]:
            _family(plan, factory, pop, family.members, family.card_pair, family.hard, missing)
    for member in np.flatnonzero(grouped & ~pop.scripted & (pop.pickup_of >= 0)):
        place = pickups[pop.pickup_of[member]]
        plan.add(int(member), "address", place.render(rng), "later_order")


def _own(
    plan: Plan,
    factory: IdentityFactory,
    pop: Population,
    i: int,
    missing: dict[str, npt.NDArray[np.bool_]],
    collectives: list[Address],
) -> None:
    rng = factory.rng
    plan.add(i, "email", factory.email(pop.first_name[i], pop.last_name[i]), "created")
    if not missing["phone"][i]:
        plan.add(i, "phone", factory.phone(), "created")
    if not missing["device"][i]:
        plan.add(i, "device", factory.token("dev"), "created")
        if rng.random() < SECOND_DEVICE_SHARE:
            plan.add(i, "device", factory.token("dev"), "later_order")
    if not missing["card"][i]:
        plan.add(i, "card", factory.token("card_fp"), "first_order")
        if rng.random() < SECOND_CARD_SHARE:
            plan.add(i, "card", factory.token("card_fp"), "later_order")
    home = collectives[pop.collective_of[i]] if pop.collective_of[i] >= 0 else factory.address()
    plan.add(i, "address", home.render(rng), "first_order")
    if not missing["ip_prefix"][i]:
        plan.add(i, "ip_prefix", factory.home_ip(), "first_order")
        if rng.random() < MOBILE_IP_SHARE:
            plan.add(i, "ip_prefix", factory.mobile_ip(), "later_order")


def _ring(
    plan: Plan,
    factory: IdentityFactory,
    pop: Population,
    cfg: ScenarioConfig,
    members: list[int],
    missing: dict[str, npt.NDArray[np.bool_]],
) -> None:
    rng = factory.rng
    lead = members[0]
    pools: dict[str, list[str]] = {
        "device": [factory.token("dev")],
        "card": [factory.token("card_fp") for _ in range(int(rng.integers(1, 3)))],
        "phone": [factory.phone()],
        "ip_prefix": [factory.home_ip() for _ in range(int(rng.integers(1, 3)))],
    }
    addresses = [factory.address() for _ in range(int(rng.integers(1, 3)))]
    base = factory.gmail_base(pop.first_name[lead], pop.last_name[lead])
    uses: dict[int, dict[str, str]] = {m: {} for m in members}  # shared value chosen per kind
    for m in members:
        for kind in ("device", "card", "phone", "ip_prefix", "address", "email"):
            if kind in missing and missing[kind][m]:
                continue
            if rng.random() >= cfg.ring_sharing[kind]:
                continue
            if kind == "address":
                uses[m][kind] = str(int(rng.integers(0, len(addresses))))
            elif kind == "email":
                uses[m][kind] = "alias"
            else:
                pool = pools[kind]
                uses[m][kind] = pool[int(rng.integers(0, len(pool)))]
    if cfg.ring_requires_strong_link:
        _link_everyone(uses, members, pools["device"][0], missing)
    for k, m in enumerate(members):
        mine = uses[m]
        if "email" in mine:
            plan.add(m, "email", factory.alias(base, k), "created")
        else:
            plan.add(m, "email", factory.email(pop.first_name[m], pop.last_name[m]), "created")
        if not missing["phone"][m]:
            plan.add(m, "phone", mine.get("phone") or factory.phone(), "created")
        if not missing["device"][m]:
            plan.add(m, "device", mine.get("device") or factory.token("dev"), "created")
            if "device" in mine and rng.random() < RING_EXTRA_DEVICE:
                plan.add(m, "device", factory.token("dev"), "later_order")
        if not missing["card"][m]:
            plan.add(m, "card", mine.get("card") or factory.token("card_fp"), "first_order")
            if "card" in mine and rng.random() < RING_EXTRA_CARD:
                plan.add(m, "card", factory.token("card_fp"), "later_order")
        if "address" in mine:
            plan.add(m, "address", addresses[int(mine["address"])].render(rng), "first_order")
        else:
            plan.add(m, "address", factory.address().render(rng), "first_order")
        if not missing["ip_prefix"][m]:
            ip = mine.get("ip_prefix") or factory.home_ip()
            plan.add(m, "ip_prefix", factory.ip(ip.rsplit(".", 1)[0]), "first_order")


def _link_everyone(
    uses: dict[int, dict[str, str]],
    members: list[int],
    device: str,
    missing: dict[str, npt.NDArray[np.bool_]],
) -> None:
    """Main scenario: every ring account shares a card, e-mail or device with another one."""

    def sharers(kind: str, value: str) -> int:
        return sum(1 for m in members if uses[m].get(kind) == value)

    def linked(m: int) -> bool:
        return any(kind in uses[m] and sharers(kind, uses[m][kind]) >= 2 for kind in STRONG_KINDS)

    for m in members:
        if not linked(m):
            missing["device"][m] = False
            uses[m]["device"] = device
    if sharers("device", device) == 1:  # the only account on the shared device: add a second
        other = next(m for m in members if uses[m].get("device") != device)
        missing["device"][other] = False
        uses[other]["device"] = device


def _family(
    plan: Plan,
    factory: IdentityFactory,
    pop: Population,
    members: list[int],
    card_pair: tuple[int, int] | None,
    hard: bool,
    missing: dict[str, npt.NDArray[np.bool_]],
) -> None:
    rng = factory.rng
    home = factory.address()
    household_device = factory.token("dev")
    household_ip = factory.home_ip().rsplit(".", 1)[0]
    shared_card = factory.token("card_fp")
    for m in members:
        plan.add(m, "email", factory.email(pop.first_name[m], pop.last_name[m]), "created")
        if hard or not missing["phone"][m]:
            plan.add(m, "phone", factory.phone(), "created")
        plan.add(m, "device", household_device, "created" if hard else "first_order")
        if not hard and not missing["device"][m]:
            plan.add(m, "device", factory.token("dev"), "created")
        plan.add(m, "address", home.render(rng), "first_order")
        plan.add(m, "ip_prefix", factory.ip(household_ip), "first_order")
        if hard:
            plan.add(m, "card", shared_card, "first_order")
        elif card_pair is not None and m == card_pair[1]:
            plan.add(m, "card", shared_card, "later_order")
            if rng.random() < 0.5:
                plan.add(m, "card", factory.token("card_fp"), "first_order")
        elif card_pair is not None and m == card_pair[0]:
            plan.add(m, "card", shared_card, "first_order")
        elif not missing["card"][m]:
            plan.add(m, "card", factory.token("card_fp"), "first_order")


def resolve(
    uses: Iterable[IdentifierUse],
    pop: Population,
    order_times: list[list[int]],
    hasher: Hasher,
    rng: np.random.Generator,
) -> tuple[list[int], list[str], list[str], list[int]]:
    """Rows of store/identifiers: (customer, kind, value_hash, first use), one per distinct
    (customer, kind, value), at the earliest of its uses."""
    first: dict[tuple[int, str, str], int] = {}
    for use in uses:
        c = use.customer
        if isinstance(use.when, int):
            at = use.when
        elif use.when == "created":
            at = int(pop.created_at[c])
        elif use.when == "first_order":
            created = int(pop.created_at[c])
            if created < START_US:
                room = min(60 * US_PER_DAY, START_US - created - US_PER_SECOND)
                at = created + int(rng.integers(0, max(room, 1)))
            else:
                at = order_times[c][0] if order_times[c] else created
        else:
            times = order_times[c]
            at = times[int(rng.integers(0, len(times)))] if times else int(pop.created_at[c])
        key = (c, use.kind, hasher.hash(use.kind, use.normalized))
        if key not in first or at < first[key]:
            first[key] = at
    rows = sorted(first.items())
    return (
        [k[0] for k, _ in rows],
        [k[1] for k, _ in rows],
        [k[2] for k, _ in rows],
        [t for _, t in rows],
    )
