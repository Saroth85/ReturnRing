"""The customer phrasebook (llm/phrasebook.py; adversarial review of 13 Oct, round 2, finding R1).

- Every sentence passes every text check of llm/text.py, in both languages.
- Every allowed selection, for every kind of engine decision, renders with the required legal
  clause, keeps amounts, terms and deadlines identical to the template fields (LLM-10), is in the
  right language and stays within 120 words before the clause.
- A selection that is unknown, repeated, in the wrong slot or not allowed is refused.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import pytest

from llm.decision import template_key
from llm.legal_clause import CLAUSES, assert_clause, required_clause
from llm.messages import (
    MessageError,
    placeholder_problems,
    placeholders_in,
    render,
    template_fields,
)
from llm.phrasebook import (
    ALL_KINDS,
    MAX_EXTRAS,
    MAX_SENTENCES,
    MIN_SENTENCES,
    PHRASEBOOK,
    SENTENCE_IDS,
    SENTENCES,
    SLOTS,
    MessageContext,
    allowed,
    allowed_sentences,
    compose,
    context_of,
    context_of_case,
    mandatory_body,
    phrase_problems,
    render_selection,
    selection_problems,
)
from llm.templates import DEFAULT_SELECTION, default_selection
from llm.text import LANGS, Lang, language_of, numeric_characters, word_count
from tests.factories import BASE
from tests.llm_factories import SCENARIOS, Built, make

MAX_CUSTOMER_WORDS = 120  # spec 8.7, before the fixed legal clause
REFUND = Decimal("59.9")


def selections(ctx: MessageContext, *, ordered: bool = True) -> Iterator[list[str]]:
    """Every allowed selection: one opening, up to three extras (in any order, or one order per
    set of extras), one closing."""
    allowed_now = allowed_sentences(ctx)
    by_slot = {slot: [s.id for s in allowed_now if s.slot == slot] for slot in SLOTS}
    choose = itertools.permutations if ordered else itertools.combinations
    for opening in by_slot["opening"]:
        for count in range(MAX_EXTRAS + 1):
            for extras in choose(by_slot["extra"], count):
                for closing in by_slot["closing"]:
                    yield [opening, *extras, closing]


def built_cases() -> Iterator[tuple[str, Built, Decimal | None]]:
    for name in sorted(SCENARIOS):
        yield name, make(name), None
        yield f"{name}+refund", make(name, refund_eur=REFUND), REFUND


# ---- The sentences --------------------------------------------------------------------------


def test_the_phrasebook_is_small_and_covers_every_slot_for_every_kind() -> None:
    assert 15 <= len(SENTENCES) <= 25
    assert tuple(s.id for s in SENTENCES) == SENTENCE_IDS
    assert len(set(SENTENCE_IDS)) == len(SENTENCE_IDS)
    for sentence in SENTENCES:
        assert re.fullmatch(r"[A-Z]+(?:_[A-Z]+)+", sentence.id), sentence.id
        assert sentence.slot in SLOTS
        assert sentence.kinds and sentence.kinds <= ALL_KINDS
    for name, built, _ in built_cases():
        ctx = context_of(built.expected)
        for slot in ("opening", "closing"):
            assert any(s.slot == slot for s in allowed_sentences(ctx)), (name, slot)


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("sentence_id", SENTENCE_IDS)
def test_every_sentence_passes_every_text_check(sentence_id: str, lang: Lang) -> None:
    """No number, unit, date, term, legal or decision wording, banned term, letter of another
    script or wrong language, and no placeholder: the sentence is pure connective text."""
    text = PHRASEBOOK[sentence_id].text(lang)
    assert phrase_problems(text, lang) == []
    assert placeholders_in(text) == []
    assert placeholder_problems(text, (), False) == []
    assert text[-1] in ".!?"


@pytest.mark.parametrize("lang", LANGS)
def test_every_body_sentence_and_every_adjacent_pair_passes_the_text_checks(lang: Lang) -> None:
    """The body is code text, but it shares the message with the chosen sentences: each body
    sentence, and each pair of parts that can stand side by side, passes the same checks, so no
    pattern of llm/text.py can form across a sentence boundary."""
    pairs: set[tuple[str, str]] = set()
    for _, built, _ in built_cases():
        ctx = context_of(built.expected)
        body = mandatory_body(ctx, lang)
        for part in body:
            assert phrase_problems(part, lang) == [], part
        for ids in selections(ctx):
            texts = [PHRASEBOOK[i].text(lang) for i in ids]
            parts = [texts[0], *body, *texts[1:]]
            pairs.update(zip(parts, parts[1:], strict=False))
    for first, second in sorted(pairs):
        assert phrase_problems(f"{first} {second}", lang) == [], (first, second)


# ---- Every allowed combination for every engine decision kind --------------------------------


def distinct_cases() -> dict[str, tuple[Built, Decimal | None]]:
    """One scenario per distinct message context (Q4 and Q4_negative_margin, for example, give
    the same messages): every kind of engine decision, with and without a known refund."""
    cases: dict[str, tuple[Built, Decimal | None]] = {}
    seen: set[MessageContext] = set()
    for name, built, refund in built_cases():
        ctx = context_of(built.expected)
        if ctx not in seen:
            seen.add(ctx)
            cases[name] = (built, refund)
    return cases


CASES = distinct_cases()


def test_the_distinct_cases_cover_every_kind() -> None:
    kinds = {context_of(built.expected).kind for built, _ in CASES.values()}
    assert kinds == ALL_KINDS
    assert {refund for _, refund in CASES.values()} == {None, REFUND}


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_allowed_selection_renders_with_the_clause_and_the_template_fields(
    name: str,
) -> None:
    """Every ordered selection: the required clause, once, at the end; each template field
    exactly once (LLM-10); no stray placeholder. Every set of sentences (order does not change
    them): within 120 words before the clause, in the right language, no digit but those of the
    fields."""
    built, refund = CASES[name]
    ctx = context_of(built.expected)
    kind = required_clause(built.decision, BASE)
    fields = {lang: template_fields(built.decision, BASE, lang, refund) for lang in LANGS}
    count = 0
    for ids in selections(ctx):
        assert selection_problems(ids, ctx) == []
        for lang in LANGS:
            final = render(compose(ids, ctx, lang), built.decision, BASE, lang, refund)
            assert_clause(final, kind, lang)
            assert all(final.count(value) == 1 for value in fields[lang].values()), (ids, lang)
            assert not set("{}[]<>") & set(final)
        count += 1
    assert count > 0
    for ids in selections(ctx, ordered=False):
        assert render_selection(ids, built.decision, BASE, "it", refund) == render(
            compose(ids, ctx, "it"), built.decision, BASE, "it", refund
        )
        for lang in LANGS:
            final = render(compose(ids, ctx, lang), built.decision, BASE, lang, refund)
            message = final.split("\n\n")[0] if kind else final
            assert word_count(message) <= MAX_CUSTOMER_WORDS, (ids, lang)
            assert language_of(message) == lang, (ids, lang)
            rest = message
            for value in fields[lang].values():
                rest = rest.replace(value, " ")
            assert numeric_characters(rest) == [], (ids, lang)


def test_the_body_writes_the_fields_the_decision_has() -> None:
    q4 = make("Q4_cluster", refund_eur=REFUND)
    text = compose(["OPEN_THANKS", "CLOSE_REGARDS"], context_of(q4.expected), "en")
    assert placeholders_in(text) == ["amount", "deadline", "terms"]
    assert "shown before each purchase: {terms}." in text
    assert "These terms take into account the returns on your previous orders." in text
    q2 = make("Q2")
    text = compose(["OPEN_THANKS", "CLOSE_REGARDS"], context_of(q2.expected), "it")
    assert placeholders_in(text) == ["deadline"]
    insufficient = make("insufficient")
    text = compose(["OPEN_THANKS", "CLOSE_REGARDS"], context_of(insufficient.expected), "en")
    assert "The return terms of your orders stay the same as always." in text
    guarantee = make("guarantee")
    text = compose(["OPEN_SORRY_ITEM", "CLOSE_PATIENCE"], context_of(guarantee.expected), "it")
    assert placeholders_in(text) == []
    assert "Abbiamo registrato la tua segnalazione" in text


def test_terms_that_change_without_a_restriction_use_the_neutral_sentence() -> None:
    ctx = MessageContext("Q2", frozenset({"return_window_days:45"}), frozenset({"terms"}), False)
    assert (
        mandatory_body(ctx, "en")[1] == "For your next orders the return terms are these: {terms}."
    )
    q1 = context_of(make("Q1").expected)
    assert "better return terms: {terms}." in mandatory_body(q1, "en")[1]


def test_the_composed_order_is_opening_body_extras_closing() -> None:
    built = make("Q3")
    ctx = context_of(built.expected)
    text = compose(["OPEN_CHOICE", "THANKS_FEEDBACK", "HELP_SIZE", "CLOSE_SOON"], ctx, "en")
    assert text.startswith(PHRASEBOOK["OPEN_CHOICE"].en + " " + mandatory_body(ctx, "en")[0])
    assert text.endswith(
        " ".join(PHRASEBOOK[i].en for i in ("THANKS_FEEDBACK", "HELP_SIZE", "CLOSE_SOON"))
    )


# ---- Refused selections -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, ids, reason",
    [
        ("Q4_cluster", ["OPEN_THANKS"], "1 ids"),
        (
            "Q4_cluster",
            [
                "OPEN_THANKS",
                "HELP_SIZE",
                "THANKS_FEEDBACK",
                "CONTACT_TEAM",
                "CONTACT_REPLY",
                "CLOSE_DAY",
            ],
            "6 ids",
        ),
        ("Q4_cluster", ["OPEN_THANKS", "Hello", "CLOSE_DAY"], "Hello: unknown"),
        (
            "Q4_cluster",
            ["OPEN_THANKS", "THANKS_FEEDBACK", "THANKS_FEEDBACK", "CLOSE_DAY"],
            "FEEDBACK: repeated",
        ),
        ("Q4_cluster", ["CLOSE_DAY", "OPEN_THANKS"], "used as the opening"),
        ("Q4_cluster", ["OPEN_THANKS", "OPEN_MESSAGE", "CLOSE_DAY"], "used as the extra"),
        ("Q4_cluster", ["OPEN_THANKS", "HELP_SIZE"], "used as the closing"),
        ("Q4_cluster", ["OPEN_SORRY_ITEM", "CLOSE_DAY"], "OPEN_SORRY_ITEM: not allowed"),
        ("Q4_cluster", ["OPEN_THANKS", "GUARANTEE_PHOTO", "CLOSE_DAY"], "not allowed"),
        ("Q4_cluster", ["OPEN_THANKS", "CLOSE_PATIENCE"], "not allowed"),
        ("guarantee", ["OPEN_SORRY_FIT", "CLOSE_PATIENCE"], "OPEN_SORRY_FIT: not allowed"),
        ("guarantee", ["OPEN_THANKS", "HELP_SIZE", "CLOSE_DAY"], "HELP_SIZE: not allowed"),
        ("guarantee", ["OPEN_THANKS", "CLOSE_HAPPY"], "CLOSE_HAPPY: not allowed"),
        ("Q2", ["OPEN_THANKS", "INFO_TERMS", "CLOSE_DAY"], "INFO_TERMS: not allowed"),
        ("insufficient", ["OPEN_THANKS", "INFO_TERMS", "CLOSE_DAY"], "INFO_TERMS: not allowed"),
    ],
)
def test_refused_selections(name: str, ids: list[str], reason: str) -> None:
    ctx = context_of(make(name).expected)
    problems = selection_problems(ids, ctx)
    assert any(reason in p for p in problems), problems
    with pytest.raises(MessageError):
        compose(ids, ctx, "it")


def test_lever_conditions_rule_out_a_sentence() -> None:
    q2 = context_of(make("Q2").expected)
    assert allowed(PHRASEBOOK["HELP_SIZE"], q2) and allowed(PHRASEBOOK["HELP_ADVICE"], q2)
    no_exchange = replace(q2, lever_ids=frozenset({"free_size_exchange:false"}))
    no_advice = replace(q2, lever_ids=frozenset({"size_advice:false"}))
    assert not allowed(PHRASEBOOK["HELP_SIZE"], no_exchange)
    assert not allowed(PHRASEBOOK["HELP_ADVICE"], no_advice)
    assert default_selection(no_exchange) == ["OPEN_THANKS", "CLOSE_REGARDS"]
    assert default_selection(q2) == list(DEFAULT_SELECTION["Q2"])


# ---- Contexts and defaults ----------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_case_and_the_engine_decision_give_the_same_context(name: str) -> None:
    for refund in (None, REFUND):
        built = make(name, refund_eur=refund)
        assert context_of_case(built.case) == context_of(built.expected)
        assert context_of(built.expected).kind == template_key(
            built.expected.path, built.expected.evidence_quality, built.expected.quadrant
        )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_default_selections_are_allowed_and_fit_with_the_clause(name: str) -> None:
    built = make(name)
    ctx = context_of(built.expected)
    ids = default_selection(ctx)
    assert selection_problems(ids, ctx) == []
    for lang in LANGS:
        final = render_selection(ids, built.decision, BASE, lang)
        assert word_count(final) <= MAX_CUSTOMER_WORDS, (name, lang)  # with the clause too


def test_selection_bounds_match_the_contract() -> None:
    assert (MIN_SENTENCES, MAX_SENTENCES) == (2, 2 + MAX_EXTRAS)
    assert set(DEFAULT_SELECTION) == ALL_KINDS


def test_render_selection_appends_the_clause_once() -> None:
    built = make("Q4_cluster")
    final = render_selection(["OPEN_THANKS", "CLOSE_REGARDS"], built.decision, BASE, "it")
    assert final.endswith("\n\n" + CLAUSES["withdrawal"]["it"])
    assert final.count(CLAUSES["withdrawal"]["it"]) == 1
