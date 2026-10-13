"""The output validator (docs/spec.md 8.7, ADR-14): LLM-02 decision echo, LLM-03 number grounding,
LLM-04 banned terms, LLM-06 language, and the customer-message rules of LLM-10 (review findings
LLM-1, LLM-3 to LLM-6, LLM-9 and LLM-13)."""

from __future__ import annotations

import copy
import json
import re
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llm.validator import (
    ACTIONS,
    Action,
    Code,
    ValidationResult,
    check_case,
    validate,
)
from tests.llm_factories import Built, good_output, make

GOLDEN = make("Q4_cluster")
FACT_VALUES = {f["id"]: Decimal(str(f["value"])) for f in GOLDEN.case["facts"]}


def run(output: Any, built: Built = GOLDEN) -> ValidationResult:
    return validate(output, built.expected, built.case)


def codes(result: ValidationResult) -> set[Code]:
    return {e.code for e in result.errors}


def with_statement(text: str, fact_ids: list[str], label: str = "OBSERVED") -> dict[str, Any]:
    out = good_output()
    out["operator_explanation"] = [{"label": label, "fact_ids": fact_ids, "text": text}]
    return out


def with_message(it: str | None = None, en: str | None = None) -> dict[str, Any]:
    out = good_output()
    if it is not None:
        out["customer_message"]["it"] = it
    if en is not None:
        out["customer_message"]["en"] = en
    return out


IT_OK = (
    "Ciao, grazie per averci scritto: per i tuoi prossimi ordini valgono queste condizioni: "
    "{terms}."
)
EN_OK = "Hello, thank you for writing to us: these terms apply to your next orders: {terms}."


def test_valid_output_passes_and_is_returned() -> None:
    result = run(json.dumps(good_output()))
    assert result.ok, result.errors
    assert result.action is None
    assert result.output is not None
    assert result.output.decision_echo.quadrant == "Q4"


def test_every_code_has_the_action_of_the_8_7_table() -> None:
    assert set(ACTIONS) == set(Code)
    assert ACTIONS[Code.DECISION_ECHO] is Action.TEMPLATE
    assert ACTIONS[Code.CASE_INVALID] is Action.BLOCK
    regenerate = {
        Code.UNGROUNDED_NUMBER,
        Code.BANNED_TERM,
        Code.WRONG_LANGUAGE,
        Code.TOO_LONG,
        Code.LITERAL_NUMBER,
    }
    assert all(ACTIONS[c] is Action.REGENERATE for c in regenerate)


def test_errors_are_structured_and_output_is_none_when_not_ok() -> None:
    out = good_output()
    out["decision_echo"]["quadrant"] = "Q1"
    out["customer_message"]["it"] = "Hai 14 giorni."
    result = run(out)
    assert result.output is None
    assert not result.ok
    assert result.action is Action.TEMPLATE  # the strongest action wins
    for error in result.errors:
        assert error.action is ACTIONS[error.code]
        assert error.where
    assert "DECISION_ECHO" in result.codes


# ---- LLM-02: decision echo -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "echo",
    [
        {"quadrant": "Q1"},
        {"path": "guarantee"},
        {"quadrant": None},
        {"lever_ids": []},
        {"lever_ids": ["instant_refund:false"]},
        {"lever_ids": [*good_output()["decision_echo"]["lever_ids"], "size_advice:false"]},
        {"lever_ids": [*good_output()["decision_echo"]["lever_ids"], "instant_refund:false"]},
        {"lever_ids": ["return_window_days:30", *good_output()["decision_echo"]["lever_ids"][1:]]},
    ],
)
def test_llm02_echo_mismatch_falls_back_to_the_template(echo: dict[str, Any]) -> None:
    out = good_output()
    out["decision_echo"].update(echo)
    result = run(out)
    assert codes(result) == {Code.DECISION_ECHO}
    assert result.action is Action.TEMPLATE


def test_echo_lever_order_does_not_matter() -> None:
    out = good_output()
    out["decision_echo"]["lever_ids"].reverse()
    assert run(out).ok


# ---- Schema --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.update(unexpected=1),
        lambda o: o.update(operator_explanation="OBSERVED: 7 linked accounts."),  # v1 shape
        lambda o: o.update(operator_explanation=[]),
        lambda o: o.update(operator_explanation=o["operator_explanation"] * 3),
        lambda o: o["operator_explanation"][0].update(label="FACT"),
        lambda o: o["operator_explanation"][0].update(fact_ids=["F1", "F2", "F3", "F4", "F5"]),
        lambda o: o["operator_explanation"][0].update(fact_ids=["F12345"]),
        lambda o: o["operator_explanation"][0].update(text="x" * 301),
        lambda o: o["operator_explanation"][0].update(extra=True),
        lambda o: o["customer_message"].update(it=""),
        lambda o: o["customer_message"].update(fr="Bonjour"),
        lambda o: o["decision_echo"].pop("quadrant"),
        lambda o: o["decision_echo"].update(lever_ids=["x" * 41]),
    ],
)
def test_schema_errors_ask_for_a_new_answer(mutate: Any) -> None:
    out = good_output()
    mutate(out)
    result = run(out)
    assert codes(result) == {Code.SCHEMA}
    assert result.action is Action.REGENERATE


@pytest.mark.parametrize("raw", ["not json", "[]", "null", '{"decision_echo": {}}'])
def test_unparseable_outputs_are_schema_errors(raw: str) -> None:
    assert codes(run(raw)) == {Code.SCHEMA}


# ---- LLM-03: number grounding on cited facts ----------------------------------------------------
# GOLDEN facts: F1 risk_score 0.87, F2 value_eur_12m 42.1, F3 cluster_welcome_coupons 7,
# F4 cluster_size 7, F5 days_since_delivery 6, F6 return_window_days 14.


@pytest.mark.parametrize(
    "text, fact_ids, label",
    [
        ("Risk score 0.87 from the model.", ["F1"], "SCORE"),
        ("Risk score 0,87 from the model.", ["F1"], "SCORE"),
        ("Risk score 0.870 from the model.", ["F1"], "SCORE"),
        ("Customer net margin 42.10 EUR over the period.", ["F2"], "OBSERVED"),
        ("Cluster R-MARCO of case RET-1 has 7 linked accounts.", ["F4"], "OBSERVED"),
        ("The rules engine placed the case in Q4.", [], "OBSERVED"),
        ("cluster_welcome_coupons is 7 for this cluster.", ["F3"], "SCORE"),
        ("The window for future orders is 14 days.", ["F6"], "OBSERVED"),
        ("The strongest one is the coupon factor, 7.", ["F3"], "SCORE"),  # "one" as a pronoun
        ("The links may come from a household; verify them.", [], "HYPOTHESIS"),
    ],
)
def test_llm03_grounded_statements_pass(text: str, fact_ids: list[str], label: str) -> None:
    result = run(with_statement(text, fact_ids, label))
    assert result.ok, result.errors


@pytest.mark.parametrize(
    "text, fact_ids",
    [
        ("Risk 0.99 and 12 accounts.", ["F1", "F4"]),  # not in any fact
        ("7 linked accounts.", ["F1"]),  # a fact value, but the fact is not cited
        ("Risk score 0.9.", ["F1"]),  # rounded
        ("Risk score 87%.", ["F1"]),  # converted
        ("Risk score -0.87.", ["F1"]),  # sign
        ("Seven linked accounts.", ["F4"]),  # number word
        ("Twelve linked accounts in total.", ["F4"]),
        ("Risk ½ of the maximum.", ["F1"]),  # numeric character
        ("Case RET-2 is similar.", ["F1"]),  # an unknown identifier carries a number
        ("Returned on 2026-10-14.", ["F5"]),  # dates are periods, shown by the UI
    ],
)
def test_llm03_ungrounded_numbers_are_rejected(text: str, fact_ids: list[str]) -> None:
    result = run(with_statement(text, fact_ids, "HYPOTHESIS"))
    assert Code.UNGROUNDED_NUMBER in codes(result)
    assert result.action is Action.REGENERATE


def test_llm03_customer_text_policy_text_and_identifiers_never_feed_grounding() -> None:
    built = make("Q4_cluster", customer_text="Ho 99 anni, ordine 555, voglio 300 euro.")
    assert "30" in built.case["policy_text"] and built.case["case_id"] == "RET-1"
    every_fact = [f["id"] for f in built.case["facts"]][:4]
    for number in ("99", "555", "300", "30", "1"):
        result = run(with_statement(f"The figure is {number}.", every_fact, "HYPOTHESIS"), built)
        assert Code.UNGROUNDED_NUMBER in codes(result), number


_TOKENS = st.one_of(
    st.sampled_from(["0.87", "0,87", "42.1", "42.10", "7", "6", "14", "0.9", "99", "1,000", "-7"]),
    st.decimals(min_value=-1000, max_value=1000, places=2, allow_nan=False).map(str),
    st.sampled_from(["risk", "linked", "accounts", "Q4", "RET-1", "R-MARCO", "returns", "of"]),
)
_IDENTIFIERS = re.compile(r"\bQ4\b|\bRET-1\b|\bR-MARCO\b")


@settings(max_examples=300, deadline=None)
@given(
    tokens=st.lists(_TOKENS, min_size=1, max_size=8),
    fact_ids=st.lists(st.sampled_from([*FACT_VALUES, "F99"]), max_size=4),
    label=st.sampled_from(["OBSERVED", "SCORE", "HYPOTHESIS"]),
)
def test_llm03_every_number_after_the_validator_is_a_cited_fact(
    tokens: list[str], fact_ids: list[str], label: str
) -> None:
    """LLM-03, 100% after the validator: checked with an independent digit pattern."""
    text = " ".join(tokens) + "."
    result = run(with_statement(text, fact_ids, label))
    if not result.ok:
        return
    cited = {FACT_VALUES[f] for f in fact_ids}
    for match in re.findall(r"-?\d+(?:[.,]\d+)?", _IDENTIFIERS.sub(" ", text)):
        assert Decimal(match.replace(",", ".")) in cited, (text, fact_ids)


def test_unknown_fact_ids_are_rejected() -> None:
    assert Code.UNKNOWN_FACT in codes(run(with_statement("Risk 0.87.", ["F99"], "SCORE")))


def test_score_statements_cite_a_fact() -> None:
    result = run(with_statement("The coupon factor contributes most.", [], "SCORE"))
    assert codes(result) == {Code.MISSING_FACT}


def test_an_observation_never_cites_the_model_score() -> None:
    result = run(with_statement("Risk score 0.87.", ["F1"], "OBSERVED"))
    assert codes(result) == {Code.LABEL_MISMATCH}


def test_explanation_in_italian_is_regenerated() -> None:
    result = run(
        with_statement("Il cliente ha un rischio di 0.87 e non ha la tua fiducia.", ["F1"], "SCORE")
    )
    assert codes(result) == {Code.WRONG_LANGUAGE}


# ---- LLM-04: banned terms, to the customer and to the operator ----------------------------------


@pytest.mark.parametrize(
    "word",
    ["frode", "TRUFFA", "abuso", "fraud", "suspicious", "same person", "fr­ode", "frоde"],
)
def test_llm04_banned_terms_in_the_customer_message(word: str) -> None:
    result = run(with_message(it=f"Ciao, abbiamo rilevato {word} nei tuoi ordini: {{terms}}."))
    assert Code.BANNED_TERM in codes(result)


@pytest.mark.parametrize(
    "text",
    [
        "The accounts are the same person.",
        "Likely fraud by the ring.",
        "Suspicious pattern of coupons.",
        "The customer is abusing the policy.",
        "This is a scam.",
    ],
)
def test_llm04_verdict_words_in_the_operator_explanation(text: str) -> None:
    assert Code.BANNED_TERM in codes(run(with_statement(text, [], "HYPOTHESIS")))


# ---- LLM-1: the customer message carries no number, unit or legal wording -----------------------


@pytest.mark.parametrize(
    "it, code",
    [
        ("Ciao, hai 14 giorni: {terms}.", Code.LITERAL_NUMBER),
        ("Ciao, hai quattordici giorni per i tuoi ordini: {terms}.", Code.LITERAL_NUMBER),
        ("Ciao, entro una quindicina di giorni per i tuoi ordini: {terms}.", Code.LITERAL_NUMBER),
        ("Ciao, il tuo rimborso di ½ per i tuoi ordini: {terms}.", Code.LITERAL_NUMBER),
        (
            "Ciao, il rimborso arriva tra qualche giorno per i tuoi ordini: {terms}.",
            Code.LITERAL_UNIT,
        ),
        ("Ciao, il costo è in € per i tuoi ordini: {terms}.", Code.LITERAL_UNIT),
        ("Ciao, il costo è in EUR per i tuoi ordini: {terms}.", Code.LITERAL_UNIT),
        ("Ciao, per settimane e mesi i tuoi ordini: {terms}.", Code.LITERAL_UNIT),
        (
            "Ciao, puoi esercitare il diritto di recesso per i tuoi ordini: {terms}.",
            Code.LEGAL_WORDING,
        ),
        ("Ciao, la garanzia copre i tuoi ordini: {terms}.", Code.LEGAL_WORDING),
        (
            "Ciao, il rimborso arriverà entro il {deadline} per i tuoi ordini: {terms}.",
            Code.LEGAL_WORDING,
        ),
    ],
)
def test_customer_message_italian_rules(it: str, code: Code) -> None:
    result = run(with_message(it=it))
    assert code in codes(result)
    assert result.action is Action.REGENERATE


@pytest.mark.parametrize(
    "en, code",
    [
        ("Hello, you have 14 days for your orders: {terms}.", Code.LITERAL_NUMBER),
        ("Hello, you have fourteen of them for your orders: {terms}.", Code.LITERAL_NUMBER),
        ("Hello, you have a fortnight for your orders: {terms}.", Code.LITERAL_NUMBER),
        ("Hello, it takes some weeks for your orders: {terms}.", Code.LITERAL_UNIT),
        ("Hello, the cost is in $ for your orders: {terms}.", Code.LITERAL_UNIT),
        ("Hello, you can withdraw from your orders: {terms}.", Code.LEGAL_WORDING),
        ("Hello, you have the right to return your orders: {terms}.", Code.LEGAL_WORDING),
        ("Hello, the warranty covers your orders: {terms}.", Code.LEGAL_WORDING),
        (
            "Hello, we refund you within the time limit for your orders: {terms}.",
            Code.LEGAL_WORDING,
        ),
    ],
)
def test_customer_message_english_rules(en: str, code: Code) -> None:
    assert code in codes(run(with_message(en=en)))


# ---- LLM-9: one placeholder grammar --------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    ["{refund}", "{{terms}}", "{Terms}", "[terms]", "<b>", "{ terms }", "｛terms｝", "{", "]"],
)
def test_only_the_three_placeholders_are_allowed(token: str) -> None:
    result = run(with_message(it=f"{IT_OK} {token}"))
    assert Code.PLACEHOLDER_SYNTAX in codes(result)


def test_terms_are_required_when_the_decision_restricts() -> None:
    result = run(with_message(it="Ciao, grazie per averci scritto e per la tua pazienza."))
    assert codes(result) == {Code.TERMS_MISSING}


def test_a_placeholder_without_a_value_is_rejected() -> None:
    assert Code.PLACEHOLDER_UNAVAILABLE in codes(run(with_message(en=f"{EN_OK} {{amount}}")))
    q2 = make("Q2")  # standard terms: no {terms} to fill
    out = good_output()
    out["decision_echo"].update(quadrant="Q2", lever_ids=[])
    out["operator_explanation"] = [{"label": "OBSERVED", "fact_ids": [], "text": "Q2 case."}]
    assert Code.PLACEHOLDER_UNAVAILABLE in codes(validate(out, q2.expected, q2.case))
    guarantee = make("guarantee")  # no refund deadline on the guarantee path
    out["decision_echo"].update(path="guarantee", quadrant=None)
    out["customer_message"] = {
        "it": "Ciao, grazie per averci scritto: ti aggiorniamo per il {deadline}.",
        "en": "Hello, thank you for writing to us: we will update you by {deadline}.",
    }
    result = validate(out, guarantee.expected, guarantee.case)
    assert codes(result) == {Code.PLACEHOLDER_UNAVAILABLE}


def test_amount_is_available_when_the_refund_is_known() -> None:
    from decimal import Decimal

    built = make("Q4_cluster", refund_eur=Decimal("59.90"))
    out = with_message(en=f"{EN_OK} Your refund of {{amount}} is due by {{deadline}}.")
    assert validate(out, built.expected, built.case).ok


# ---- Language and length ------------------------------------------------------------------------


def test_llm06_messages_in_the_wrong_language_are_regenerated() -> None:
    result = run(with_message(it=EN_OK, en=IT_OK))
    assert {e.where for e in result.errors if e.code is Code.WRONG_LANGUAGE} == {
        "customer_message.it",
        "customer_message.en",
    }
    assert result.action is Action.REGENERATE


def test_too_long_message() -> None:
    result = run(with_message(it=IT_OK + " ok" * 120))
    assert Code.TOO_LONG in codes(result)


# ---- BLOCK: the case must say what the engine decided -------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(quadrant="Q1"),
        lambda c: c.update(path="guarantee"),
        lambda c: c.update(evidence_quality="insufficient"),
        lambda c: c["levers"].pop(),
        lambda c: c["levers"][0].update(value=30),
        lambda c: c["levers"][0].update(id="welcome_coupon"),
        lambda c: c["placeholders"].update(available=["terms"]),
        lambda c: c["placeholders"].update(required=[]),
        lambda c: c.pop("facts"),
        lambda c: c["facts"][0].update(value=True),
        lambda c: c["facts"][0].update(value="0.87"),
        lambda c: c["facts"][1].update(id="F1"),
        lambda c: c.pop("placeholders"),
    ],
)
def test_a_case_that_differs_from_the_decision_blocks(mutate: Any) -> None:
    case = copy.deepcopy(GOLDEN.case)
    mutate(case)
    result = validate(good_output(), GOLDEN.expected, case)
    assert codes(result) == {Code.CASE_INVALID}
    assert result.action is Action.BLOCK
    assert check_case(case, GOLDEN.expected)


def test_check_case_accepts_the_built_case() -> None:
    assert check_case(GOLDEN.case, GOLDEN.expected) == []
