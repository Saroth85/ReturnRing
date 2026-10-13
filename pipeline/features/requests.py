"""Request-level features of a return, at the request's own decision time (spec 8.4; rule 7).

The risk score has two units: the customer (nightly, features/customer_features) and the single
return request (at request time), which adds days since delivery, reason, price and the base
return rate of the item. request_features gives those for every return request known at a
snapshot, each computed at the instant the request became known (its available_at): the variant
history counts only lines settled by then, leaving the requesting customer out (rule 8). The same
call at scoring time, on a snapshot taken when the request arrives, gives the same row (MOD-08).

Non-conformity requests follow the guarantee path before any scoring (rule 4, ADR-09): they are
left out unless include_guarantee is set, and then carry path "guarantee".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import numpy as np
import pandas as pd

from ml.reference.features import Encoding, design, history_frame, line_queries
from ml.reference.model import DEFAULT_K, LineModel
from pipeline.features.lines import NON_CONFORMITY, line_frame, observations
from pipeline.features.snapshot import StoreSnapshot
from policy.types import Path

REQUEST_FEATURE_COLUMNS: Final = (
    "days_since_delivery",
    "reason",
    "price_eur",
    "variant_rate",
    "variant_n",
    "category_rate",
)


def request_features(
    snap: StoreSnapshot,
    return_ids: Iterable[str] | None = None,
    *,
    model: LineModel | None = None,
    include_guarantee: bool = False,
) -> pd.DataFrame:
    """One row per return request known at snap.as_of (or per id of `return_ids`), with
    return_id, line_id, customer_id, variant_id, decided_at, path, REQUEST_FEATURE_COLUMNS and,
    with a reference `model`, its p_l of the line at decided_at (column reference_p)."""
    lines = line_frame(snap)
    req = lines.loc[lines["return_id"].notna()]
    if return_ids is not None:
        wanted = set(return_ids)
        unknown = wanted - set(req["return_id"])
        if unknown:
            raise KeyError(f"return requests not known at the snapshot: {sorted(unknown)}")
        req = req.loc[req["return_id"].isin(wanted)]
    guarantee = req["reason"].isin(NON_CONFORMITY)
    if not include_guarantee:
        req = req.loc[~guarantee]
        guarantee = guarantee.loc[req.index]
    req = req.reset_index(drop=True)
    decided_at = req["return_known_at"].reset_index(drop=True)
    frame = history_frame(observations(lines), line_queries(req, decided_at))
    k = model.encoding.k if model is not None else DEFAULT_K
    rates = design(frame, Encoding(k, (), ()), ["variant_rate", "category_rate"])
    out = pd.DataFrame(
        {
            "return_id": req["return_id"],
            "line_id": req["line_id"],
            "customer_id": req["customer_id"],
            "variant_id": req["variant_id"],
            "decided_at": decided_at,
            "path": np.where(guarantee.to_numpy(), Path.GUARANTEE.value, Path.WITHDRAWAL.value),
            "days_since_delivery": req["days_since_delivery"].astype(np.int64),
            "reason": req["reason"],
            "price_eur": (req["price_cents"] / 100.0).astype(np.float64),
            "variant_rate": rates["variant_rate"],
            "variant_n": frame["variant_n"].astype(np.int64),
            "category_rate": rates["category_rate"],
        }
    )
    if model is not None:
        out["reference_p"] = model.predict(frame) if len(frame) else np.zeros(0)
    return out.sort_values("return_id", kind="stable").reset_index(drop=True)
