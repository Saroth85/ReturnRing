"""Tiny raw ASOS tables for the US-201 tests: hand-built rows and a seeded random replica.

Every number here is made up (nature: synthetic). The random replica keeps the properties of the
real dataset that matter to the propensity model (ADR-10):

- node aggregates (sales, returns) are counted over a window that contains the labelled events,
  plus unlabelled purchases;
- a customer or variant row exists only with at least one return, and some rows are missing for
  no reason tied to the label, so the joins leave explicit nulls;
- variant aggregates are identical in the train and test tables, as in the real files.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from contracts.io import Provenance, write_table
from contracts.parquet import ASOS_REASON_CODES, SCHEMAS

FIXTURE = Provenance(
    origin="Test fixture shaped like ASOS GraphReturns (CC BY 4.0), not measured",
    period_start=date(2021, 9, 1),
    period_end=date(2021, 11, 30),
    nature="synthetic",
)
TYPES = ("Jeans", "productType_A", "productType_B")
BRANDS = ("Brand_B", "Brand_K", "Pull&Bear")


def shares(code: str = "A") -> dict[str, float]:
    return {f"reason_share_{c}": 1.0 if c == code else 0.0 for c in ASOS_REASON_CODES}


def customer_row(split: str, h: int, sales: int, returns: int, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "split": split,
        "customer_hash": h,
        "sales": sales,
        "returns": returns,
        "return_rate": returns / sales,
        **shares(),
        "premier": False,
        "is_male": True,
        "shipping_country": "Country_A",
        "year_of_birth": 1990,
    }
    row.update(extra)
    return row


def variant_row(split: str, h: int, sales: int, returns: int, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "split": split,
        "variant_hash": h,
        "product_hash": h // 10,
        "supplier_hash": h // 100,
        "product_type": "Jeans",
        "brand": "Brand_K",
        "avg_price_gbp": 20.0,
        "avg_discount": 1.5,
        "sales": sales,
        "returns": returns,
        "return_rate": returns / sales,
        **shares("D2"),
    }
    row.update(extra)
    return row


def event_row(split: str, variant: int, customer: int, returned: bool) -> dict[str, Any]:
    return {
        "split": split,
        "variant_hash": variant,
        "customer_hash": customer,
        "is_returned": returned,
    }


def write_raw(
    root: Path,
    events: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    variants: list[dict[str, Any]],
    provenance: Provenance = FIXTURE,
) -> Path:
    """Write the three raw tables under root/raw/ through the contracts (synthetic nature)."""
    for name, rows in (
        ("raw/asos_events", events),
        ("raw/asos_customers", customers),
        ("raw/asos_variants", variants),
    ):
        table = pa.Table.from_pylist(rows, schema=SCHEMAS[name])
        write_table(name, table, root, provenance, nature_override="synthetic")
    return root / "raw"


def _sigmoid(x: np.ndarray) -> np.ndarray:
    out: np.ndarray = 1.0 / (1.0 + np.exp(-x))
    return out


def random_raw(
    seed: int = 7,
    n_customers: int = 400,
    n_variants: int = 150,
    events_per_split: int = 2500,
    missing_customer: float = 0.05,
    missing_variant: float = 0.3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Events, customer rows and variant rows of a seeded replica (see the module docstring)."""
    rng = np.random.default_rng(seed)
    cust_effect = rng.normal(0.0, 1.2, n_customers)
    var_effect = rng.normal(0.0, 0.8, n_variants)
    var_type = rng.integers(0, len(TYPES), n_variants)
    var_brand = rng.integers(0, len(BRANDS), n_variants)
    var_price = np.round(rng.uniform(5.0, 80.0, n_variants), 2)
    type_effect = np.array([0.6, -0.3, 0.0])
    customers_hash = 7_000_000 + np.arange(n_customers)
    variants_hash = 9_000_000 + np.arange(n_variants)

    events: list[dict[str, Any]] = []
    var_sales = np.zeros(n_variants, dtype=np.int64)
    var_returns = np.zeros(n_variants, dtype=np.int64)
    cust_counts: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for split in ("train", "test"):
        c = rng.integers(0, n_customers, events_per_split)
        v = rng.integers(0, n_variants, events_per_split)
        logit = cust_effect[c] + var_effect[v] + type_effect[var_type[v]] - 0.2
        y = rng.random(events_per_split) < _sigmoid(logit)
        events += [
            event_row(split, int(variants_hash[vi]), int(customers_hash[ci]), bool(yi))
            for vi, ci, yi in zip(v, c, y, strict=True)
        ]
        # Customer aggregates per split: the labelled events plus unlabelled purchases.
        extra = rng.poisson(4.0, n_customers)
        extra_returns = rng.binomial(extra, _sigmoid(cust_effect))
        sales = np.bincount(c, minlength=n_customers) + extra
        returns = np.bincount(c, weights=y, minlength=n_customers).astype(np.int64)
        cust_counts[split] = (sales, returns + extra_returns)
        np.add.at(var_sales, v, 1)
        np.add.at(var_returns, v, y.astype(np.int64))
    extra = rng.poisson(6.0, n_variants)
    var_sales += extra
    var_returns += rng.binomial(extra, _sigmoid(var_effect - 0.5))

    customers: list[dict[str, Any]] = []
    for split, (sales, returns) in cust_counts.items():
        keep = (returns >= 1) & (rng.random(n_customers) >= missing_customer)
        customers += [
            customer_row(split, int(customers_hash[i]), int(sales[i]), int(returns[i]))
            for i in np.flatnonzero(keep)
        ]
    keep_variant = (var_returns >= 1) & (rng.random(n_variants) >= missing_variant)
    variants = [
        variant_row(
            split,
            int(variants_hash[i]),
            int(var_sales[i]),
            int(var_returns[i]),
            product_type=TYPES[var_type[i]],
            brand=BRANDS[var_brand[i]],
            avg_price_gbp=float(var_price[i]),
            avg_discount=float(round(var_price[i] * 0.1, 2)),
        )
        for split in ("train", "test")
        for i in np.flatnonzero(keep_variant)
    ]
    return events, customers, variants
