"""Read the generator calibration: ASOS aggregates (contracts/json/calibration.schema.json, ADR-13).

The pipeline image installs only requirements/base.txt, which has no JSON Schema validator, so
parse_calibration checks the shape itself: it rejects every document the schema rejects (the tests
compare it with jsonschema) and then runs contracts.calibration.check_calibration for the rules a
schema cannot express. Any file valid against the schema is accepted: any product type names, any
number of types, extreme but valid values.

How the generator uses each part (docs/synthetic-store.md):

- product_types: variant_share and sales_share split the catalogue and the sales; prices are drawn
  from price_quantiles_gbp (converted with GBP_TO_EUR) and each variant's latent base_return_rate
  from return_rate_quantiles;
- overall.product_sales_weighted_return_rate: the store's overall return rate (DATA-02);
- purchases_per_customer: the shape of the purchases per customer, rescaled to the spec volume;
- reason_shares, join_coverage, event_label_rate: reported, not used. The ASOS reason codes are
  masked (A..L), so they cannot be mapped to policy.types.ReturnReason.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from contracts.calibration import QUANTILE_KEYS, check_calibration
from contracts.parquet import ASOS_REASON_CODES, ASOS_SPLITS, CONTRACT_VERSION
from pipeline.generator.config import GBP_TO_EUR

__all__ = [
    "GBP_TO_EUR",
    "Calibration",
    "CalibrationError",
    "ProductType",
    "QuantileDistribution",
    "Quantiles",
    "load_calibration",
    "parse_calibration",
    "type_return_targets",
]

QUANTILE_PROBS = (0.10, 0.25, 0.50, 0.75, 0.90)
NATURES = ("real", "synthetic", "simulated")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class CalibrationError(ValueError):
    """A calibration document that is not valid against its contract."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("invalid calibration: " + "; ".join(self.errors))


@dataclass(frozen=True)
class Quantiles:
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float

    def values(self) -> tuple[float, float, float, float, float]:
        return (self.p10, self.p25, self.p50, self.p75, self.p90)


@dataclass(frozen=True)
class ProductType:
    name: str
    variant_share: float
    sales_share: float
    price_gbp: Quantiles
    return_rate: Quantiles  # sales-weighted quantiles of the variant return rate


@dataclass(frozen=True)
class Calibration:
    origin: str
    period_start: date
    period_end: date
    nature: str
    splits: tuple[str, ...]
    event_label_rate: float
    product_sales_weighted_return_rate: float
    n_events: int
    n_customers: int
    n_variants: int
    product_types: tuple[ProductType, ...]  # sorted by name: the file order never matters
    reason_shares: tuple[tuple[str, float], ...]
    purchases_per_customer: Quantiles
    join_coverage: tuple[tuple[str, float], ...]
    sha256: str  # of the file content, recorded in the provenance of every generated table


@dataclass(frozen=True)
class QuantileDistribution:
    """A distribution known by its p10..p90: the inverse CDF is linear between the knots; each tail
    extends the slope of the nearest inner segment to probability 0 or 1, clipped to
    [floor, ceiling]."""

    probs: tuple[float, ...]
    values: tuple[float, ...]

    @classmethod
    def from_quantiles(
        cls, quantiles: Quantiles | Mapping[str, float], floor: float, ceiling: float | None
    ) -> QuantileDistribution:
        if isinstance(quantiles, Quantiles):
            p10, p25, p50, p75, p90 = quantiles.values()
        else:
            p10, p25, p50, p75, p90 = (float(quantiles[k]) for k in QUANTILE_KEYS)
        low = p10 - (p25 - p10) * (0.10 / 0.15)
        high = p90 + (p90 - p75) * (0.10 / 0.15)
        low = min(max(low, floor), p10)
        if ceiling is not None:
            high = min(high, ceiling)
        high = max(high, p90)
        return cls(
            probs=(0.0, *QUANTILE_PROBS, 1.0),
            values=(low, p10, p25, p50, p75, p90, high),
        )

    def sample(self, u: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        """Values at probabilities u (inverse CDF)."""
        return np.interp(u, self.probs, self.values)

    def mean(self) -> float:
        """Exact mean of the piecewise-linear inverse CDF."""
        total = 0.0
        for i in range(len(self.probs) - 1):
            width = self.probs[i + 1] - self.probs[i]
            total += width * (self.values[i] + self.values[i + 1]) / 2
        return total


# ---- Structural checks (the JSON Schema, by hand) -----------------------------------------------


class _Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def obj(self, node: Any, path: str, required: Sequence[str], closed: bool = True) -> bool:
        if not isinstance(node, Mapping):
            self.fail(path, "must be an object")
            return False
        missing = [k for k in required if k not in node]
        if missing:
            self.fail(path, f"missing {missing}")
        if closed:
            extra = sorted(str(k) for k in node if k not in required)
            if extra:
                self.fail(path, f"unexpected keys {extra}")
        return not missing

    def number(
        self, node: Any, path: str, low: float | None = 0.0, high: float | None = None
    ) -> float:
        if isinstance(node, bool) or not isinstance(node, int | float) or not math.isfinite(node):
            self.fail(path, "must be a finite number")
            return 0.0
        if low is not None and node < low:
            self.fail(path, f"must be >= {low}")
        if high is not None and node > high:
            self.fail(path, f"must be <= {high}")
        return float(node)

    def share(self, node: Any, path: str) -> float:
        return self.number(node, path, 0.0, 1.0)

    def integer(self, node: Any, path: str, low: int) -> int:
        value = self.number(node, path, low)
        if not float(value).is_integer():
            self.fail(path, "must be an integer")
        return int(value)

    def text(self, node: Any, path: str) -> str:
        if not isinstance(node, str) or not node:
            self.fail(path, "must be a non-empty string")
            return ""
        return node

    def day(self, node: Any, path: str) -> date:
        if not isinstance(node, str) or not _DATE.fullmatch(node):
            self.fail(path, "must be a YYYY-MM-DD date")
            return date.min
        try:
            return date.fromisoformat(node)
        except ValueError:
            self.fail(path, "must be a YYYY-MM-DD date")
            return date.min

    def quantiles(self, node: Any, path: str, rate: bool) -> Quantiles:
        if not self.obj(node, path, QUANTILE_KEYS):
            return Quantiles(0.0, 0.0, 0.0, 0.0, 0.0)
        high = 1.0 if rate else None
        p10, p25, p50, p75, p90 = (
            self.number(node[k], f"{path}.{k}", 0.0, high) for k in QUANTILE_KEYS
        )
        return Quantiles(p10, p25, p50, p75, p90)

    def shares(self, node: Any, path: str, keys: Sequence[str]) -> tuple[tuple[str, float], ...]:
        if not self.obj(node, path, keys):
            return ()
        return tuple((k, self.share(node[k], f"{path}.{k}")) for k in keys)


_TOP = (
    "contract_version",
    "provenance",
    "splits",
    "overall",
    "product_types",
    "reason_shares",
    "purchases_per_customer",
    "join_coverage",
)
_OVERALL = (
    "event_label_rate",
    "product_sales_weighted_return_rate",
    "n_events",
    "n_customers",
    "n_variants",
)
_TYPE = ("variant_share", "sales_share", "price_quantiles_gbp", "return_rate_quantiles")
_JOIN = ("events_with_customer", "events_with_variant", "events_with_both")


def _product_types(c: _Checker, node: Any) -> tuple[ProductType, ...]:
    if not isinstance(node, Mapping) or not node:
        c.fail("product_types", "must be an object with at least one product type")
        return ()
    out = []
    for name in sorted(node, key=str):
        path = f"product_types.{name}"
        if not isinstance(name, str) or not name:
            c.fail("product_types", "type names must be non-empty strings")
            continue
        entry = node[name]
        if not c.obj(entry, path, _TYPE):
            continue
        out.append(
            ProductType(
                name=name,
                variant_share=c.share(entry["variant_share"], f"{path}.variant_share"),
                sales_share=c.share(entry["sales_share"], f"{path}.sales_share"),
                price_gbp=c.quantiles(entry["price_quantiles_gbp"], f"{path}.price", rate=False),
                return_rate=c.quantiles(
                    entry["return_rate_quantiles"], f"{path}.return_rate", rate=True
                ),
            )
        )
    return tuple(out)


def parse_calibration(doc: Any, sha256: str = "") -> Calibration:
    """A Calibration from a decoded JSON document, or CalibrationError with every problem found."""
    c = _Checker()
    if not c.obj(doc, "calibration", _TOP):
        raise CalibrationError(c.errors)
    if doc["contract_version"] != CONTRACT_VERSION:
        c.fail("contract_version", f"must be {CONTRACT_VERSION!r}")

    origin, start, end, nature = "", date.min, date.min, ""
    prov = doc["provenance"]
    if c.obj(prov, "provenance", ("origin", "period", "nature")):
        origin = c.text(prov["origin"], "provenance.origin")
        if c.obj(prov["period"], "provenance.period", ("start", "end")):
            start = c.day(prov["period"]["start"], "provenance.period.start")
            end = c.day(prov["period"]["end"], "provenance.period.end")
        nature = prov["nature"] if prov["nature"] in NATURES else ""
        if not nature:
            c.fail("provenance.nature", f"must be one of {list(NATURES)}")

    splits = doc["splits"]
    if (
        not isinstance(splits, list)
        or not splits
        or any(s not in ASOS_SPLITS for s in splits)
        or len(set(splits)) != len(splits)
    ):
        c.fail("splits", f"must be a non-empty list of distinct values in {list(ASOS_SPLITS)}")
        splits = []

    overall = doc["overall"]
    rates = (0.0, 0.0)
    counts = (0, 0, 0)
    if c.obj(overall, "overall", _OVERALL):
        rates = (
            c.share(overall["event_label_rate"], "overall.event_label_rate"),
            c.share(
                overall["product_sales_weighted_return_rate"],
                "overall.product_sales_weighted_return_rate",
            ),
        )
        counts = (
            c.integer(overall["n_events"], "overall.n_events", 1),
            c.integer(overall["n_customers"], "overall.n_customers", 1),
            c.integer(overall["n_variants"], "overall.n_variants", 1),
        )

    types = _product_types(c, doc["product_types"])
    reasons = c.shares(doc["reason_shares"], "reason_shares", ASOS_REASON_CODES)
    purchases = c.quantiles(doc["purchases_per_customer"], "purchases_per_customer", rate=False)
    join = c.shares(doc["join_coverage"], "join_coverage", _JOIN)

    if not c.errors:
        c.errors.extend(check_calibration(doc))
    if c.errors:
        raise CalibrationError(c.errors)
    return Calibration(
        origin=origin,
        period_start=start,
        period_end=end,
        nature=nature,
        splits=tuple(splits),
        event_label_rate=rates[0],
        product_sales_weighted_return_rate=rates[1],
        n_events=counts[0],
        n_customers=counts[1],
        n_variants=counts[2],
        product_types=types,
        reason_shares=reasons,
        purchases_per_customer=purchases,
        join_coverage=join,
        sha256=sha256,
    )


def load_calibration(path: Path | str) -> Calibration:
    """Read and check a calibration file."""
    try:
        raw = Path(path).read_bytes()
        doc = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise CalibrationError([f"{path}: cannot read JSON ({exc})"]) from None
    return parse_calibration(doc, sha256=hashlib.sha256(raw).hexdigest())


def rate_distribution(product_type: ProductType) -> QuantileDistribution:
    return QuantileDistribution.from_quantiles(product_type.return_rate, 0.0, 1.0)


def type_return_targets(cal: Calibration) -> dict[str, float]:
    """Target return rate of each product type: the mean of its sales-weighted rate distribution,
    rescaled by one common factor so that the sales-weighted sum is the calibration's overall
    product_sales_weighted_return_rate (the two agree within a few percent on a consistent file)."""
    means = {t.name: rate_distribution(t).mean() for t in cal.product_types}
    implied = sum(t.sales_share * means[t.name] for t in cal.product_types)
    factor = cal.product_sales_weighted_return_rate / implied if implied > 0 else 1.0
    return {name: min(1.0, mean * factor) for name, mean in means.items()}


def implied_overall_rate(cal: Calibration) -> float:
    """Sales-weighted mean of the per-type distributions, before rescaling (reported)."""
    return sum(t.sales_share * rate_distribution(t).mean() for t in cal.product_types)
