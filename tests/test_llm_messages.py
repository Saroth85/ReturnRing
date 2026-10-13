"""Deterministic customer messages: rendering from the Decision (LLM-8, LLM-10), the legal clause
(LEG-06, LLM-11), templates (LLM-12) and the prompt of spec 19.D with the phrasebook amendment of
ADR-14."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from contracts.levers import LEVERS, LeverObject
from llm.legal_clause import (
    CLAUSES,
    LegalClauseError,
    append_clause,
    assert_clause,
    clause,
    required_clause,
)
from llm.messages import (
    MessageError,
    available_placeholders,
    format_amount,
    format_date,
    render,
    template_fields,
    term_phrases,
)
from llm.phrasebook import render_selection
from llm.prompts import SYSTEM_PROMPT
from llm.templates import format_value, output_from_case, template_output
from llm.text import LANGS, Lang, word_count
from llm.validator import validate
from policy.engine import decide
from policy.types import Levers
from tests.factories import BASE, THRESHOLDS, TODAY, request, scores
from tests.llm_factories import ROOT, SCENARIOS, make

SPEC = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8")


def _section(title: str) -> str:
    start = SPEC.index(title)
    return SPEC[start : SPEC.index("\n### ", start + 1)]


def _spec_clause(prefix: str) -> str:
    """The approved text, read from docs/spec.md appendix 19.C (the source of truth)."""
    line = next(
        x for x in _section("### C. Clausola legale fissa").splitlines() if x.startswith(prefix)
    )
    return line.removeprefix(prefix)


# ---- LEG-06 and LLM-11: the fixed clauses are the texts of spec 19.C ---------------------------


def test_leg06_withdrawal_clause_is_the_approved_text() -> None:
    assert clause("withdrawal", "it") == _spec_clause("IT — ")
    assert clause("withdrawal", "en") == _spec_clause("EN — ")
    assert clause("withdrawal", "it").count("14 giorni") == 2
    assert clause("withdrawal", "en").count("14 days") == 2


def test_leg06_guarantee_clause_is_the_proposed_text_of_19c() -> None:
    assert clause("guarantee", "it") == _spec_clause("IT garanzia — ")
    assert clause("guarantee", "en") == _spec_clause("EN garanzia — ")
    section = _section("### C. Clausola legale fissa")
    assert "sempre sul percorso di garanzia" in section  # its trigger
    assert "D-14" in section  # still a draft


def test_llm11_guarantee_clause_follows_article_13() -> None:
    it, en = clause("guarantee", "it"), clause("guarantee", "en")
    # Repair or replacement first, at no cost; reduction or termination only where applicable.
    assert it.index("riparazione") < it.index("riduzione del prezzo") < it.index("risoluzione")
    assert "senza spese" in it and "lieve entità" in it and "Nei casi previsti" in it
    assert en.index("repair") < en.index("price reduction") < en.index("end the contract")
    assert "free of charge" in en and "unless the defect is minor" in en
    assert "Where the law provides" in en
    assert "rimborso" not in it and "refund" not in en  # no refund as a free choice


@pytest.mark.parametrize(
    "name, kind",
    [
        ("Q1", None),
        ("Q2", None),
        ("Q3", "withdrawal"),
        ("Q3_cluster", "withdrawal"),
        ("Q4", "withdrawal"),
        ("Q4_cluster", "withdrawal"),
        ("insufficient", None),
        ("guarantee", "guarantee"),
        ("guarantee_insufficient", "guarantee"),
    ],
)
def test_llm08_required_clause_per_decision(name: str, kind: str | None) -> None:
    assert required_clause(make(name).decision, BASE) == kind


def test_assert_clause_accepts_exactly_one_clause_at_the_end() -> None:
    for lang in LANGS:
        for kind in CLAUSES:
            assert_clause(append_clause("Testo.", kind, lang), kind, lang)
        assert_clause("Testo.", None, lang)
        assert append_clause("Testo.", None, lang) == "Testo."


@pytest.mark.parametrize(
    "text, kind",
    [
        ("Testo.", "withdrawal"),  # missing
        ("Testo.\n\n" + CLAUSES["withdrawal"]["it"].replace("14", "15"), "withdrawal"),  # altered
        ("Testo.\n\n" + CLAUSES["withdrawal"]["it"][:-1], "withdrawal"),  # truncated
        (CLAUSES["withdrawal"]["it"] + "\n\nTesto.", "withdrawal"),  # not at the end
        ("Testo.\n\n" + CLAUSES["withdrawal"]["it"] * 2, "withdrawal"),  # twice
        ("Testo.\n\n" + CLAUSES["withdrawal"]["it"], "guarantee"),  # wrong clause
        ("Testo.\n\n" + CLAUSES["guarantee"]["it"], None),  # a clause where none is due
    ],
)
def test_assert_clause_raises_on_a_code_bug(text: str, kind: Any) -> None:
    with pytest.raises(LegalClauseError):
        assert_clause(text, kind, "it")


# ---- LLM-10: amounts, terms and deadlines are the template fields --------------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_llm10_rendered_values_are_identical_to_the_template_fields(lang: Lang) -> None:
    built = make("Q4_cluster", refund_eur=Decimal("59.9"))
    fields = template_fields(built.decision, BASE, lang, Decimal("59.9"))
    text = "Rimborso {amount}, previsto per il {deadline}. Prossimi ordini: {terms}."
    final = render(text, built.decision, BASE, lang, Decimal("59.9"))
    body = final.split("\n\n")[0]
    assert body == (
        f"Rimborso {fields['amount']}, previsto per il {fields['deadline']}. "
        f"Prossimi ordini: {fields['terms']}."
    )
    assert final.endswith(clause("withdrawal", lang))
    expected_amount = {"it": "59,90 €", "en": "€59.90"}[lang]
    expected_deadline = {"it": "28 ottobre 2026", "en": "28 October 2026"}[lang]
    assert fields["amount"] == expected_amount
    assert fields["deadline"] == expected_deadline
    assert built.decision.refund_due_by == date(2026, 10, 28)


def test_terms_field_lists_the_q4_levers() -> None:
    fields = template_fields(make("Q4_cluster").decision, BASE, "it")
    assert fields["terms"] == (
        "reso entro 14 giorni dalla consegna; spedizione del reso a tuo carico; "
        "rimborso nei tempi di legge, non istantaneo; codici sconto non utilizzabili"
    )
    assert "amount" not in fields  # refund amount unknown


def test_fields_exist_exactly_for_the_available_placeholders() -> None:
    for name in SCENARIOS:
        built = make(name, refund_eur=Decimal("10"))
        for lang in LANGS:
            fields = template_fields(built.decision, BASE, lang, Decimal("10"))
            assert set(fields) == built.expected.placeholders
            assert set(built.case["placeholders"]["available"]) == set(fields)


def test_guarantee_fields_and_clause() -> None:
    built = make("guarantee")
    assert template_fields(built.decision, BASE, "en", Decimal("10")) == {}
    final = render("We are sorry about the problem.", built.decision, BASE, "en")
    assert final.endswith("\n\n" + clause("guarantee", "en"))


def test_q2_renders_without_a_clause() -> None:
    built = make("Q2")
    final = render("Ciao, il rimborso è previsto per il {deadline}.", built.decision, BASE, "it")
    assert final == "Ciao, il rimborso è previsto per il 28 ottobre 2026."


@pytest.mark.parametrize(
    "text",
    [
        "Refund {refund}.",  # unknown placeholder
        "Value {amount}. {terms}",  # amount not available
        "Hello, no terms here.",  # {terms} required for Q4
        "Terms <b>{terms}</b>.",  # stray tokens
    ],
)
def test_render_uses_the_validator_grammar(text: str) -> None:
    with pytest.raises(MessageError):
        render(text, make("Q4_cluster").decision, BASE, "en")


def test_render_never_fills_a_placeholder_twice() -> None:
    built = make("Q4_cluster")
    final = render("{terms} {deadline}", built.decision, BASE, "en")
    assert "{" not in final


def test_amount_and_date_formats() -> None:
    assert format_amount(Decimal("42.105"), "it") == "42,11 €"
    assert format_amount(Decimal("0"), "en") == "€0.00"
    for bad in (Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(MessageError):
            format_amount(bad, "en")
    assert format_date(date(2026, 1, 5), "it") == "5 gennaio 2026"
    assert format_date(date(2026, 12, 31), "en") == "31 December 2026"


def test_every_catalogue_lever_has_a_phrase_or_is_not_a_term() -> None:
    not_terms = {
        ("transparency_notice", True),
        ("transparency_notice", False),
        ("size_advice", True),
        ("size_advice", False),
        ("cluster_coupons_disabled", False),
        ("priority_size_exchange", False),
    }
    for lever in LEVERS:
        values: list[int | bool] = [60] if lever.value_type == "integer" else [True, False]
        for value in values:
            obj = LeverObject(id=lever.id, value=value, scope=lever.scope)
            phrases = {lang: term_phrases([obj], lang) for lang in LANGS}
            if (lever.id, value) in not_terms:
                assert phrases == {"it": [], "en": []}
            else:
                assert len(phrases["it"]) == len(phrases["en"]) == 1, lever.id


def test_duplicate_coupon_phrases_are_merged() -> None:
    levers = [
        LeverObject(id="coupons_enabled", value=False, scope="future_orders"),
        LeverObject(id="cluster_coupons_disabled", value=True, scope="future_orders_cluster"),
    ]
    assert term_phrases(levers, "en") == ["discount codes not available"]


def test_available_placeholders_rules() -> None:
    window = [LeverObject(id="return_window_days", value=60, scope="future_orders")]
    notice = [LeverObject(id="transparency_notice", value=True, scope="communication")]
    assert available_placeholders("withdrawal", window, True) == {"terms", "deadline", "amount"}
    assert available_placeholders("withdrawal", notice, False) == {"deadline"}
    assert available_placeholders("guarantee", [], True) == frozenset()


def test_restriction_without_term_changes_does_not_require_terms() -> None:
    """A Q3 decision on a base without instant refund changes only the transparency notice."""
    base = replace(BASE, instant_refund=False)
    req = request(terms=base)
    decision = decide(req, scores(0.9, "500"), base, THRESHOLDS, TODAY)
    assert decision.levers.to_contract(base) == [
        LeverObject(id="transparency_notice", value=True, scope="communication")
    ]
    final = render("Ciao, grazie per averci scritto.", decision, base, "it")
    assert final.endswith(clause("withdrawal", "it"))


def test_standard_levers_have_no_terms() -> None:
    assert term_phrases(Levers.standard(BASE).to_contract(BASE), "it") == []


# ---- LLM-12: templates ------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_llm12_every_template_validates_renders_and_stays_short(name: str) -> None:
    built = make(name)
    output = template_output(built.case, built.expected)
    result = validate(output, built.expected, built.case)
    assert result.ok, result.errors
    kind = required_clause(built.decision, BASE)
    for lang in LANGS:
        final = render_selection(
            output["customer_message"]["sentence_ids"], built.decision, BASE, lang
        )
        assert_clause(final, kind, lang)
        assert word_count(final) <= 120, (name, lang, word_count(final))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_mock_output_from_the_case_equals_the_template(name: str) -> None:
    built = make(name)
    assert output_from_case(built.case) == template_output(built.case, built.expected)


def test_templates_with_a_known_refund_still_validate() -> None:
    built = make("Q1", refund_eur=Decimal("80"))
    assert validate(template_output(built.case, built.expected), built.expected, built.case).ok


def test_format_value() -> None:
    assert format_value(42.1) == "42.1"
    assert format_value(0.87) == "0.87"
    assert format_value(7) == "7"
    assert format_value(100.0) == "100"


# ---- Spec 19.D: the system prompt ----------------------------------------------------------------


# The phrasebook amendment of ADR-14 (round 2, finding R1) replaced the customer-message lines of
# the 19.D draft: the model picks sentence ids and writes no customer prose. Until 19.D is
# updated, every other line stays identical to the spec, and the replaced lines are exactly these.
REPLACED_19D_LINES = (
    "3. customer_message: a short, warm message",
    "Rules for the customer message:",
    "- Never write numbers, in digits or in words",
    "- Never write about withdrawal rights",
)
ADDED_LINES = (
    "3. customer_message: choose sentence_ids for a short, warm message",
    "- Use only the letters a-z and the Italian accented vowels",
)


def test_system_prompt_is_the_text_of_spec_19d_with_the_phrasebook_amendment() -> None:
    section = _section("### D. Prompt di sistema di Gemma")
    spec = [x for x in section.split("```text\n", 1)[1].split("\n```", 1)[0].splitlines() if x]
    prompt = [x for x in SYSTEM_PROMPT.splitlines() if x]
    removed = [line for line in spec if line not in prompt]
    added = [line for line in prompt if line not in spec]
    assert len(removed) == len(REPLACED_19D_LINES)
    assert all(line.startswith(p) for line, p in zip(removed, REPLACED_19D_LINES, strict=True))
    assert all(line.startswith(p) for line, p in zip(added, ADDED_LINES, strict=True))
    assert [x for x in spec if x in prompt] == [x for x in prompt if x in spec]  # same order
    for rule in ("<customer_text>", "linked accounts", "sentence_ids", "fact_ids"):
        assert rule in SYSTEM_PROMPT
    assert "placeholders" not in SYSTEM_PROMPT  # code places them, the model never does
