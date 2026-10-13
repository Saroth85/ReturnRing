"""Calibration input of the Navigli Fashion generator (US-102, DATA-02).

The calibration file used here is contracts/examples/calibration.json: an illustrative example whose
numbers are not measured (its provenance says so). Every number in this module is a test fixture.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pytest

from pipeline.generator.calibration import (
    GBP_TO_EUR,
    CalibrationError,
    QuantileDistribution,
    load_calibration,
    parse_calibration,
    type_return_targets,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "contracts" / "examples" / "calibration.json"
SCHEMA = json.loads((ROOT / "contracts" / "json" / "calibration.schema.json").read_text())


def example() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(EXAMPLE.read_text())
    return doc


def test_example_calibration_parses() -> None:
    cal = load_calibration(EXAMPLE)
    assert [t.name for t in cal.product_types] == sorted(example()["product_types"])
    assert cal.product_sales_weighted_return_rate == pytest.approx(0.3)
    assert cal.origin.startswith("Illustrative contract example")
    assert cal.nature == "synthetic"
    assert len(cal.sha256) == 64


def test_product_type_order_does_not_depend_on_the_file_order() -> None:
    doc = example()
    doc["product_types"] = dict(reversed(list(doc["product_types"].items())))
    assert parse_calibration(doc).product_types == parse_calibration(example()).product_types


def test_gbp_to_eur_is_a_stated_constant() -> None:
    assert pytest.approx(1.17) == GBP_TO_EUR


def _mutations() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []

    def mutate(label: str, fn: Any) -> None:
        doc = copy.deepcopy(example())
        fn(doc)
        out.append((label, doc))

    mutate("missing overall", lambda d: d.pop("overall"))
    mutate("extra top-level key", lambda d: d.update(rows=[1, 2]))
    mutate("wrong version", lambda d: d.update(contract_version="1.0.0"))
    mutate("share above one", lambda d: d["overall"].update(event_label_rate=1.2))
    mutate("boolean as number", lambda d: d["overall"].update(event_label_rate=True))
    mutate("string as number", lambda d: d["purchases_per_customer"].update(p50="2"))
    mutate(
        "negative price",
        lambda d: d["product_types"]["Jeans"]["price_quantiles_gbp"].update(p10=-1),
    )
    mutate(
        "rate above one",
        lambda d: d["product_types"]["Jeans"]["return_rate_quantiles"].update(p90=1.5),
    )
    mutate(
        "missing quantile", lambda d: d["product_types"]["Jeans"]["price_quantiles_gbp"].pop("p75")
    )
    mutate("extra type key", lambda d: d["product_types"]["Jeans"].update(colour="blue"))
    mutate("no product type", lambda d: d.update(product_types={}))
    mutate(
        "empty type name", lambda d: d["product_types"].update({"": d["product_types"]["Jeans"]})
    )
    mutate("zero events", lambda d: d["overall"].update(n_events=0))
    mutate("fractional count", lambda d: d["overall"].update(n_events=10.5))
    mutate("unknown split", lambda d: d.update(splits=["validation"]))
    mutate("duplicate split", lambda d: d.update(splits=["train", "train"]))
    mutate("bad nature", lambda d: d["provenance"].update(nature="measured"))
    mutate("bad date", lambda d: d["provenance"]["period"].update(start="1 Sep 2021"))
    mutate("empty origin", lambda d: d["provenance"].update(origin=""))
    mutate("missing reason code", lambda d: d["reason_shares"].pop("D2"))
    mutate("missing join coverage", lambda d: d["join_coverage"].pop("events_with_both"))
    return out


@pytest.mark.parametrize(("label", "doc"), _mutations(), ids=[m[0] for m in _mutations()])
def test_documents_invalid_against_the_schema_are_rejected(label: str, doc: dict[str, Any]) -> None:
    """The generator needs no jsonschema at runtime (base.txt), so it checks the shape itself; it
    must reject at least everything the JSON Schema rejects."""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)
    with pytest.raises(CalibrationError):
        parse_calibration(doc)


def test_semantic_errors_are_rejected() -> None:
    """Valid against the schema, inconsistent for contracts.calibration.check_calibration."""
    doc = example()
    doc["product_types"]["Jeans"]["price_quantiles_gbp"].update(p25=80.0)  # not increasing
    doc["reason_shares"]["A"] = 0.9  # shares no longer sum to one
    jsonschema.validate(doc, SCHEMA)
    with pytest.raises(CalibrationError) as err:
        parse_calibration(doc)
    assert any("increasing" in e for e in err.value.errors)
    assert any("reason_shares" in e for e in err.value.errors)


def test_any_valid_file_is_accepted() -> None:
    """Other type names, a single type, extreme but valid values."""
    doc = example()
    only = doc["product_types"]["Jeans"]
    only.update(variant_share=1.0, sales_share=1.0)
    only["return_rate_quantiles"] = {"p10": 0.0, "p25": 0.0, "p50": 0.5, "p75": 1.0, "p90": 1.0}
    only["price_quantiles_gbp"] = {"p10": 0.0, "p25": 1.0, "p50": 1.0, "p75": 2.0, "p90": 900.0}
    doc["product_types"] = {"Dresses & Jumpsuits": only}
    doc["overall"]["n_events"] = 1.0  # an integral number is an integer for JSON Schema
    jsonschema.validate(doc, SCHEMA)
    cal = parse_calibration(doc)
    assert [t.name for t in cal.product_types] == ["Dresses & Jumpsuits"]


def test_load_rejects_a_file_that_is_not_json(tmp_path: Path) -> None:
    bad = tmp_path / "calibration.json"
    bad.write_text("{not json")
    with pytest.raises(CalibrationError):
        load_calibration(bad)
    with pytest.raises(CalibrationError):
        load_calibration(tmp_path / "missing.json")


def test_quantile_distribution_hits_the_knots() -> None:
    q = {"p10": 0.1, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p90": 0.9}
    dist = QuantileDistribution.from_quantiles(q, floor=0.0, ceiling=1.0)
    probs = np.array([0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0])
    assert dist.sample(probs) == pytest.approx([0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0])
    assert dist.mean() == pytest.approx(0.5)


def test_quantile_distribution_tails_are_clipped() -> None:
    q = {"p10": 0.02, "p25": 0.1, "p50": 0.2, "p75": 0.6, "p90": 0.95}
    dist = QuantileDistribution.from_quantiles(q, floor=0.0, ceiling=1.0)
    values = dist.sample(np.linspace(0.0, 1.0, 1001))
    assert values.min() == pytest.approx(0.0)
    assert values.max() == pytest.approx(1.0)
    assert np.all(np.diff(values) >= 0)


def test_quantile_distribution_mean_matches_sampling() -> None:
    q = {"p10": 8.0, "p25": 12.0, "p50": 20.0, "p75": 32.0, "p90": 50.0}
    dist = QuantileDistribution.from_quantiles(q, floor=0.0, ceiling=None)
    u = (np.arange(200_000) + 0.5) / 200_000
    assert dist.sample(u).mean() == pytest.approx(dist.mean(), rel=1e-4)


def test_type_targets_reproduce_the_overall_rate() -> None:
    cal = load_calibration(EXAMPLE)
    targets = type_return_targets(cal)
    assert set(targets) == {t.name for t in cal.product_types}
    implied = sum(t.sales_share * targets[t.name] for t in cal.product_types)
    assert implied == pytest.approx(cal.product_sales_weighted_return_rate, rel=1e-9)
    # Each target stays close to the quantile-implied mean of its type (the example is consistent).
    for t in cal.product_types:
        mean = QuantileDistribution.from_quantiles(t.return_rate, 0.0, 1.0).mean()
        assert targets[t.name] == pytest.approx(mean, rel=0.05)
