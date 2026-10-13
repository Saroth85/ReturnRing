"""Return probability of the lines of a cart at checkout (moment M1, spec 8.1; US-401 input).

The checkout uses the personalized predictor: the reference features of each cart line plus the
customer's own settled history at the checkout instant. The cart itself is the order context (lines
in the order, other sizes or colours of the same product), as at training.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.reference.features import QUERY_COLUMNS, history_frame
from ml.reference.model import F64, LineModel
from pipeline.features.lines import line_frame, observations
from pipeline.features.snapshot import StoreSnapshot


@dataclass(frozen=True)
class CartItem:
    variant_id: str
    size: str | None = None
    price_cents: int | None = None  # None: the catalogue price


def cart_queries(snap: StoreSnapshot, customer_id: str, cart: Sequence[CartItem]) -> pd.DataFrame:
    """Query rows of the cart lines at snap.as_of."""
    catalogue = snap["store/variants"].set_index("variant_id")
    unknown = sorted({i.variant_id for i in cart} - set(catalogue.index))
    if unknown:
        raise KeyError(f"variants not in the catalogue at the checkout instant: {unknown}")
    rows = []
    products = [catalogue.at[i.variant_id, "product_id"] for i in cart]
    for n, (item, product) in enumerate(zip(cart, products, strict=True)):
        price = item.price_cents
        rows.append(
            {
                "line_id": f"cart-{n}",
                "customer_id": customer_id,
                "variant_id": item.variant_id,
                "product_id": product,
                "category": catalogue.at[item.variant_id, "category"],
                "size_range": catalogue.at[item.variant_id, "size_range"],
                "price_cents": catalogue.at[item.variant_id, "price_cents"]
                if price is None
                else price,
                "order_lines": len(cart),
                "order_same_product": products.count(product) - 1,
            }
        )
    out = pd.DataFrame(rows, columns=list(QUERY_COLUMNS))
    out["t"] = pd.Timestamp(snap.as_of)
    return out


def checkout_probabilities(
    snap: StoreSnapshot, model: LineModel, customer_id: str, cart: Sequence[CartItem]
) -> F64:
    """p_return of each cart line, from the snapshot at the checkout instant."""
    if not cart:
        return np.zeros(0)
    frame = history_frame(observations(line_frame(snap)), cart_queries(snap, customer_id, cart))
    return model.predict(frame)
