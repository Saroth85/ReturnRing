"""Calibration contract: ASOS aggregates that the generator (US-102) reads (ADR-13).

contracts/json/calibration.schema.json fixes the shape; check_calibration adds the checks a JSON
Schema cannot express (ordered quantiles, shares that sum to one, a consistent period). The file
holds aggregates only, never row-level data: the ASOS dataset is not redistributed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from contracts.parquet import ASOS_REASON_CODES

QUANTILE_KEYS = ("p10", "p25", "p50", "p75", "p90")
SHARE_TOLERANCE = 1e-3  # shares are rounded when written


def _mapping(node: Any) -> Mapping[str, Any]:
    return node if isinstance(node, Mapping) else {}


def _ordered(path: str, quantiles: Any) -> list[str]:
    q = _mapping(quantiles)
    try:
        values = [float(q[k]) for k in QUANTILE_KEYS]
    except (KeyError, TypeError, ValueError):
        return [f"{path}: needs numeric {', '.join(QUANTILE_KEYS)}"]
    if any(a > b for a, b in zip(values, values[1:], strict=False)):
        return [f"{path}: quantiles are not in increasing order"]
    return []


def _sums_to_one(path: str, shares: list[Any]) -> list[str]:
    try:
        total = sum(float(s) for s in shares)
    except (TypeError, ValueError):
        return [f"{path}: shares must be numbers"]
    if abs(total - 1.0) > SHARE_TOLERANCE:
        return [f"{path}: shares sum to {total:.6f}, not 1"]
    return []


def check_calibration(doc: Mapping[str, Any]) -> list[str]:
    """Semantic errors of a calibration document; an empty list means consistent."""
    errors: list[str] = []

    period = _mapping(_mapping(doc.get("provenance")).get("period"))
    try:
        if date.fromisoformat(period["start"]) > date.fromisoformat(period["end"]):
            errors.append("provenance.period: start is after end")
    except (KeyError, TypeError, ValueError):
        errors.append("provenance.period: needs ISO dates start and end")

    types = _mapping(doc.get("product_types"))
    if not types:
        errors.append("product_types: at least one product type is required")
    for name, entry in types.items():
        entry = _mapping(entry)
        errors += _ordered(
            f"product_types.{name}.price_quantiles_gbp", entry.get("price_quantiles_gbp")
        )
        errors += _ordered(
            f"product_types.{name}.return_rate_quantiles", entry.get("return_rate_quantiles")
        )
    if types:
        for share in ("variant_share", "sales_share"):
            errors += _sums_to_one(
                f"product_types.*.{share}", [_mapping(e).get(share) for e in types.values()]
            )

    reasons = _mapping(doc.get("reason_shares"))
    if set(reasons) != set(ASOS_REASON_CODES):
        errors.append(f"reason_shares: needs exactly the codes {list(ASOS_REASON_CODES)}")
    else:
        errors += _sums_to_one("reason_shares", list(reasons.values()))

    errors += _ordered("purchases_per_customer", doc.get("purchases_per_customer"))
    return errors
