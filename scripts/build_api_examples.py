"""Build the golden HTTP API examples in contracts/examples/api/ (ADR-15).

The examples are the mock-server data of the UI (phase 5) and fixtures of the API (phase 4): the
four demo cases of spec section 4 (Giulia; C-MARCO-1, an account of the ring of 7 linked accounts;
the Rossi family with and without the shared card; Elena and the defective jacket V-JKT-DEF), a
case card for every other queued return, and an example for every other endpoint. Nothing is
typed by hand that code can compute: decisions come from policy.engine, net benefits from
policy.simulator (unit costs of assumptions-v1), link weights from the formulas of spec 8.3,
customer value from the orders (spec 8.5), message fields from the renderers of
apps/api/schemas.py. Every provenance says "Contract example" and is synthetic or simulated:
these numbers are not results.

    python -m scripts.build_api_examples           # write
    python -m scripts.build_api_examples --check   # exit 1 if a committed file differs
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel

from apps.api import schemas as s
from contracts.io import Nature
from contracts.vocab import (
    DEMO_USERS,
    AuditAction,
    AuditEntityType,
    DecisionStatus,
    GenerationPath,
    Lang,
    OverrideCategory,
    RevisionTrigger,
    Tone,
)
from policy.engine import NAVIGLI_BASE_TERMS, Thresholds, approve, decide, quadrant_for
from policy.legal_guard import check, is_restrictive
from policy.simulator import PROPOSAL_EXAMPLE
from policy.simulator import Assumptions as SimAssumptions
from policy.types import (
    CaseScores,
    Decision,
    DecisionState,
    DiminishedValueClaim,
    EvidenceQuality,
    Quadrant,
    ReturnReason,
    ReturnRequest,
)
from policy.types import (
    Path as ReturnPath,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "contracts" / "examples" / "api"

# ---- Demo clock, policy settings and provenance -----------------------------------------------

AS_OF = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)  # end of the synthetic year (spec 7)
DATA_START, DATA_END = date(2025, 10, 1), date(2026, 9, 30)
NEXT_START, NEXT_END = date(2026, 10, 1), date(2027, 9, 30)
BASE = NAVIGLI_BASE_TERMS
THRESHOLDS = Thresholds(risk=0.6, value_eur=Decimal("185.00"))
MODEL_VERSIONS = {
    "risk": "risk-xgb-0.1.0",
    "propensity": "propensity-xgb-0.1.0",
    "graph": "graph-0.1.0",
    "evidence": "evidence-0.1.0",
    "value": "value-0.1.0",
}
ASSUMPTIONS_V1 = "assumptions-v1"
V1 = s.Assumptions.from_policy(PROPOSAL_EXAMPLE)  # the stored version: unit costs 8, 12, 22 EUR
assert V1.version == ASSUMPTIONS_V1
COUPON_RULE = "coupon-rule-v1"
MARGIN = Decimal("0.55")  # spec 4: average gross margin (hypothesis)
LOGISTICS = Decimal("8")  # spec 4: logistics cost per return (hypothesis)
CENT = Decimal("0.01")
EXAMPLE = "Contract example: "


def prov(
    origin: str, start: date = DATA_START, end: date = DATA_END, nature: Nature = "synthetic"
) -> s.Provenance:
    return s.Provenance(origin=EXAMPLE + origin, period_start=start, period_end=end, nature=nature)


SHOP = prov("Navigli Fashion synthetic shop, generator seed 42")
SCORES = prov("risk model risk-xgb-0.1.0 on decision-time features of the synthetic shop")
VALUE = prov("customer value, 12-month net margin (spec 8.5) of the synthetic shop")
EVIDENCE = prov("evidence quality evidence-0.1.0 (US-209) on the synthetic shop")
GRAPH = prov("account graph graph-0.1.0 (spec 8.3) of the synthetic shop")
THRESH = prov("matrix thresholds chosen on the validation months of the synthetic shop")
SIM = prov(
    "economic simulator, unit costs of assumptions-v1 (spec 4 and 8.10)",
    NEXT_START,
    NEXT_END,
    "simulated",
)
EVAL = prov(
    "offline evaluation report eval-2026-09-30 on synthetic labels, test months",
    date(2026, 7, 1),
    DATA_END,
)
ANOMALIES = prov("product anomalies anomalies-0.1.0 (spec 8.9) of the synthetic shop")
ILLUSTRATIVE = prov(
    "Illustrative mock for UI development, not measured",
    date(2026, 10, 13),
    date(2026, 10, 13),
    "simulated",
)


def eur(x: Decimal | str | float) -> float:
    return float(Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP))


def m(value: float | int | Decimal, unit: s.Unit, provenance: s.Provenance) -> s.Metric:
    if isinstance(value, Decimal):
        value = int(value) if unit == "count" else float(value)
    return s.Metric(value=value, unit=unit, provenance=provenance)


def at(day: date, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def revalidate(model: BaseModel, **updates: Any) -> Any:
    """A copy of `model` with `updates`, validated again (model_copy does not validate)."""
    return type(model).model_validate({**model.model_dump(), **updates})


def metric_or_none(value: float | None, unit: s.Unit, provenance: s.Provenance) -> s.Metric | None:
    return None if value is None else m(value, unit, provenance)


# ---- Accounts and links (spec 8.3) -------------------------------------------------------------

W_K = {"card": 1.0, "email": 0.9, "device": 0.8, "address": 0.6, "phone": 0.6, "ip_prefix": 0.2}
KIND_ORDER = ("card", "email", "device", "address", "phone", "ip_prefix")
TAU_DAYS = 180.0  # spec 8.3 (hypothesis)


@dataclass(frozen=True)
class Account:
    customer_id: str
    display_name: str
    created_at: datetime
    welcome_coupon_at: datetime | None
    orders_12m: int
    returns_12m: int
    identifiers: tuple[tuple[str, str], ...]  # (kind, value token), first use = created_at
    first_uses: dict[str, datetime] = field(default_factory=dict)  # token -> first use

    def first_use(self, token: str) -> datetime:
        return self.first_uses.get(token, self.created_at)


def build_links(
    accounts: Sequence[Account],
    sharers: dict[str, int] | None = None,
    disputes: dict[str, s.LinkDisputeInfo] | None = None,
    explained: dict[str, str] | None = None,
) -> list[s.AccountLink]:
    """Links of spec 8.3 between accounts sharing identifier values. `sharers` gives the number
    of accounts of the whole shop using a value (a pickup point); by default the accounts here."""
    sharers, disputes, explained = sharers or {}, disputes or {}, explained or {}
    links = []
    for i, a in enumerate(accounts):
        for b in accounts[i + 1 :]:
            shared = sorted(set(a.identifiers) & set(b.identifiers))
            if not shared:
                continue
            ca, cb = sorted((a.customer_id, b.customer_id))
            lid = s.link_id(ca, cb)
            kinds = sorted({k for k, _ in shared}, key=KIND_ORDER.index)
            at_by_value = {t: max(a.first_use(t), b.first_use(t)) for _, t in shared}
            first, last = min(at_by_value.values()), max(at_by_value.values())
            d_by_value = {
                t: sharers.get(t, sum((k, t) in x.identifiers for x in accounts)) for k, t in shared
            }
            weight = 1 - math.prod(1 - W_K[k] for k in kinds)
            decayed_by_kind: dict[str, float] = {}
            for k, t in shared:
                days = (AS_OF - at_by_value[t]).total_seconds() / 86400
                w = W_K[k] * math.exp(-days / TAU_DAYS) / math.log2(max(d_by_value[t], 2))
                decayed_by_kind[k] = max(decayed_by_kind.get(k, 0.0), w)
            decayed = 1 - math.prod(1 - w for w in decayed_by_kind.values())
            dispute = disputes.get(lid)
            links.append(
                s.AccountLink(
                    link_id=lid,
                    customer_a=ca,
                    customer_b=cb,
                    kinds=cast(list[s.IdentifierKind], kinds),
                    weight=round(weight, 4),
                    weight_decayed=round(decayed, 4),
                    first_shared_at=first,
                    last_shared_at=last,
                    n_shared_events=len(shared),
                    max_sharers=max(d_by_value.values()),
                    disputed=dispute is not None,
                    dispute=dispute,
                    explained_by_evidence_id=explained.get(lid),
                )
            )
    return links


def account_nodes(accounts: Sequence[Account], case_customer: str) -> list[s.AccountNode]:
    return [
        s.AccountNode(
            customer_id=a.customer_id,
            display_name=a.display_name,
            created_at=a.created_at,
            is_case_customer=a.customer_id == case_customer,
            welcome_coupon_redeemed_at=a.welcome_coupon_at,
            orders_12m=a.orders_12m,
            returns_12m=a.returns_12m,
        )
        for a in accounts
    ]


def creation_spread_days(accounts: Sequence[Account]) -> float:
    times = [a.created_at for a in accounts]
    return round((max(times) - min(times)).total_seconds() / 86400, 1)


def ring_accounts() -> list[Account]:
    offsets = (0, 1, 2, 4, 5, 7, 9)  # created within 9 days
    names = (
        "Marco Bianchi",
        "M. Bianchi",
        "Marco B.",
        "Marc Bianchi",
        "Mario Bianchi",
        "Marco Bianco",
        "M. Bianco",
    )
    cards = ("card:K1", "card:K2", "card:K1", "card:K2", "card:K2", "card:K1", "card:K2")
    out = []
    for i, (offset, name, card) in enumerate(zip(offsets, names, cards, strict=True), start=1):
        created = at(date(2026, 9, 1), 10) + timedelta(days=offset)
        ids = [("device", "dev:R1"), ("card", card)]
        if i <= 5:
            ids.append(("address", "addr:R1"))
        if i in (2, 7):
            ids.append(("email", "email:R1"))
        if i in (4, 5):
            ids.append(("phone", "phone:R1"))
        if i in (1, 7):
            ids.append(("ip_prefix", "ip:R1"))
        orders, returns = (4, 6) if i == 1 else (2, 2 + i % 2)
        out.append(
            Account(
                customer_id=f"C-MARCO-{i}",
                display_name=name,
                created_at=created,
                welcome_coupon_at=created + timedelta(days=1, hours=2),
                orders_12m=orders,
                returns_12m=returns,
                identifiers=tuple(ids),
            )
        )
    return out


def rossi_accounts(card_shared: bool) -> list[Account]:
    prefix = "C-ROSSI-CARD" if card_shared else "C-ROSSI"
    created = (
        (at(date(2024, 5, 20)), at(date(2025, 2, 11)), at(date(2025, 11, 28)))
        if card_shared
        else (at(date(2024, 11, 3)), at(date(2025, 6, 12)), at(date(2025, 12, 8)))
    )
    names = ("Giorgio Rossi", "Paola Rossi", "Sofia Rossi")
    family = "F2" if card_shared else "F1"
    out = []
    for i, (name, when) in enumerate(zip(names, created, strict=True), start=1):
        card = f"card:{family}-1" if card_shared and i <= 2 else f"card:{family}-{i}"
        out.append(
            Account(
                customer_id=f"{prefix}-{i}",
                display_name=name,
                created_at=when,
                welcome_coupon_at=when + timedelta(days=3) if i == 2 else None,
                orders_12m=(3, 4, 2)[i - 1],
                returns_12m=(1, 1 if not card_shared else 2, 0)[i - 1],
                identifiers=(
                    ("address", f"addr:{family}"),
                    ("device", f"dev:{family}"),
                    ("card", card),
                ),
            )
        )
    return out


def esposito_accounts() -> list[Account]:
    chiara = Account(
        customer_id="C-10233",
        display_name="Chiara Esposito",
        created_at=at(date(2025, 3, 2)),
        welcome_coupon_at=at(date(2025, 10, 5), 11),
        orders_12m=5,
        returns_12m=2,
        identifiers=(("card", "card:X1"), ("address", "addr:PICKUP-17")),
        first_uses={"card:X1": at(date(2026, 8, 30), 15), "addr:PICKUP-17": at(date(2025, 9, 2))},
    )
    return [
        chiara,
        Account(
            customer_id="C-20871",
            display_name="Luisa Ferrante",
            created_at=at(date(2026, 2, 11)),
            welcome_coupon_at=at(date(2026, 2, 12)),
            orders_12m=6,
            returns_12m=5,
            identifiers=(
                ("card", "card:X1"),
                ("device", "dev:W1"),
                ("email", "email:W1"),
                ("address", "addr:PICKUP-17"),
            ),
        ),
        Account(
            customer_id="C-20872",
            display_name="L. Ferrante",
            created_at=at(date(2026, 2, 14)),
            welcome_coupon_at=at(date(2026, 2, 15)),
            orders_12m=4,
            returns_12m=4,
            identifiers=(
                ("device", "dev:W1"),
                ("email", "email:W1"),
                ("address", "addr:PICKUP-17"),
            ),
        ),
    ]


PICKUP_SHARERS = {"addr:PICKUP-17": 41}  # a pickup point: a common node of spec 8.3


# ---- Orders and customer value (spec 8.5) -------------------------------------------------------


@dataclass(frozen=True)
class Line:
    variant_id: str
    category: str
    size: str | None
    price: str
    return_reason: ReturnReason | None = None
    return_id: str | None = None


@dataclass(frozen=True)
class Order:
    order_id: str
    placed: date
    lines: tuple[Line, ...]
    welcome_coupon: str | None = None  # 15% on the first order (spec 4)

    @property
    def discount(self) -> Decimal:
        if self.welcome_coupon is None:
            return Decimal(0)
        gross = sum((Decimal(x.price) for x in self.lines), Decimal(0))
        return (gross * Decimal("0.15")).quantize(CENT, rounding=ROUND_HALF_UP)


def order_view(o: Order) -> s.OrderView:
    gross = sum((Decimal(x.price) for x in o.lines), Decimal(0))
    return s.OrderView(
        order_id=o.order_id,
        placed_at=at(o.placed, 20, 15),
        delivered_on=o.placed + timedelta(days=4),
        total_eur=eur(gross - o.discount),
        coupon_code=o.welcome_coupon,
        coupon_kind="welcome" if o.welcome_coupon else None,
        terms_snapshot_id=o.order_id.replace("ORD-", "TS-"),
        lines=[
            s.OrderLineView(
                line_id=f"{o.order_id.replace('ORD-', 'L-')}-{n}",
                variant_id=x.variant_id,
                category=x.category,
                size=x.size,
                price_eur=eur(x.price),
                returned=x.return_reason is not None,
                return_id=x.return_id,
                return_reason=x.return_reason,
            )
            for n, x in enumerate(o.lines, start=1)
        ],
    )


def order_history(orders: Sequence[Order]) -> s.OrderHistory:
    lines = [x for o in orders for x in o.lines]
    returned = sum(x.return_reason is not None for x in lines)
    return s.OrderHistory(
        orders=[order_view(o) for o in orders],
        lines=m(len(lines), "count", SHOP),
        returned_lines=m(returned, "count", SHOP),
        return_rate=m(round(returned / len(lines), 4), "ratio", SHOP),
        provenance=SHOP,
    )


def value_parts(orders: Sequence[Order]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Kept margin, return logistics, coupon discounts and value (spec 8.5)."""
    lines = [x for o in orders for x in o.lines]
    kept = sum((Decimal(x.price) for x in lines if x.return_reason is None), Decimal(0))
    margin = (MARGIN * kept).quantize(CENT, rounding=ROUND_HALF_UP)
    logistics = LOGISTICS * sum(x.return_reason is not None for x in lines)
    coupons = sum((o.discount for o in orders), Decimal(0))
    return margin, logistics, coupons, margin - logistics - coupons


def value_view(orders: Sequence[Order]) -> s.ValueView:
    margin, logistics, coupons, value = value_parts(orders)
    return s.ValueView(
        value_eur=m(eur(value), "eur", VALUE),
        threshold=m(eur(THRESHOLDS.value_eur), "eur", THRESH),
        band="high" if value >= THRESHOLDS.value_eur else "low",
        kept_margin_eur=m(eur(margin), "eur", VALUE),
        return_logistics_eur=m(eur(logistics), "eur", VALUE),
        coupon_discounts_eur=m(eur(coupons), "eur", VALUE),
    )


SIZE_ORDERS_GIULIA = (
    Order(
        "ORD-001204",
        date(2025, 10, 12),
        (
            Line("V-DRS-1042", "Dresses", "40", "89.00"),
            Line("V-DRS-1042", "Dresses", "42", "89.00", ReturnReason.SIZE, "RET-000031"),
        ),
        "WELCOME-G7Q2",
    ),
    Order(
        "ORD-001877",
        date(2025, 12, 3),
        (
            Line("V-JNS-0311", "Jeans", "W27", "79.00"),
            Line("V-JNS-0311", "Jeans", "W28", "79.00", ReturnReason.SIZE, "RET-000038"),
        ),
    ),
    Order(
        "ORD-002650",
        date(2026, 2, 20),
        (
            Line("V-COA-0907", "Coats", "M", "189.00"),
            Line("V-COA-0907", "Coats", "L", "189.00", ReturnReason.SIZE, "RET-000047"),
        ),
    ),
    Order(
        "ORD-003412",
        date(2026, 5, 8),
        (
            Line("V-SHI-0520", "Shirts", "S", "45.00", ReturnReason.SIZE, "RET-000063"),
            Line("V-SHI-0520", "Shirts", "XS", "45.00"),
        ),
    ),
    Order(
        "ORD-004120",
        date(2026, 7, 15),
        (Line("V-SAN-0118", "Shoes", "38", "65.00", ReturnReason.NOT_AS_EXPECTED, "RET-000072"),),
    ),
    Order(
        "ORD-004811",
        date(2026, 9, 20),
        (
            Line("V-DRS-2210", "Dresses", "M", "119.00"),
            Line("V-DRS-2210", "Dresses", "L", "119.00", ReturnReason.SIZE, "RET-000101"),
        ),
    ),
)
ORDERS_MARCO = (
    Order(
        "ORD-004410",
        date(2026, 9, 2),
        (
            Line("V-JKT-2207", "Jackets", "M", "69.00", ReturnReason.CHANGED_MIND, "RET-000081"),
            Line("V-JKT-2207", "Jackets", "L", "69.00", ReturnReason.CHANGED_MIND, "RET-000082"),
            Line("V-TSH-0450", "T-shirts", "M", "68.00"),
        ),
        "WELCOME-M1A9",
    ),
    Order(
        "ORD-004702",
        date(2026, 9, 9),
        (
            Line("V-BAG-0133", "Bags", None, "129.00", ReturnReason.CHANGED_MIND, "RET-000086"),
            Line("V-TSH-0451", "T-shirts", "M", "39.00"),
        ),
    ),
    Order(
        "ORD-004988",
        date(2026, 9, 14),
        (
            Line("V-SNK-1102", "Shoes", "43", "59.00", ReturnReason.SIZE, "RET-000090"),
            Line("V-BLT-0210", "Accessories", None, "45.00"),
            Line("V-JNS-0377", "Jeans", "W32", "49.00", ReturnReason.CHANGED_MIND, "RET-000091"),
        ),
    ),
    Order(
        "ORD-005230",
        date(2026, 9, 18),
        (
            Line("V-COA-1180", "Coats", "L", "99.00", ReturnReason.CHANGED_MIND, "RET-000102"),
            Line("V-SCF-0040", "Accessories", None, "30.00"),
            Line("V-TSH-0452", "T-shirts", "L", "38.00"),
        ),
    ),
)
ORDERS_ROSSI = (
    Order(
        "ORD-001502",
        date(2025, 11, 20),
        (
            Line("V-TSH-0300", "T-shirts", "M", "49.00"),
            Line("V-TSH-0301", "T-shirts", "M", "49.00"),
        ),
        "WELCOME-P3R1",
    ),
    Order("ORD-002931", date(2026, 3, 2), (Line("V-SKR-0071", "Skirts", "40", "59.00"),)),
    Order(
        "ORD-005301",
        date(2026, 9, 15),
        (
            Line(
                "V-KNT-0620", "Knitwear", "M", "75.00", ReturnReason.NOT_AS_EXPECTED, "RET-000103"
            ),
            Line("V-SCF-0041", "Accessories", None, "35.00"),
        ),
    ),
)
ORDERS_ROSSI_CARD = (
    Order(
        "ORD-001388",
        date(2025, 10, 28),
        (
            Line("V-TSH-0310", "T-shirts", "S", "60.00"),
            Line("V-TSH-0311", "T-shirts", "S", "60.00"),
        ),
        "WELCOME-S8C4",
    ),
    Order(
        "ORD-002115",
        date(2026, 1, 14),
        (
            Line("V-DRS-0815", "Dresses", "38", "89.00"),
            Line("V-DRS-0815", "Dresses", "40", "89.00", ReturnReason.SIZE, "RET-000052"),
        ),
    ),
    Order("ORD-003301", date(2026, 4, 22), (Line("V-SKR-0090", "Skirts", "38", "45.00"),)),
    Order(
        "ORD-005344",
        date(2026, 9, 16),
        (
            Line("V-JNS-0390", "Jeans", "W26", "79.00", ReturnReason.SIZE, "RET-000104"),
            Line("V-TSH-0312", "T-shirts", "S", "39.00"),
        ),
    ),
)
ORDERS_ELENA = (
    Order(
        "ORD-001950",
        date(2026, 1, 10),
        (Line("V-KNT-0711", "Knitwear", "M", "129.00"),),
        "WELCOME-E2L5",
    ),
    Order(
        "ORD-003040",
        date(2026, 4, 3),
        (
            Line("V-TRS-0230", "Trousers", "40", "59.00"),
            Line("V-TRS-0230", "Trousers", "42", "59.00", ReturnReason.SIZE, "RET-000060"),
        ),
    ),
    Order(
        "ORD-005512",
        date(2026, 9, 14),
        (Line("V-JKT-DEF", "Jackets", "M", "149.00", ReturnReason.DEFECTIVE, "RET-000105"),),
    ),
)
ORDERS_ESPOSITO = (
    Order(
        "ORD-001101",
        date(2025, 10, 5),
        (Line("V-COA-0950", "Coats", "M", "120.00"), Line("V-KNT-0702", "Knitwear", "M", "90.00")),
        "WELCOME-C1E7",
    ),
    Order("ORD-001990", date(2025, 12, 18), (Line("V-JKT-1501", "Jackets", "M", "240.00"),)),
    Order(
        "ORD-002799",
        date(2026, 3, 9),
        (
            Line("V-DRS-1990", "Dresses", "40", "180.00"),
            Line(
                "V-BLT-0300", "Accessories", None, "60.00", ReturnReason.CHANGED_MIND, "RET-000044"
            ),
        ),
    ),
    Order("ORD-003870", date(2026, 6, 21), (Line("V-SAN-0160", "Shoes", "39", "150.00"),)),
    Order(
        "ORD-005402",
        date(2026, 9, 21),
        (
            Line("V-DRS-2301", "Dresses", "40", "210.00", ReturnReason.CHANGED_MIND, "RET-000106"),
            Line("V-SKR-0120", "Skirts", "40", "95.00"),
        ),
    ),
)

# ---- Decisions (policy.engine) -----------------------------------------------------------------


def base_snapshot(order: Order, customer_id: str) -> s.TermsSnapshotView:
    shown = at(order.placed, 20, 14)
    return s.TermsSnapshotView(
        terms_snapshot_id=order.order_id.replace("ORD-", "TS-"),
        order_id=order.order_id,
        customer_id=customer_id,
        source="base",
        quadrant_at_purchase=None,
        terms=s.Terms.from_policy(BASE),
        base_terms=s.Terms.from_policy(BASE),
        review_at=None,
        shown_at=shown,
        confirmed_at=shown + timedelta(minutes=1),
    )


@dataclass(frozen=True)
class Engine:
    request: ReturnRequest
    scores: CaseScores
    decision: Decision


def run_engine(
    return_id: str,
    order_id: str,
    customer_id: str,
    reason: ReturnReason,
    delivered_on: date,
    notice_on: date,
    risk: float | None,
    value: Decimal | None,
    evidence: EvidenceQuality = EvidenceQuality.SUFFICIENT,
    cluster_id: str | None = None,
) -> Engine:
    request = ReturnRequest(
        return_id=return_id,
        order_id=order_id,
        customer_id=customer_id,
        reason=reason,
        delivered_on=delivered_on,
        notice_on=notice_on,
        order_terms=BASE,
    )
    scores = CaseScores(
        risk=risk if risk is not None else 0.0,
        value_eur=value if value is not None else Decimal(0),
        evidence_quality=evidence,
        cluster_id=cluster_id,
    )
    decision = decide(request, scores, BASE, THRESHOLDS, notice_on, COUPON_RULE)
    return Engine(request, scores, decision)


_STATES: dict[DecisionState, DecisionStatus] = {
    DecisionState.PROPOSED: "proposed",
    DecisionState.APPROVED: "approved",
    DecisionState.REJECTED: "rejected",
}


def in_force(
    state: DecisionStatus,
    requires_approval: bool,
    review_at: date | None,
    as_of: datetime,
    superseded_by: str | None,
) -> bool:
    """The rule of DecisionView.in_force (US-607: a restriction lapses at its review date)."""
    lapsed = review_at is not None and s.utc_day(as_of) >= review_at
    standing = state in ("approved", "overridden") or (
        state == "proposed" and not requires_approval
    )
    return standing and not lapsed and superseded_by is None


INPUTS_ORIGIN = (
    "engine inputs: risk-xgb-0.1.0 and value-0.1.0 scores on decision-time features of the "
    "synthetic shop, matrix thresholds of the validation months"
)


def decision_view(
    engine: Engine,
    *,
    version: int,
    created_at: datetime,
    as_of: datetime = AS_OF,
    trigger: RevisionTrigger = "initial",
    supersedes: str | None = None,
    superseded_by: str | None = None,
    missing: Sequence[str] = (),
    generation_id: str | None = None,
    coupon_application_id: str | None = None,
    affected: Sequence[str] = (),
    approved_at: datetime | None = None,
    claim: s.ClaimView | None = None,
    **outcome: Any,
) -> s.DecisionView:
    """The view of the engine's decision. `outcome` replaces fields before the view is validated:
    a person's outcome that is not policy.engine's (an override, a lift; see acted_fields)."""
    d, req, sc = engine.decision, engine.request, engine.scores
    levers = s.levers_from_policy(d.levers, BASE)
    coupon_rule = None
    if d.coupon_rule is not None and "coupon_rule" not in outcome:
        assert d.coupon_rule.rule_version and sc.cluster_id and coupon_application_id
        coupon_rule = s.CouponRuleRef(
            rule_version=d.coupon_rule.rule_version,
            application_id=coupon_application_id,
            cluster_id=sc.cluster_id,
            affected_customer_ids=list(affected),
            confirmed_by=d.coupon_rule.confirmed_by,
        )
    scored = d.path is ReturnPath.WITHDRAWAL
    state = _STATES[d.state]
    fields: dict[str, Any] = dict(
        decision_id=f"DEC-{req.return_id.removeprefix('RET-')}-{version}",
        return_id=req.return_id,
        version=version,
        revision_trigger=trigger,
        supersedes=supersedes,
        superseded_by=superseded_by,
        created_at=created_at,
        as_of=as_of,
        decided_on=d.decided_on,
        state=state,
        path=d.path,
        evidence_quality=d.evidence_quality,
        quadrant=d.quadrant,
        missing_evidence=list(missing),
        proposed_levers=levers,
        levers=levers,
        lever_ids=d.levers.lever_ids(BASE),
        requires_approval=d.requires_approval,
        in_force=in_force(state, d.requires_approval, d.review_on, as_of, superseded_by),
        current_return_terms=s.Terms.from_policy(d.current_return_terms),
        future_terms=s.Terms.from_policy(d.levers.future_terms),
        refund_due_by=d.refund_due_by,
        review_at=d.review_on,
        approver=d.approver,
        approved_at=approved_at,
        override_reason=None,
        override_category=None,
        coupon_rule=coupon_rule,
        diminished_value_claim=claim,
        inputs=s.DecisionInputs(
            order_id=req.order_id,
            customer_id=req.customer_id,
            reason=req.reason,
            delivered_on=req.delivered_on,
            notice_on=req.notice_on,
            order_terms=s.Terms.from_policy(req.order_terms),
            base_terms=s.Terms.from_policy(BASE),
            risk=sc.risk if scored else None,
            value_eur=eur(sc.value_eur) if scored else None,
            evidence_quality=sc.evidence_quality,
            cluster_id=sc.cluster_id,
            thresholds=s.DecisionThresholds(
                risk=THRESHOLDS.risk, value_eur=eur(THRESHOLDS.value_eur)
            ),
            provenance=prov(INPUTS_ORIGIN, DATA_START, d.decided_on),  # data up to the decision
        ),
        rules_version=d.rules_version,
        model_versions=MODEL_VERSIONS,
        assumptions_version=ASSUMPTIONS_V1,
        generation_id=generation_id,
    )
    return s.DecisionView.model_validate({**fields, **outcome})


def acted_fields(
    decided_on: date,
    superseded_by: str | None,
    state: Literal["overridden", "rejected"],
    levers: Sequence[s.Lever],
    approver: str,
    when: datetime,
    reason: str,
    category: OverrideCategory,
) -> dict[str, Any]:
    """The fields of a view after a person overrode (with `levers`) or rejected (no lever) the
    proposal: the levers, the approval flag and the review date now describe that choice."""
    policy_levers = s.to_policy_levers(levers, BASE)
    restrictive = is_restrictive(policy_levers, BASE)
    review = decided_on + timedelta(days=90) if restrictive else None
    return {
        "state": state,
        "levers": [x.model_dump() for x in levers],
        "lever_ids": [x.lever_id() for x in levers],
        "requires_approval": restrictive,
        "review_at": review,
        "in_force": in_force(state, restrictive, review, when, superseded_by),
        "future_terms": s.Terms.from_policy(policy_levers.future_terms).model_dump(),
        "approver": approver,
        "approved_at": when,
        "as_of": when,
        "override_reason": reason,
        "override_category": category,
    }


def acted(
    view: s.DecisionView,
    state: Literal["overridden", "rejected"],
    levers: Sequence[s.Lever],
    approver: str,
    when: datetime,
    reason: str,
    category: OverrideCategory,
) -> s.DecisionView:
    """The view after a person overrode or rejected its proposal (acted_fields)."""
    fields = acted_fields(
        view.decided_on, view.superseded_by, state, levers, approver, when, reason, category
    )
    out: s.DecisionView = revalidate(view, **fields)
    return out


def application_of(view: s.DecisionView, as_of: datetime) -> s.CouponRuleApplication:
    """The cluster coupon application of a decision version, as served at `as_of`: proposed
    with the decision, confirmed by the person who approved it, with its review date."""
    rule = view.coupon_rule
    assert rule is not None and view.review_at is not None
    confirmed = rule.confirmed_by is not None
    return s.CouponRuleApplication(
        application_id=rule.application_id,
        rule_version=rule.rule_version,
        cluster_id=rule.cluster_id,
        decision_id=view.decision_id,
        affected_customer_ids=rule.affected_customer_ids,
        state="confirmed" if confirmed else "proposed",
        proposed_at=view.created_at,
        confirmed_by=rule.confirmed_by,
        confirmed_at=view.approved_at if confirmed else None,
        review_at=view.review_at,
        reversed_by=None,
        reversed_at=None,
        reversal_reason=None,
        as_of=as_of,
        in_force=confirmed and s.utc_day(as_of) < view.review_at,
    )


# ---- Net benefit (policy.simulator) -------------------------------------------------------------


def nb(
    label: str,
    period: str,
    provenance: s.Provenance = SIM,
    quantities: Mapping[str, int | str] | None = None,
    **values: int | str,
) -> s.NetBenefitView:
    """A net benefit on quantities of its own, labelled `label`, with the unit costs of
    assumptions-v1 (8 EUR per return, 12 EUR per undue discount, 22 EUR per hour)."""
    merged = {**(quantities or {}), **values}
    numbers = {k: v if isinstance(v, int) else float(Decimal(v)) for k, v in merged.items()}
    return s.NetBenefitView.build(V1.derive(label, period, **numbers), provenance)


VERIFICATION = {"system_cost_eur": "4.40"}  # 12 minutes of an operator at 22 EUR per hour


def alternative(
    kind: s.AlternativeKind,
    summary: str,
    decision_id: str,
    period: str,
    levers: Sequence[s.Lever] = (),
    recommended: bool = False,
    quantities: Mapping[str, int | str] | None = None,
    **values: int | str,
) -> s.Alternative:
    """An alternative of a decision version; its assumptions are labelled decision_id/kind."""
    return s.Alternative(
        kind=kind,
        summary=summary,
        recommended=recommended,
        levers=list(levers),
        net_benefit=nb(
            f"{decision_id}/{kind}", period, quantities={**(quantities or {}), **values}
        ),
    )


# ---- Drafts -------------------------------------------------------------------------------------


def generation(
    generation_id: str,
    created_at: datetime,
    path: GenerationPath = "llm",
    errors: Sequence[str] = (),
    latency_ms: int = 14,
) -> s.GenerationInfo:
    attempts = {"llm": 1, "llm_regenerated": 2, "template": 2}[path]
    return s.GenerationInfo(
        generation_id=generation_id,
        generation_path=path,
        llm_mode="mock",
        served_from="live",
        model="mock",
        attempts=attempts,
        validator_errors=list(errors),
        created_at=created_at,
        latency_ms=latency_ms,
    )


def statement(
    label: s.StatementLabel,
    text: str,
    entity: str | None = None,
    metric: str | None = None,
    value: str | None = None,
    period: str | None = None,
) -> s.ExplanationStatement:
    return s.ExplanationStatement(
        label=label, text=text, entity=entity, metric=metric, value=value, period=period
    )


def draft(
    decision: s.DecisionView,
    gen: s.GenerationInfo,
    statements: Sequence[s.ExplanationStatement],
    connective: dict[Lang, str],
    tone: Tone = "warm",
) -> s.DraftView:
    """A mock draft: the fields come from s.template_fields, the clause from s.required_clause."""
    kind = s.required_clause(decision)
    messages = []
    for lang, text in connective.items():
        fields = s.template_fields(decision, lang)
        messages.append(
            s.CustomerMessageView(
                lang=lang,
                connective_text=text,
                fields=fields,
                legal_clause_kind=kind,
                rendered_text=s.render_customer_message(text, fields.as_mapping(), lang, kind),
            )
        )
    return s.DraftView(
        generation=gen,
        tone=tone,
        decision_echo=s.DecisionEchoView(
            path=decision.path, quadrant=decision.quadrant, lever_ids=decision.lever_ids
        ),
        explanation=list(statements),
        messages=messages,
    )


def outbox_message(
    message_id: str,
    decision: s.DecisionView,
    lang: Lang,
    text: str,
    created_by: str,
    created_at: datetime,
    generation_id: str | None = None,
) -> s.OutboxMessage:
    """A message sent after a person acted: the text, the fields of the decision, the clause."""
    fields = s.template_fields(decision, lang)
    kind = s.required_clause(decision)
    subject = "Il tuo reso" if lang == "it" else "Your return"
    return s.OutboxMessage(
        message_id=message_id,
        return_id=decision.return_id,
        decision_id=decision.decision_id,
        customer_id=decision.inputs.customer_id,
        lang=lang,
        subject=f"{subject} {decision.return_id}",
        connective_text=text,
        fields=fields,
        body=s.render_customer_message(text, fields.as_mapping(), lang, kind),
        legal_clause_kind=kind,
        generation_id=generation_id,
        status="sent_simulated",
        created_by=created_by,
        created_at=created_at,
    )


PERIOD = "2025-10-01 to 2026-09-30"
TEXT_UPGRADE: dict[Lang, str] = {
    "it": "Ciao, grazie per averci scritto. Abbiamo registrato il reso e il rimborso arriverà "
    "entro il {deadline}. Per i tuoi prossimi ordini valgono queste condizioni: {terms}.",
    "en": "Hello, thank you for letting us know. We have registered your return and your refund "
    "will arrive by {deadline}. Your next orders come with these terms: {terms}.",
}
TEXT_RESTRICTED: dict[Lang, str] = {
    "it": "Ciao, abbiamo ricevuto la tua richiesta di reso e il rimborso arriverà entro il "
    "{deadline}. Dal prossimo ordine valgono queste condizioni: {terms}. Le condizioni "
    "dell'ordine attuale non cambiano.",
    "en": "Hello, we have received your return request and your refund will arrive by "
    "{deadline}. From your next order these terms apply: {terms}. The terms of your current "
    "order do not change.",
}
TEXT_REGISTERED: dict[Lang, str] = {
    "it": "Ciao, grazie per averci scritto. Abbiamo registrato il reso e il rimborso arriverà "
    "entro il {deadline}.",
    "en": "Hello, thank you for letting us know. We have registered your return and your refund "
    "will arrive by {deadline}.",
}
TEXT_WRONG_ITEM: dict[Lang, str] = {
    "it": "Ciao, ci dispiace per l'errore: hai ricevuto un articolo diverso da quello ordinato. "
    "Il reso è gratuito e il rimborso arriverà entro il {deadline}.",
    "en": "Hello, we are sorry for the mistake: you received a different item from the one you "
    "ordered. The return is free and your refund will arrive by {deadline}.",
}
TEXT_RENEWED: dict[Lang, str] = {
    "it": "Ciao, abbiamo rivisto le condizioni del tuo account. Dal prossimo ordine continuano a "
    "valere queste condizioni: {terms}. Le rivedremo di nuovo alla prossima data di riesame.",
    "en": "Hello, we have reviewed the terms of your account. From your next order these terms "
    "still apply: {terms}. We will review them again at the next review date.",
}
TEXT_LIFTED: dict[Lang, str] = {
    "it": "Ciao, abbiamo rivisto le condizioni del tuo account. Dal prossimo ordine valgono di "
    "nuovo le condizioni standard: {terms}.",
    "en": "Hello, we have reviewed the terms of your account. From your next order the standard "
    "terms apply again: {terms}.",
}


def evidence_view(
    observations: int, completeness: float, consistency: float, missing: Sequence[str] = ()
) -> s.EvidenceView:
    thresholds = {"observations": 4, "identifier_completeness": 0.5, "reason_consistency": 0.6}
    values: dict[str, float | int] = {
        "observations": observations,
        "identifier_completeness": completeness,
        "reason_consistency": consistency,
    }
    components = []
    for cid in s.EVIDENCE_COMPONENTS:
        unit: s.Unit = "count" if cid == "observations" else "ratio"
        components.append(
            s.EvidenceComponent(
                id=cid,
                value=m(values[cid], unit, EVIDENCE),
                threshold=m(thresholds[cid], unit, EVIDENCE),
                passed=values[cid] >= thresholds[cid],
            )
        )
    quality = (
        EvidenceQuality.SUFFICIENT
        if all(c.passed for c in components)
        else EvidenceQuality.INSUFFICIENT
    )
    return s.EvidenceView(quality=quality, components=components, missing_evidence=list(missing))


def factor(feature: str, value: Any, contribution: float, phrase: str) -> s.ScoreContribution:
    return s.ScoreContribution(
        feature=feature,
        feature_value=value,
        contribution=contribution,
        direction="raises" if contribution > 0 else "lowers",
        phrase=phrase,
    )


def risk_view(
    risk: float,
    factors: Sequence[s.ScoreContribution],
    residual: float,
    favourable: Sequence[s.EvidencePoint],
    unfavourable: Sequence[s.EvidencePoint],
) -> s.RiskView:
    return s.RiskView(
        score=m(risk, "probability", SCORES),
        threshold=m(THRESHOLDS.risk, "probability", THRESH),
        band="high" if risk >= THRESHOLDS.risk else "low",
        top_factors=list(factors),
        residual_z=m(residual, "z_score", SCORES),
        favourable=list(favourable),
        unfavourable=list(unfavourable),
        model_version=MODEL_VERSIONS["risk"],
        provenance=SCORES,
    )


def point(label: s.StatementLabel, text: str, *refs: tuple[s.EntityKind, str]) -> s.EvidencePoint:
    return s.EvidencePoint(
        label=label, statement=text, refs=[s.EntityRef(kind=k, id=i) for k, i in refs]
    )


def cluster_view(
    cluster_id: str,
    accounts: Sequence[Account],
    case_customer: str,
    links: Sequence[s.AccountLink],
    *,
    cards: int,
    welcome_per_account: float,
    return_rate: float,
    strong_share: float,
) -> s.LinkedAccountsView:
    return s.LinkedAccountsView(
        cluster_id=cluster_id,
        cluster_size=len(accounts),
        accounts=account_nodes(accounts, case_customer),
        links=list(links),
        features=s.ClusterFeatures(
            n_distinct_cards=m(cards, "count", GRAPH),
            creation_spread_days=m(creation_spread_days(accounts), "days", GRAPH),
            welcome_coupons_per_account=m(welcome_per_account, "ratio", GRAPH),
            cluster_return_rate=m(return_rate, "ratio", GRAPH),
            strong_id_share=m(strong_share, "ratio", GRAPH),
        ),
        as_of=AS_OF,
        provenance=GRAPH,
    )


def returned_item(order: Order, line_no: int, product: str, brand: str) -> s.ReturnedItem:
    line = order.lines[line_no - 1]
    return s.ReturnedItem(
        line_id=f"{order.order_id.replace('ORD-', 'L-')}-{line_no}",
        variant_id=line.variant_id,
        product_id=product,
        category=line.category,
        brand=brand,
        size=line.size,
        price_eur=eur(line.price),
    )


def request_view(
    order: Order, line_no: int, notice_on: date, hour: int, text: str | None
) -> s.ReturnRequestView:
    line = order.lines[line_no - 1]
    assert line.return_reason is not None
    delivered = order.placed + timedelta(days=4)
    return s.ReturnRequestView(
        reason=line.return_reason,
        customer_text=text,
        requested_at=at(notice_on, hour, 12),
        delivered_on=delivered,
        notice_on=notice_on,
        days_since_delivery=(notice_on - delivered).days,
    )


def revision(view: s.DecisionView) -> s.RevisionSummary:
    return s.RevisionSummary(
        decision_id=view.decision_id,
        version=view.version,
        trigger=view.revision_trigger,
        created_at=view.created_at,
        quadrant=view.quadrant,
        risk=metric_or_none(view.inputs.risk, "probability", SCORES),
        lever_ids=view.lever_ids,
        state=view.state,
    )


def kept_of(orders: Sequence[Order]) -> tuple[int, int]:
    lines = [x for o in orders for x in o.lines]
    return sum(x.return_reason is None for x in lines), len(lines)


# ---- The golden cases ---------------------------------------------------------------------------


@dataclass
class Case:
    key: str
    card: s.CaseCard
    engine: Engine
    queue_item: s.QueueItem


def queue_item(card: s.CaseCard) -> s.QueueItem:
    d = card.decision
    recommended = next(a for a in card.alternatives if a.recommended)
    linked = card.linked_accounts
    return s.QueueItem(
        return_id=card.return_id,
        order_id=card.order_id,
        customer_id=card.customer_id,
        display_name=card.display_name,
        requested_at=card.request.requested_at,
        reason=card.request.reason,
        path=card.path,
        evidence_quality=d.evidence_quality,
        quadrant=d.quadrant,
        decision_id=d.decision_id,
        state=d.state,
        requires_approval=d.requires_approval,
        lever_ids=d.lever_ids,
        review_at=d.review_at,
        review_due=(
            d.state in ("approved", "overridden")
            and d.review_at is not None
            and d.review_at <= AS_OF.date()
        ),
        risk=card.risk.score if card.risk else None,
        value=card.value.value_eur if card.value else None,
        expected_net_benefit=recommended.net_benefit.total,
        linked_account_count=linked.cluster_size - 1 if linked else 0,
        variant_anomaly=card.product_anomaly is not None,
        draft_status=card.draft_status,
    )


def _card(
    *,
    return_id: str,
    order: Order,
    line_no: int,
    customer_id: str,
    display_name: str,
    orders: Sequence[Order],
    product: str,
    brand: str,
    notice_on: date,
    hour: int,
    customer_text: str | None,
    evidence: s.EvidenceView,
    risk: s.RiskView | None,
    linked: s.LinkedAccountsView | None,
    decision: s.DecisionView,
    alternatives: Sequence[s.Alternative],
    draft_view: s.DraftView | None,
    anomaly: s.ProductAnomaly | None = None,
) -> s.CaseCard:
    return s.CaseCard(
        return_id=return_id,
        order_id=order.order_id,
        customer_id=customer_id,
        display_name=display_name,
        as_of=AS_OF,
        item=returned_item(order, line_no, product, brand),
        request=request_view(order, line_no, notice_on, hour, customer_text),
        path=decision.path,
        evidence=evidence,
        risk=risk,
        value=value_view(orders),
        linked_accounts=linked,
        orders=order_history(orders),
        order_terms=base_snapshot(order, customer_id),
        decision=decision,
        alternatives=list(alternatives),
        draft_status="ready" if draft_view else "pending",
        draft=draft_view,
        product_anomaly=anomaly,
        revisions=[revision(decision)],
        provenance=SHOP,
    )


def giulia() -> Case:
    orders, order = SIZE_ORDERS_GIULIA, SIZE_ORDERS_GIULIA[-1]
    notice = date(2026, 9, 27)
    value = value_parts(orders)[3]
    engine = run_engine(
        "RET-000101", order.order_id, "C-GIULIA", ReturnReason.SIZE,
        order.placed + timedelta(days=4), notice, 0.12, value,
    )  # fmt: skip
    created = at(notice, 10, 14)
    view = decision_view(engine, version=1, created_at=created, generation_id="GEN-000101-1")
    proposal = s.levers_from_policy(engine.decision.levers, BASE)
    period = "12 months after the decision, customer C-GIULIA"
    did = view.decision_id
    alternatives = [
        alternative(
            "proposal",
            "Q1 upgrade for future orders: 60-day window and priority size exchange",
            did, period, proposal, True,
            incremental_margin_eur="32.00", incentives_eur="6.00", system_cost_eur="0.50",
        ),
        alternative(
            "size_exchange",
            "Offer size M of the same dress instead of a refund",
            did, period,
            incremental_margin_eur="20.90", system_cost_eur="0.50", error_cost_eur="2.00",
        ),
        alternative("current_terms", "Keep the current terms", did, period),
    ]  # fmt: skip
    gen = generation("GEN-000101-1", created + timedelta(minutes=1))
    kept, lines = kept_of(orders)
    statements = [
        statement(
            "OBSERVED",
            f"C-GIULIA kept {kept} of {lines} items in 12 months; 5 of her 6 returns were for "
            "size.",
            "C-GIULIA", "kept_lines", f"{kept} of {lines}", PERIOD,
        ),
        statement(
            "SCORE",
            "Size returns lower the risk score to 0.12: a contribution to the score, not a cause.",
            "C-GIULIA", "risk", "0.12", PERIOD,
        ),
        statement(
            "OBSERVED",
            f"Customer value is {value:.2f} EUR, above the {THRESHOLDS.value_eur:.2f} EUR "
            "threshold: quadrant Q1.",
            "C-GIULIA", "value_eur", f"{value:.2f}", PERIOD,
        ),
    ]  # fmt: skip
    connective: dict[Lang, str] = {
        "it": "Ciao, grazie per averci scritto. Abbiamo registrato il reso per la taglia e il "
        "rimborso arriverà entro il {deadline}. Per i tuoi prossimi ordini valgono queste "
        "condizioni: {terms}.",
        "en": "Hello, thank you for letting us know. We have registered your size return and "
        "your refund will arrive by {deadline}. Your next orders come with these terms: {terms}.",
    }
    card = _card(
        return_id="RET-000101",
        order=order,
        line_no=2,
        customer_id="C-GIULIA",
        display_name="Giulia Conti",
        orders=orders,
        product="P-DRS-2210",
        brand="Navigli Studio",
        notice_on=notice,
        hour=10,
        customer_text="La L mi sta larga, tengo la M.",
        evidence=evidence_view(11, 0.83, 0.86),
        risk=risk_view(
            0.12,
            [
                factor("size_share", 0.8333, -0.48, "5 of 6 returns were for size"),
                factor("residual_z", 0.4, -0.21, "Returns close to what the products predict"),
                factor("cluster_size", 1, -0.12, "No linked accounts"),
            ],
            0.4,
            [
                point("OBSERVED", "Kept 5 of 11 items in 12 months", ("customer", "C-GIULIA")),
                point(
                    "OBSERVED",
                    "Returns are for size, with the other size kept",
                    ("order", "ORD-004811"),
                ),
            ],
            [point("OBSERVED", "Return rate 55% of lines in 12 months", ("customer", "C-GIULIA"))],
        ),
        linked=None,
        decision=view,
        alternatives=alternatives,
        draft_view=draft(view, gen, statements, connective),
    )
    return Case("giulia", card, engine, queue_item(card))


MARCO_CLUSTER = "CL-000417"
MARCO_APPLICATION = "CRA-000031"


def marco() -> Case:
    orders, order = ORDERS_MARCO, ORDERS_MARCO[-1]
    notice = date(2026, 9, 28)
    accounts = ring_accounts()
    value = value_parts(orders)[3]
    engine = run_engine(
        "RET-000102", order.order_id, "C-MARCO-1", ReturnReason.CHANGED_MIND,
        order.placed + timedelta(days=4), notice, 0.87, value, cluster_id=MARCO_CLUSTER,
    )  # fmt: skip
    created = at(notice, 9, 16)
    affected = [a.customer_id for a in accounts]
    view = decision_view(
        engine,
        version=1,
        created_at=created,
        generation_id="GEN-000102-1",
        coupon_application_id=MARCO_APPLICATION,
        affected=affected,
    )
    period = f"12 months after the decision, cluster {MARCO_CLUSTER} (7 linked accounts)"
    did = view.decision_id
    alternatives = [
        alternative(
            "proposal",
            "Q4 terms for future orders and coupons paused for the cluster, until the review",
            did, period, s.levers_from_policy(engine.decision.levers, BASE), True,
            logistics_returns_avoided=12, undue_discounts_avoided=4, minutes_saved=30,
            system_cost_eur="0.50", error_cost_eur="6.00", abandonment_margin_lost_eur="25.00",
        ),
        alternative(
            "verification",
            "Check the item at the warehouse before closing the return",
            did, period, quantities=VERIFICATION,
        ),
        alternative("current_terms", "Keep the current terms", did, period),
    ]  # fmt: skip
    links = build_links(accounts)
    gen = generation("GEN-000102-1", created + timedelta(minutes=1))
    statements = [
        statement(
            "OBSERVED",
            f"The 7 linked accounts of cluster {MARCO_CLUSTER} each redeemed a welcome coupon "
            "between 2026-09-02 and 2026-09-11.",
            MARCO_CLUSTER, "welcome_coupons", "7", "2026-09-02 to 2026-09-11",
        ),
        statement(
            "SCORE",
            "The welcome coupons of the linked accounts raise the risk score to 0.87: "
            "contributions to the score, not causes.",
            "C-MARCO-1", "risk", "0.87", PERIOD,
        ),
        statement(
            "HYPOTHESIS",
            "The linked accounts may be managed together: verify the shared cards before "
            "approving.",
            MARCO_CLUSTER,
        ),
    ]  # fmt: skip
    card = _card(
        return_id="RET-000102",
        order=order,
        line_no=1,
        customer_id="C-MARCO-1",
        display_name="Marco Bianchi",
        orders=orders,
        product="P-COA-1180",
        brand="Navigli Studio",
        notice_on=notice,
        hour=9,
        customer_text="Non mi piace più.",
        evidence=evidence_view(11, 1.0, 0.83),
        risk=risk_view(
            0.87,
            [
                factor(
                    "welcome_coupons_per_account",
                    1.0,
                    1.12,
                    "Each of the 7 linked accounts used a welcome coupon",
                ),
                factor(
                    "creation_spread_days",
                    9.0,
                    0.64,
                    "The 7 linked accounts were created within 9 days",
                ),
                factor(
                    "changed_mind_share", 0.8333, 0.41, "5 of 6 returns were for change of mind"
                ),
            ],  # fmt: skip
            1.9,
            [point("OBSERVED", "Kept 5 of 11 items in 12 months", ("customer", "C-MARCO-1"))],
            [
                point(
                    "OBSERVED",
                    "7 linked accounts created within 9 days, each with a welcome coupon",
                    ("cluster", MARCO_CLUSTER),
                ),
                point(
                    "OBSERVED",
                    "One device shared by the 7 linked accounts",
                    ("link", s.link_id("C-MARCO-1", "C-MARCO-2")),
                ),
                point("HYPOTHESIS", "The linked accounts may be managed together"),
            ],
        ),
        linked=cluster_view(
            MARCO_CLUSTER,
            accounts,
            "C-MARCO-1",
            links,
            cards=2,
            welcome_per_account=1.0,
            return_rate=0.79,
            strong_share=0.86,
        ),
        decision=view,
        alternatives=alternatives,
        draft_view=draft(view, gen, statements, TEXT_RESTRICTED),
    )
    return Case("marco", card, engine, queue_item(card))


def rossi(card_shared: bool) -> Case:
    orders = ORDERS_ROSSI_CARD if card_shared else ORDERS_ROSSI
    order = orders[-1]
    return_id = "RET-000104" if card_shared else "RET-000103"
    customer = "C-ROSSI-CARD-2" if card_shared else "C-ROSSI-2"
    cluster = "CL-000588" if card_shared else "CL-000233"
    notice = date(2026, 9, 26) if card_shared else date(2026, 9, 25)
    risk = 0.34 if card_shared else 0.18
    accounts = rossi_accounts(card_shared)
    line = order.lines[0]
    assert line.return_reason is not None
    engine = run_engine(
        return_id, order.order_id, customer, line.return_reason,
        order.placed + timedelta(days=4), notice, risk, value_parts(orders)[3],
        cluster_id=cluster,
    )  # fmt: skip
    number = return_id.removeprefix("RET-")
    created = at(notice, 11, 3)
    view = decision_view(engine, version=1, created_at=created, generation_id=f"GEN-{number}-1")
    period = f"12 months after the decision, customer {customer}"
    did = view.decision_id
    alternatives = [
        alternative(
            "proposal",
            "Standard terms: the linked accounts alone do not justify a change",
            did, period, recommended=True,
        ),
        alternative(
            "verification",
            "Check the item at the warehouse before closing the return",
            did, period, quantities=VERIFICATION,
        ),
    ]  # fmt: skip
    spread = creation_spread_days(accounts)
    shared = "an address, a device and a payment card" if card_shared else "an address and a device"
    if card_shared:
        factors = [
            factor(
                "strong_id_share", 0.5, 0.31, "A payment card is shared by 2 of 3 linked accounts"
            ),
            factor(
                "welcome_coupons_per_account", 0.3333, -0.35,
                "Only 1 of 3 linked accounts used a welcome coupon",
            ),
            factor(
                "creation_spread_days", spread, -0.27,
                "The linked accounts were created more than a year apart",
            ),
        ]  # fmt: skip
    else:
        factors = [
            factor(
                "n_distinct_cards", 3, -0.38, "3 distinct payment cards across the linked accounts"
            ),
            factor(
                "creation_spread_days", spread, -0.29,
                "The linked accounts were created more than a year apart",
            ),
            factor("cluster_size", 3, 0.12, f"3 linked accounts share {shared}"),
        ]  # fmt: skip
    gen = generation(f"GEN-{number}-1", created + timedelta(minutes=1))
    statements = [
        statement(
            "OBSERVED",
            f"3 linked accounts share {shared}; their return rate is in line with the shop.",
            cluster, "linked_accounts", "3", PERIOD,
        ),
        statement(
            "SCORE",
            f"The account features lower the risk score to {risk}: contributions, not causes.",
            customer, "risk", str(risk), PERIOD,
        ),
    ]  # fmt: skip
    links = build_links(accounts)
    card = _card(
        return_id=return_id,
        order=order,
        line_no=1,
        customer_id=customer,
        display_name="Paola Rossi",
        orders=orders,
        product="P-JNS-0390" if card_shared else "P-KNT-0620",
        brand="Navigli Denim" if card_shared else "Navigli Studio",
        notice_on=notice,
        hour=11,
        customer_text=None,
        evidence=evidence_view(len([x for o in orders for x in o.lines]), 1.0, 0.8),
        risk=risk_view(
            risk,
            factors,
            0.2 if card_shared else -0.1,
            [
                point(
                    "OBSERVED",
                    f"{'2' if card_shared else '3'} distinct payment cards in the cluster",
                    ("cluster", cluster),
                ),
                point(
                    "OBSERVED",
                    "Linked accounts created more than a year apart",
                    ("cluster", cluster),
                ),
                point("OBSERVED", "Welcome coupon used by 1 of 3 linked accounts"),
            ],
            [point("OBSERVED", f"3 linked accounts share {shared}", ("cluster", cluster))],
        ),
        linked=cluster_view(
            cluster,
            accounts,
            customer,
            links,
            cards=2 if card_shared else 3,
            welcome_per_account=0.3333,
            return_rate=0.21,
            strong_share=0.5 if card_shared else 0.0,
        ),
        decision=view,
        alternatives=alternatives,
        draft_view=draft(view, gen, statements, TEXT_REGISTERED),
    )
    return Case("rossi_card" if card_shared else "rossi", card, engine, queue_item(card))


def jacket_anomaly(task_id: str | None = "CT-000012") -> s.ProductAnomaly:
    week = date(2026, 9, 21)
    observed, expected = 11, 1.8
    trend = []
    series = ((38, 1), (41, 0), (44, 1), (40, 1), (43, 2), (45, 4), (46, 7), (46, 11))
    for i, (sales, returns) in enumerate(series):
        trend.append(
            s.WeeklyReturnRate(
                week=week - timedelta(weeks=len(series) - 1 - i),
                sales=sales,
                returns=returns,
                return_rate=round(returns / sales, 4),
            )
        )
    return s.ProductAnomaly(
        anomaly_id=s.anomaly_id("V-JKT-DEF", ReturnReason.DEFECTIVE, week),
        variant_id="V-JKT-DEF",
        product_id="P-JKT-0412",
        category="Jackets",
        brand="Navigli Studio",
        reason=ReturnReason.DEFECTIVE,
        week=week,
        observed=m(observed, "count", ANOMALIES),
        expected=m(expected, "expected_count", ANOMALIES),
        z=m(round((observed - expected) / math.sqrt(expected), 2), "z_score", ANOMALIES),
        sales=m(46, "count", ANOMALIES),
        status="open" if task_id is None else "task_opened",
        catalog_task_id=task_id,
        return_ids=["RET-000097", "RET-000099", "RET-000105"],
        trend=trend,
    )


def elena() -> Case:
    orders, order = ORDERS_ELENA, ORDERS_ELENA[-1]
    notice = date(2026, 9, 26)
    engine = run_engine(
        "RET-000105", order.order_id, "C-ELENA", ReturnReason.DEFECTIVE,
        order.placed + timedelta(days=4), notice, None, None,
    )  # fmt: skip
    created = at(notice, 15, 41)
    view = decision_view(engine, version=1, created_at=created, generation_id="GEN-000105-1")
    period = "12 months after the decision, variant V-JKT-DEF"
    did = view.decision_id
    alternatives = [
        alternative(
            "product_report",
            "Guarantee return, free of charge, and a catalogue task on the variant",
            did, period, recommended=True,
            logistics_returns_avoided=9, minutes_saved=20, system_cost_eur="2.00",
        ),
        alternative("current_terms", "Guarantee return without a catalogue task", did, period),
    ]  # fmt: skip
    gen = generation(
        "GEN-000105-1",
        created + timedelta(minutes=1),
        "llm_regenerated",
        ["UNGROUNDED_NUMBER: ['12']"],
        latency_ms=31,
    )
    statements = [
        statement(
            "OBSERVED",
            "Variant V-JKT-DEF had 11 returns for defect in the week of 2026-09-21, against 1.8 "
            "expected from its category.",
            "V-JKT-DEF", "returns_defective", "11", "week of 2026-09-21",
        ),
        statement(
            "HYPOTHESIS",
            "The sleeves of one production batch may be shorter than the size guide: the "
            "catalogue task checks it.",
            "V-JKT-DEF",
        ),
    ]  # fmt: skip
    connective: dict[Lang, str] = {
        "it": "Ciao, ci dispiace per il problema con la giacca. Il reso è gratuito e il rimborso "
        "arriverà entro il {deadline}. Abbiamo segnalato il capo al nostro team catalogo.",
        "en": "Hello, we are sorry about the problem with the jacket. The return is free and "
        "your refund will arrive by {deadline}. We have reported the item to our catalogue team.",
    }
    card = _card(
        return_id="RET-000105",
        order=order,
        line_no=1,
        customer_id="C-ELENA",
        display_name="Elena Marchetti",
        orders=orders,
        product="P-JKT-0412",
        brand="Navigli Studio",
        notice_on=notice,
        hour=15,
        customer_text="Le maniche sono più corte di quanto indicato nella guida alle misure.",
        evidence=evidence_view(4, 0.67, 1.0),
        risk=None,
        linked=None,
        decision=view,
        alternatives=alternatives,
        draft_view=draft(view, gen, statements, connective),
        anomaly=jacket_anomaly(),
    )
    return Case("elena", card, engine, queue_item(card))


ESPOSITO_CLUSTER = "CL-000731"
ESPOSITO_LINK = s.link_id("C-10233", "C-20871")
ESPOSITO_FACTORS = (
    ("strong_id_share", 0.5, 0.52, "A payment card is shared with a linked account"),
    ("cluster_return_rate", 0.71, 0.33, "The linked accounts returned 71% of delivered lines"),
    ("residual_z", 0.6, 0.08, "Returns slightly above what the products predict"),
)


def esposito_engine(after_dispute: bool) -> Engine:
    order = ORDERS_ESPOSITO[-1]
    return run_engine(
        "RET-000106", order.order_id, "C-10233", ReturnReason.CHANGED_MIND,
        order.placed + timedelta(days=4), date(2026, 9, 29),
        0.41 if after_dispute else 0.66, value_parts(ORDERS_ESPOSITO)[3],
        cluster_id=None if after_dispute else ESPOSITO_CLUSTER,
    )  # fmt: skip


def chiara() -> Case:
    """Chiara Esposito: Q3 through a card shared once with a linked account (US-310 dispute)."""
    orders, order = ORDERS_ESPOSITO, ORDERS_ESPOSITO[-1]
    engine = esposito_engine(after_dispute=False)
    created = at(date(2026, 9, 29), 12, 2)
    view = decision_view(engine, version=1, created_at=created, generation_id="GEN-000106-1")
    period = "12 months after the decision, customer C-10233"
    did = view.decision_id
    alternatives = [
        alternative(
            "proposal", "Q3 terms for future orders, until the review", did, period,
            s.levers_from_policy(engine.decision.levers, BASE), True,
            logistics_returns_avoided=2, system_cost_eur="0.50", abandonment_margin_lost_eur="6.00",
        ),
        alternative("verification", "Check the item at the warehouse", did, period,
                    quantities=VERIFICATION),
        alternative("current_terms", "Keep the current terms", did, period),
    ]  # fmt: skip
    accounts = esposito_accounts()
    links = build_links(accounts, PICKUP_SHARERS)
    gen = generation("GEN-000106-1", created + timedelta(minutes=1))
    statements = [
        statement(
            "OBSERVED",
            "C-10233 shares a payment card with linked account C-20871 since 2026-08-30.",
            ESPOSITO_LINK, "card_shared_since", "2026-08-30", PERIOD,
        ),
        statement(
            "SCORE",
            "The shared card raises the risk score to 0.66: contributions to the score, not "
            "causes.",
            "C-10233", "risk", "0.66", PERIOD,
        ),
        statement(
            "HYPOTHESIS",
            "The card may have been used once for a gift: ask the customer before approving.",
            ESPOSITO_LINK,
        ),
    ]  # fmt: skip
    kept, lines = kept_of(orders)
    card = _card(
        return_id="RET-000106",
        order=order,
        line_no=1,
        customer_id="C-10233",
        display_name="Chiara Esposito",
        orders=orders,
        product="P-DRS-2301",
        brand="Navigli Studio",
        notice_on=date(2026, 9, 29),
        hour=11,
        customer_text="Il colore è diverso da come lo immaginavo.",
        evidence=evidence_view(lines, 1.0, 0.75),
        risk=risk_view(
            0.66,
            [factor(*f) for f in ESPOSITO_FACTORS],
            0.6,
            [
                point(
                    "OBSERVED",
                    f"Kept {kept} of {lines} items in 12 months",
                    ("customer", "C-10233"),
                )
            ],
            [
                point(
                    "OBSERVED",
                    "A payment card shared with a linked account since 2026-08-30",
                    ("link", ESPOSITO_LINK),
                )
            ],
        ),  # fmt: skip
        linked=cluster_view(
            ESPOSITO_CLUSTER,
            accounts,
            "C-10233",
            links,
            cards=1,
            welcome_per_account=1.0,
            return_rate=0.71,
            strong_share=0.5,
        ),
        decision=view,
        alternatives=alternatives,
        draft_view=draft(view, gen, statements, TEXT_RESTRICTED),
    )
    return Case("chiara", card, engine, queue_item(card))


# ---- The other queued returns (one card each) ----------------------------------------------------


@dataclass(frozen=True)
class Simple:
    """A queued return with a plain card: no linked accounts, three score factors."""

    key: str
    return_id: str
    customer_id: str
    name: str
    orders: tuple[Order, ...]
    line_no: int  # the returned line of the last order
    notice_on: date
    risk: float  # the customer's score (not given to the engine on the guarantee path)
    product: str
    brand: str
    customer_text: str | None
    factors: tuple[tuple[str, float | int, float, str], ...]
    proposal: str
    benefit: dict[str, int | str] = field(default_factory=dict)
    approved: tuple[str, datetime] | None = None  # (approver, when): a restriction approved


SIMPLE = (
    Simple(
        "andrea", "RET-000107", "C-11872", "Andrea Ferri",
        (
            Order("ORD-002204", date(2025, 11, 8),
                  (Line("V-SHI-0702", "Shirts", "L", "79.00"),
                   Line("V-TRS-0415", "Trousers", "50", "99.00")), "WELCOME-A4F2"),
            Order("ORD-003615", date(2026, 4, 2),
                  (Line("V-JKT-1820", "Jackets", "L", "229.00"),
                   Line("V-KNT-0830", "Knitwear", "L", "89.00"))),
            Order("ORD-005507", date(2026, 9, 19),
                  (Line("V-SHI-0705", "Shirts", "L", "69.00", ReturnReason.SIZE, "RET-000107"),
                   Line("V-SHI-0705", "Shirts", "XL", "69.00"))),
        ),
        1, date(2026, 9, 28), 0.22, "P-SHI-0705", "Navigli Studio", "Mi sta stretta, tengo la XL.",
        (("size_share", 1.0, -0.42, "The only return was for size, with the other size kept"),
         ("residual_z", -0.3, -0.18, "Returns below what the products predict"),
         ("tenure_days", 326, -0.09, "Customer for almost a year")),
        "Q1 upgrade for future orders: 60-day window and priority size exchange",
        {"incremental_margin_eur": "18.00", "incentives_eur": "4.00", "system_cost_eur": "0.50"},
    ),
    Simple(
        "martina", "RET-000108", "C-17305", "Martina Gallo",
        (
            Order("ORD-001730", date(2025, 11, 25),
                  (Line("V-DRS-1610", "Dresses", "40", "159.00"),
                   Line("V-BAG-0210", "Bags", None, "119.00")), "WELCOME-M5G1"),
            Order("ORD-003088", date(2026, 4, 10),
                  (Line("V-COA-1020", "Coats", "40", "249.00", ReturnReason.CHANGED_MIND,
                        "RET-000066"),
                   Line("V-KNT-0910", "Knitwear", "40", "99.00"))),
            Order("ORD-004390", date(2026, 7, 14),
                  (Line("V-DRS-1955", "Dresses", "40", "189.00", ReturnReason.CHANGED_MIND,
                        "RET-000079"),
                   Line("V-SAN-0290", "Shoes", "38", "129.00"))),
            Order("ORD-005508", date(2026, 9, 20),
                  (Line("V-DRS-2380", "Dresses", "40", "199.00", ReturnReason.CHANGED_MIND,
                        "RET-000108"),
                   Line("V-BLT-0330", "Accessories", None, "49.00"))),
        ),
        1, date(2026, 9, 29), 0.71, "P-DRS-2380", "Navigli Studio", "Non è come pensavo.",
        (("changed_mind_share", 1.0, 0.58, "3 of 3 returns were for change of mind"),
         ("return_value_share", 0.54, 0.37, "The returned items are the most expensive ones"),
         ("residual_z", 1.4, 0.21, "Returns above what the products predict")),
        "Q3 terms for future orders, until the review",
        {"logistics_returns_avoided": 3, "abandonment_margin_lost_eur": "8.00",
         "system_cost_eur": "0.50", "error_cost_eur": "3.00"},
    ),
    Simple(
        "valentina", "RET-000110", "C-14502", "Valentina Greco",
        (
            Order("ORD-003377", date(2026, 4, 18),
                  (Line("V-TSH-0520", "T-shirts", "S", "29.90"),
                   Line("V-SKR-0140", "Skirts", "38", "59.90")), "WELCOME-V8G3"),
            Order("ORD-005510", date(2026, 9, 17),
                  (Line("V-BLS-0412", "Blouses", "S", "89.90", ReturnReason.CHANGED_MIND,
                        "RET-000110"),
                   Line("V-TSH-0521", "T-shirts", "S", "29.90"))),
        ),
        1, date(2026, 9, 26), 0.31, "P-BLS-0412", "Navigli Studio", None,
        (("tenure_days", 165, 0.14, "Customer for less than six months"),
         ("changed_mind_share", 1.0, 0.11, "The only return was for change of mind"),
         ("residual_z", 0.2, -0.05, "Returns close to what the products predict")),
        "Standard terms",
    ),
    Simple(
        "simone", "RET-000111", "C-16634", "Simone Rizzo",
        (
            Order("ORD-002480", date(2026, 1, 30),
                  (Line("V-JNS-0455", "Jeans", "W33", "89.00"),
                   Line("V-SHI-0611", "Shirts", "L", "59.00"),
                   Line("V-TSH-0612", "T-shirts", "L", "25.00")), "WELCOME-S2R6"),
            Order("ORD-005511", date(2026, 9, 18),
                  (Line("V-SNK-1340", "Shoes", "44", "119.00", ReturnReason.CHANGED_MIND,
                        "RET-000111"),)),
        ),
        1, date(2026, 9, 27), 0.28, "P-SNK-1340", "Navigli Sport", "Scomode, le restituisco.",
        (("changed_mind_share", 1.0, 0.12, "The only return was for change of mind"),
         ("tenure_days", 243, -0.08, "Customer for eight months"),
         ("residual_z", 0.1, -0.03, "Returns close to what the products predict")),
        "Standard terms",
    ),
    Simple(
        "francesca", "RET-000112", "C-18120", "Francesca Moretti",
        (
            Order("ORD-003902", date(2026, 5, 5),
                  (Line("V-DRS-1702", "Dresses", "42", "139.00"),
                   Line("V-SCF-0090", "Accessories", None, "29.00")), "WELCOME-F3M8"),
            Order("ORD-005512", date(2026, 9, 21),
                  (Line("V-TRS-0520", "Trousers", "42", "79.00", ReturnReason.WRONG_ITEM,
                        "RET-000112"),
                   Line("V-TSH-0601", "T-shirts", "M", "35.00"))),
        ),
        1, date(2026, 9, 30), 0.12, "P-TRS-0520", "Navigli Studio",
        "Ho ricevuto un pantalone diverso da quello ordinato.",
        (), "Guarantee return, free of charge, and the right item sent again",
        {"minutes_saved": 5, "system_cost_eur": "0.50"},
    ),
    Simple(
        "lorenzo", "RET-000088", "C-15077", "Lorenzo Ricci",
        (
            Order("ORD-001620", date(2025, 11, 14),
                  (Line("V-COA-0990", "Coats", "50", "259.00"),
                   Line("V-KNT-0750", "Knitwear", "50", "99.00")), "WELCOME-L6R4"),
            Order("ORD-002990", date(2026, 3, 28),
                  (Line("V-JKT-1610", "Jackets", "50", "199.00", ReturnReason.CHANGED_MIND,
                        "RET-000057"),
                   Line("V-SHI-0810", "Shirts", "L", "69.00"))),
            Order("ORD-004015", date(2026, 6, 20),
                  (Line("V-SNK-1210", "Shoes", "43", "139.00", ReturnReason.CHANGED_MIND,
                        "RET-000088"),
                   Line("V-TSH-0710", "T-shirts", "L", "39.00"),
                   Line("V-BLT-0410", "Accessories", None, "59.00"))),
        ),
        1, date(2026, 6, 29), 0.68, "P-SNK-1210", "Navigli Sport", "Non mi convincono.",
        (("changed_mind_share", 1.0, 0.49, "2 of 2 returns were for change of mind"),
         ("return_value_share", 0.48, 0.31, "The returned items are the most expensive ones"),
         ("residual_z", 1.1, 0.17, "Returns above what the products predict")),
        "Q3 terms for future orders, until the review",
        {"logistics_returns_avoided": 2, "system_cost_eur": "0.50", "error_cost_eur": "2.00"},
        ("luca", at(date(2026, 6, 30), 10)),
    ),
)  # fmt: skip
SIMPLE_BY_KEY = {c.key: c for c in SIMPLE}


def simple_engine(c: Simple) -> Engine:
    order = c.orders[-1]
    line = order.lines[c.line_no - 1]
    assert line.return_reason is not None
    engine = run_engine(
        c.return_id, order.order_id, c.customer_id, line.return_reason,
        order.placed + timedelta(days=4), c.notice_on, c.risk, value_parts(c.orders)[3],
    )  # fmt: skip
    if c.approved is not None:
        engine = replace(
            engine, decision=approve(engine.decision, engine.request, BASE, c.approved[0])
        )
    return engine


def simple_view(c: Simple, claim: s.ClaimView | None = None) -> s.DecisionView:
    engine = simple_engine(c)
    number = c.return_id.removeprefix("RET-")
    return decision_view(
        engine,
        version=1,
        created_at=at(c.notice_on, 13, 5),
        generation_id=f"GEN-{number}-1",
        approved_at=c.approved[1] if c.approved else None,
        claim=claim,
    )


def simple_alternatives(c: Simple, view: s.DecisionView) -> list[s.Alternative]:
    """The alternatives of a version of a plain case: the engine's proposal (recommended), a
    check at the warehouse and, when the proposal changes the terms, the current terms."""
    period = f"12 months after the decision, customer {c.customer_id}"
    did = view.decision_id
    proposal = view.proposed_levers
    alternatives = [
        alternative("proposal", c.proposal, did, period, proposal, True, c.benefit),
        alternative(
            "verification", "Check the item at the warehouse", did, period, quantities=VERIFICATION
        ),
    ]
    if proposal:
        alternatives.append(alternative("current_terms", "Keep the current terms", did, period))
    return alternatives


def simple_case(c: Simple) -> Case:
    engine = simple_engine(c)
    view = simple_view(c)
    order = c.orders[-1]
    guarantee = view.path is ReturnPath.GUARANTEE
    proposal = view.proposed_levers
    alternatives = simple_alternatives(c, view)
    kept, lines = kept_of(c.orders)
    returned = lines - kept
    statements = [
        statement(
            "OBSERVED",
            f"{c.customer_id} kept {kept} of {lines} items in 12 months.",
            c.customer_id, "kept_lines", f"{kept} of {lines}", PERIOD,
        ),
    ]  # fmt: skip
    if guarantee:
        statements.append(
            statement(
                "OBSERVED",
                "The item received is not the one ordered: the return follows the legal "
                "guarantee and is not scored.",
                c.return_id,
            )
        )
        connective = TEXT_WRONG_ITEM
    else:
        verb = "raise" if c.risk >= THRESHOLDS.risk else "lower"
        statements.append(
            statement(
                "SCORE",
                f"The account features {verb} the risk score to {c.risk}: contributions to the "
                "score, not causes.",
                c.customer_id, "risk", str(c.risk), PERIOD,
            )
        )  # fmt: skip
        if view.requires_approval:
            statements.append(
                statement(
                    "HYPOTHESIS",
                    "The returns for change of mind may have a reason the customer can explain: "
                    "ask before approving.",
                    c.customer_id,
                )
            )
            connective = TEXT_RESTRICTED
        else:
            connective = TEXT_UPGRADE if proposal else TEXT_REGISTERED
    number = c.return_id.removeprefix("RET-")
    gen = generation(f"GEN-{number}-1", view.created_at + timedelta(minutes=1))
    risk = None
    if not guarantee:
        risk = risk_view(
            c.risk,
            [factor(*f) for f in c.factors],
            next((float(v) for name, v, _, _ in c.factors if name == "residual_z"), 0.0),
            [point("OBSERVED", f"Kept {kept} of {lines} items in 12 months",
                   ("customer", c.customer_id))],
            [point("OBSERVED", f"{returned} returned items in 12 months",
                   ("customer", c.customer_id))],
        )  # fmt: skip
    card = _card(
        return_id=c.return_id,
        order=order,
        line_no=c.line_no,
        customer_id=c.customer_id,
        display_name=c.name,
        orders=c.orders,
        product=c.product,
        brand=c.brand,
        notice_on=c.notice_on,
        hour=12,
        customer_text=c.customer_text,
        evidence=evidence_view(lines, 1.0, 0.8),
        risk=risk,
        linked=None,
        decision=view,
        alternatives=alternatives,
        draft_view=draft(view, gen, statements, connective),
    )
    return Case(c.key, card, engine, queue_item(card))


MISSING_EVIDENCE = [
    "Fewer than 4 order lines observed for this customer",
    "One identifier kind on file: email only (no device, address or card)",
]
ORDERS_DAVIDE = (
    Order(
        "ORD-005509",
        date(2026, 9, 20),
        (
            Line("V-SHI-0601", "Shirts", "M", "49.00"),
            Line("V-TRS-0310", "Trousers", "44", "69.00", ReturnReason.NOT_AS_EXPECTED,
                 "RET-000109"),
        ),
    ),
)  # fmt: skip


def insufficient() -> Case:
    """US-307: too little evidence. Standard terms and the missing evidence, no quadrant."""
    orders, order = ORDERS_DAVIDE, ORDERS_DAVIDE[-1]
    notice = date(2026, 9, 29)
    engine = run_engine(
        "RET-000109", order.order_id, "C-19921", ReturnReason.NOT_AS_EXPECTED,
        order.placed + timedelta(days=4), notice, 0.4, value_parts(orders)[3],
        EvidenceQuality.INSUFFICIENT,
    )  # fmt: skip
    view = decision_view(
        engine,
        version=1,
        created_at=at(notice, 13, 5),
        missing=MISSING_EVIDENCE,
        generation_id="GEN-000109-1",
    )
    period = "12 months after the decision, customer C-19921"
    did = view.decision_id
    alternatives = [
        alternative("proposal", "Standard terms until there is enough evidence", did, period,
                    recommended=True),
        alternative("verification", "Check the item at the warehouse", did, period,
                    quantities=VERIFICATION),
    ]  # fmt: skip
    gen = generation("GEN-000109-1", view.created_at + timedelta(minutes=1))
    statements = [
        statement(
            "OBSERVED",
            "Only 2 order lines and one identifier kind are on file for C-19921: the evidence "
            "is insufficient, so the standard terms apply.",
            "C-19921", "observations", "2", PERIOD,
        ),
        statement(
            "HYPOTHESIS",
            "With more orders the case can be scored; until then no quadrant is assigned.",
            "C-19921",
        ),
    ]  # fmt: skip
    connective: dict[Lang, str] = {
        "it": "Ciao, abbiamo registrato il reso e il rimborso arriverà entro il {deadline}.",
        "en": "Hello, we have registered your return and your refund will arrive by {deadline}.",
    }
    card = _card(
        return_id="RET-000109",
        order=order,
        line_no=2,
        customer_id="C-19921",
        display_name="Davide Costa",
        orders=orders,
        product="P-TRS-0310",
        brand="Navigli Studio",
        notice_on=notice,
        hour=12,
        customer_text="Il tessuto è diverso dalla foto.",
        evidence=evidence_view(2, 0.33, 1.0, MISSING_EVIDENCE),
        risk=None,
        linked=None,
        decision=view,
        alternatives=alternatives,
        draft_view=draft(view, gen, statements, connective),
    )
    return Case("insufficient", card, engine, queue_item(card))


# ---- Builders per endpoint ---------------------------------------------------------------------

Files = dict[str, BaseModel | str]


def queue_page(items: Sequence[s.QueueItem]) -> s.QueueResponse:
    ordered = sorted(items, key=lambda x: (-x.expected_net_benefit.value, x.return_id))
    return s.QueueResponse(
        items=ordered,
        total=len(ordered),
        page=1,
        page_size=25,
        sort="-expected_net_benefit",
        as_of=AS_OF,
        provenance=GRAPH,
    )


def queue_examples(cases: Sequence[Case]) -> dict[str, BaseModel]:
    """The whole queue (one item per case card) and the queue filtered by each query example."""
    full = queue_page([c.queue_item for c in cases])
    approval = s.QueueQuery(state=["proposed"], quadrant=["Q3", "Q4"], requires_approval=True)
    review = s.QueueQuery(review_due=True)
    return {
        "queue_response": full,
        "queue_query": approval,
        "queue_response.approval": queue_page([i for i in full.items if approval.matches(i)]),
        "queue_query.review_due": review,
        "queue_response.review_due": queue_page([i for i in full.items if review.matches(i)]),
    }


def audit_entry(
    entry_id: int,
    when: datetime,
    actor: str,
    action: AuditAction,
    entity_type: AuditEntityType,
    entity_id: str,
    return_id: str | None,
    decision_id: str | None,
    reason: str | None,
    before: dict[str, Any] | s.DecisionAuditState | None = None,
    after: dict[str, Any] | s.DecisionAuditState | None = None,
) -> s.AuditEntry:
    decision_related = decision_id is not None
    return s.AuditEntry(
        entry_id=entry_id,
        at=when,
        actor=actor,
        actor_role=DEMO_USERS.get(actor, "system"),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        return_id=return_id,
        decision_id=decision_id,
        reason=reason,
        before=before,
        after=after,
        rules_version="policy-2026-10-12" if decision_related else None,
        model_versions=MODEL_VERSIONS if decision_related else None,
        assumptions_version=ASSUMPTIONS_V1 if decision_related else None,
        request_id=f"req-{entry_id:06d}",
    )


def decision_entry(
    entry_id: int,
    when: datetime,
    actor: str,
    action: AuditAction,
    view: s.DecisionView,
    reason: str | None = None,
    before: s.DecisionView | None = None,
) -> s.AuditEntry:
    """An audit entry about a decision version: typed before and after (US-604)."""
    return audit_entry(
        entry_id, when, actor, action, "decision", view.decision_id, view.return_id,
        view.decision_id, reason,
        s.DecisionAuditState.from_view(before) if before is not None else None,
        s.DecisionAuditState.from_view(view),
    )  # fmt: skip


def marco_approval(case: Case) -> tuple[s.DecisionRequest, s.DecisionResult, Decision]:
    approved_at = AS_OF + timedelta(minutes=20)
    approved = approve(case.engine.decision, case.engine.request, BASE, "sara")
    accounts = [a.customer_id for a in ring_accounts()]
    proposed = case.card.decision
    view = decision_view(
        replace(case.engine, decision=approved),
        version=1,
        created_at=proposed.created_at,
        as_of=approved_at,
        generation_id="GEN-000102-1",
        coupon_application_id=MARCO_APPLICATION,
        affected=accounts,
        approved_at=approved_at,
    )
    message = case.card.draft.messages[0] if case.card.draft else None
    assert message is not None
    request = s.DecisionRequest(
        decision_id=view.decision_id,
        action="approve",
        levers=view.proposed_levers,
        message=s.MessageChoice(lang="it", text=None),
        approver="sara",
    )
    result = s.DecisionResult(
        decision=view,
        audit_entry=decision_entry(
            3, approved_at, "sara", "decision_approved", view, before=proposed
        ),  # fmt: skip
        outbox_message=outbox_message(
            "MSG-000102-1",
            view,
            "it",
            message.connective_text,
            "sara",
            approved_at,
            "GEN-000102-1",
        ),  # fmt: skip
        coupon_application=application_of(view, approved_at),  # confirmed by the approval
    )
    return request, result, approved


def override_example(case: Case) -> tuple[s.DecisionRequest, s.DecisionResult]:
    """Sara overrides a Q3 proposal to the standard terms (US-306)."""
    view = case.card.decision
    when = AS_OF + timedelta(minutes=50)
    reason = "The customer explained two wrong deliveries by phone; restriction not justified"
    overridden = acted(view, "overridden", [], "sara", when, reason, "customer_context")
    text = TEXT_REGISTERED["it"]
    request = s.DecisionRequest(
        decision_id=view.decision_id,
        action="override",
        levers=[],
        message=s.MessageChoice(lang="it", text=text),
        override_reason=reason,
        override_category="customer_context",
        approver="sara",
    )
    result = s.DecisionResult(
        decision=overridden,
        audit_entry=decision_entry(
            11, when, "sara", "decision_overridden", overridden, reason, before=view
        ),  # fmt: skip
        outbox_message=outbox_message("MSG-000108-1", overridden, "it", text, "sara", when),
        coupon_application=None,
    )
    return request, result


def reject_example(case: Case) -> tuple[s.DecisionRequest, s.DecisionResult]:
    """Sara discards a Q1 upgrade: no lever applies and the order keeps its terms."""
    view = case.card.decision
    when = AS_OF + timedelta(minutes=55)
    reason = "The customer asked to keep the standard terms"
    rejected = acted(view, "rejected", [], "sara", when, reason, "customer_context")
    request = s.DecisionRequest(
        decision_id=view.decision_id,
        action="reject",
        message=s.MessageChoice(lang="en"),
        override_reason=reason,
        override_category="customer_context",
        approver="sara",
    )
    result = s.DecisionResult(
        decision=rejected,
        audit_entry=decision_entry(
            12, when, "sara", "decision_rejected", rejected, reason, before=view
        ),  # fmt: skip
        outbox_message=outbox_message(
            "MSG-000107-1", rejected, "en", TEXT_REGISTERED["en"], "sara", when
        ),
        coupon_application=None,
    )
    return request, result


def review_result(
    engine: Engine,
    before: s.DecisionView,
    action: Literal["lift", "renew"],
    person: str,
    when: datetime,
    *,
    entry_id: int,
    message_id: str,
    reason: str | None = None,
    category: OverrideCategory | None = None,
    lang: Lang = "it",
    application_id: str | None = None,
) -> tuple[s.DecisionRequest, s.DecisionResult]:
    """US-607: a person reviews the restriction `before` (approved or overridden) at `when`.

    Both outcomes create the next version (trigger review) from policy.engine.decide() on the
    latest scores at the day of the review (here the scores of `engine`). lift overrides it to
    the standard levers and reverses the coupon application of `before`, if any; renew approves
    the fresh restriction, with a new review date and, if its levers block the cluster's
    coupons, a new application (`application_id`) confirmed by the person."""
    fresh = decide(engine.request, engine.scores, BASE, THRESHOLDS, when.date(), COUPON_RULE)
    common: dict[str, Any] = {
        "version": before.version + 1,
        "created_at": when,
        "as_of": when,
        "trigger": "review",
        "supersedes": before.decision_id,
    }
    application: s.CouponRuleApplication | None = None
    if action == "lift":
        assert reason is not None and category is not None
        outcome = acted_fields(fresh.decided_on, None, "overridden", [], person, when, reason,
                               category)  # fmt: skip
        after = decision_view(
            replace(engine, decision=fresh), **{**common, "coupon_rule": None, **outcome}
        )
        if before.coupon_rule is not None:
            application = revalidate(
                application_of(before, when),
                state="reversed",
                reversed_by=person,
                reversed_at=when,
                reversal_reason=reason,
                in_force=False,
            )
        text = TEXT_LIFTED[lang]
    else:
        assert reason is None and category is None
        renewed = approve(fresh, engine.request, BASE, person)
        cluster = engine.scores.cluster_id
        after = decision_view(
            replace(engine, decision=renewed), **common, approved_at=when,
            coupon_application_id=application_id,
            affected=before.coupon_rule.affected_customer_ids if before.coupon_rule else (),
        )  # fmt: skip
        if after.coupon_rule is not None:
            assert cluster is not None and application_id is not None
            application = application_of(after, when)
        text = TEXT_RENEWED[lang]
    request = s.DecisionRequest(
        decision_id=before.decision_id,
        action=action,
        message=s.MessageChoice(lang=lang),
        override_reason=reason,
        override_category=category,
        approver=person,
    )
    audit_action: AuditAction = "restriction_lifted" if action == "lift" else "restriction_renewed"
    result = s.DecisionResult(
        decision=after,
        audit_entry=decision_entry(entry_id, when, person, audit_action, after, reason, before),
        outbox_message=outbox_message(message_id, after, lang, text, person, when),
        coupon_application=application,
    )
    return request, result


LIFT_REASON = "Reviewed at the due date: the returns are back in line with the products"
LIFT_AT = AS_OF + timedelta(minutes=70)


def lift_example(case: Case) -> tuple[s.DecisionRequest, s.DecisionResult]:
    """US-607: Luca reviews a Q3 restriction after its review date and lifts it. The approved
    version stays in the log; the new version has the standard levers."""
    return review_result(
        case.engine, case.card.decision, "lift", "luca", LIFT_AT, entry_id=14,
        message_id="MSG-000088-2", reason=LIFT_REASON, category="periodic_review",
    )  # fmt: skip


def legal_guard_error(elena_case: Case) -> tuple[s.DecisionRequest, s.ErrorResponse]:
    """An override that would charge return shipping on a defect: refused by the legal guard."""
    d, req = elena_case.engine.decision, elena_case.engine.request
    shipping = s.Lever(id="free_return_shipping", value=False, scope="future_orders")
    levers = s.to_policy_levers([shipping], BASE)
    attempt = replace(
        d,
        levers=levers,
        requires_approval=True,
        review_on=d.decided_on + timedelta(days=90),
        state=DecisionState.APPROVED,
        approver="sara",
    )
    violations = check(attempt, req, BASE)
    assert violations
    request = s.DecisionRequest(
        decision_id=elena_case.card.decision.decision_id,
        action="override",
        levers=[shipping],
        message=s.MessageChoice(lang="it"),
        override_reason="Try charging the return shipping",
        override_category="other",
        approver="sara",
    )
    error = s.ErrorResponse(
        code="legal_guard",
        message="The override violates the legal guard (policy/legal_guard.py)",
        violations=[s.ViolationView(code=v.code, message=v.message) for v in violations],
        request_id="req-000120",
    )
    return request, error


def regenerate_example(giulia_case: Case) -> tuple[s.RegenerateRequest, s.RegenerateResponse]:
    view = giulia_case.card.decision
    gen = generation("GEN-000101-2", AS_OF + timedelta(minutes=5), latency_ms=11)
    assert giulia_case.card.draft is not None
    text: dict[Lang, str] = {
        "en": "Dear customer, thank you for your message. Your size return is registered and the "
        "refund will reach you by {deadline}. Your next orders come with these terms: {terms}."
    }
    request = s.RegenerateRequest(
        decision_id=view.decision_id, languages=["en"], tone="formal", requested_by="sara"
    )
    response = s.RegenerateResponse(
        return_id=view.return_id,
        decision=revalidate(view, as_of=gen.created_at),
        draft=draft(view, gen, giulia_case.card.draft.explanation, text, tone="formal"),
    )
    return request, response


def snapshot(
    view: s.DecisionView,
    risk: float | None,
    factors: Sequence[s.ScoreContribution],
    cluster_size: int | None,
    active_links: int,
) -> s.CaseSnapshot:
    return s.CaseSnapshot(
        decision_id=view.decision_id,
        version=view.version,
        quadrant=view.quadrant,
        evidence_quality=view.evidence_quality,
        risk=metric_or_none(risk, "probability", SCORES),
        top_factors=list(factors),
        lever_ids=view.lever_ids,
        requires_approval=view.requires_approval,
        cluster_size=cluster_size,
        active_links=active_links,
    )


def evidence_example(case: Case) -> tuple[s.EvidenceRequest, s.RecomputeResponse]:
    before_view = case.card.decision
    link = s.link_id("C-ROSSI-CARD-1", "C-ROSSI-CARD-2")
    request = s.EvidenceRequest(
        decision_id=before_view.decision_id,
        kind="shared_payment_explained",
        note="The card belongs to the parents: mother and daughter in one household (phone call)",
        target_link_ids=[link],
        author="sara",
    )
    after_engine = run_engine(
        "RET-000104", "ORD-005344", "C-ROSSI-CARD-2", ReturnReason.SIZE,
        date(2026, 9, 20), date(2026, 9, 26), 0.21, value_parts(ORDERS_ROSSI_CARD)[3],
        cluster_id="CL-000588",
    )  # fmt: skip
    when = AS_OF + timedelta(minutes=30)
    after_view = decision_view(
        after_engine,
        version=2,
        created_at=when,
        as_of=when,
        trigger="evidence_added",
        supersedes=before_view.decision_id,
        generation_id="GEN-000104-2",
    )
    before_factors = case.card.risk.top_factors if case.card.risk else []
    after_factors = [
        factor(
            "welcome_coupons_per_account", 0.3333, -0.35,
            "Only 1 of 3 linked accounts used a welcome coupon",
        ),
        factor(
            "creation_spread_days", before_factors[2].feature_value, -0.27,
            "The linked accounts were created more than a year apart",
        ),
        factor("strong_id_share", 0.0, -0.12, "No identifier shared beyond address and device"),
    ]  # fmt: skip
    gen = generation("GEN-000104-2", when + timedelta(seconds=40))
    statements = [
        statement(
            "OBSERVED",
            "The shared card is explained by the operator: the link no longer counts in the "
            "cluster features.",
            link,
        ),
        statement(
            "SCORE",
            "The risk score goes from 0.34 to 0.21: contributions to the score, not causes.",
            "C-ROSSI-CARD-2", "risk", "0.21", PERIOD,
        ),
    ]  # fmt: skip
    response = s.RecomputeResponse(
        return_id="RET-000104",
        trigger="evidence_added",
        evidence_id="EV-000004",
        dispute_id=None,
        before=snapshot(before_view, 0.34, before_factors, 3, 3),
        after=snapshot(after_view, 0.21, after_factors, 3, 2),
        decision=after_view,
        draft=draft(after_view, gen, statements, TEXT_REGISTERED),
        audit_entry_ids=[5, 6],
        provenance=SCORES,
    )
    return request, response


def dispute_example(case: Case) -> tuple[s.LinkDisputeRequest, s.LinkDisputeResponse]:
    reason = "Card used once for a gift; the accounts are in different households"
    when = AS_OF + timedelta(minutes=40)
    info = s.LinkDisputeInfo(dispute_id="LD-000002", reason=reason, author="sara", created_at=when)
    before = case.card.decision
    request = s.LinkDisputeRequest(
        reason=reason, author="sara", return_id="RET-000106", decision_id=before.decision_id
    )
    accounts = esposito_accounts()
    link = next(
        x
        for x in build_links(accounts, PICKUP_SHARERS, {ESPOSITO_LINK: info})
        if x.link_id == ESPOSITO_LINK
    )
    after = decision_view(
        esposito_engine(True),
        version=2,
        created_at=when,
        as_of=when,
        trigger="link_disputed",
        supersedes=before.decision_id,
        generation_id="GEN-000106-2",
    )
    gen = generation(
        "GEN-000106-2",
        when + timedelta(seconds=50),
        "template",
        ["DECISION_ECHO: output does not match the engine decision"] * 2,
    )
    statements = [
        statement(
            "OBSERVED",
            "The operator disputed the link between the case customer and a linked account: the "
            "case customer is no longer in a cluster.",
            ESPOSITO_LINK,
        ),
        statement("SCORE", "The risk score is 0.41, below the 0.6 threshold.", "C-10233", "risk",
                  "0.41", PERIOD),
    ]  # fmt: skip
    recompute = s.RecomputeResponse(
        return_id="RET-000106",
        trigger="link_disputed",
        evidence_id=None,
        dispute_id=info.dispute_id,
        before=snapshot(before, 0.66, [factor(*f) for f in ESPOSITO_FACTORS], 3, 3),
        after=snapshot(
            after,
            0.41,
            [
                factor("cluster_size", 1, -0.3, "No linked accounts after the dispute"),
                factor("changed_mind_share", 1.0, 0.21, "2 of 2 returns were for change of mind"),
                factor("residual_z", 0.6, 0.09, "Returns slightly above what the products predict"),
            ],
            None,
            0,
        ),
        decision=after,
        draft=draft(after, gen, statements, TEXT_UPGRADE),
        audit_entry_ids=[7, 8],
        provenance=SCORES,
    )
    return request, s.LinkDisputeResponse(
        link=link, affected_return_ids=["RET-000106"], recompute=recompute, provenance=GRAPH
    )


def recompute_alternatives(view: s.DecisionView) -> list[s.Alternative]:
    """The alternatives of a version made by a recompute (US-310): the engine's new proposal
    (recommended) and the alternatives that fit it."""
    period = f"12 months after the decision, customer {view.inputs.customer_id}"
    did = view.decision_id
    if view.proposed_levers:  # the Q1 upgrade of Chiara once the disputed link is gone
        return [
            alternative(
                "proposal", "Q1 upgrade for future orders: 60-day window and priority size "
                "exchange", did, period, view.proposed_levers, True,
                incremental_margin_eur="15.00", incentives_eur="4.00", system_cost_eur="0.50",
            ),
            alternative("current_terms", "Keep the current terms", did, period),
        ]  # fmt: skip
    return [
        alternative("proposal", "Standard terms: the linked accounts alone do not justify a "
                    "change", did, period, recommended=True),
        alternative("verification", "Check the item at the warehouse before closing the "
                    "return", did, period, quantities=VERIFICATION),
    ]  # fmt: skip


def decision_records(
    versions: Sequence[tuple[str, s.DecisionView, list[s.Alternative], str | None]],
) -> dict[str, BaseModel]:
    """GET /api/decisions/{decision_id} after the evidence, the dispute and the lift: each
    version named by a version chain of the examples, as served two hours after the demo clock
    (US-604). A version given with the id of its successor is history: superseded, not in
    force."""
    served = AS_OF + timedelta(hours=2)
    out: dict[str, BaseModel] = {}
    for key, view, alternatives, next_id in versions:
        state, approval, review = view.state, view.requires_approval, view.review_at
        later = revalidate(
            view,
            superseded_by=next_id,
            as_of=served,
            in_force=in_force(state, approval, review, served, next_id),
        )
        out[f"decision_record.{key}"] = s.DecisionRecord(decision=later, alternatives=alternatives)
    return out


def checkout_examples(giulia_case: Case, marco_approved: s.DecisionView) -> dict[str, BaseModel]:
    shown = AS_OF + timedelta(minutes=30)
    model = prov("return-propensity model propensity-xgb-0.1.0 on a synthetic cart")
    threshold = 0.35
    base_terms = s.Terms.from_policy(BASE)
    giulia_view = giulia_case.card.decision
    g_request = s.CheckoutEvaluateRequest(
        customer_id="C-GIULIA",
        lines=[
            s.CartLine(variant_id="V-DRS-2500", size="M"),
            s.CartLine(variant_id="V-DRS-2500", size="L"),
        ],
    )
    g_response = s.CheckoutEvaluateResponse(
        customer_id="C-GIULIA",
        quadrant=giulia_view.quadrant,
        lines=[
            s.CheckoutLine(variant_id="V-DRS-2500", size="M", p_return=0.18, size_advice=None),
            s.CheckoutLine(
                variant_id="V-DRS-2500",
                size="L",
                p_return=0.47,
                size_advice=s.SizeAdvice(
                    kind="specific",
                    suggested_size="M",
                    text="Customers with your measurements kept size M of this dress",
                ),
            ),
        ],
        terms=giulia_view.future_terms,
        base_terms=base_terms,
        coupon_eligible=True,
        coupon_block=None,
        terms_snapshot_id="TS-CHK-000981",
        terms_source=giulia_view.decision_id,
        review_at=None,
        shown_at=shown,
        p_return_threshold=threshold,
        model_version=MODEL_VERSIONS["propensity"],
        provenance=model,
    )
    confirm_request = s.CheckoutConfirmRequest(
        terms_snapshot_id="TS-CHK-000981", customer_id="C-GIULIA", lines=g_request.lines
    )
    confirm_response = s.CheckoutConfirmResponse(
        order_id="ORD-CHK-000001",
        terms_snapshot=s.TermsSnapshotView(
            terms_snapshot_id="TS-CHK-000981",
            order_id="ORD-CHK-000001",
            customer_id="C-GIULIA",
            source=giulia_view.decision_id,
            quadrant_at_purchase=giulia_view.quadrant,
            terms=giulia_view.future_terms,
            base_terms=base_terms,
            review_at=None,
            shown_at=shown,
            confirmed_at=shown + timedelta(minutes=2),
        ),
        coupon_applied=False,
    )
    m_request = s.CheckoutEvaluateRequest(
        customer_id="C-MARCO-1",
        lines=[s.CartLine(variant_id="V-JKT-2207", size="M")],
        coupon_code="WELCOME-Z9K3",
    )
    assert marco_approved.review_at is not None
    m_response = s.CheckoutEvaluateResponse(
        customer_id="C-MARCO-1",
        quadrant=marco_approved.quadrant,
        lines=[s.CheckoutLine(variant_id="V-JKT-2207", size="M", p_return=0.29, size_advice=None)],
        terms=marco_approved.future_terms,
        base_terms=base_terms,
        coupon_eligible=False,
        coupon_block=s.CouponBlock(
            rule_version=COUPON_RULE,
            application_id=MARCO_APPLICATION,
            review_at=marco_approved.review_at,
            text="Coupons are paused for the linked accounts of this cluster until the review date",
        ),
        terms_snapshot_id="TS-CHK-000982",
        terms_source=marco_approved.decision_id,
        review_at=marco_approved.review_at,
        shown_at=shown + timedelta(minutes=5),
        p_return_threshold=threshold,
        model_version=MODEL_VERSIONS["propensity"],
        provenance=model,
    )
    return {
        "checkout_evaluate_request.giulia": g_request,
        "checkout_evaluate_response.giulia": g_response,
        "checkout_confirm_request.giulia": confirm_request,
        "checkout_confirm_response.giulia": confirm_response,
        "checkout_evaluate_request.marco": m_request,
        "checkout_evaluate_response.marco": m_response,
    }


VISION = prov("vision checklist of the mock model on a synthetic photo (declared confidences)")


def inspection_examples(valentina: Case, simone: Case) -> dict[str, BaseModel]:
    view = valentina.card.decision
    created = AS_OF + timedelta(minutes=41)
    gen = s.GenerationInfo(
        generation_id="GEN-INS-000014",
        generation_path="llm",
        llm_mode="mock",
        served_from="live",
        model="mock",
        attempts=1,
        validator_errors=[],
        created_at=created,
        latency_ms=22,
    )
    checklist = [
        s.ChecklistSuggestion(
            item="tag_attached", suggestion="no", model_confidence=0.78, note="No tag visible"
        ),
        s.ChecklistSuggestion(
            item="visible_damage",
            suggestion="yes",
            model_confidence=0.71,
            note="Make-up stain near the collar",
        ),
        s.ChecklistSuggestion(
            item="photo_quality", suggestion="yes", model_confidence=0.93, note=None
        ),
    ]
    rule_text = "Visible damage, not resellable as new: 20% of the line price (rule DV-RULE-1)"
    amount = eur(Decimal(str(valentina.card.item.price_eur)) * Decimal("0.20"))
    claim_prov = prov("diminished-value rule DV-RULE-1 applied to a synthetic return")
    awaiting = s.InspectionView(
        inspection_id="INS-000014",
        return_id=view.return_id,
        decision_id=view.decision_id,
        path=view.path,
        inspector="ahmed",
        photo_id="PH-000014",
        created_at=created,
        state="awaiting_confirmation",
        checklist=checklist,
        manual_review_reason=None,
        proposal=s.CompensationProposal(
            rule_id="DV-RULE-1",
            amount=m(amount, "eur", claim_prov),
            basis=["visible_damage"],
            text=rule_text,
        ),
        confirmed_observations=None,
        confirmed_by=None,
        confirmed_at=None,
        generation=gen,
        provenance=VISION,
    )
    manual_view = simone.card.decision
    manual = s.InspectionView(
        inspection_id="INS-000015",
        return_id=manual_view.return_id,
        decision_id=manual_view.decision_id,
        path=manual_view.path,
        inspector="ahmed",
        photo_id="PH-000015",
        created_at=created + timedelta(minutes=12),
        state="manual_review",
        checklist=[
            s.ChecklistSuggestion(
                item="tag_attached", suggestion="not_observable", model_confidence=0.35, note=None
            ),
            s.ChecklistSuggestion(
                item="visible_damage", suggestion="not_observable", model_confidence=0.3, note=None
            ),
            s.ChecklistSuggestion(
                item="photo_quality", suggestion="no", model_confidence=0.88, note="Too dark"
            ),
        ],
        manual_review_reason="Photo too dark to check the tag and the fabric: verify by hand",
        proposal=None,
        confirmed_observations=None,
        confirmed_by=None,
        confirmed_at=None,
        generation=revalidate(gen, generation_id="GEN-INS-000015", latency_ms=19),
        provenance=VISION,
    )
    observations = [
        s.ConfirmedObservation(item="tag_attached", observation="no"),
        s.ConfirmedObservation(item="visible_damage", observation="yes"),
        s.ConfirmedObservation(item="photo_quality", observation="yes"),
    ]
    confirmed_at = created + timedelta(minutes=6)
    claim = s.ClaimView(
        inspection_id="INS-000014",
        amount=m(amount, "eur", claim_prov),
        photo_evidence_ids=["PH-000014"],
        confirmed_by="ahmed",
        rule_id="DV-RULE-1",
    )
    policy_claim = DiminishedValueClaim(
        amount_eur=Decimal(str(amount)),
        photo_evidence_ids=("PH-000014",),
        confirmed_by="ahmed",
        rule_id="DV-RULE-1",
    )
    assert not check(
        replace(valentina.engine.decision, diminished_value_claim=policy_claim),
        valentina.engine.request,
        BASE,
    )
    with_claim = revalidate(view, diminished_value_claim=claim, as_of=confirmed_at)
    text = (
        "Ciao, abbiamo controllato il capo reso: presenta una macchia visibile, quindi "
        "tratteniamo {amount} dal rimborso, che arriverà entro il {deadline}."
    )
    confirm_response = s.InspectionConfirmResponse(
        inspection=revalidate(
            awaiting,
            state="confirmed",
            confirmed_observations=[x.model_dump() for x in observations],
            confirmed_by="ahmed",
            confirmed_at=confirmed_at,
        ),
        claim=claim,
        decision=with_claim,
        outbox_message=outbox_message(
            "MSG-000110-1", with_claim, "it", text, "ahmed", confirmed_at
        ),  # fmt: skip
    )
    return {
        "inspection_create_request": s.InspectionCreateRequest(
            return_id=view.return_id,
            inspector="ahmed",
            photo=s.PhotoUpload(
                media_type="image/jpeg", data_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2Q=="
            ),
        ),  # fmt: skip
        "inspection_create_request.manual_review": s.InspectionCreateRequest(
            return_id=manual_view.return_id,
            inspector="ahmed",
            photo=s.PhotoUpload(
                media_type="image/jpeg", data_base64="/9j/4AAQSkZJRgABAQAAAQABAAD/2Q=="
            ),
        ),  # fmt: skip
        "inspection.awaiting": awaiting,
        "inspection.manual_review": manual,
        "inspection_confirm_request": s.InspectionConfirmRequest(
            inspector="ahmed", observations=observations, apply_compensation=True
        ),
        "inspection_confirm_response": confirm_response,
    }


# The score of a customer whose open return is on the guarantee path: the customer is scored by
# the nightly job, the defect return is not (rule 4).
CUSTOMER_SCORES = {"C-ELENA": 0.09, **{c.customer_id: c.risk for c in SIMPLE}}


def matrix(cases: Sequence[Case]) -> s.PortfolioMatrix:
    """Every scored customer. A customer with a case card is the card: risk, value, cluster,
    returns and welcome coupons come from it (spec 8.5); the others are synthetic."""
    rnd = random.Random(42)
    points: list[s.MatrixPoint] = []

    def add(
        cid: str,
        name: str | None,
        risk: float | None,
        value: float,
        evidence: EvidenceQuality = EvidenceQuality.SUFFICIENT,
        cluster: str | None = None,
        returns: int = 1,
        coupons: int = 0,
    ) -> None:
        quadrant = None
        if evidence is EvidenceQuality.SUFFICIENT:
            assert risk is not None
            quadrant = quadrant_for(
                CaseScores(risk, Decimal(str(value)), evidence, cluster), THRESHOLDS
            )
        points.append(
            s.MatrixPoint(
                customer_id=cid,
                display_name=name,
                risk=risk if evidence is EvidenceQuality.SUFFICIENT else None,
                value_eur=value,
                evidence_quality=evidence,
                quadrant=quadrant,
                cluster_id=cluster,
                returns_12m=returns,
                welcome_coupons=coupons,
            )
        )

    carded = set()
    for case in cases:
        card, d = case.card, case.card.decision
        assert card.value is not None
        risk = card.risk.score.value if card.risk else CUSTOMER_SCORES.get(card.customer_id)
        add(
            card.customer_id,
            card.display_name,
            float(risk) if risk is not None else None,
            float(card.value.value_eur.value),
            d.evidence_quality,
            d.inputs.cluster_id,
            sum(line.returned for o in card.orders.orders for line in o.lines),
            sum(o.coupon_kind == "welcome" for o in card.orders.orders),
        )
        carded.add(card.customer_id)
    for i, a in enumerate(ring_accounts(), start=1):
        if a.customer_id not in carded:
            add(a.customer_id, a.display_name, round(0.81 + 0.01 * i, 2), eur(18 + 4.5 * i),
                cluster=MARCO_CLUSTER, returns=a.returns_12m, coupons=1)  # fmt: skip
    for shared, cluster in ((False, "CL-000233"), (True, "CL-000588")):
        for i, a in enumerate(rossi_accounts(shared), start=1):
            if a.customer_id not in carded:
                risk = round((0.31 if shared else 0.15) + 0.02 * i, 2)
                coupons = int(a.welcome_coupon_at is not None)
                add(a.customer_id, a.display_name, risk, eur(60 + 31.7 * i), cluster=cluster,
                    returns=a.returns_12m, coupons=coupons)  # fmt: skip
    for n in range(480):
        evidence = (
            EvidenceQuality.INSUFFICIENT if rnd.random() < 0.05 else EvidenceQuality.SUFFICIENT
        )
        risk = round(rnd.betavariate(2, 9), 3)
        value = eur(max(-40.0, rnd.lognormvariate(4.6, 0.7) - 30))
        random_cluster = f"CL-{rnd.randint(1, 999):06d}" if rnd.random() < 0.08 else None
        add(
            f"C-{30000 + n * 7}",
            None,
            risk,
            value,
            evidence,
            random_cluster,
            rnd.randint(0, 6),
            int(rnd.random() < 0.6),
        )
    quadrants = []
    for q in Quadrant:
        inside = [p for p in points if p.quadrant is q]
        quadrants.append(
            s.QuadrantSummary(
                quadrant=q,
                customers=m(len(inside), "count", SCORES),
                value_eur=m(
                    eur(sum((Decimal(str(p.value_eur)) for p in inside), Decimal(0))), "eur", VALUE
                ),
                returns_12m=m(sum(p.returns_12m for p in inside), "count", SHOP),
            )
        )
    insufficient = sum(p.quadrant is None for p in points)
    return s.PortfolioMatrix(
        as_of=AS_OF,
        thresholds=s.MatrixThresholds(
            risk=m(THRESHOLDS.risk, "probability", THRESH),
            value_eur=m(eur(THRESHOLDS.value_eur), "eur", THRESH),
            risk_basis="Lowest risk with precision >= 0.8 on the validation months",
            value_basis="70th percentile of customer value",
        ),
        quadrants=quadrants,
        insufficient_evidence=m(insufficient, "count", EVIDENCE),
        points=points,
        points_provenance=SCORES,
    )


def assumption_version(
    a: SimAssumptions,
    created_by: str,
    created_at: datetime,
    note: str,
    supersedes: str | None,
    provenance: s.Provenance,
) -> s.AssumptionVersion:
    return s.AssumptionVersion(
        version=a.version,
        created_by=created_by,
        created_at=created_at,
        note=note,
        supersedes=supersedes,
        baseline="Navigli base policy (spec 4) on the same period",
        net_benefit=s.NetBenefitView.build(a, provenance),
        sensitivity=[
            s.SensitivityScenario(id=k, net_benefit=s.NetBenefitView.build(v_a, provenance))
            for k, v_a in _sensitivity_inputs(a).items()
        ],
    )


def _sensitivity_inputs(a: SimAssumptions) -> dict[s.SensitivityId, s.Assumptions]:
    """The scenarios of policy.simulator.sensitivity as labelled sets: "<version>/<scenario>",
    based on the version (the base scenario is the version itself)."""
    halved = a.logistics_cost_per_return_eur / 2
    doubled = a.abandonment_margin_lost_eur * 2
    variants: dict[s.SensitivityId, SimAssumptions] = {
        "logistics_halved": replace(a, logistics_cost_per_return_eur=halved),
        "abandonment_doubled": replace(a, abandonment_margin_lost_eur=doubled),
        "both": replace(
            a, logistics_cost_per_return_eur=halved, abandonment_margin_lost_eur=doubled
        ),
    }
    out: dict[s.SensitivityId, s.Assumptions] = {"base": s.Assumptions.from_policy(a)}
    for key, v in variants.items():
        labelled = replace(v, version=f"{a.version}/{key}")
        out[key] = s.Assumptions.from_policy(labelled, based_on=a.version)
    return out


PROPOSAL_PROVENANCE = s.Provenance(
    origin=EXAMPLE + "illustrative example from the proposals document (12 Oct 2026); "
    "not a ReturnRing result",
    period_start=date(2026, 10, 1),
    period_end=date(2026, 10, 31),
    nature="simulated",
)


def simulator_examples() -> dict[str, BaseModel]:
    v1 = assumption_version(
        PROPOSAL_EXAMPLE,
        "system",
        at(date(2026, 9, 30), 8),
        "Seeded with the illustrative example of the proposals document (ECO-01)",
        None,
        PROPOSAL_PROVENANCE,
    )
    get = s.SimulatorAssumptionsResponse(current=v1, history=[v1])
    base_inputs = V1.model_dump(exclude={"version", "basis", "based_on"})
    inputs = s.AssumptionInputs.model_validate(
        {**base_inputs, "logistics_cost_per_return_eur": 6.5}
    )
    note = "Carrier contract renegotiated: 6.50 EUR per return (hypothesis)"
    update = s.SimulatorAssumptionsUpdate(
        base_version="assumptions-v1", assumptions=inputs, note=note, author="luca"
    )
    v2 = assumption_version(
        inputs.to_policy("assumptions-v2", "simulated"),
        "luca",
        AS_OF + timedelta(hours=1),
        note,
        "assumptions-v1",
        PROPOSAL_PROVENANCE,
    )
    put = s.SimulatorAssumptionsResponse(current=v2, history=[v2, v1])
    return {
        "simulator_assumptions.v1": get,
        "simulator_assumptions_update": update,
        "simulator_assumptions.v2": put,
    }


def kpi() -> s.KpiResponse:
    week = date(2026, 9, 21)
    period = "week 2026-09-21 to 2026-09-27, all decisions"
    weekly = s.Provenance(
        origin=EXAMPLE + "economic simulator on the decisions of one week, assumptions-v1",
        period_start=week,
        period_end=week + timedelta(days=6),
        nature="simulated",
    )
    demo = prov("decisions and audit log of the demo week", week, week + timedelta(days=6))
    simulated = nb(
        f"kpi/{week.isoformat()}",
        period,
        weekly,
        logistics_returns_avoided=18,
        undue_discounts_avoided=6,
        minutes_saved=95,
        incentives_eur="41.00",
        system_cost_eur="28.00",
        error_cost_eur="23.00",
        abandonment_margin_lost_eur="55.00",
        incremental_margin_eur="37.00",
    )
    cohorts: tuple[tuple[s.CohortId, float, int], ...] = (
        ("all", 0.021, 4180),
        ("new_customers", 0.034, 610),
        ("households", 0.028, 702),
        ("low_data", 0.041, 388),
        ("high_value", 0.009, 1254),
    )
    return s.KpiResponse(
        period_start=week,
        period_end=week + timedelta(days=6),
        net_benefit_simulated=simulated,
        net_benefit_realized=None,
        evaluation_report_id="eval-2026-09-30",
        false_positives=[
            s.CohortRate(
                cohort=c,
                false_positive_rate=m(rate, "ratio", EVAL),
                customers=m(n, "count", EVAL),
            )
            for c, rate, n in cohorts
        ],
        decision_time_p50=m(41.0, "seconds", demo),
        decision_time_p95=m(118.0, "seconds", demo),
        abstentions_insufficient_evidence=m(9, "count", demo),
        abstention_rate=m(0.0703, "ratio", demo),
        decisions=s.DecisionCounts(
            total=m(128, "count", demo),
            approved=m(97, "count", demo),
            overridden=m(6, "count", demo),
            rejected=m(2, "count", demo),
            pending=m(23, "count", demo),
        ),
        override_reasons=[
            s.OverrideReasonCount(category="customer_context", count=m(3, "count", demo)),
            s.OverrideReasonCount(category="linked_accounts_explained", count=m(2, "count", demo)),
            s.OverrideReasonCount(category="evidence_disagreement", count=m(1, "count", demo)),
        ],
        open_catalog_tasks=m(2, "count", demo),
    )


def fairness() -> s.FairnessResponse:
    dims: tuple[tuple[s.FairnessDimension, tuple[tuple[str, float, int], ...]], ...] = (
        ("new_customers", (("new, under 90 days", 0.031, 610), ("established", 0.019, 3570))),
        (
            "households",
            (("address shared with linked accounts", 0.027, 702), ("single account", 0.02, 3478)),
        ),  # noqa: E501
        ("low_data", (("fewer than 4 lines", 0.072, 388), ("4 lines or more", 0.011, 3792))),
        (
            "tenure",
            (
                ("under 1 year", 0.024, 1460),
                ("1 to 3 years", 0.018, 1820),
                ("over 3 years", 0.015, 900),
            ),
        ),  # noqa: E501
        (
            "price_band",
            (
                ("under 50 EUR", 0.017, 1290),
                ("50 to 150 EUR", 0.021, 2210),
                ("over 150 EUR", 0.026, 680),
            ),
        ),  # noqa: E501
        (
            "category",
            (
                ("Dresses", 0.022, 1310),
                ("Jackets", 0.019, 840),
                ("Shoes", 0.025, 960),
                ("Jeans", 0.018, 1070),
            ),
        ),  # noqa: E501
    )
    threshold = 5.0
    reports = []
    for dim, segments in dims:
        rates = [r for _, r, _ in segments]
        gap = round((max(rates) - min(rates)) * 100, 2)
        reports.append(
            s.FairnessDimensionReport(
                dimension=dim,
                segments=[
                    s.FairnessSegment(
                        segment=name,
                        false_positive_rate=m(rate, "ratio", EVAL),
                        customers=m(n, "count", EVAL),
                    )
                    for name, rate, n in segments
                ],
                max_gap=m(gap, "percentage_points", EVAL),
                warning=gap > threshold,
            )
        )
    return s.FairnessResponse(
        report_id="eval-2026-09-30",
        scenario="main",
        model_version=MODEL_VERSIONS["risk"],
        max_gap_threshold=m(threshold, "percentage_points", prov("NFR-04 threshold")),
        dimensions=reports,
    )


def audit_page(
    marco_proposed: s.DecisionView,
    results: Sequence[s.DecisionResult],
    recomputes: Sequence[tuple[s.DecisionView, s.DecisionView]],
) -> s.AuditPage:
    """The log of the demo session. Entries about a decision carry typed before and after."""
    a = audit_entry
    marco_result, override, reject, lift = results
    (rossi_v1, rossi_v2), (chiara_v1, chiara_v2) = recomputes
    entries = [
        decision_entry(1, at(date(2026, 9, 28), 9, 16), "system", "decision_proposed",
                       marco_proposed),
        a(2, at(date(2026, 9, 28), 9, 17), "system", "draft_generated", "llm_generation",
          "GEN-000102-1", "RET-000102", "DEC-000102-1", None, None,
          {"generation_path": "llm", "llm_mode": "mock"}),
        marco_result.audit_entry,
        a(4, AS_OF + timedelta(minutes=20), "system", "message_queued", "outbox_message",
          "MSG-000102-1", "RET-000102", "DEC-000102-1", None, None, {"lang": "it"}),
        a(5, AS_OF + timedelta(minutes=30), "sara", "evidence_added", "evidence_addition",
          "EV-000004", "RET-000104", "DEC-000104-1",
          "The card belongs to the parents: mother and daughter in one household (phone call)",
          None, {"kind": "shared_payment_explained",
                 "target_link_ids": ["C-ROSSI-CARD-1~C-ROSSI-CARD-2"]}),
        decision_entry(6, AS_OF + timedelta(minutes=30), "system", "case_recomputed", rossi_v2,
                       before=rossi_v1),
        a(7, AS_OF + timedelta(minutes=40), "sara", "link_disputed", "link_dispute", "LD-000002",
          "RET-000106", "DEC-000106-1",
          "Card used once for a gift; the accounts are in different households",
          {"disputed": False}, {"disputed": True, "link_id": ESPOSITO_LINK}),
        decision_entry(8, AS_OF + timedelta(minutes=40), "system", "case_recomputed", chiara_v2,
                       before=chiara_v1),
        a(9, AS_OF + timedelta(minutes=45), "luca", "catalog_task_opened", "catalog_task",
          "CT-000012", "RET-000105", None, None, None,
          {"kind": "batch_check", "anomaly_id": "V-JKT-DEF~defective~2026-09-21"}),
        a(10, AS_OF + timedelta(minutes=47), "ahmed",
          "inspection_confirmed", "inspection", "INS-000014", "RET-000110", "DEC-000110-1", None,
          {"state": "awaiting_confirmation"},
          {"state": "confirmed", "compensation_eur": 17.98, "rule_id": "DV-RULE-1"}),
        override.audit_entry,
        reject.audit_entry,
        a(13, AS_OF + timedelta(hours=1), "luca", "assumptions_updated",
          "simulator_assumptions", "assumptions-v2", None, None, None,
          {"version": "assumptions-v1"}, {"version": "assumptions-v2"}),
        lift.audit_entry,
    ]  # fmt: skip
    entries.sort(key=lambda e: (e.at, e.entry_id), reverse=True)
    return s.AuditPage(items=entries, total=len(entries), page=1, page_size=50)


def anomalies_and_tasks() -> dict[str, BaseModel]:
    dress_week = date(2026, 7, 27)
    dress = s.ProductAnomaly(
        anomaly_id=s.anomaly_id("V-DRS-0815", ReturnReason.NOT_AS_DESCRIBED, dress_week),
        variant_id="V-DRS-0815",
        product_id="P-DRS-0815",
        category="Dresses",
        brand="Navigli Studio",
        reason=ReturnReason.NOT_AS_DESCRIBED,
        week=dress_week,
        observed=m(9, "count", ANOMALIES),
        expected=m(2.1, "expected_count", ANOMALIES),
        z=m(round((9 - 2.1) / math.sqrt(2.1), 2), "z_score", ANOMALIES),
        sales=m(43, "count", ANOMALIES),
        status="resolved",
        catalog_task_id="CT-000007",
        return_ids=["RET-000071", "RET-000074"],
        trend=[
            s.WeeklyReturnRate(week=dress_week + timedelta(weeks=i), sales=n, returns=r,
                               return_rate=round(r / n, 4))
            for i, (n, r) in enumerate(((43, 9), (40, 7), (38, 6), (41, 4), (39, 3), (42, 3)))
        ],
    )  # fmt: skip
    sneaker_week = date(2026, 9, 21)
    sneaker = s.ProductAnomaly(
        anomaly_id=s.anomaly_id("V-SNK-2290", ReturnReason.DEFECTIVE, sneaker_week),
        variant_id="V-SNK-2290",
        product_id="P-SNK-2290",
        category="Shoes",
        brand="Navigli Sport",
        reason=ReturnReason.DEFECTIVE,
        week=sneaker_week,
        observed=m(7, "count", ANOMALIES),
        expected=m(1.2, "expected_count", ANOMALIES),
        z=m(round((7 - 1.2) / math.sqrt(1.2), 2), "z_score", ANOMALIES),
        sales=m(31, "count", ANOMALIES),
        status="open",
        catalog_task_id=None,
        return_ids=["RET-000094", "RET-000098"],
        trend=[],
    )
    anomalies = s.ProductAnomaliesResponse(
        as_of=AS_OF,
        thresholds=s.AnomalyThresholds(z=3.0, min_sales=20),
        items=[jacket_anomaly(), sneaker, dress],
        provenance=ANOMALIES,
    )
    created = AS_OF + timedelta(minutes=45)
    create = s.CatalogTaskCreate(
        anomaly_id=jacket_anomaly().anomaly_id,
        kind="batch_check",
        note="Check the sleeve length of the September batch against the size guide",
        author="luca",
        return_id="RET-000105",
    )
    task = s.CatalogTask(
        task_id="CT-000012",
        anomaly_id=create.anomaly_id,
        variant_id="V-JKT-DEF",
        reason=ReturnReason.DEFECTIVE,
        kind="batch_check",
        status="open",
        note=create.note,
        created_by="luca",
        created_at=created,
        updated_at=created,
        done_at=None,
        return_id="RET-000105",
        follow_up=None,
    )
    done_at = at(date(2026, 8, 28), 16)
    done = s.CatalogTask(
        task_id="CT-000007",
        anomaly_id=dress.anomaly_id,
        variant_id="V-DRS-0815",
        reason=ReturnReason.NOT_AS_DESCRIBED,
        kind="size_guide",
        status="done",
        note="Size guide corrected: the waist runs one size small",
        created_by="luca",
        created_at=at(date(2026, 8, 4), 10),
        updated_at=done_at,
        done_at=done_at,
        return_id=None,
        follow_up=s.FollowUp(
            baseline_rate=m(0.1871, "ratio", ANOMALIES),
            after_rate=m(0.0741, "ratio", ANOMALIES),
            weeks_after=4,
        ),
    )
    update = s.CatalogTaskUpdate(
        status="in_progress", note="Batch pulled from stock for checks", author="luca"
    )  # noqa: E501
    return {
        "product_anomalies": anomalies,
        "catalog_task_create": create,
        "catalog_task.elena": task,
        "catalog_task_update": update,
        "catalog_task.in_progress": revalidate(
            task, status="in_progress", note=update.note, updated_at=created + timedelta(hours=2)
        ),
        "catalog_tasks": s.CatalogTasksResponse(items=[task, done]),
    }


def coupon_examples(marco_view: s.DecisionView) -> dict[str, BaseModel]:
    v1 = s.CouponRule(
        rule_version=COUPON_RULE,
        text="Welcome coupons and referrals are paused, on future orders, for every account of "
        "a cluster when a case of the cluster is in Q4 and an operator confirms it; review "
        "after 90 days.",
        approved_by="luca",
        approved_at=at(date(2026, 9, 29), 10),
        supersedes=None,
        active=True,
    )
    served = AS_OF + timedelta(minutes=90)  # GET /api/coupon-rules, after the approval of Marco
    marco_app = application_of(marco_view, served)
    assert marco_app.state == "confirmed" and marco_app.in_force
    reversed_reason = "The linked accounts share a pickup point only: application reversed"
    reversed_at = AS_OF - timedelta(hours=1)
    reversed_app = s.CouponRuleApplication(
        application_id="CRA-000027",
        rule_version=COUPON_RULE,
        cluster_id="CL-000655",
        decision_id="DEC-000077-1",
        affected_customer_ids=["C-12007", "C-12930", "C-15561"],
        state="reversed",
        proposed_at=at(date(2026, 9, 29), 14),
        confirmed_by="sara",
        confirmed_at=at(date(2026, 9, 29), 15),
        review_at=date(2026, 12, 28),
        reversed_by="luca",
        reversed_at=reversed_at,
        reversal_reason=reversed_reason,
        as_of=reversed_at,  # as returned by POST .../reverse
        in_force=False,
    )
    current = s.CouponRulesResponse(
        current=v1,
        history=[v1],
        applications=[marco_app, revalidate(reversed_app, as_of=served)],
        as_of=served,
    )
    create = s.CouponRuleCreate(
        text=v1.text.replace(
            "Welcome coupons and referrals", "Welcome, referral and promo coupons"
        ),
        approved_by="luca",
    )
    v2 = s.CouponRule(
        rule_version="coupon-rule-v2",
        text=create.text,
        approved_by="luca",
        approved_at=AS_OF + timedelta(hours=2),
        supersedes=COUPON_RULE,
        active=True,
    )
    created = v2.approved_at
    after_create = s.CouponRulesResponse(
        current=v2,
        history=[v2, revalidate(v1, active=False)],
        applications=[revalidate(x, as_of=created) for x in (marco_app, reversed_app)],
        as_of=created,
    )
    return {
        "coupon_rules": current,
        "coupon_rule_create": create,
        "coupon_rules.after_create": after_create,
        "coupon_application_reverse": s.CouponApplicationReverse(
            author="luca", reason=reversed_reason
        ),
        "coupon_rule_application.reversed": reversed_app,
    }


def agent_examples() -> dict[str, BaseModel | str]:
    gen_at = AS_OF + timedelta(minutes=10)

    def done(seq: int, steps: int, gid: str) -> dict[str, Any]:
        info = generation(gid, gen_at, latency_ms=27).model_dump(mode="json")
        return {"type": "done", "seq": seq, "steps_used": steps, "generation": info}

    def stream(
        calls: Sequence[tuple[str, dict[str, str], str, list[dict[str, str]]]],
        answer: str,
        consulted: list[dict[str, str]],
        missing: list[str],
        gid: str,
    ) -> s.AgentStream:
        events: list[dict[str, Any]] = []
        for step, (tool, args, summary, refs) in enumerate(calls, start=1):
            events.append({"type": "tool_call", "seq": len(events), "step": step, "tool": tool,
                           "arguments": args})  # fmt: skip
            events.append({"type": "tool_result", "seq": len(events), "step": step, "tool": tool,
                           "ok": True, "summary": summary, "refs": refs})  # fmt: skip
        words = answer.split(" ")
        chunk = max(1, len(words) // 4)
        for i in range(0, len(words), chunk):
            text = " ".join(words[i : i + chunk]) + (" " if i + chunk < len(words) else "")
            events.append({"type": "token", "seq": len(events), "text": text})
        events.append({"type": "answer", "seq": len(events), "text": answer,
                       "evidence_consulted": consulted, "evidence_missing": missing,
                       "refs": consulted})  # fmt: skip
        events.append(done(len(events), len(calls), gid))
        return s.AgentStream.model_validate(events)

    link13 = s.link_id("C-MARCO-1", "C-MARCO-3")
    cluster_ref = {"kind": "cluster", "id": MARCO_CLUSTER}
    link_ref = {"kind": "link", "id": link13}
    link_question = stream(
        [
            ("get_cluster", {"customer_id": "C-MARCO-1"},
             f"Cluster {MARCO_CLUSTER}: 7 linked accounts; {link13} shares card, device, address",
             [cluster_ref, link_ref]),
            ("get_orders", {"customer_id": "C-MARCO-3"},
             "C-MARCO-3: 2 orders, welcome coupon redeemed on 2026-09-04",
             [{"kind": "customer", "id": "C-MARCO-3"}]),
        ],
        "C-MARCO-1 and C-MARCO-3 are linked by a payment card, a device and a delivery address, "
        "all shared since 2026-09-03, when C-MARCO-3 was created. Both accounts redeemed a welcome "
        "coupon within a day of creation. A link is a shared identifier, not proof of who uses "
        "the accounts.",
        [cluster_ref, link_ref, {"kind": "customer", "id": "C-MARCO-3"}],
        ["No contact with the customer about the shared card"],
        "GEN-AGENT-000001",
    )  # fmt: skip
    rossi_question = stream(
        [
            ("get_cluster", {"customer_id": "C-MARCO-1"},
             f"Cluster {MARCO_CLUSTER}: 7 accounts created within 9 days, 2 distinct cards",
             [cluster_ref]),
            ("get_score_factors", {"return_id": "RET-000102"},
             "Top contributions: welcome coupons per account, creation spread, change of mind",
             [{"kind": "return", "id": "RET-000102"}]),
        ],
        "The Rossi family shares an address and a device, but its 3 accounts were created more "
        "than a year apart, use 3 distinct cards and only 1 used a welcome coupon. The 7 linked "
        "accounts of this case were created within 9 days and each used a welcome coupon.",
        [cluster_ref, {"kind": "return", "id": "RET-000102"}],
        [],
        "GEN-AGENT-000002",
    )  # fmt: skip
    terms_question = stream(
        [
            ("get_case", {"return_id": "RET-000102"},
             "Order ORD-005230 placed on 2026-09-18 with terms snapshot TS-005230 (base terms)",
             [{"kind": "order", "id": "ORD-005230"},
              {"kind": "terms_snapshot", "id": "TS-005230"}]),
            ("get_policy_text", {"terms_snapshot_id": "TS-005230"},
             "Base terms: 30-day window, free return shipping, instant refund, free size exchange",
             [{"kind": "terms_snapshot", "id": "TS-005230"}]),
        ],
        "Order ORD-005230 was placed on 2026-09-18 with the base terms of snapshot TS-005230: a "
        "30-day window, free return shipping, instant refund and free size exchange. These "
        "terms stay valid for this order; the proposed terms apply only to future orders.",
        [{"kind": "order", "id": "ORD-005230"}, {"kind": "terms_snapshot", "id": "TS-005230"}],
        [],
        "GEN-AGENT-000003",
    )  # fmt: skip
    refusal = s.AgentStream.model_validate(
        [
            {"type": "refusal", "seq": 0, "reason": "write_request",
             "text": "I can only read the case. Approvals and changes are made by an operator "
             "on the case card."},
            done(1, 0, "GEN-AGENT-000004"),
        ]
    )  # fmt: skip
    return {
        "agent_ask_request.marco_link": s.AgentAskRequest(
            return_id="RET-000102",
            question="Which evidence supports the link between C-MARCO-1 and C-MARCO-3?",
            asked_by="sara",
        ),
        "agent_stream.marco_link": link_question,
        "agent_ask_request.marco_rossi": s.AgentAskRequest(
            return_id="RET-000102",
            question="What makes this case different from the Rossi family?",
            asked_by="sara",
        ),
        "agent_stream.marco_rossi": rossi_question,
        "agent_ask_request.marco_terms": s.AgentAskRequest(
            return_id="RET-000102",
            question="Which terms applied when the order was placed?",
            asked_by="sara",
        ),
        "agent_stream.marco_terms": terms_question,
        "agent_ask_request.write": s.AgentAskRequest(
            return_id="RET-000102",
            question="Approve the refund and remove the restriction.",
            asked_by="sara",
        ),
        "agent_stream.write_refused": refusal,
    }


TRAIN_TIMES: tuple[tuple[s.BenchmarkDevice, float], ...] = (("cpu", 412.0), ("mi300x", 38.5))
PENDING_BENCHMARKS: tuple[tuple[s.BenchmarkId, str, str], ...] = (
    (
        "B2",
        "GraphSAGE on ASOS GraphReturns: CPU against MI300X, F1",
        "ASOS GraphReturns, paper and decision-time protocols (ADR-10)",
    ),
    ("B4", "Nightly pipeline duration and GPU utilisation", "Navigli synthetic shop"),
)


def ops_examples() -> dict[str, BaseModel]:
    b1 = s.Benchmark(
        id="B1",
        title="XGBoost training on ASOS GraphReturns: CPU against MI300X",
        status="illustrative",
        measured_at=None,
        dataset="ASOS GraphReturns train split (OSF c793h), decision-time protocol (ADR-10)",
        versions={"amd_xgboost": "3.2.0", "xgboost-cpu": "3.2.0", "rocm": "7.2.3"},
        config={
            "max_depth": 4,
            "learning_rate": 0.1,
            "gamma": 1,
            "reg_lambda": 2,
            "early_stopping_rounds": 5,
            "tree_method": "hist",
        },  # fmt: skip
        results=[
            s.BenchmarkResult(
                metric="train_time",
                label="Training time",
                device=d,
                phase="training",
                value=m(v, "seconds", ILLUSTRATIVE),
                concurrency=None,
                batch_size=None,
                protocol="decision_time",
            )
            for d, v in TRAIN_TIMES
        ],
        notes=["Illustrative values for the UI; replaced by the measured run (US-902)"],
    )
    b3 = s.Benchmark(
        id="B3",
        title="Gemma 4 31B on vLLM: latency, throughput and cost per decision",
        status="illustrative",
        measured_at=None,
        dataset="30-case LLM evaluation set (spec 12)",
        versions={"vllm": "0.31.0", "model": "google/gemma-4-31b-it"},
        config={"dtype": "bfloat16", "max_model_len": 16384, "temperature": 0.2},
        results=[
            s.BenchmarkResult(
                metric="latency_p50",
                label="Latency p50",
                device="mi300x",
                phase="generation",
                value=m(3900.0, "milliseconds", ILLUSTRATIVE),
                concurrency=1,
                batch_size=None,
                protocol=None,
            ),
            s.BenchmarkResult(
                metric="latency_p95",
                label="Latency p95",
                device="mi300x",
                phase="generation",
                value=m(9100.0, "milliseconds", ILLUSTRATIVE),
                concurrency=1,
                batch_size=None,
                protocol=None,
            ),
            s.BenchmarkResult(
                metric="throughput",
                label="Output tokens per second, batch 32",
                device="mi300x",
                phase="generation",
                value=m(1450.0, "tokens_per_second", ILLUSTRATIVE),
                concurrency=32,
                batch_size=32,
                protocol=None,
            ),
            s.BenchmarkResult(
                metric="cost_per_decision",
                label="Cost per decision, idle included",
                device="mi300x",
                phase="cost",
                value=m(0.01, "eur", ILLUSTRATIVE),
                concurrency=32,
                batch_size=32,
                protocol=None,
            ),
        ],  # fmt: skip
        notes=["Illustrative values for the UI; replaced by the measured run (US-902)"],
    )
    pending = [
        s.Benchmark(
            id=i,
            title=t,
            status="pending",
            measured_at=None,
            dataset=ds,
            versions={},
            config={},
            results=[],
            notes=["Not run yet"],
        )
        for i, t, ds in PENDING_BENCHMARKS
    ]
    benchmarks = s.BenchmarksResponse(items=sorted([b1, b3, *pending], key=lambda b: b.id))

    def sample(minute: int, util: float, mem: float) -> s.GpuSample:
        return s.GpuSample(
            sampled_at=at(date(2026, 10, 13), 21, minute),
            utilization=m(util, "percent", ILLUSTRATIVE),
            memory_used=m(mem, "gib", ILLUSTRATIVE),
            memory_total=m(192.0, "gib", ILLUSTRATIVE),
            power=m(round(180 + util * 5, 1), "watts", ILLUSTRATIVE),
        )

    gpu_load = ((12.0, 118.4), (64.0, 141.2), (88.0, 147.9), (71.0, 143.0))
    history = [sample(i, u, g) for i, (u, g) in enumerate(gpu_load)]
    live = s.TelemetryResponse(
        mode="live",
        sampled_at=history[-1].sampled_at,
        device="AMD Instinct MI300X",
        gpu=history[-1],
        gpu_history=history,
        llm=s.LlmStats(
            window_s=300,
            requests=m(64, "count", ILLUSTRATIVE),
            latency_p50=m(3900.0, "milliseconds", ILLUSTRATIVE),
            latency_p95=m(9100.0, "milliseconds", ILLUSTRATIVE),
            tokens_per_second=m(1450.0, "tokens_per_second", ILLUSTRATIVE),
        ),
        api_latency_p95=m(84.0, "milliseconds", ILLUSTRATIVE),
        note=None,
    )
    cached = s.TelemetryResponse(
        mode="cached",
        sampled_at=AS_OF,
        device=None,
        gpu=None,
        gpu_history=[],
        llm=None,
        api_latency_p95=None,
        note="GPU off: cached mode serves results precomputed on the AMD MI300X",
    )
    versions: dict[str, Any] = {
        "contract_version": s.API_CONTRACT_VERSION,
        "rules_version": "policy-2026-10-12",
        "assumptions_version": ASSUMPTIONS_V1,
        "model_versions": MODEL_VERSIONS,
    }

    def check_(
        c: s.ComponentName,
        st: Literal["ok", "unavailable", "disabled"],
        detail: str | None = None,
    ) -> s.ComponentCheck:
        return s.ComponentCheck(component=c, status=st, detail=detail)

    ready_live = s.ReadyzResponse(
        status="ready",
        mode="live",
        llm_mode="vllm",
        device="gpu",
        label=None,
        checks=[check_("database", "ok"), check_("scores", "ok"), check_("models", "ok"),
                check_("vllm", "ok"), check_("demo_cache", "disabled"), check_("telemetry", "ok")],
        **versions,
    )  # fmt: skip
    ready_cached = s.ReadyzResponse(
        status="ready",
        mode="cached",
        llm_mode="mock",
        device="cpu",
        label=s.CACHED_LABEL,
        checks=[check_("database", "ok"), check_("scores", "ok"), check_("models", "ok"),
                check_("vllm", "disabled", "GPU off in cached mode"), check_("demo_cache", "ok"),
                check_("telemetry", "disabled")],
        **versions,
    )  # fmt: skip
    return {
        "healthz_response": s.HealthzResponse(status="ok"),
        "readyz_response.live": ready_live,
        "readyz_response.cached": ready_cached,
        "benchmarks": benchmarks,
        "telemetry.live": live,
        "telemetry.cached": cached,
    }


def query_examples() -> dict[str, BaseModel]:
    return {
        "kpi_query": s.KpiQuery(week_start=date(2026, 9, 21)),
        "audit_query": s.AuditQuery(return_id="RET-000102", format="csv"),
        "outbox_query": s.OutboxQuery(return_id="RET-000102"),
        "anomalies_query": s.AnomaliesQuery(status=["open", "task_opened"]),
    }


def error_examples() -> dict[str, BaseModel]:
    return {
        "error.not_found": s.ErrorResponse(
            code="not_found", message="Return RET-999999 not found", request_id="req-000121"
        ),
        "error.stale_decision": s.ErrorResponse(
            code="stale_decision",
            message="DEC-000104-1 is not the latest version of RET-000104: DEC-000104-2 is",
            request_id="req-000122",
        ),
        "error.invalid_request": s.ErrorResponse(
            code="invalid_request",
            message="The request body is invalid",
            violations=[
                s.ViolationView(
                    code="value_error",
                    message="body: override needs override_reason and override_category",
                )
            ],
            request_id="req-000123",
        ),
    }


# ---- Assembly -----------------------------------------------------------------------------------


def build() -> tuple[Files, list[dict[str, Any]]]:
    cases = [giulia(), marco(), rossi(False), rossi(True), elena(), chiara(), insufficient()]
    cases += [simple_case(c) for c in SIMPLE]
    by_key = {c.key: c for c in cases}
    files: Files = {f"case_card.{c.key}": c.card for c in cases}
    files.update(queue_examples(cases))
    approve_request, approve_result, _ = marco_approval(by_key["marco"])
    files["decision_request.marco_approve"] = approve_request
    files["decision_result.marco_approved"] = approve_result
    override_request, override_result = override_example(by_key["martina"])
    files["decision_request.override"] = override_request
    files["decision_result.override"] = override_result
    reject_request, reject_result = reject_example(by_key["andrea"])
    files["decision_request.reject"] = reject_request
    files["decision_result.reject"] = reject_result
    lift_request, lift_result = lift_example(by_key["lorenzo"])
    files["decision_request.lift"] = lift_request
    files["decision_result.lift"] = lift_result
    guard_request, guard_error = legal_guard_error(by_key["elena"])
    files["decision_request.elena_shipping"] = guard_request
    files["error.legal_guard"] = guard_error
    files.update(error_examples())
    regen_request, regen_response = regenerate_example(by_key["giulia"])
    files["regenerate_request"] = regen_request
    files["regenerate_response"] = regen_response
    evidence_request, evidence_response = evidence_example(by_key["rossi_card"])
    files["evidence_request.rossi_card"] = evidence_request
    files["recompute_response.rossi_card"] = evidence_response
    dispute_request, dispute_response = dispute_example(by_key["chiara"])
    files["link_dispute_request"] = dispute_request
    files["link_dispute_response"] = dispute_response
    assert dispute_response.recompute is not None
    chiara_v1, lorenzo_v1 = by_key["chiara"].card, by_key["lorenzo"].card
    rossi_v1 = by_key["rossi_card"].card
    chiara_v2, rossi_v2 = dispute_response.recompute.decision, evidence_response.decision
    lorenzo_v2 = lift_result.decision
    files.update(
        decision_records(
            [
                ("chiara_v1", chiara_v1.decision, chiara_v1.alternatives, "DEC-000106-2"),
                ("chiara_v2", chiara_v2, recompute_alternatives(chiara_v2), None),
                ("lorenzo_v1", lorenzo_v1.decision, lorenzo_v1.alternatives, "DEC-000088-2"),
                (
                    "lorenzo_v2",
                    lorenzo_v2,
                    simple_alternatives(SIMPLE_BY_KEY["lorenzo"], lorenzo_v2),
                    None,
                ),
                (
                    "rossi_card_v1",
                    rossi_v1.decision,
                    rossi_v1.alternatives,
                    "DEC-000104-2",
                ),
                ("rossi_card_v2", rossi_v2, recompute_alternatives(rossi_v2), None),
            ]
        )
    )
    files.update(checkout_examples(by_key["giulia"], approve_result.decision))
    files.update(inspection_examples(by_key["valentina"], by_key["simone"]))
    files["portfolio_matrix"] = matrix(cases)
    files.update(simulator_examples())
    files["kpi"] = kpi()
    files["fairness"] = fairness()
    files["audit_page"] = audit_page(
        by_key["marco"].card.decision,
        (approve_result, override_result, reject_result, lift_result),
        (
            (by_key["rossi_card"].card.decision, evidence_response.decision),
            (by_key["chiara"].card.decision, dispute_response.recompute.decision),
        ),
    )
    confirm = files["inspection_confirm_response"]
    assert isinstance(confirm, s.InspectionConfirmResponse) and confirm.outbox_message
    messages = [
        lift_result.outbox_message,
        reject_result.outbox_message,
        override_result.outbox_message,
        confirm.outbox_message,
        approve_result.outbox_message,
    ]
    files["outbox"] = s.OutboxResponse(items=messages, total=len(messages))
    files.update(anomalies_and_tasks())
    files.update(coupon_examples(approve_result.decision))
    files.update(agent_examples())
    files.update(ops_examples())
    files.update(query_examples())
    stream = files["agent_stream.marco_link"]
    assert isinstance(stream, s.AgentStream)
    files["agent_stream.marco_link.sse"] = "".join(s.sse_frame(e) for e in stream.root)
    return files, mock_routes(cases)


def mock_routes(cases: Sequence[Case]) -> list[dict[str, Any]]:
    """The mock server table: which example answers which request (index.json). A route is
    chosen by method, concrete path, query example, body example and scenario: "default" unless
    the request asks for another one (for example with an X-Mock-Scenario header)."""

    def r(
        method: str,
        endpoint: str,
        response: str,
        path: str | None = None,
        request: str | None = None,
        status: int = 200,
        query: str | None = None,
        scenario: str = "default",
    ) -> dict[str, Any]:
        ep = next(e for e in s.ENDPOINTS if (e.method, e.path) == (method, endpoint))
        return {
            "method": method,
            "endpoint": endpoint,
            "path": path or endpoint,
            "query": query,
            "request": request,
            "scenario": scenario,
            "status": status,
            "media_type": ep.media_type,
            "response": response,
        }

    routes = [
        r("GET", "/healthz", "healthz_response.json"),
        r("GET", "/readyz", "readyz_response.live.json"),
        r("GET", "/readyz", "readyz_response.cached.json", scenario="cached"),
        r("GET", "/api/returns", "queue_response.json"),
        r("GET", "/api/returns", "queue_response.approval.json", query="queue_query.json"),
        r("GET", "/api/returns", "queue_response.review_due.json",
          query="queue_query.review_due.json"),
        *[
            r("GET", "/api/returns/{return_id}", f"case_card.{c.key}.json",
              f"/api/returns/{c.card.return_id}")
            for c in sorted(cases, key=lambda c: c.card.return_id)
        ],
        r("GET", "/api/returns/{return_id}", "error.not_found.json", "/api/returns/RET-999999",
          status=404),
        r("POST", "/api/returns/{return_id}/decision", "decision_result.marco_approved.json",
          "/api/returns/RET-000102/decision", "decision_request.marco_approve.json"),
        r("POST", "/api/returns/{return_id}/decision", "decision_result.override.json",
          "/api/returns/RET-000108/decision", "decision_request.override.json"),
        r("POST", "/api/returns/{return_id}/decision", "decision_result.reject.json",
          "/api/returns/RET-000107/decision", "decision_request.reject.json"),
        r("POST", "/api/returns/{return_id}/decision", "decision_result.lift.json",
          "/api/returns/RET-000088/decision", "decision_request.lift.json"),
        r("POST", "/api/returns/{return_id}/decision", "error.legal_guard.json",
          "/api/returns/RET-000105/decision", "decision_request.elena_shipping.json", 422),
        r("POST", "/api/returns/{return_id}/decision", "error.invalid_request.json",
          "/api/returns/RET-000111/decision", None, 422, scenario="invalid_request"),
        *[
            r("GET", "/api/decisions/{decision_id}", f"decision_record.{key}.json",
              f"/api/decisions/{decision_id}")
            for key, decision_id in (
                ("lorenzo_v1", "DEC-000088-1"), ("lorenzo_v2", "DEC-000088-2"),
                ("rossi_card_v1", "DEC-000104-1"), ("rossi_card_v2", "DEC-000104-2"),
                ("chiara_v1", "DEC-000106-1"), ("chiara_v2", "DEC-000106-2"),
            )
        ],
        r("POST", "/api/returns/{return_id}/regenerate", "regenerate_response.json",
          "/api/returns/RET-000101/regenerate", "regenerate_request.json"),
        r("POST", "/api/returns/{return_id}/evidence", "recompute_response.rossi_card.json",
          "/api/returns/RET-000104/evidence", "evidence_request.rossi_card.json"),
        r("POST", "/api/returns/{return_id}/evidence", "error.stale_decision.json",
          "/api/returns/RET-000104/evidence", "evidence_request.rossi_card.json", 409,
          scenario="stale_decision"),
        r("POST", "/api/links/{link_id}/dispute", "link_dispute_response.json",
          f"/api/links/{ESPOSITO_LINK}/dispute", "link_dispute_request.json"),
        r("POST", "/api/checkout/evaluate", "checkout_evaluate_response.giulia.json",
          request="checkout_evaluate_request.giulia.json"),
        r("POST", "/api/checkout/evaluate", "checkout_evaluate_response.marco.json",
          request="checkout_evaluate_request.marco.json"),
        r("POST", "/api/checkout/confirm", "checkout_confirm_response.giulia.json",
          request="checkout_confirm_request.giulia.json", status=201),
        r("POST", "/api/inspections", "inspection.awaiting.json",
          request="inspection_create_request.json", status=201),
        r("POST", "/api/inspections", "inspection.manual_review.json",
          request="inspection_create_request.manual_review.json", status=201),
        r("POST", "/api/inspections/{inspection_id}/confirm", "inspection_confirm_response.json",
          "/api/inspections/INS-000014/confirm", "inspection_confirm_request.json"),
        r("GET", "/api/portfolio/matrix", "portfolio_matrix.json"),
        r("GET", "/api/simulator/assumptions", "simulator_assumptions.v1.json"),
        r("PUT", "/api/simulator/assumptions", "simulator_assumptions.v2.json",
          request="simulator_assumptions_update.json"),
        r("GET", "/api/kpi", "kpi.json", query="kpi_query.json"),
        r("GET", "/api/fairness", "fairness.json"),
        r("GET", "/api/audit", "audit_page.json"),
        r("GET", "/api/outbox", "outbox.json"),
        r("GET", "/api/products/anomalies", "product_anomalies.json"),
        r("GET", "/api/catalog-tasks", "catalog_tasks.json"),
        r("POST", "/api/catalog-tasks", "catalog_task.elena.json",
          request="catalog_task_create.json", status=201),
        r("PATCH", "/api/catalog-tasks/{task_id}", "catalog_task.in_progress.json",
          "/api/catalog-tasks/CT-000012", "catalog_task_update.json"),
        r("GET", "/api/coupon-rules", "coupon_rules.json"),
        r("POST", "/api/coupon-rules", "coupon_rules.after_create.json",
          request="coupon_rule_create.json", status=201),
        r("POST", "/api/coupon-rules/applications/{application_id}/reverse",
          "coupon_rule_application.reversed.json",
          "/api/coupon-rules/applications/CRA-000027/reverse", "coupon_application_reverse.json"),
        *[
            r("POST", "/api/agent/ask", f"agent_stream.{k}.json",
              request=f"agent_ask_request.{k.replace('_refused', '')}.json")
            for k in ("marco_link", "marco_rossi", "marco_terms", "write_refused")
        ],
        r("GET", "/api/benchmarks", "benchmarks.json"),
        r("GET", "/api/telemetry", "telemetry.live.json"),
        r("GET", "/api/telemetry", "telemetry.cached.json", scenario="cached"),
    ]  # fmt: skip
    return routes


def _text(item: BaseModel | str) -> str:
    if isinstance(item, str):
        return item
    return json.dumps(item.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def render() -> dict[str, str]:
    """Every file of contracts/examples/api/, by file name."""
    files, routes = build()
    out = {
        (name if name.endswith(".sse") else f"{name}.json"): _text(item)
        for name, item in files.items()
    }
    index = {
        "$comment": "Generated by scripts/build_api_examples.py: do not edit. Mock server table "
        "of the UI: which example answers which request (ADR-15). A route is chosen by method, "
        "path, query, request body and scenario; the scenario is 'default' unless the request "
        "names another one in the X-Mock-Scenario header.",
        "as_of": AS_OF.isoformat().replace("+00:00", "Z"),
        "routes": routes,
    }
    out["index.json"] = json.dumps(index, indent=2, ensure_ascii=False) + "\n"
    return out


def write(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = render()
    for stale in out.iterdir():
        if stale.is_file() and stale.name not in files:
            stale.unlink()
    paths = []
    for name, text in files.items():
        path = out / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


def check_files(out: Path = OUT) -> list[str]:
    files = render()
    found = {p.name for p in out.iterdir() if p.is_file()} if out.exists() else set()
    problems = [f"stale: {name}" for name in sorted(found - set(files))]
    for name, text in files.items():
        path = out / name
        if not path.exists():
            problems.append(f"missing: {name}")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"differs: {name}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the golden API examples (ADR-15)")
    parser.add_argument("--check", action="store_true", help="fail if files are out of date")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.check:
        problems = check_files(args.out)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print("run: python -m scripts.build_api_examples", file=sys.stderr)
        return 1 if problems else 0
    for path in write(args.out):
        print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
