"""DATA-01 (contracts) and FAIR-01 (forbidden fields) at schema level."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from contracts.parquet import FORBIDDEN_FEATURE_FIELDS, SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["llm_case", "llm_output", "checkout_evaluate_response", "decision_request"]


@pytest.mark.parametrize("name", NAMES)
def test_json_schema_is_valid_and_example_conforms(name: str) -> None:
    schema = json.loads((ROOT / f"contracts/json/{name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    example = json.loads((ROOT / f"contracts/examples/{name}.json").read_text())
    Draft202012Validator(schema).validate(example)


def test_override_requires_a_reason() -> None:
    schema = json.loads((ROOT / "contracts/json/decision_request.schema.json").read_text())
    errors = list(
        Draft202012Validator(schema).iter_errors({"action": "override", "approver": "sara"})
    )
    assert errors


def test_future_window_never_below_14_in_checkout_contract() -> None:
    schema = json.loads(
        (ROOT / "contracts/json/checkout_evaluate_response.schema.json").read_text()
    )
    example = json.loads((ROOT / "contracts/examples/checkout_evaluate_response.json").read_text())
    example["terms"]["return_window_days"] = 7
    assert list(Draft202012Validator(schema).iter_errors(example))


def test_store_tables_are_event_tables() -> None:
    for name, schema in SCHEMAS.items():
        if name.startswith("store/"):
            assert {"event_at", "available_at"} <= set(schema.names), name


def test_features_and_scores_have_no_forbidden_fields() -> None:
    for name, schema in SCHEMAS.items():
        if name.startswith(("features/", "scores/")):
            assert not FORBIDDEN_FEATURE_FIELDS & set(schema.names), name


def test_llm_output_example_passes_the_python_validator() -> None:
    from llm.validator import ExpectedDecision, validate

    case = json.loads((ROOT / "contracts/examples/llm_case.json").read_text())
    output = json.loads((ROOT / "contracts/examples/llm_output.json").read_text())
    expected = ExpectedDecision("withdrawal", "Q4", ["instant_refund:false"])
    result = validate(output, expected, case)
    assert result.ok, result.errors
