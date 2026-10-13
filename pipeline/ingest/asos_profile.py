"""Profile the raw ASOS tables: a plain-text report and the generator calibration (US-101).

Reads raw/asos_{events,customers,variants}.parquet through contracts.io.read_table, which checks
the contract, the version and the provenance. It produces two outputs:

- report() covers the schema, rows per split, distinct customers and variants, the label rate,
  join coverage, return-rate distributions, product types, reason shares, data quality and the
  leakage note of ADR-10 (review finding ASOS-5);
- calibration() builds the document of contracts/json/calibration.schema.json from the train
  split only. It holds aggregates only, never row-level data, because the dataset is not
  redistributed. write_calibration() runs contracts.calibration.check_calibration before it
  writes the file.

Every number carries the origin and nature of the input files (rule 11). Quantiles are
inverted-CDF quantiles (numpy method "inverted_cdf"), weighted where stated: each is a value
that occurs in the data.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl

from contracts.calibration import check_calibration
from contracts.io import Provenance, read_table
from contracts.parquet import ASOS_REASON_CODES, ASOS_SPLITS, CONTRACT_VERSION, SCHEMAS
from pipeline.ingest.asos_convert import SPLIT_PERIODS
from pipeline.ingest.asos_download import ATTRIBUTION

RAW = ("raw/asos_events", "raw/asos_customers", "raw/asos_variants")
CALIBRATION_SPLIT = "train"  # the generator is calibrated on the training split only
# The period of the calibration (review I1). Events and purchases come from the train events,
# September-October 2021. The variant aggregates come from the train product table, which holds the
# same aggregates as the test table for every shared variant (ADR-10): their window is the test
# window too, so the calibration covers September to November 2021.
CALIBRATION_PERIOD = (SPLIT_PERIODS["train"][0], SPLIT_PERIODS["test"][1])
QUANTILES = (("p10", 0.10), ("p25", 0.25), ("p50", 0.50), ("p75", 0.75), ("p90", 0.90))
DIGITS = 6  # rounding of every number in the calibration
UNKNOWN_TYPE = "unknown"  # product type of a variant row without one
SHARES = [f"reason_share_{code}" for code in ASOS_REASON_CODES]
# Variant columns compared across splits: the return aggregates, then the static attributes.
RETURN_AGGREGATES = ["sales", "returns", "return_rate", *SHARES]
ATTRIBUTES = [
    "product_hash",
    "supplier_hash",
    "product_type",
    "brand",
    "avg_price_gbp",
    "avg_discount",
]

LEAKAGE_NOTE = """\
The customer and product aggregates (sales, returns, return_rate, reason shares) are computed
over a window that contains the labelled purchases, so they are not known at decision time
(rule 7). Measured on 12 Oct 2026 (origin: ASOS GraphReturns, OSF c793h; nature: real; ADR-10):
the single rule customerReturnRate >= 0.5 reaches F1 0.759 on train and 0.753 on test, against
0.774 for the paper's XGBoost; with the event's own label removed (leave-one-out) the best rule
reaches 0.705 and 0.699. The product return aggregates are identical in the train and test
tables for every shared variant. Every ASOS model is therefore reported under two protocols on the
same splits:
A (paper features, for benchmarks only, never shown as product performance) and B (leave-one-out
counts and static product attributes: the decision-time protocol used by the product). Events
carry no timestamp, so protocol B still sees other purchases of the same window.
The dataset holds only customers with at least one return (paper): its rates do not describe
all ASOS customers."""


class ProfileError(ValueError):
    """Raw tables from which no consistent profile or calibration can be computed."""


@dataclass(frozen=True)
class RawAsos:
    """The three raw ASOS tables and their common provenance."""

    events: pl.DataFrame
    customers: pl.DataFrame
    variants: pl.DataFrame
    provenance: Provenance


def load_raw(root: Path) -> RawAsos:
    frames: list[pl.DataFrame] = []
    provenances: set[Provenance] = set()
    for name in RAW:
        table, provenance = read_table(name, root)
        frame = pl.from_arrow(table)
        assert isinstance(frame, pl.DataFrame)
        frames.append(frame)
        provenances.add(provenance)
    if len(provenances) != 1:
        raise ProfileError(f"the raw tables have different provenance: {provenances}")
    return RawAsos(frames[0], frames[1], frames[2], provenances.pop())


# ---- Statistics ---------------------------------------------------------------------------------


def quantiles(
    values: npt.NDArray[Any], weights: npt.NDArray[Any] | None = None, what: str = "values"
) -> dict[str, float]:
    """p10..p90 of `values`, optionally weighted (inverted CDF: values that occur in the data)."""
    if values.size == 0:
        raise ProfileError(f"no values for the quantiles of {what}")
    qs = [q for _, q in QUANTILES]
    if weights is None:
        found = np.quantile(values, qs, method="inverted_cdf")
    else:
        if not float(weights.sum()) > 0:
            raise ProfileError(f"the weights of {what} sum to zero")
        found = np.quantile(values, qs, method="inverted_cdf", weights=weights)
    return {key: round(float(v), DIGITS) for (key, _), v in zip(QUANTILES, found, strict=True)}


def _share(part: float, whole: float) -> float:
    return round(part / whole, DIGITS)


def _value(frame: pl.DataFrame, expr: pl.Expr) -> Any:
    return frame.select(expr).item()


def _split(frame: pl.DataFrame, split: str) -> pl.DataFrame:
    return frame.filter(pl.col("split") == split)


def _coverage(events: pl.DataFrame, customers: pl.DataFrame, variants: pl.DataFrame) -> list[int]:
    """Events with a customer row, with a variant row and with both (same split)."""
    keys_c = customers.select("split", "customer_hash").unique()
    keys_v = variants.select("split", "variant_hash").unique()
    with_c = events.join(keys_c, on=["split", "customer_hash"], how="semi")
    with_v = events.join(keys_v, on=["split", "variant_hash"], how="semi")
    both = with_c.join(keys_v, on=["split", "variant_hash"], how="semi")
    return [with_c.height, with_v.height, both.height]


def _sales_weighted_rate(variants: pl.DataFrame) -> float:
    sales = _value(variants, pl.col("sales").sum())
    if not sales:
        raise ProfileError("no sales in the variant rows")
    return float(_value(variants, pl.col("returns").sum())) / float(sales)


# ---- Calibration --------------------------------------------------------------------------------


def calibration(raw: RawAsos) -> dict[str, Any]:
    """The generator calibration (contracts/json/calibration.schema.json), train split only."""
    split = CALIBRATION_SPLIT
    events = _split(raw.events, split)
    customers = _split(raw.customers, split)
    variants = _split(raw.variants, split)
    if events.height == 0:
        raise ProfileError(f"no {split} events")
    if variants.height == 0:
        raise ProfileError(f"no {split} variant rows")
    events_start, events_end = SPLIT_PERIODS[split]
    start, end = CALIBRATION_PERIOD
    with_c, with_v, both = _coverage(events, customers, variants)
    purchases = events.group_by("customer_hash").len().get_column("len").to_numpy()
    return {
        "contract_version": CONTRACT_VERSION,
        "provenance": {
            "origin": f"{raw.provenance.origin}; calibration: aggregates of the {split} split by "
            f"pipeline/ingest/asos_profile.py. Rates, counts and purchases per customer come from "
            f"the {split} events ({events_start.isoformat()}..{events_end.isoformat()}); product "
            f"types, prices, return rates and reason shares come from the {split} product table, "
            "whose aggregates equal the test table's for every shared variant, so their window "
            "is the test window too (ADR-10); hence the period "
            f"{start.isoformat()}..{end.isoformat()}",
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "nature": raw.provenance.nature,
        },
        "splits": [split],
        "overall": {
            "event_label_rate": round(float(_value(events, pl.col("is_returned").mean())), DIGITS),
            "product_sales_weighted_return_rate": round(_sales_weighted_rate(variants), DIGITS),
            "n_events": events.height,
            "n_customers": int(_value(events, pl.col("customer_hash").n_unique())),
            "n_variants": int(_value(events, pl.col("variant_hash").n_unique())),
        },
        "product_types": _product_types(variants),
        "reason_shares": _reason_shares(variants),
        "purchases_per_customer": quantiles(purchases, what="purchases per customer"),
        "join_coverage": {
            "events_with_customer": _share(with_c, events.height),
            "events_with_variant": _share(with_v, events.height),
            "events_with_both": _share(both, events.height),
        },
    }


def _product_types(variants: pl.DataFrame) -> dict[str, Any]:
    typed = variants.with_columns(pl.col("product_type").fill_null(UNKNOWN_TYPE))
    total_sales = float(_value(typed, pl.col("sales").sum()))
    out: dict[str, Any] = {}
    for name in sorted(typed.get_column("product_type").unique().to_list()):
        part = typed.filter(pl.col("product_type") == name)
        prices = part.get_column("avg_price_gbp").drop_nulls().to_numpy()
        rates = part.select("return_rate", "sales")
        out[name] = {
            "variant_share": _share(part.height, typed.height),
            "sales_share": _share(float(_value(part, pl.col("sales").sum())), total_sales),
            "price_quantiles_gbp": quantiles(prices, what=f"the price of {name}"),
            "return_rate_quantiles": quantiles(
                rates.get_column("return_rate").to_numpy(),
                rates.get_column("sales").to_numpy(),
                what=f"the return rate of {name}",
            ),
        }
    return out


def _reason_shares(variants: pl.DataFrame) -> dict[str, float]:
    """Share of returns per masked reason code: row shares weighted by the row's returns."""
    weighted = variants.select(
        [(pl.col("returns") * pl.col(c)).sum().alias(c) for c in SHARES]
    ).row(0)
    total = float(sum(weighted))
    if not total > 0:
        raise ProfileError("no returns to weight the reason shares with")
    return {
        code: _share(float(w), total) for code, w in zip(ASOS_REASON_CODES, weighted, strict=True)
    }


def dumps(doc: Mapping[str, Any]) -> str:
    """The calibration file text: stable key order, two-space indent, final newline."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def write_calibration(doc: Mapping[str, Any], path: Path) -> Path:
    """Write the calibration after the checks of contracts.calibration, atomically."""
    errors = check_calibration(doc)
    if errors:
        raise ProfileError("calibration is inconsistent: " + "; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(dumps(doc))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # nothing left behind when the write or the rename fails
    return path


# ---- Report -------------------------------------------------------------------------------------


def _row(label: str, *values: object) -> str:
    return f"  {label:<40}" + "".join(f"{v!s:>14}" for v in values)


def _int(n: Any) -> str:
    return f"{int(n):,}"


def _rate(x: Any) -> str:
    return f"{float(x):.4f}"


def _ratio(part: Any, whole: Any) -> str:
    """part / whole with four decimals, or n/a when there is nothing to divide by."""
    return _rate(float(part) / float(whole)) if whole else "n/a"


def _q(values: Mapping[str, float], digits: int = 4) -> str:
    return " / ".join(f"{values[k]:.{digits}f}" for k, _ in QUANTILES)


def _schema_lines() -> list[str]:
    lines = []
    for name in RAW:
        lines.append(name)
        for f in SCHEMAS[name]:
            lines.append(f"  {f.name}: {f.type}{'' if f.nullable else ' not null'}")
    return lines


def _distributions(raw: RawAsos, split: str) -> list[tuple[str, dict[str, float]]]:
    events, customers, variants = (
        _split(t, split) for t in (raw.events, raw.customers, raw.variants)
    )
    out: list[tuple[str, dict[str, float]]] = []
    if customers.height:
        out.append(("customer return_rate", quantiles(customers["return_rate"].to_numpy())))
        out.append(("sales per customer row", quantiles(customers["sales"].to_numpy())))
    if variants.height:
        out.append(("variant return_rate", quantiles(variants["return_rate"].to_numpy())))
        out.append(
            (
                "variant return_rate, sales-weighted",
                quantiles(variants["return_rate"].to_numpy(), variants["sales"].to_numpy()),
            )
        )
        prices = variants["avg_price_gbp"].drop_nulls().to_numpy()
        if prices.size:
            out.append(("variant avg_price_gbp", quantiles(prices)))
    if events.height:
        per_customer = events.group_by("customer_hash").len().get_column("len").to_numpy()
        out.append(("events per customer", quantiles(per_customer)))
    return out


def _quality(raw: RawAsos) -> list[str]:
    lines = []
    for split in ASOS_SPLITS:
        pairs = (
            _split(raw.events, split)
            .group_by("variant_hash", "customer_hash")
            .len()
            .filter(pl.col("len") > 1)
        )
        lines.append(
            f"  {split}: (variant, customer) pairs bought more than once: {_int(pairs.height)}, "
            f"covering {_int(_value(pairs, pl.col('len').sum()) or 0)} events"
        )
    train_c, test_c = (_split(raw.customers, s) for s in ASOS_SPLITS)
    shared_c = train_c.join(test_c, on="customer_hash", how="semi").height
    lines.append(f"  customers in both splits: {_int(shared_c)}")
    train_v, test_v = (_split(raw.variants, s) for s in ASOS_SPLITS)
    joined = train_v.join(test_v, on="variant_hash", how="inner", suffix="_test")

    def identical(columns: list[str]) -> str:
        same = pl.all_horizontal([pl.col(c).eq_missing(pl.col(f"{c}_test")) for c in columns])
        return _int(joined.filter(same).height)

    lines.append(
        f"  variants in both splits: {_int(joined.height)}, with identical return aggregates "
        f"(sales, returns, return_rate, reason shares): {identical(RETURN_AGGREGATES)}, "
        f"with identical attributes ({', '.join(ATTRIBUTES)}): {identical(ATTRIBUTES)}"
    )
    for label, frame in (("customer", raw.customers), ("variant", raw.variants)):
        off = frame.filter((pl.sum_horizontal(SHARES) - 1.0).abs() > 1e-6).height
        lines.append(f"  {label} rows whose 13 reason shares do not sum to 1: {_int(off)}")
    years = raw.customers.get_column("year_of_birth")
    lines.append(
        "  customer rows, both splits (descriptive only): "
        f"year_of_birth at the placeholder 1900: {_int((years == 1900).sum())}, "
        f"year_of_birth after 2021: {_int((years > 2021).sum())}, "
        f"missing: {_int(years.null_count())}"
    )
    return lines


def report(raw: RawAsos, doc: Mapping[str, Any], calibration_path: Path | None = None) -> str:
    """The plain-text profile of the raw tables, with the calibration summary."""
    prov = raw.provenance
    per_split = {
        s: (_split(raw.events, s), _split(raw.customers, s), _split(raw.variants, s))
        for s in ASOS_SPLITS
    }
    lines = [
        "ASOS GraphReturns profile (US-101)",
        "",
        f"Source: {prov.origin}",
        f"Origin of every number below: the source above; nature: {prov.nature}; "
        f"period {prov.period_start.isoformat()}..{prov.period_end.isoformat()}.",
        f"Attribution: {ATTRIBUTION}",
        "",
        f"1. Schema (contract {CONTRACT_VERSION})",
        *_schema_lines(),
        "",
        "2. Rows and keys",
        _row("split", *ASOS_SPLITS),
        _row("events", *(_int(e.height) for e, _, _ in per_split.values())),
        _row("customer rows", *(_int(c.height) for _, c, _ in per_split.values())),
        _row("variant rows", *(_int(v.height) for _, _, v in per_split.values())),
        _row(
            "distinct customers in events",
            *(
                _int(_value(e, pl.col("customer_hash").n_unique()))
                for e, _, _ in per_split.values()
            ),
        ),
        _row(
            "distinct variants in events",
            *(_int(_value(e, pl.col("variant_hash").n_unique())) for e, _, _ in per_split.values()),
        ),
        _row(
            "distinct products in variant rows",
            *(_int(_value(v, pl.col("product_hash").n_unique())) for _, _, v in per_split.values()),
        ),
        "",
        "3. Labels and rates",
        _row(
            "events labelled returned",
            *(
                _ratio(_value(e, pl.col("is_returned").sum()), e.height)
                for e, _, _ in per_split.values()
            ),
        ),
        _row(
            "sales-weighted product return rate",
            *(
                _ratio(_value(v, pl.col("returns").sum()), _value(v, pl.col("sales").sum()))
                for _, _, v in per_split.values()
            ),
        ),
        "",
        "4. Join coverage: share of events whose customer or variant has a row in the same split",
    ]
    coverage = {s: _coverage(*tables) for s, tables in per_split.items()}
    for i, label in enumerate(("with a customer row", "with a variant row", "with both")):
        lines.append(
            _row(label, *(_ratio(coverage[s][i], per_split[s][0].height) for s in ASOS_SPLITS))
        )
    lines += ["", "5. Distributions (p10 / p25 / p50 / p75 / p90)"]
    for split in ASOS_SPLITS:
        for label, values in _distributions(raw, split):
            lines.append(f"  {split:<6}{label:<38}{_q(values)}")
    lines += [
        "",
        f"6. Product types ({', '.join(doc['splits'])} split, as in the calibration)",
        f"  {'product type':<20}{'variant share':>14}{'sales share':>14}"
        f"{'rate p50 (sales-w.)':>21}{'price p50 GBP':>15}",
    ]
    for name, entry in doc["product_types"].items():
        lines.append(
            f"  {name:<20}{entry['variant_share']:>14.4f}{entry['sales_share']:>14.4f}"
            f"{entry['return_rate_quantiles']['p50']:>21.4f}"
            f"{entry['price_quantiles_gbp']['p50']:>15.2f}"
        )
    lines += [
        "",
        f"7. Reason shares ({', '.join(doc['splits'])} split, weighted by variant returns; "
        "masked codes, D2 is the second D of the source)",
        "  " + "  ".join(f"{code} {share:.4f}" for code, share in doc["reason_shares"].items()),
        "",
        "8. Data quality and overlap",
        *_quality(raw),
        "",
        "9. Descriptive-only columns (rule 6, ADR-11)",
        "  is_male, shipping_country, year_of_birth are kept in raw/asos_customers for descriptive",
        "  statistics only; they never feed a model or a score.",
        "",
        "10. Leakage (ADR-10)",
        *(f"  {line}" for line in LEAKAGE_NOTE.splitlines()),
        "",
        "11. Calibration",
        f"  {calibration_path if calibration_path is not None else '(not written)'}: aggregates "
        f"of the {', '.join(doc['splits'])} split only, no row-level data.",
        f"  overall: {json.dumps(doc['overall'])}",
        f"  join coverage: {json.dumps(doc['join_coverage'])}",
        f"  purchases per customer: {_q(doc['purchases_per_customer'], 0)}",
    ]
    return "\n".join(lines) + "\n"


def profile(
    root: Path, calibration_out: Path, report_out: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Profile the raw tables under `root`; write the calibration and, if asked, the report."""
    raw = load_raw(root)
    doc = calibration(raw)
    write_calibration(doc, calibration_out)
    text = report(raw, doc, calibration_out)
    if report_out is not None:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(text)
    return doc, text
