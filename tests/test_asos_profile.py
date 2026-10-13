"""US-101 profiler: plain-text report and calibration JSON from the raw/asos_* tables (ASOS-5).

The raw tables below are small made-up fixtures (nature: synthetic); every expected number is
computed by hand in the comments. The committed calibration from the real data is checked in
test_committed_calibration_*.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pyarrow as pa
import pytest

from contracts.calibration import check_calibration
from contracts.io import Provenance, write_table
from contracts.parquet import ASOS_REASON_CODES, SCHEMAS
from pipeline.ingest import asos_profile as ap

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts/json/calibration.schema.json").read_text())
FIXTURE = Provenance(
    origin="Test fixture shaped like ASOS GraphReturns (CC BY 4.0), not measured",
    period_start=date(2021, 9, 1),
    period_end=date(2021, 11, 30),
    nature="synthetic",
)

# Hashes are large and distinctive so that a leak into the calibration would be visible.
C1, C2, C3, C4, C5 = 7_100_000_001, 7_100_000_002, 7_100_000_003, 7_100_000_004, 7_100_000_005
V1, V2, V3, V4, V9 = 8_200_000_001, 8_200_000_002, 8_200_000_003, 8_200_000_004, 8_200_000_009


def shares(**values: float) -> dict[str, float]:
    return {f"reason_share_{c}": values.get(c, 0.0) for c in ASOS_REASON_CODES}


def events() -> list[dict[str, Any]]:
    train = [  # (variant, customer, returned)
        (V1, C1, True),
        (V1, C2, False),
        (V2, C1, True),
        (V3, C3, False),
        (V9, C4, True),  # no variant row, no customer row
        (V2, C4, False),  # no customer row
    ]
    test = [(V1, C1, True), (V4, C5, True)]
    return [
        {"split": split, "variant_hash": v, "customer_hash": c, "is_returned": r}
        for split, rows in (("train", train), ("test", test))
        for v, c, r in rows
    ]


def customer(split: str, h: int, sales: int, returns: int, **extra: Any) -> dict[str, Any]:
    row = {
        "split": split,
        "customer_hash": h,
        "sales": sales,
        "returns": returns,
        "return_rate": returns / sales,
        **shares(A=1.0),
        "premier": False,
        "is_male": None,
        "shipping_country": None,
        "year_of_birth": None,
    }
    row.update(extra)
    return row


def customers() -> list[dict[str, Any]]:
    return [
        customer("train", C1, 4, 2, year_of_birth=1990),
        customer("train", C2, 2, 1, year_of_birth=1900),
        customer("train", C3, 1, 1, year_of_birth=2023),
        customer("test", C1, 5, 3),
        customer("test", C5, 2, 2),
    ]


def variant(
    split: str, h: int, type_: str, price: float, sales: int, returns: int, **codes: float
) -> dict[str, Any]:
    return {
        "split": split,
        "variant_hash": h,
        "product_hash": h // 10,
        "supplier_hash": None,
        "product_type": type_,
        "brand": "Brand_K",
        "avg_price_gbp": price,
        "avg_discount": 0.0,
        "sales": sales,
        "returns": returns,
        "return_rate": returns / sales,
        **shares(**codes),
    }


def variants() -> list[dict[str, Any]]:
    return [
        variant("train", V1, "productType_A", 10.0, 10, 5, A=1.0),
        variant("train", V2, "productType_A", 30.0, 30, 3, A=0.5, B=0.5),
        variant("train", V3, "Jeans", 50.0, 60, 30, D2=1.0),
        variant("test", V1, "productType_A", 11.0, 10, 5, A=1.0),  # same returns, new price
        variant("test", V4, "productType_B", 99.0, 8, 2, E=1.0),
    ]


@pytest.fixture
def raw_root(tmp_path: Path) -> Path:
    for name, rows in (
        ("raw/asos_events", events()),
        ("raw/asos_customers", customers()),
        ("raw/asos_variants", variants()),
    ):
        write_table(name, pa.Table.from_pylist(rows, schema=SCHEMAS[name]), tmp_path, FIXTURE)
    return tmp_path


# ---- Quantiles ----------------------------------------------------------------------------------


def test_quantiles_are_inverted_cdf_and_can_be_weighted() -> None:
    assert ap.quantiles(np.array([2.0, 1.0, 2.0, 1.0])) == {
        "p10": 1.0,
        "p25": 1.0,
        "p50": 1.0,
        "p75": 2.0,
        "p90": 2.0,
    }
    # 0.1 carries 30 of the 40 units of weight: it is every quantile up to p75.
    weighted = ap.quantiles(np.array([0.5, 0.1]), np.array([10, 30]))
    assert weighted == {"p10": 0.1, "p25": 0.1, "p50": 0.1, "p75": 0.1, "p90": 0.5}
    with pytest.raises(ap.ProfileError, match="no values"):
        ap.quantiles(np.array([]))


# ---- Calibration --------------------------------------------------------------------------------


def test_calibration_numbers_match_the_hand_computation(raw_root: Path) -> None:
    doc = ap.calibration(ap.load_raw(raw_root))
    assert doc["contract_version"] == "2.0.0"
    assert doc["splits"] == ["train"]
    assert doc["overall"] == {
        "event_label_rate": 0.5,  # 3 of 6 train events returned
        "product_sales_weighted_return_rate": 0.38,  # (5 + 3 + 30) / (10 + 30 + 60)
        "n_events": 6,
        "n_customers": 4,  # C1..C4 buy in train
        "n_variants": 4,  # V1, V2, V3, V9 are bought in train
    }
    assert doc["join_coverage"] == {
        "events_with_customer": round(4 / 6, 6),
        "events_with_variant": round(5 / 6, 6),
        "events_with_both": round(4 / 6, 6),
    }
    # C1: 2 events, C2: 1, C3: 1, C4: 2.
    assert doc["purchases_per_customer"] == {"p10": 1, "p25": 1, "p50": 1, "p75": 2, "p90": 2}
    a, jeans = doc["product_types"]["productType_A"], doc["product_types"]["Jeans"]
    assert set(doc["product_types"]) == {"productType_A", "Jeans"}
    assert (a["variant_share"], jeans["variant_share"]) == (round(2 / 3, 6), round(1 / 3, 6))
    assert (a["sales_share"], jeans["sales_share"]) == (0.4, 0.6)
    assert a["price_quantiles_gbp"] == {
        "p10": 10.0,
        "p25": 10.0,
        "p50": 10.0,
        "p75": 30.0,
        "p90": 30.0,
    }
    assert a["return_rate_quantiles"] == {
        "p10": 0.1,
        "p25": 0.1,
        "p50": 0.1,
        "p75": 0.1,
        "p90": 0.5,
    }
    assert jeans["return_rate_quantiles"] == dict.fromkeys(("p10", "p25", "p50", "p75", "p90"), 0.5)
    # Returns-weighted reason shares: A = 5*1 + 3*0.5, B = 3*0.5, D2 = 30; total 38 returns.
    expected = {code: 0.0 for code in ASOS_REASON_CODES}
    expected.update(A=round(6.5 / 38, 6), B=round(1.5 / 38, 6), D2=round(30 / 38, 6))
    assert doc["reason_shares"] == expected


def test_calibration_is_valid_and_holds_only_aggregates(raw_root: Path) -> None:
    doc = ap.calibration(ap.load_raw(raw_root))
    jsonschema.validate(doc, SCHEMA)
    assert check_calibration(doc) == []
    text = json.dumps(doc)
    for h in (C1, C2, C3, C4, C5, V1, V2, V3, V4, V9, V1 // 10):
        assert str(h) not in text
    provenance = doc["provenance"]
    assert provenance["period"] == {"start": "2021-09-01", "end": "2021-10-31"}
    assert provenance["nature"] == "synthetic"  # carried from the input files, never invented
    assert "CC BY 4.0" in provenance["origin"]


def test_calibration_of_real_files_carries_the_attribution(raw_root: Path) -> None:
    raw = ap.load_raw(raw_root)
    real = Provenance(
        origin="ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0",
        period_start=date(2021, 9, 1),
        period_end=date(2021, 11, 30),
        nature="real",
    )
    doc = ap.calibration(ap.RawAsos(raw.events, raw.customers, raw.variants, real))
    assert doc["provenance"]["nature"] == "real"
    assert doc["provenance"]["origin"].startswith("ASOS GraphReturns")
    assert "train split" in doc["provenance"]["origin"]


def test_calibration_refuses_inconsistent_input(raw_root: Path) -> None:
    raw = ap.load_raw(raw_root)
    no_train = raw.events.filter(raw.events["split"] == "test")
    with pytest.raises(ap.ProfileError, match="no train events"):
        ap.calibration(ap.RawAsos(no_train, raw.customers, raw.variants, raw.provenance))
    test_only = raw.variants.filter(raw.variants["split"] == "test")
    with pytest.raises(ap.ProfileError, match="no train variant rows"):
        ap.calibration(ap.RawAsos(raw.events, raw.customers, test_only, raw.provenance))
    no_sales = raw.variants.with_columns(sales=0, returns=0)
    with pytest.raises(ap.ProfileError, match="no sales"):
        ap.calibration(ap.RawAsos(raw.events, raw.customers, no_sales, raw.provenance))
    no_returns = raw.variants.with_columns(returns=0)
    with pytest.raises(ap.ProfileError, match="no returns"):
        ap.calibration(ap.RawAsos(raw.events, raw.customers, no_returns, raw.provenance))
    with pytest.raises(ap.ProfileError, match="sum to zero"):
        ap.quantiles(np.array([0.1, 0.2]), np.array([0, 0]))


def test_load_raw_refuses_tables_of_different_provenance(raw_root: Path) -> None:
    other = Provenance("Another source", date(2021, 9, 1), date(2021, 11, 30), "synthetic")
    rows = pa.Table.from_pylist(events(), schema=SCHEMAS["raw/asos_events"])
    write_table("raw/asos_events", rows, raw_root, other)
    with pytest.raises(ap.ProfileError, match="different provenance"):
        ap.load_raw(raw_root)


def test_write_calibration_is_deterministic(raw_root: Path, tmp_path: Path) -> None:
    doc = ap.calibration(ap.load_raw(raw_root))
    first = ap.write_calibration(doc, tmp_path / "a" / "calibration.json")
    second = ap.write_calibration(doc, tmp_path / "b" / "calibration.json")
    assert first.read_bytes() == second.read_bytes()
    assert first.read_text().endswith("}\n")
    assert json.loads(first.read_text()) == doc


def test_write_calibration_refuses_an_invalid_document(tmp_path: Path) -> None:
    with pytest.raises(ap.ProfileError, match="calibration"):
        ap.write_calibration({"product_types": {}}, tmp_path / "bad.json")
    assert not (tmp_path / "bad.json").exists()


# ---- Report -------------------------------------------------------------------------------------


def row(label: str, *values: str) -> str:
    """A report row: the label, then the train and test values, whatever the padding."""
    return r"(?m)^\s*" + re.escape(label) + "".join(r"\s+" + re.escape(v) for v in values) + "$"


def test_report_has_schema_counts_rates_coverage_and_the_leakage_note(raw_root: Path) -> None:
    raw = ap.load_raw(raw_root)
    text = ap.report(raw, ap.calibration(raw))
    for expected in (
        "CC BY 4.0",
        "nature: synthetic",
        "raw/asos_events",
        "variant_hash: int64 not null",
        "year_of_birth: int32",
        "ADR-10",
        "leave-one-out",
        "is_male, shipping_country, year_of_birth",
    ):
        assert expected in text, expected
    for pattern in (
        row("split", "train", "test"),
        row("events", "6", "2"),
        row("customer rows", "3", "2"),
        row("variant rows", "3", "2"),
        row("distinct customers in events", "4", "2"),
        row("distinct variants in events", "4", "2"),
        row("events labelled returned", "0.5000", "1.0000"),  # 3/6 train, 2/2 test
        # train 4/6, 5/6, 4/6; in test, C1, C5, V1 and V4 all have rows
        row("with a customer row", "0.6667", "1.0000"),
        row("with a variant row", "0.8333", "1.0000"),
        row("with both", "0.6667", "1.0000"),
        # (5 + 3 + 30) / (10 + 30 + 60) in train, (5 + 2) / (10 + 8) in test
        row("sales-weighted product return rate", "0.3800", "0.3889"),
    ):
        assert re.search(pattern, text), pattern
    # data quality: one placeholder year, one after the data period; V1 shared, price changed
    assert "year_of_birth at the placeholder 1900: 1" in text
    assert "year_of_birth after 2021: 1" in text
    assert "variants in both splits: 1, with identical return aggregates" in text
    assert re.search(
        r"return aggregates \([^)]*\): 1, with identical attributes \([^)]*\): 0", text
    )
    assert "customers in both splits: 1" in text
    for h in (C1, V1):
        assert str(h) not in text


def test_report_of_a_split_without_node_rows(raw_root: Path) -> None:
    raw = ap.load_raw(raw_root)
    train_only = ap.RawAsos(
        raw.events,
        raw.customers.filter(raw.customers["split"] == "train"),
        raw.variants.filter(raw.variants["split"] == "train"),
        raw.provenance,
    )
    text = ap.report(train_only, ap.calibration(train_only))
    assert re.search(row("sales-weighted product return rate", "0.3800", "n/a"), text)
    assert "test  variant return_rate" not in text
    no_test_events = ap.RawAsos(
        raw.events.filter(raw.events["split"] == "train"),
        raw.customers.filter(raw.customers["split"] == "train"),
        raw.variants,
        raw.provenance,
    )
    text = ap.report(no_test_events, ap.calibration(no_test_events))
    assert re.search(row("events labelled returned", "0.5000", "n/a"), text)
    assert re.search(row("with both", "0.6667", "n/a"), text)
    assert "train customer return_rate" in text
    assert "test  customer return_rate" not in text
    assert "test  events per customer" not in text
    assert "test  variant return_rate" in text


def test_profile_writes_both_outputs(raw_root: Path, tmp_path: Path) -> None:
    calibration_path = tmp_path / "out" / "asos_calibration.json"
    report_path = tmp_path / "out" / "asos_profile.txt"
    doc, text = ap.profile(raw_root, calibration_path, report_path)
    assert json.loads(calibration_path.read_text()) == doc
    assert report_path.read_text() == text
    assert str(calibration_path) in text
    again, _ = ap.profile(raw_root, tmp_path / "again.json")  # no report file asked for
    assert again == doc
    assert not (tmp_path / "out" / "again.txt").exists()


# ---- The committed calibration (real ASOS aggregates) -------------------------------------------


def test_committed_calibration_is_valid_real_and_attributed() -> None:
    path = ROOT / "pipeline/calibration/asos_calibration.json"
    doc = json.loads(path.read_text())
    jsonschema.validate(doc, SCHEMA)
    assert check_calibration(doc) == []
    provenance = doc["provenance"]
    assert provenance["nature"] == "real"
    assert provenance["period"] == {"start": "2021-09-01", "end": "2021-10-31"}
    for text in ("ASOS GraphReturns", "McGowan", "arXiv:2302.14096", "OSF c793h", "CC BY 4.0"):
        assert text in provenance["origin"]
    assert doc["splits"] == ["train"]
    # Counts of ADR-10 (origin: ASOS GraphReturns, OSF c793h; nature: real; train split).
    assert doc["overall"]["n_events"] == 1_369_133
    assert doc["overall"]["event_label_rate"] == round(757_227 / 1_369_133, 6)
    assert path.read_text() == ap.dumps(doc)  # written by the profiler, not by hand
