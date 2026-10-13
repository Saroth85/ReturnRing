"""Calibration input of the Navigli Fashion generator (US-102, DATA-02).

The calibration file used here is contracts/examples/calibration.json: an illustrative example whose
numbers are not measured (its provenance says so). Every number in this module is a test fixture.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import numpy as np
import pytest

from pipeline.generator.__main__ import main
from pipeline.generator.calibration import (
    GBP_TO_EUR,
    CalibrationError,
    QuantileDistribution,
    load_calibration,
    parse_calibration,
    type_return_targets,
)
from pipeline.generator.config import START_US, US_PER_DAY
from pipeline.generator.core import generate
from pipeline.generator.orders import VariantSampler

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


def _single_type(name: str) -> dict[str, Any]:
    doc = example()
    only = doc["product_types"]["Jeans"]
    only.update(variant_share=1.0, sales_share=1.0)
    only["return_rate_quantiles"] = {"p10": 0.0, "p25": 0.0, "p50": 0.5, "p75": 1.0, "p90": 1.0}
    only["price_quantiles_gbp"] = {"p10": 0.0, "p25": 1.0, "p50": 1.0, "p75": 2.0, "p90": 900.0}
    doc["product_types"] = {name: only}
    doc["overall"]["n_events"] = 1.0  # an integral number is an integer for JSON Schema
    return doc


@pytest.mark.parametrize("scenario", ["main", "ood"])
def test_any_valid_file_is_accepted(tmp_path: Path, scenario: str) -> None:
    """Other type names, a single type, extreme but valid values: parsed, then generated."""
    doc = _single_type("Dresses & Jumpsuits")
    jsonschema.validate(doc, SCHEMA)
    cal = parse_calibration(doc)
    assert [t.name for t in cal.product_types] == ["Dresses & Jumpsuits"]
    summary = generate(cal, tmp_path, seed=11, scale=0.005, scenario=scenario)
    assert summary["rows"]["store/order_lines"] > 0
    assert summary["defective_variants"] >= 1


@pytest.mark.parametrize("name", ["Bags", "Accessories", "Jewellery & Watches"])
def test_a_one_size_only_calibration_is_refused_clearly(tmp_path: Path, name: str) -> None:
    """G3: valid against the schema, but no product type has sizes, so neither the size
    behaviour (12% of customers) nor the golden cases can exist: a CalibrationError that says
    so, never an IndexError."""
    doc = _single_type(name)
    jsonschema.validate(doc, SCHEMA)
    cal = parse_calibration(doc)
    for scenario in ("main", "ood"):
        with pytest.raises(CalibrationError, match="size"):
            generate(cal, tmp_path, seed=1, scale=0.005, scenario=scenario)
    assert not list(tmp_path.rglob("*.parquet"))
    path = tmp_path / "one_size.json"
    path.write_text(json.dumps(doc))
    assert main(["--calibration", str(path), "--out", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out").exists()


def _few_sized_variants(sized: str, share: float) -> dict[str, Any]:
    """Review P2: one sized type with a tiny share next to a one-size type, both built from the
    example's Jeans entry. Valid, but at scale 0.005 the catalogue has at most a sized variant
    or two, listed after the period starts."""
    doc = example()
    jeans = doc["product_types"]["Jeans"]
    doc["product_types"] = {
        sized: {**copy.deepcopy(jeans), "variant_share": share, "sales_share": share},
        "Bags": {**copy.deepcopy(jeans), "variant_share": 1 - share, "sales_share": 1 - share},
    }
    return doc


@pytest.mark.parametrize(("sized", "share"), [("Dresses", 0.02), ("Jeans", 0.001)])
def test_p2_too_few_sized_variants_is_a_calibration_error(
    tmp_path: Path, sized: str, share: float
) -> None:
    """Before the fix the OOD scenario crashed with "ValueError: -1 is not in list": its only
    sized variant was listed after the period started, so a bracketing pair fell back to a
    one-size variant. A catalogue without a sized variant that sells from the first day is now a
    CalibrationError in every scenario (exit 1, no traceback); with one, the store is built."""
    doc = _few_sized_variants(sized, share)
    jsonschema.validate(doc, SCHEMA)
    cal = parse_calibration(doc)
    path = tmp_path / "few_sizes.json"
    path.write_text(json.dumps(doc))
    argv = ["--calibration", str(path), "--seed", "2", "--scale", "0.005"]
    with pytest.raises(CalibrationError, match="size"):
        generate(cal, tmp_path / "ood", seed=2, scale=0.005, scenario="ood")
    assert not list((tmp_path / "ood").rglob("*.parquet"))
    assert main([*argv, "--out", str(tmp_path / "cli-ood"), "--scenario", "ood"]) == 1
    # Main, same seed: its catalogue has a sized variant listed before the period, so it builds.
    summary = generate(cal, tmp_path / "main", seed=2, scale=0.005, scenario="main")
    assert summary["customers_by_behaviour"]["size"] > 0
    assert main([*argv, "--out", str(tmp_path / "cli-main"), "--scenario", "main"]) == 0


@pytest.mark.parametrize("seed", range(1, 9))
@pytest.mark.parametrize("scenario", ["main", "ood"])
def test_p2_few_sized_variants_never_crash(tmp_path: Path, seed: int, scenario: str) -> None:
    """Over seeds: the store is built, or the calibration is refused with a CalibrationError."""
    cal = parse_calibration(_few_sized_variants("Dresses", 0.02))
    try:
        generate(cal, tmp_path, seed=seed, scale=0.005, scenario=scenario)
    except CalibrationError as exc:
        assert "size" in str(exc)


def test_p2_the_variant_sampler_never_draws_a_zero_weight_variant() -> None:
    """The fallback draws among variants listed before the period. With no such variant of
    positive weight it raises, instead of returning the last (zero-weight) variant."""
    listed = np.asarray([START_US + US_PER_DAY, START_US - US_PER_DAY], dtype=np.int64)
    cat = SimpleNamespace(listed_at=listed)
    placed = np.full(50, START_US, dtype=np.int64)
    rng = np.random.default_rng(0)
    sampler = VariantSampler(cat, np.asarray([1.0, 0.0]))  # type: ignore[arg-type]
    with pytest.raises(CalibrationError, match="listed before the period"):
        sampler.draw(rng, placed)
    fine = VariantSampler(cat, np.asarray([1.0, 0.5]))  # type: ignore[arg-type]
    assert set(fine.draw(rng, placed).tolist()) == {1}
    nothing = VariantSampler(cat, np.zeros(2))  # type: ignore[arg-type]
    with pytest.raises(CalibrationError, match="positive weight"):
        nothing.draw(rng, placed)
    late = np.full(50, START_US + 2 * US_PER_DAY, dtype=np.int64)
    weights = np.asarray([1.0, 0.0, 0.0])
    tail = VariantSampler(  # type: ignore[arg-type]
        SimpleNamespace(listed_at=np.asarray([START_US - US_PER_DAY] * 3, dtype=np.int64)),
        weights,
    )
    assert set(tail.draw(rng, late).tolist()) == {0}


def test_generator_cli_exits_1_when_the_output_cannot_be_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review P5: a data/ folder the job cannot write (here a file in the way) is a clean exit 1
    with a message, never a traceback."""
    out = tmp_path / "data"
    out.write_text("a file, not a folder")
    argv = ["--calibration", str(EXAMPLE), "--out", str(out), "--scale", "0.005"]
    assert main(argv) == 1
    assert "cannot write" in capsys.readouterr().err


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
