"""Fixed parameters of the Navigli Fashion generator (US-102, US-105; docs/synthetic-store.md).

Every value here is a design hypothesis of the project. The only numbers measured on real data
come from the calibration file (ASOS GraphReturns aggregates,
contracts/json/calibration.schema.json). All instants are UTC and are handled as integer
microseconds since the Unix epoch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final, Literal

Scenario = Literal["main", "ood"]
SCENARIO_NAMES: Final = ("main", "ood")

# ---- Period: 12 months, October 2025 to September 2026 (spec section 7) ------------------------

PERIOD_START: Final = datetime(2025, 10, 1, tzinfo=UTC)
# Exclusive end: the store is extracted at this instant, so a row exists only if it was available
# before it (a delivery, a return or a state that happens later is not in the data yet).
PERIOD_END: Final = datetime(2026, 10, 1, tzinfo=UTC)
PERIOD_FIRST_DAY: Final = PERIOD_START.date()
PERIOD_LAST_DAY: Final = (PERIOD_END - timedelta(days=1)).date()
# A line ordered at least this many days before the extraction has all its returns in the data:
# return rates (DATA-02) are measured on these mature lines.
MATURITY_DAYS: Final = 60

US_PER_SECOND: Final = 1_000_000
US_PER_MINUTE: Final = 60 * US_PER_SECOND
US_PER_HOUR: Final = 60 * US_PER_MINUTE
US_PER_DAY: Final = 24 * US_PER_HOUR


def to_us(moment: datetime) -> int:
    """Microseconds since the epoch of an aware datetime."""
    if moment.tzinfo is None:
        raise ValueError("instants must be timezone-aware")
    delta = moment - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * US_PER_SECOND + delta.microseconds


START_US: Final = to_us(PERIOD_START)
END_US: Final = to_us(PERIOD_END)

# ---- Provenance (rule 11, NFR-16) ---------------------------------------------------------------

BASE_ORIGIN: Final = "Navigli Fashion generator, calibrated on ASOS GraphReturns aggregates"
# Prices of the calibration are in GBP. Constant conversion at about the ECB reference rate of the
# ASOS data period (September to November 2021: EUR/GBP about 0.85, so 1 GBP is about 1.17 EUR).
GBP_TO_EUR: Final = 1.17
MIN_PRICE_CENTS: Final = 100

# ---- Volumes at scale 1.0 (spec section 7) ------------------------------------------------------

CUSTOMERS_AT_SCALE_1: Final = 20_000
VARIANTS_AT_SCALE_1: Final = 5_000
MIN_CUSTOMERS: Final = 100  # room for the golden cases at any scale
MIN_VARIANTS: Final = 40
LINES_PER_CUSTOMER_YEAR: Final = 10.0  # about 200,000 lines for 20,000 customers
EXTRA_LINES_MEAN: Final = 1.5  # lines per order = 1 + Poisson(1.5), about 2.5 on average
MAX_LINES_PER_ORDER: Final = 8
NEW_ACCOUNT_SHARE: Final = 0.35  # accounts created during the period
OLD_ACCOUNT_YEARS: Final = 4  # older accounts are created up to 4 years before the period


def customers_at(scale: float) -> int:
    """Number of customers of a store at `scale`."""
    return max(MIN_CUSTOMERS, round(CUSTOMERS_AT_SCALE_1 * scale))


def expected_weekly_lines(scale: float) -> float:
    """Average order lines a week of the whole store at `scale` (before seasonality)."""
    return LINES_PER_CUSTOMER_YEAR * customers_at(scale) / 52.0


# ---- Behaviours (spec section 7 table; ground truth in truth/labels) ----------------------------

BEHAVIOUR_SHARES: Final = MappingProxyType(
    {"size": 0.12, "wardrober": 0.03, "promo_ring": 0.015, "shared_benign": 0.035}
)  # normal: the rest, 80%
DEFECTIVE_VARIANT_SHARE: Final = 0.02
# A defective batch sells strongly in the first weeks of its window (review G6): about this many
# sales a week, so that the weekly detector of spec 8.9 (at least 20 sales a week, z > 3) can see
# it. Lines of the same product type switch to the variant, so type sales shares do not move.
DEFECT_WEEKLY_SALES: Final = 28
DEFECT_BOOST_WEEKS: Final = 4
# At most this share of a type's lines in a week switch to one surging variant; so a variant is
# eligible for a defect only if its type sells DEFECT_WEEKLY_SALES / DEFECT_MAX_TYPE_SHARE lines a
# week on average (all types at scale 1.0 with the real calibration; fewer at small scales).
DEFECT_MAX_TYPE_SHARE: Final = 0.5
# Matched controls (review P1): for each defective variant, this many non-defective best sellers
# of the same pool get the same DEFECT_BOOST_WEEKS-week surge at a random Monday. Without them a
# sales surge, or reaching the spec 8.9 minimum at all, would identify a defect (94% of the
# variants that ever sold 20 in a week were defective at commit 472d798).
SURGE_CONTROLS_PER_DEFECT: Final = 4

# Return probability of a line for a non-abusive customer: k_type x base_return_rate x propensity,
# with the propensity drawn from Gamma(shape, 1/shape) (mean 1) and k fitted per product type so
# that the store matches the calibration once the abusive behaviours are included.
PROPENSITY_SHAPE: Final = 6.0
MAX_LINE_RETURN_PROBABILITY: Final = 0.95
WARDROBE_RETURN_PROBABILITY: Final = 0.80  # high-value lines of a wardrober
# A tendency, not a rule (review G2): this share of wardrober orders moves to the Tuesday,
# Wednesday or Thursday of its week, so the items are in hand for the weekend; the other orders
# keep their calendar day, and every order has the shop's delivery lag.
WARDROBE_MIDWEEK_SHARE: Final = 0.55
BRACKETING_ORDER_SHARE: Final = 0.5  # orders of a size customer with two sizes of one item
LOST_PARCEL_SHARE: Final = 0.003  # orders never delivered

# Welcome coupon of the Navigli base policy (spec section 4): 15% on the first order, one each.
WELCOME_PERCENT: Final = 15
WELCOME_REDEEM_PROBABILITY: Final = 0.55  # new accounts outside rings
REFERRAL_SHARE: Final = 0.03  # new accounts with a referral coupon (10 EUR on the second order)
PROMO_SHARE: Final = 0.08  # accounts with a personal Black Friday code (10 EUR)
FIXED_COUPON_CENTS: Final = 1_000
BLACK_FRIDAY_CODES_AT: Final = datetime(2025, 11, 24, 8, 0, tzinfo=UTC)
BLACK_FRIDAY_DAYS: Final = 8

# Hard situations (spec section 7): pickup points and collective addresses shared by many
# unrelated customers; counts at scale 1.0, sizes as a share of the customers.
PICKUP_POINTS_AT_SCALE_1: Final = 10
PICKUP_SIZE_SHARE: Final = (0.0025, 0.0125)  # 50 to 250 customers each at scale 1.0
PICKUP_MIN_SIZE: Final = (15, 30)
COLLECTIVE_ADDRESSES_AT_SCALE_1: Final = 30
COLLECTIVE_SIZE: Final = (4, 14)

# Golden defective jacket: share of all sales, so that its anomaly is visible at scale 1.0.
JACKET_SALES_SHARE: Final = 0.006


@dataclass(frozen=True)
class ScenarioConfig:
    """What differs between the main store and the out-of-distribution store (US-105)."""

    name: Scenario
    index: int  # separates the random streams of the scenarios for the same seed
    id_prefix: str
    golden: bool
    rings_at_scale_1: int
    ring_size: tuple[int, int]
    ring_creation_spread_days: tuple[int, int]
    ring_activity_days: int
    ring_orders: tuple[int, int]
    ring_return_probability: float
    ring_welcome_redeem: float
    # Probability that a ring account uses the ring's shared value of each kind ("with noise").
    ring_sharing: MappingProxyType[str, float]
    ring_requires_strong_link: bool  # every account shares a card, e-mail or device
    # Probability that an account has no identifier of a kind at all (OOD: missing identifiers).
    missing: MappingProxyType[str, float]
    family_card_share: float  # share of generic families where two accounts share a card
    hard_family: bool  # OOD: a family that looks like a ring (new accounts, coupons, one card)
    hard_family_share: float  # generic families created together, each with a welcome coupon


MAIN = ScenarioConfig(
    name="main",
    index=0,
    id_prefix="",
    golden=True,
    rings_at_scale_1=40,
    ring_size=(5, 9),
    ring_creation_spread_days=(4, 14),
    ring_activity_days=60,
    ring_orders=(2, 3),
    ring_return_probability=0.95,
    ring_welcome_redeem=1.0,
    ring_sharing=MappingProxyType(
        {"device": 0.8, "address": 0.7, "card": 0.65, "email": 0.45, "phone": 0.3, "ip_prefix": 0.8}
    ),
    ring_requires_strong_link=True,
    missing=MappingProxyType(
        {"phone": 0.12, "device": 0.0, "card": 0.0, "ip_prefix": 0.0, "email": 0.0, "address": 0.0}
    ),
    family_card_share=0.15,
    hard_family=False,
    hard_family_share=0.0,
)

OOD = ScenarioConfig(
    name="ood",
    index=1,
    id_prefix="OOD-",
    golden=False,
    rings_at_scale_1=75,  # smaller rings, so more of them for the same 1.5% share
    ring_size=(3, 5),
    ring_creation_spread_days=(45, 120),
    ring_activity_days=240,
    ring_orders=(2, 5),
    ring_return_probability=0.72,
    ring_welcome_redeem=0.7,
    ring_sharing=MappingProxyType(
        {"device": 0.45, "address": 0.4, "card": 0.3, "email": 0.1, "phone": 0.15, "ip_prefix": 0.5}
    ),
    ring_requires_strong_link=False,
    missing=MappingProxyType(
        {"phone": 0.4, "device": 0.3, "card": 0.25, "ip_prefix": 0.2, "email": 0.0, "address": 0.0}
    ),
    family_card_share=0.6,
    hard_family=True,
    hard_family_share=0.15,
)

SCENARIOS: Final = MappingProxyType({"main": MAIN, "ood": OOD})
