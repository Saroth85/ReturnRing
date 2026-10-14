"""The price paid for an order line (ADR-15 section 15, H2; rules 3 and 9).

A coupon lowers what the customer paid for an order (store/orders.total_eur) below the sum of
the list prices of its lines (store/order_lines.price_eur). A refund, and a diminished-value
claim on it, are on what was paid for the returned line: its list price less its share of the
order's discount, pro rata of the list prices, to the cent (ROUND_HALF_UP). A line of an order
without a discount is paid at its list price.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from apps.api import schemas as s
from apps.api.services.provenance import provenance
from apps.api.serving import Serving

CENT: Final = Decimal("0.01")
_CACHE: Final = "_paid_line_prices"  # kept with the snapshot: it never changes once loaded
PAID_ORIGIN: Final = (
    "price paid for the returned line: its list price less its share of the order's coupon "
    "(store/order_lines, store/orders)"
)


def _discounted(serving: Serving) -> dict[str, Decimal]:
    """line_id -> price paid, for the lines of the orders paid below their list prices."""
    cache = vars(serving)
    found = cache.get(_CACHE)
    if isinstance(found, dict):
        return found
    lines = serving.frame("store/order_lines")
    orders = serving.frame("store/orders")
    totals = {
        str(o): Decimal(str(t))
        for o, t in zip(orders["order_id"], orders["total_eur"], strict=True)
    }
    by_order: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for line_id, order_id, price in zip(
        lines["line_id"], lines["order_id"], lines["price_eur"], strict=True
    ):
        by_order[str(order_id)].append((str(line_id), Decimal(str(price))))
    paid: dict[str, Decimal] = {}
    for order_id, items in by_order.items():
        listed = sum((p for _, p in items), Decimal(0))
        total = totals.get(order_id)
        if total is None or listed <= 0 or total == listed:
            continue
        for line_id, price in items:
            paid[line_id] = (price * total / listed).quantize(CENT, rounding=ROUND_HALF_UP)
    cache[_CACHE] = paid
    return paid


def paid_price(serving: Serving, line_id: str) -> Decimal:
    """What the customer paid for the line (its list price without a discount); 0 for a line
    the store does not hold."""
    found = _discounted(serving).get(line_id)
    if found is not None:
        return found
    line = serving.by_key("store/order_lines", line_id) or {}
    return Decimal(str(line.get("price_eur", 0)))


def paid_provenance(day: date) -> s.Provenance:
    return provenance(PAID_ORIGIN, day, day, "synthetic")
