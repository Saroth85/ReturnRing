"""LLM-02, LLM-03, LLM-04, LLM-10 on the deterministic side (docs/spec.md 8.7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm.legal_clause import clause
from llm.messages import MessageError, render_customer_message
from llm.validator import ExpectedDecision, numbers_in, validate

CASE: dict[str, Any] = {
    "case_id": "RET-000123",
    "quadrant": "Q4",
    "risk": {"score": 0.87, "top_factors": [{"value": 7, "phrase": "7 linked accounts"}]},
    "value_eur": 42.10,
}
EXPECTED = ExpectedDecision(
    path="withdrawal",
    quadrant="Q4",
    lever_ids=["return_window_days:14", "instant_refund:false"],
)


def good_output(**overrides: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "decision_echo": {
            "path": "withdrawal",
            "quadrant": "Q4",
            "lever_ids": ["instant_refund:false", "return_window_days:14"],
        },
        "operator_explanation": "OBSERVED: 7 linked accounts. SCORE: risk 0.87 from the model.",
        "customer_message": {
            "it": "Ciao, per i prossimi ordini valgono queste condizioni: {terms}.",
            "en": "Hello, these terms apply to your next orders: {terms}.",
        },
    }
    out.update(overrides)
    return out


def test_valid_output_passes() -> None:
    result = validate(json.dumps(good_output()), EXPECTED, CASE)
    assert result.ok, result.errors


def test_llm02_decision_echo_mismatch() -> None:
    bad = good_output(decision_echo={"path": "withdrawal", "quadrant": "Q1", "lever_ids": []})
    assert not validate(bad, EXPECTED, CASE).ok


@pytest.mark.parametrize("word", ["frode", "TRUFFA", "abuso", "fraud", "suspicious", "same person"])
def test_llm04_banned_terms(word: str) -> None:
    msg = {"it": f"Abbiamo rilevato {word}.", "en": "Hello."}
    result = validate(good_output(customer_message=msg), EXPECTED, CASE)
    assert any(e.startswith("BANNED_TERM") for e in result.errors)


def test_llm10_literal_numbers_are_rejected() -> None:
    msg = {"it": "Hai 14 giorni e un rimborso di 30 euro.", "en": "Hello {deadline}."}
    result = validate(good_output(customer_message=msg), EXPECTED, CASE)
    assert any(e.startswith("LITERAL_NUMBER") for e in result.errors)


def test_llm03_ungrounded_number() -> None:
    result = validate(good_output(operator_explanation="Risk 0.99, 12 accounts."), EXPECTED, CASE)
    assert any(e.startswith("UNGROUNDED_NUMBER") for e in result.errors)


def test_grounded_number_with_comma_decimal() -> None:
    result = validate(good_output(operator_explanation="Valore 42,1 euro."), EXPECTED, CASE)
    assert result.ok, result.errors


def test_schema_errors() -> None:
    assert not validate("not json", EXPECTED, CASE).ok
    extra = good_output()
    extra["unexpected"] = 1
    assert not validate(extra, EXPECTED, CASE).ok


def test_too_long_message() -> None:
    msg = {"it": "ok " * 130, "en": "Hello."}
    result = validate(good_output(customer_message=msg), EXPECTED, CASE)
    assert any(e.startswith("TOO_LONG") for e in result.errors)


def test_same_person_banned_in_explanation() -> None:
    result = validate(good_output(operator_explanation="They are the same person."), EXPECTED, CASE)
    assert any("operator explanation" in e for e in result.errors)


def test_numbers_in_walks_nested_structures() -> None:
    assert {"0.87", "7", "42.1"} <= numbers_in(CASE)
    assert numbers_in(True) == set()
    assert numbers_in(None) == set()


def test_render_fills_placeholders_and_appends_clause() -> None:
    text = render_customer_message(
        "Per i prossimi ordini: {terms}.", {"terms": "reso entro 14 giorni"}, "it", "withdrawal"
    )
    assert "reso entro 14 giorni" in text
    assert text.endswith(clause("withdrawal", "it"))


def test_render_without_clause() -> None:
    assert render_customer_message("Hello.", {}, "en", None) == "Hello."


def test_render_rejects_unknown_or_missing_placeholders() -> None:
    with pytest.raises(MessageError):
        render_customer_message("Refund {refund}.", {}, "en", None)
    with pytest.raises(MessageError):
        render_customer_message("Due {deadline}.", {}, "en", None)


def _spec_clause(prefix: str) -> str:
    """The approved clause, read from docs/spec.md appendix 19.C (the source of truth)."""
    spec = (Path(__file__).resolve().parents[1] / "docs" / "spec.md").read_text(encoding="utf-8")
    section = spec[spec.index("### C. Clausola legale fissa") :]
    line = next(x for x in section.splitlines() if x.startswith(prefix))
    return line.removeprefix(prefix)


def test_leg06_withdrawal_clause_is_the_approved_text() -> None:
    assert clause("withdrawal", "it") == _spec_clause("IT — ")
    assert clause("withdrawal", "en") == _spec_clause("EN — ")
    assert clause("withdrawal", "it").count("14 giorni") == 2
    assert clause("withdrawal", "en").count("14 days") == 2
