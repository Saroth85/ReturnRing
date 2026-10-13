"""Order lines at a decision time and their settled outcomes (spec 8.1, 8.2; rules 4 and 7).

line_frame(snapshot) has one row per order line known at snapshot.as_of, with the facts known then:
order, customer, catalogue attributes, order context, delivery and return request.

Settlement. The withdrawal outcome of a line is final once its return window has closed (the
window of the order's terms snapshot, ADR-05), plus one day of slack for the request to be
recorded:

    settled_at = max(delivered_at + return_window_days + 1 day, delivery available_at)

The outcome is read at settled_at, from the facts available by then:

- returned: a withdrawal-path return request available by settled_at;
- excluded: a non-conformity return (defective, damaged in shipping, wrong item, not as described)
  available by settled_at. It follows the guarantee path and says nothing about the customer's
  behaviour (rule 4, ADR-09), so the line is not an observation.

A line is observed at T when settled_at <= T and it is not excluded. Every fact behind settled_at
and the outcome is available at settled_at, so the observations at T are the same in any snapshot
taken at or after T (MOD-08). A line delivered yesterday is not an observation yet: counting it
as kept would bias return rates and residuals downwards.

line_history gives, for query rows (t, customer, variant, product, category), the observed lines
and their returns with settled_at <= t in each group, leaving the query's customer out (rule 8),
so a feature can be computed at a different instant per row: a return request is scored at its
own request time.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from pipeline.features.snapshot import StoreSnapshot
from policy.types import NON_CONFORMITY_REASONS

NON_CONFORMITY: Final = frozenset(r.value for r in NON_CONFORMITY_REASONS)
SETTLEMENT_SLACK: Final = pd.Timedelta(days=1)
I64 = npt.NDArray[np.int64]


def micros(values: pd.Series) -> I64:
    """UTC instants as int64 microseconds since the epoch (no NaT allowed)."""
    if values.isna().any():
        raise ValueError(f"{values.name}: missing instants")
    utc = values.dt.tz_convert("UTC").dt.as_unit("us")
    return np.asarray(utc.astype("int64"), dtype=np.int64)


def _require(frame: pd.DataFrame, column: str, what: str) -> None:
    missing = int(frame[column].isna().sum())
    if missing:
        raise ValueError(f"{missing} order lines whose {what} is not known at the snapshot")


def line_frame(snap: StoreSnapshot) -> pd.DataFrame:
    """One row per order line known at snap.as_of; see the module docstring for settlement."""
    lines = snap["store/order_lines"].rename(columns={"event_at": "ordered_at"})
    lines = lines.drop(columns="available_at")
    orders = snap["store/orders"][["order_id", "customer_id", "terms_snapshot_id", "coupon_code"]]
    out = lines.merge(orders, on="order_id", how="left", validate="many_to_one")
    _require(out, "customer_id", "order")
    terms = snap["store/terms_snapshots"][["terms_snapshot_id", "return_window_days"]]
    out = out.merge(terms, on="terms_snapshot_id", how="left", validate="many_to_one")
    _require(out, "return_window_days", "terms snapshot")
    variants = snap["store/variants"][["variant_id", "product_id", "category", "size_range"]]
    out = out.merge(variants, on="variant_id", how="left", validate="many_to_one")
    _require(out, "product_id", "catalogue variant")
    deliveries = snap["store/deliveries"].rename(
        columns={"event_at": "delivered_at", "available_at": "delivery_known_at"}
    )
    out = out.merge(deliveries, on="order_id", how="left", validate="many_to_one")
    returns = snap["store/returns"].rename(
        columns={"event_at": "requested_at", "available_at": "return_known_at"}
    )
    out = out.merge(returns, on="line_id", how="left", validate="one_to_one")

    by_order = out.groupby("order_id", sort=False)["line_id"]
    out["order_lines"] = by_order.transform("size").astype(np.int64)
    by_product = out.groupby(["order_id", "product_id"], sort=False)["line_id"]
    out["order_same_product"] = by_product.transform("size").astype(np.int64) - 1

    window = pd.to_timedelta(out["return_window_days"].astype(np.int64), unit="D")
    close = out["delivered_at"] + window + SETTLEMENT_SLACK
    settled_at = close.where(close >= out["delivery_known_at"], out["delivery_known_at"])
    settled = (settled_at <= pd.Timestamp(snap.as_of)).fillna(False).astype(bool)
    known_then = (out["return_known_at"] <= settled_at).fillna(False).astype(bool)
    nonconformity = out["reason"].isin(NON_CONFORMITY)
    has_return = out["return_id"].notna()

    out["delivered"] = out["delivered_at"].notna()
    out["withdrawal_return"] = has_return & ~nonconformity
    out["nonconformity_return"] = has_return & nonconformity
    out["settled_at"] = settled_at.where(settled)
    out["excluded"] = settled & known_then & nonconformity
    out["observed"] = settled & ~out["excluded"]
    out["returned"] = out["observed"] & known_then & ~nonconformity
    return out.sort_values("line_id", kind="stable").reset_index(drop=True)


def observations(lines: pd.DataFrame) -> pd.DataFrame:
    """The observed lines (settled, not excluded): the events of line_history."""
    return lines.loc[lines["observed"]].reset_index(drop=True)


def as_of_counts(
    events: pd.DataFrame, queries: pd.DataFrame, keys: Sequence[str]
) -> tuple[I64, I64]:
    """Per query row: the events with settled_at <= the row's t in the row's group of `keys`
    (every event when keys is empty), and how many of them were returned."""
    ev_t = micros(events["settled_at"]) if len(events) else np.empty(0, dtype=np.int64)
    q_t = micros(queries["t"]) if len(queries) else np.empty(0, dtype=np.int64)
    returned = events["returned"].to_numpy(dtype=bool).astype(np.int64)
    if not keys:
        order = np.argsort(ev_t, kind="stable")
        cum = np.concatenate([[0], np.cumsum(returned[order])])
        idx = np.searchsorted(ev_t[order], q_t, side="right")
        return idx.astype(np.int64), cum[idx].astype(np.int64)
    cols = list(keys)
    # Object keys on both sides: an empty column would otherwise get another dtype than a full one.
    right = pd.DataFrame({k: events[k].to_numpy(dtype=object) for k in cols}).astype(object)
    right["_t"], right["_r"] = ev_t, returned
    right = right.sort_values("_t", kind="stable")
    group = right.groupby(cols, sort=False)
    right["_n"] = group.cumcount().astype(np.int64) + 1
    right["_cr"] = group["_r"].cumsum()
    left = pd.DataFrame({k: queries[k].to_numpy(dtype=object) for k in cols}).astype(object)
    left["_t"], left["_i"] = q_t, np.arange(len(queries))
    left = left.sort_values("_t", kind="stable")
    merged = pd.merge_asof(
        left,
        right[[*cols, "_t", "_n", "_cr"]],
        on="_t",
        by=cols,
        direction="backward",
        allow_exact_matches=True,
    ).sort_values("_i")
    n = merged["_n"].fillna(0).to_numpy(dtype=np.int64)
    r = merged["_cr"].fillna(0).to_numpy(dtype=np.int64)
    return n, r


LEVELS: Final = ("variant_id", "product_id", "category")


def line_history(events: pd.DataFrame, queries: pd.DataFrame) -> pd.DataFrame:
    """As-of history counts per query row (columns t, line_id, customer_id, variant_id,
    product_id, category of `queries`):

    - variant_n/r, product_n/r, category_n/r, all_n/r: observed lines and returns of the group,
      leaving every line of the query's customer out (never the customer's own history, rule 8);
    - customer_n/r: the customer's own observed lines, leaving the query line itself out (for the
      personalized predictor only).
    """
    out = pd.DataFrame(index=queries.index)
    own_n, own_r = as_of_counts(events, queries, ["customer_id"])
    for level in LEVELS:
        n, r = as_of_counts(events, queries, [level])
        mine_n, mine_r = as_of_counts(events, queries, [level, "customer_id"])
        name = level.removesuffix("_id")
        out[f"{name}_n"], out[f"{name}_r"] = n - mine_n, r - mine_r
    all_n, all_r = as_of_counts(events, queries, [])
    out["all_n"], out["all_r"] = all_n - own_n, all_r - own_r
    # The query line itself, when it is an observation at t, is part of its customer's counts.
    if len(events):
        indexed = events.set_index("line_id")
        own_t = queries["line_id"].map(indexed["settled_at"])
        itself = (own_t <= queries["t"]).fillna(False).to_numpy(dtype=bool)
        flag = queries["line_id"].map(indexed["returned"]).eq(True).to_numpy(dtype=bool)
        itself_r = itself & flag
    else:
        itself = itself_r = np.zeros(len(queries), dtype=bool)
    out["customer_n"] = own_n - itself.astype(np.int64)
    out["customer_r"] = own_r - itself_r.astype(np.int64)
    return out
