"""Customer behaviour features at a decision time (spec 8.4 inputs; rules 4, 6, 7).

One row per account known at T (created and recorded by T), from a StoreSnapshot at T and its
line_frame. Rates and shares are missing (null), not zero, when there is nothing to divide by: a
new account is not a customer who never returns.

- n_orders, n_lines, n_delivered_lines: what is known at T;
- n_observations, return_rate: settled lines (return window closed, see pipeline.features.lines)
  and the share returned on the withdrawal path;
- n_returns, return_rate_to_date: withdrawal-path returns known at T, over the delivered lines (a
  lower bound of the final rate, available before the windows close);
- size_share, changed_mind_share, not_as_expected_share, other_reason_share, median_days_to_return:
  over the withdrawal-path returns known at T;
- tenure_days: days since the account was opened;
- n_welcome_coupons_used, n_promo_coupons_used (promo and referral), coupon_order_share: coupon
  redemptions known at T;
- weekend_order_share: orders placed on Saturday or Sunday (UTC);
- mean_line_price_eur: mean price of the lines ordered.

Non-conformity returns (defective, damaged in shipping, wrong item, not as described) follow the
guarantee path and enter none of these features (rule 4). No customer value is computed here: it
never feeds risk (rule 6, pipeline.features.value).
"""

from __future__ import annotations

from typing import Final

import numpy as np
import pandas as pd

from pipeline.features.snapshot import StoreSnapshot
from policy.types import ReturnReason

REASON_SHARES: Final = {
    "size_share": ReturnReason.SIZE.value,
    "changed_mind_share": ReturnReason.CHANGED_MIND.value,
    "not_as_expected_share": ReturnReason.NOT_AS_EXPECTED.value,
    "other_reason_share": ReturnReason.OTHER.value,
}
CUSTOMER_FEATURE_COLUMNS: Final = (
    "n_orders",
    "n_lines",
    "n_delivered_lines",
    "n_observations",
    "n_returns",
    "return_rate",
    "return_rate_to_date",
    *REASON_SHARES,
    "median_days_to_return",
    "tenure_days",
    "n_welcome_coupons_used",
    "n_promo_coupons_used",
    "coupon_order_share",
    "weekend_order_share",
    "mean_line_price_eur",
)
_COUNTS: Final = (
    "n_orders",
    "n_lines",
    "n_delivered_lines",
    "n_observations",
    "n_returns",
    "n_welcome_coupons_used",
    "n_promo_coupons_used",
)
DAY: Final = pd.Timedelta(days=1)


def _ratio(num: pd.Series, den: pd.Series) -> pd.Series:
    return (num / den.where(den > 0)).astype(np.float64)


def behaviour_features(snap: StoreSnapshot, lines: pd.DataFrame) -> pd.DataFrame:
    """CUSTOMER_FEATURE_COLUMNS per customer known at snap.as_of; `lines` is line_frame(snap)."""
    as_of = pd.Timestamp(snap.as_of)
    accounts = snap["store/customers"].set_index("customer_id")["event_at"].sort_index()
    out = pd.DataFrame(index=accounts.index)

    orders = snap["store/orders"]
    by_order = orders.groupby("customer_id")
    out["n_orders"] = by_order.size()
    weekend = orders["event_at"].dt.dayofweek >= 5
    out["weekend_orders"] = weekend.groupby(orders["customer_id"]).sum()
    out["coupon_orders"] = orders["coupon_code"].notna().groupby(orders["customer_id"]).sum()

    by_line = lines.groupby("customer_id")
    out["n_lines"] = by_line.size()
    out["n_delivered_lines"] = by_line["delivered"].sum()
    out["n_observations"] = by_line["observed"].sum()
    out["observed_returns"] = by_line["returned"].sum()
    out["n_returns"] = by_line["withdrawal_return"].sum()
    out["nonconformity_returns"] = by_line["nonconformity_return"].sum()
    out["price_cents"] = by_line["price_cents"].mean()

    withdrawal = lines.loc[lines["withdrawal_return"]]
    by_return = withdrawal.groupby("customer_id")
    for column, reason in REASON_SHARES.items():
        out[f"_{column}"] = (
            (withdrawal["reason"] == reason).groupby(withdrawal["customer_id"]).sum()
        )
    out["median_days_to_return"] = by_return["days_since_delivery"].median()

    coupons = snap["store/coupons"]
    used = coupons.loc[coupons["order_id"].notna()]
    welcome = used["kind"] == "welcome"
    out["n_welcome_coupons_used"] = welcome.groupby(used["customer_id"]).sum()
    out["n_promo_coupons_used"] = (~welcome).groupby(used["customer_id"]).sum()

    counts = [*_COUNTS, "weekend_orders", "coupon_orders", "observed_returns"]
    counts += ["nonconformity_returns", *(f"_{c}" for c in REASON_SHARES)]
    out[counts] = out[counts].fillna(0).astype(np.int64)

    out["return_rate"] = _ratio(out["observed_returns"], out["n_observations"])
    judged = out["n_delivered_lines"] - out["nonconformity_returns"]
    out["return_rate_to_date"] = _ratio(out["n_returns"], judged)
    for column in REASON_SHARES:
        out[column] = _ratio(out[f"_{column}"], out["n_returns"])
    out["median_days_to_return"] = out["median_days_to_return"].astype(np.float64)
    out["tenure_days"] = ((as_of - accounts) / DAY).astype(np.float64)
    out["coupon_order_share"] = _ratio(out["coupon_orders"], out["n_orders"])
    out["weekend_order_share"] = _ratio(out["weekend_orders"], out["n_orders"])
    out["mean_line_price_eur"] = (out["price_cents"] / 100.0).astype(np.float64)
    out.index.name = "customer_id"
    return out[list(CUSTOMER_FEATURE_COLUMNS)].reset_index()
