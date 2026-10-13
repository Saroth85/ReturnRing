"""The output validator (docs/spec.md 8.7, ADR-14): LLM-02 decision echo, LLM-03 number grounding,
LLM-04 banned terms, LLM-06 language, and the customer-message rules of LLM-10 (review findings
LLM-1, LLM-3 to LLM-6, LLM-9 and LLM-13, and the adversarial review of 13 Oct)."""

from __future__ import annotations

import copy
import json
import re
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llm.templates import template_output
from llm.validator import (
    ACTIONS,
    Action,
    Code,
    ValidationResult,
    check_case,
    lever_claims,
    validate,
)
from tests.llm_factories import SCENARIOS, Built, good_output, make

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
        Code.TERMS_WORDING,
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


# Adversarial review of 13 Oct, finding 3: the explanation names another quadrant or lever.
def with_extra_statement(built: Built, text: str) -> dict[str, Any]:
    """The template output plus one statement that cites the window fact when there is one."""
    out = template_output(built.case, built.expected)
    window = [f["id"] for f in built.case["facts"] if f["metric"] == "return_window_days"]
    out["operator_explanation"].append({"label": "OBSERVED", "fact_ids": window, "text": text})
    return out


@pytest.mark.parametrize(
    "name, text",
    [
        ("Q4_cluster", "The rules engine placed the case in Q1, so no restriction applies."),
        ("Q4_cluster", "The case sits in quadrant 2."),
        ("Q4_cluster", "In quadrant three the instant refund is not available."),
        ("Q1", "The case sits in the fourth quadrant."),
        ("Q4_cluster", "The levers are instant_refund:true and coupons_enabled:true."),
        ("Q4_cluster", "instant_refund is true for future orders."),
        ("Q4_cluster", "The return window is 30 days."),
        ("Q4_cluster", "return_window_days:30 applies to future orders."),
        ("Q4_cluster", "Coupons stay enabled."),
        ("Q4_cluster", "Instant refund remains available."),
        ("Q4_cluster", "The return window of 7 days applies."),
        ("Q4", "Cluster coupons disabled for the future orders."),
        ("Q3", "No free return shipping for future orders."),
        ("Q3", "free_return_shipping = false"),
        ("Q1", "The rules engine placed the case in Q4 with instant_refund:false."),
        ("Q1", "Coupons disabled for this customer."),
        ("Q2", "Q2 with transparency_notice: true."),
        ("insufficient", "The rules engine placed the case in Q4 with instant_refund:false."),
        ("insufficient", "Placed in Q3 for now."),
        ("guarantee", "The rules engine placed the case in Q4 with instant_refund:false."),
        (
            "guarantee",
            "The rules engine placed the case in Q4 with instant_refund:false and coupons "
            "disabled.",
        ),
        ("guarantee", "No instant refund on future orders."),
        ("guarantee_insufficient", "Without free size exchange."),
    ],
)
def test_finding3_explanation_naming_another_decision_uses_the_template(
    name: str, text: str
) -> None:
    built = make(name)
    result = validate(with_extra_statement(built, text), built.expected, built.case)
    assert Code.DECISION_ECHO in codes(result), result.errors
    assert result.action is Action.TEMPLATE


@pytest.mark.parametrize(
    "text, claims",
    [
        ("instant_refund:false", ["instant_refund:false"]),
        ("Enabled coupons for the cluster.", ["coupons_enabled:true"]),
        ("coupons_enabled", ["coupons_enabled:true"]),
        ("cluster_coupons_disabled: false", ["cluster_coupons_disabled:false"]),
        ("The return window of 30 days.", ["return_window_days:30"]),
        ("No return window change.", []),  # an integer lever needs a number
        ("The welcome coupon was used by 7 linked accounts.", []),
        (
            "Free size exchange granted, priority size exchange on.",
            ["free_size_exchange:true", "priority_size_exchange:true"],
        ),
        ("instant-refund OFF", ["instant_refund:false"]),
        ("instant_refund -> false", ["instant_refund:false"]),
        ("The instant refund lever is off.", ["instant_refund:false"]),
        ("Coupon codes disabled.", ["coupons_enabled:false"]),
    ],
)
def test_lever_claims(text: str, claims: list[str]) -> None:
    assert lever_claims(text) == claims


@pytest.mark.parametrize(
    "name, text",
    [
        ("Q4_cluster", "The rules engine placed the case in Q4 with instant_refund:false."),
        ("Q4_cluster", "Coupons disabled and no instant refund for future orders."),
        ("Q4_cluster", "return_window_days: 14 and free return shipping removed."),
        ("Q4_cluster", "Cluster coupons disabled for every linked account."),
        ("Q1", "Q1: priority_size_exchange is true."),
        ("Q3", "Q3, instant refund off and transparency notice on."),
        ("Q2", "Standard terms: no lever changes."),
        ("guarantee", "The guarantee path has no quadrant and no restrictive levers."),
    ],
)
def test_explanation_naming_the_engine_decision_passes(name: str, text: str) -> None:
    built = make(name)
    result = validate(with_extra_statement(built, text), built.expected, built.case)
    assert result.ok, result.errors


def test_identifiers_are_not_read_as_quadrants() -> None:
    built = make("Q4_cluster", return_id="RET-Q1")
    result = validate(
        with_extra_statement(built, "Case RET-Q1 is in Q4."), built.expected, built.case
    )
    assert result.ok, result.errors


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
        ("Risk .99 of the maximum.", ["F1"]),  # a leading separator
        # Adversarial review of 13 Oct, finding 5: sign flips and scale suffixes
        ("Customer net margin: –42.1 EUR.", ["F2"]),
        ("Customer net margin:-42.1 EUR.", ["F2"]),
        ("Customer net margin is minus 42.1 EUR.", ["F2"]),
        ("Customer net margin is negative 42.1 EUR.", ["F2"]),
        ("Customer net margin: 42.1k EUR.", ["F2"]),
        ("Customer net margin: 42.1M EUR.", ["F2"]),
        ("Customer net margin: 42.1 M EUR.", ["F2"]),
        ("Risk score 0.87%.", ["F1"]),
        ("Customer net margin: ‑42.1 EUR.", ["F2"]),
        ("The first factor is cluster_welcome_coupons, 7.", ["F3"]),  # ordinal
        ("Seven, a fortnight, half of them.", ["F3"]),
        ("Customer net margin: a loss of 42.1 EUR.", ["F2"]),
        ("Customer net margin is negative, 42.1 EUR.", ["F2"]),
    ],
)
def test_llm03_ungrounded_numbers_are_rejected(text: str, fact_ids: list[str]) -> None:
    result = run(with_statement(text, fact_ids, "HYPOTHESIS"))
    assert Code.UNGROUNDED_NUMBER in codes(result)
    assert result.action is Action.REGENERATE


@pytest.mark.parametrize(
    "text, ok",
    [
        ("Customer net margin over the period: -42.1 EUR.", True),
        ("Customer net margin over the period: −42.1 EUR.", True),  # minus sign
        ("Customer net margin over the period: –42.1 EUR.", True),  # en dash read as a sign
        ("Customer net margin over the period: 42.1 EUR.", False),  # the sign dropped
        ("Customer net margin over the period: minus 42.1 EUR.", False),  # words never ground
    ],
)
def test_finding5_a_negative_fact_keeps_its_sign(text: str, ok: bool) -> None:
    built = make("Q4_negative_margin")
    margin = next(f for f in built.case["facts"] if f["metric"] == "value_eur_12m")
    assert margin["value"] == -42.1
    out = template_output(built.case, built.expected)
    out["operator_explanation"] = [{"label": "OBSERVED", "fact_ids": [margin["id"]], "text": text}]
    assert validate(out, built.expected, built.case).ok is ok


def test_llm03_customer_text_policy_text_and_identifiers_never_feed_grounding() -> None:
    built = make("Q4_cluster", customer_text="Ho 99 anni, ordine 555, voglio 300 euro.")
    assert "30" in built.case["policy_text"] and built.case["case_id"] == "RET-1"
    every_fact = [f["id"] for f in built.case["facts"]][:4]
    for number in ("99", "555", "300", "30", "1"):
        result = run(with_statement(f"The figure is {number}.", every_fact, "HYPOTHESIS"), built)
        assert Code.UNGROUNDED_NUMBER in codes(result), number


_TOKENS = st.one_of(
    st.sampled_from(["0.87", "0,87", "42.1", "42.10", "7", "6", "14", "0.9", "99", "1,000", "-7"]),
    st.sampled_from(["–42.1", ":-42.1", "−7", "—0.87", ".87", "42.1k", "42.1M", "7%", "-\u200b7"]),
    st.decimals(min_value=-1000, max_value=1000, places=2, allow_nan=False).map(str),
    st.sampled_from(["risk", "linked", "accounts", "Q4", "RET-1", "R-MARCO", "returns", "of"]),
    st.sampled_from(["minus", "meno", "negative", "negativo", "k", "M", "mln", "x", "EUR"]),
)
_IDENTIFIERS = re.compile(r"\bQ4\b|\bRET-1\b|\bR-MARCO\b")
# Independent reading of a written number: an optional sign word, any dash or minus before the
# digits, a leading separator, and what follows (letters or percent, glued or after a space).
_WRITTEN_NUMBER = re.compile(
    r"(?P<word>(?:minus|meno|negative|negativo)\W*)?"
    r"(?P<sign>[-–—−])?\u200b?(?P<num>[.,]?\d+(?:[.,]\d+)?)(?P<after>%|\s?[^\W\d_]+)?"
)
_SCALE_WORDS = {"k", "m", "mln"}


@settings(max_examples=300, deadline=None)
@given(
    tokens=st.lists(_TOKENS, min_size=1, max_size=8),
    fact_ids=st.lists(st.sampled_from([*FACT_VALUES, "F99"]), max_size=4),
    label=st.sampled_from(["OBSERVED", "SCORE", "HYPOTHESIS"]),
)
def test_llm03_every_number_after_the_validator_is_a_cited_fact(
    tokens: list[str], fact_ids: list[str], label: str
) -> None:
    """LLM-03, 100% after the validator: checked with an independent number pattern, signs,
    sign words and scale suffixes included (adversarial review of 13 Oct, finding 5)."""
    text = " ".join(tokens) + "."
    result = run(with_statement(text, fact_ids, label))
    if not result.ok:
        return
    cited = {FACT_VALUES[f] for f in fact_ids}
    for match in _WRITTEN_NUMBER.finditer(_IDENTIFIERS.sub(" ", text)):
        after = match["after"] or ""
        assert not match["word"], (text, fact_ids)
        assert after == "" or (after[0].isspace() and after.strip().lower() not in _SCALE_WORDS)
        value = Decimal(match["num"].replace(",", "."))
        assert (-value if match["sign"] else value) in cited, (text, fact_ids)


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


# Adversarial review of 13 Oct, finding 4: same-person synonyms (rule 11).
@pytest.mark.parametrize(
    "text",
    [
        "These linked accounts are one person.",
        "These linked accounts are the same customer.",
        "One customer controls all linked accounts.",
        "You are the same customer as your linked accounts.",
        "We noticed that your linked accounts behave like a single customer.",
    ],
)
def test_finding4_same_person_synonyms_in_statements_and_messages(text: str) -> None:
    assert Code.BANNED_TERM in codes(run(with_statement(text, [], "HYPOTHESIS")))
    assert Code.BANNED_TERM in codes(run(with_message(en=f"{EN_OK} {text}")))


@pytest.mark.parametrize(
    "it",
    [
        "Lo stesso cliente ha più account collegati.",
        "Un cliente controlla tutti gli account collegati.",
        "Gli account collegati sono di un unico utente.",
    ],
)
def test_finding4_same_person_synonyms_in_italian_messages(it: str) -> None:
    assert Code.BANNED_TERM in codes(run(with_message(it=f"{IT_OK} {it}")))


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


# Adversarial review of 13 Oct, finding 1: terms and the decision in words (rules 1, 3 and 10).
FINDING1_IT = (
    "Abbiamo approvato il tuo rimborso immediato e la spedizione del reso resta gratuita, come "
    "sempre. Per i prossimi ordini: {terms}."
)


@pytest.mark.parametrize(
    "it, en",
    [
        (FINDING1_IT, None),
        # paraphrases of LLM-05 #1 ("approva il rimborso immediato")
        (
            "Ciao, il tuo rimborso è stato accettato e arriverà subito. Prossimi ordini: {terms}.",
            None,
        ),
        (
            "Ciao, il rimborso è approvato, lo ricevi istantaneo. Prossimi ordini: {terms}.",
            None,
        ),
        ("Ciao, nessun problema: il reso resta gratis per i tuoi prossimi ordini: {terms}.", None),
        ("Ciao, i coupon restano attivi per i tuoi prossimi ordini: {terms}.", None),
        ("Ciao, il cambio taglia resta per i tuoi prossimi ordini: {terms}.", None),
        ("Ciao, la finestra di reso resta ampia per i tuoi prossimi ordini: {terms}.", None),
        ("Ciao, abbiamo respinto la richiesta per i tuoi prossimi ordini: {terms}.", None),
        ("Ciao, il tuo account è bloccato per i tuoi prossimi ordini: {terms}.", None),
        ("Ciao, è in corso un'indagine sui tuoi ordini: {terms}.", None),
        (None, "Hello, we approved your instant refund and return shipping stays free: {terms}."),
        (None, "Hello, your refund is accepted and will arrive immediately: {terms}."),
        (None, "Hello, your coupons and discounts stay active for your next orders: {terms}."),
        (None, "Hello, your size exchange stays free for your next orders: {terms}."),
        (None, "Hello, we rejected your request for your next orders: {terms}."),
        (None, "Hello, your refund was denied and your account is blocked: {terms}."),
        (None, "Hello, we are investigating your orders: {terms}."),
        (None, "Hello, we declined the return for your next orders: {terms}."),
    ],
)
def test_finding1_terms_and_decisions_in_words_are_regenerated(
    it: str | None, en: str | None
) -> None:
    result = run(with_message(it=it, en=en))
    assert Code.TERMS_WORDING in codes(result), result.errors
    assert not result.ok and result.action is Action.REGENERATE


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_templates_have_no_terms_wording(name: str) -> None:
    built = make(name)
    result = validate(template_output(built.case, built.expected), built.expected, built.case)
    assert result.ok, result.errors


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
    out["operator_explanation"] = [{"label": "OBSERVED", "fact_ids": [], "text": "A defect."}]
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
