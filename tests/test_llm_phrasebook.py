"""The customer phrasebook (llm/phrasebook.py; adversarial review of 13 Oct, round 2, finding R1;
round 3, findings T1 to T5).

- Every sentence passes every text check of llm/text.py, in both languages.
- Every allowed selection, for every kind of engine decision, on several merchant bases and with
  and without a known refund, renders with the required legal clause, keeps amounts, terms and
  deadlines identical to the template fields (LLM-10), is in the right language and stays within
  120 words before the clause.
- The transparency notice never reads as if the current refund depended on past returns, on any
  base of the sweep: alone it names the next orders, "these terms" comes only after {terms}.
- A selection that is unknown, repeated, in the wrong slot, not allowed or incoherent (two
  requests to reply, "thanks again" without a thanks) is refused.
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
    REPLY,
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
    selection_rules,
)
from llm.templates import DEFAULT_SELECTION, default_selection
from llm.text import LANGS, Lang, language_of, numeric_characters, word_count
from policy.types import ReturnTerms
from tests.factories import BASE
from tests.llm_factories import SCENARIOS, Built, make

MAX_CUSTOMER_WORDS = 120  # spec 8.7, before the fixed legal clause
REFUND = Decimal("59.9")
# The merchant bases of the exhaustive render: Navigli, a base without instant refund and a
# legal-minimum base (round 3, finding T1), a base without free size exchange (finding T5).
BASES = (
    BASE,
    ReturnTerms(30, True, False, True, True),
    ReturnTerms(14, False, False, True, False),
    ReturnTerms(30, True, True, False, True),
)
# The sweep of the body checks: three windows and every combination of the four boolean terms.
SWEEP = tuple(
    ReturnTerms(window, *flags)
    for window in (14, 30, 45)
    for flags in itertools.product((True, False), repeat=4)
)
NOTICE_AFTER_TERMS = {
    "it": "Queste condizioni tengono conto dei tuoi resi precedenti.",
    "en": "These terms take into account your previous returns.",
}
RESTRICTIVE_TERMS = {
    "it": (
        "I tuoi prossimi ordini avranno queste condizioni di reso, mostrate prima "
        "dell'acquisto: {terms}."
    ),
    "en": "Your next orders will have these return terms, shown before each purchase: {terms}.",
}
NOTICE_ALONE = {
    "it": "Le condizioni di reso dei tuoi prossimi ordini tengono conto dei tuoi resi precedenti.",
    "en": "The return terms of your next orders take into account your previous returns.",
}


def selections(ctx: MessageContext, *, ordered: bool = True) -> Iterator[list[str]]:
    """Every allowed selection: one opening, up to three extras (two in Q3 and Q4; in any order,
    or one order per set of extras), one closing, without the incoherent ones."""
    allowed_now = allowed_sentences(ctx)
    by_slot = {slot: [s.id for s in allowed_now if s.slot == slot] for slot in SLOTS}
    choose = itertools.permutations if ordered else itertools.combinations
    for opening in by_slot["opening"]:
        for count in range(MAX_EXTRAS + 1):
            for extras in choose(by_slot["extra"], count):
                for closing in by_slot["closing"]:
                    ids = [opening, *extras, closing]
                    if not selection_problems(ids, ctx):
                        yield ids


def built_cases(bases: tuple[ReturnTerms, ...] = (BASE,)) -> Iterator[tuple[str, Built]]:
    for index, base in enumerate(bases):
        for name in sorted(SCENARIOS):
            yield f"{name}@{index}", make(name, base=base)
            yield f"{name}@{index}+refund", make(name, base=base, refund_eur=REFUND)


def refund_of(built: Built) -> Decimal | None:
    return REFUND if "amount" in built.expected.placeholders else None


# ---- The sentences --------------------------------------------------------------------------


def test_the_phrasebook_is_small_and_covers_every_slot_for_every_kind() -> None:
    assert 15 <= len(SENTENCES) <= 25
    assert tuple(s.id for s in SENTENCES) == SENTENCE_IDS
    assert len(set(SENTENCE_IDS)) == len(SENTENCE_IDS)
    for sentence in SENTENCES:
        assert re.fullmatch(r"[A-Z]+(?:_[A-Z]+)+", sentence.id), sentence.id
        assert sentence.slot in SLOTS
        assert sentence.kinds and sentence.kinds <= ALL_KINDS
        assert sentence.after <= set(SENTENCE_IDS)
    for name, built in built_cases(BASES):
        ctx = context_of(built.expected)
        for slot in ("opening", "closing"):
            assert any(s.slot == slot for s in allowed_sentences(ctx)), (name, slot)
        assert default_selection(ctx) in list(selections(ctx)), name


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("sentence_id", SENTENCE_IDS)
def test_every_sentence_passes_every_text_check(sentence_id: str, lang: Lang) -> None:
    """No number, unit, date, term, legal or decision wording, banned term, character outside
    the allowlist or wrong language, and no placeholder: the sentence is pure connective text."""
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
    contexts = {context_of(built.expected) for _, built in built_cases(BASES)}
    for ctx in contexts:
        body = mandatory_body(ctx, lang)
        for part in body:
            assert phrase_problems(part, lang) == [], part
        for ids in selections(ctx):
            texts = [PHRASEBOOK[i].text(lang) for i in ids]
            parts = [texts[0], *body, *texts[1:]]
            pairs.update(zip(parts, parts[1:], strict=False))
    for first, second in sorted(pairs):
        assert phrase_problems(f"{first} {second}", lang) == [], (first, second)


# ---- Every allowed combination for every engine decision kind and merchant base --------------


def distinct_cases() -> dict[str, Built]:
    """One case per distinct message context (Q4 and Q4_negative_margin, for example, give the
    same messages): every kind of engine decision on every base of BASES, with and without a
    known refund."""
    cases: dict[str, Built] = {}
    seen: set[MessageContext] = set()
    for name, built in built_cases(BASES):
        ctx = context_of(built.expected)
        if ctx not in seen:
            seen.add(ctx)
            cases[name] = built
    return cases


CASES = distinct_cases()


def test_the_distinct_cases_cover_every_kind_base_and_refund() -> None:
    kinds = {context_of(built.expected).kind for built in CASES.values()}
    assert kinds == ALL_KINDS
    assert {refund_of(built) for built in CASES.values()} == {None, REFUND}
    assert {built.base for built in CASES.values()} == set(BASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_allowed_selection_renders_with_the_clause_and_the_template_fields(
    name: str,
) -> None:
    """Every set of sentences (the order of the extras does not change the text checks): the
    required clause, once, at the end; each template field exactly once (LLM-10); no stray
    placeholder; within 120 words before the clause; the right language; no digit but those of
    the fields. The composed order is checked on every ordered selection."""
    built = CASES[name]
    refund, base = refund_of(built), built.base
    ctx = context_of(built.expected)
    kind = required_clause(built.decision, base)
    fields = {lang: template_fields(built.decision, base, lang, refund) for lang in LANGS}
    count = 0
    for ids in selections(ctx, ordered=False):
        assert render_selection(ids, built.decision, base, "it", refund) == render(
            compose(ids, ctx, "it"), built.decision, base, "it", refund
        )
        for lang in LANGS:
            final = render(compose(ids, ctx, lang), built.decision, base, lang, refund)
            assert_clause(final, kind, lang)
            assert all(final.count(value) == 1 for value in fields[lang].values()), (ids, lang)
            assert not set("{}[]<>") & set(final)
            message = final.split("\n\n")[0] if kind else final
            assert word_count(message) <= MAX_CUSTOMER_WORDS, (ids, lang)
            assert language_of(message) == lang, (ids, lang)
            rest = message
            for value in fields[lang].values():
                rest = rest.replace(value, " ")
            assert numeric_characters(rest) == [], (ids, lang)
        count += 1
    assert count > 0
    for ids in selections(ctx):
        text = compose(ids, ctx, "en")
        assert text.startswith(PHRASEBOOK[ids[0]].en) and text.endswith(PHRASEBOOK[ids[-1]].en)


def test_the_body_writes_the_fields_the_decision_has() -> None:
    q4 = make("Q4_cluster", refund_eur=REFUND)
    text = compose(["OPEN_THANKS", "CLOSE_REGARDS"], context_of(q4.expected), "en")
    assert placeholders_in(text) == ["amount", "deadline", "terms"]
    assert "shown before each purchase: {terms}. " + NOTICE_AFTER_TERMS["en"] in text
    q2 = make("Q2")
    text = compose(["OPEN_THANKS", "CLOSE_REGARDS"], context_of(q2.expected), "it")
    assert placeholders_in(text) == ["deadline"]
    assert "il rimborso è previsto al più tardi per il {deadline}." in text
    insufficient = make("insufficient")
    text = compose(["OPEN_THANKS", "CLOSE_REGARDS"], context_of(insufficient.expected), "en")
    assert "Your next orders have our standard return terms." in text
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


# ---- Round 3, finding T1: the notice is about future orders, on every base --------------------


@pytest.mark.parametrize("lang", LANGS)
def test_the_notice_never_follows_the_registration_as_these_terms(lang: Lang) -> None:
    """On every base of the sweep, for every scenario, with and without a refund: the notice
    says "these terms" only right after the restrictive {terms} sentence; otherwise it names the
    next orders (also after the neutral {terms} sentence of a benefit alone, round 4, finding
    U4). So it never reads as if the current refund depended on past returns (rule 3)."""
    alone = 0
    for base, name in itertools.product(SWEEP, sorted(SCENARIOS)):
        for refund in (None, REFUND):
            ctx = context_of(make(name, base=base, refund_eur=refund).expected)
            body = mandatory_body(ctx, lang)
            notice = [s for s in body if s in (NOTICE_AFTER_TERMS[lang], NOTICE_ALONE[lang])]
            assert len(notice) == ("transparency_notice:true" in ctx.lever_ids), (base, name)
            for before, sentence in zip(body, body[1:], strict=False):
                if sentence == NOTICE_AFTER_TERMS[lang]:
                    assert before == RESTRICTIVE_TERMS[lang], (base, name)
                if sentence == NOTICE_ALONE[lang]:
                    assert before != RESTRICTIVE_TERMS[lang], (base, name)
                    alone += 1
    assert alone > 0  # the sweep reaches the case of the review


def test_the_notice_alone_is_used_on_the_bases_of_the_review() -> None:
    for name, base in (
        ("Q3", BASES[1]),
        ("Q3_cluster", BASES[1]),
        ("Q4", BASES[2]),
    ):
        built = make(name, base=base, refund_eur=REFUND)
        for lang in LANGS:
            final = render_selection(
                default_selection(context_of(built.expected)), built.decision, base, lang, REFUND
            )
            assert NOTICE_ALONE[lang] in final and NOTICE_AFTER_TERMS[lang] not in final


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
        # Round 3, finding T4: one request to reply, "thanks again" after a thanks
        (
            "Q4_cluster",
            ["OPEN_THANKS", "HELP_SIZE", "CONTACT_REPLY", "CLOSE_REGARDS"],
            "CONTACT_REPLY: one reply sentence only, HELP_SIZE is there",
        ),
        (
            "guarantee",
            ["OPEN_THANKS", "GUARANTEE_PHOTO", "CONTACT_REPLY", "CLOSE_REGARDS"],
            "one reply sentence only",
        ),
        ("Q4_cluster", ["OPEN_SORRY_FIT", "CLOSE_DAY"], "CLOSE_DAY: needs OPEN_CHOICE or"),
        ("guarantee", ["OPEN_SORRY_ITEM", "CONTACT_TEAM", "CLOSE_DAY"], "CLOSE_DAY: needs"),
    ],
)
def test_refused_selections(name: str, ids: list[str], reason: str) -> None:
    ctx = context_of(make(name).expected)
    problems = selection_problems(ids, ctx)
    assert any(reason in p for p in problems), problems
    with pytest.raises(MessageError):
        compose(ids, ctx, "it")


def test_coherent_selections_pass() -> None:
    ctx = context_of(make("Q4_cluster").expected)
    for ids in (
        ["OPEN_THANKS", "CONTACT_REPLY", "CLOSE_DAY"],
        ["OPEN_CHOICE", "HELP_SIZE", "CONTACT_TEAM", "CLOSE_DAY"],
        ["OPEN_SORRY_FIT", "INFO_TERMS", "CLOSE_REGARDS"],
    ):
        assert selection_problems(ids, ctx) == [], ids
    assert sum(PHRASEBOOK[i].group == REPLY for i in SENTENCE_IDS) == 4


def test_the_selection_rules_are_listed_for_the_prompt() -> None:
    assert selection_rules(context_of(make("Q4_cluster").expected)) == [
        "At most one of HELP_SIZE, CONTACT_REPLY, INFO_TERMS.",
        "CLOSE_DAY only after OPEN_THANKS or OPEN_MESSAGE or OPEN_CHOICE.",
    ]
    assert selection_rules(context_of(make("Q2").expected)) == [
        "At most one of HELP_SIZE, CONTACT_REPLY.",
        "CLOSE_DAY only after OPEN_THANKS or OPEN_MESSAGE or OPEN_CHOICE.",
    ]
    no_exchange = replace(
        context_of(make("Q2").expected), lever_ids=frozenset({"free_size_exchange:false"})
    )
    assert selection_rules(no_exchange)[0] == (
        "CLOSE_DAY only after OPEN_THANKS or OPEN_MESSAGE or OPEN_CHOICE."
    )


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
    for base, refund in itertools.product(BASES, (None, REFUND)):
        built = make(name, refund_eur=refund, base=base)
        assert context_of_case(built.case) == context_of(built.expected)
        assert context_of(built.expected).kind == template_key(
            built.expected.path, built.expected.evidence_quality, built.expected.quadrant
        )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_default_selections_are_allowed_and_fit_before_the_clause(name: str) -> None:
    """Round 3, finding T5: the default selections stay within 120 words before the clause, on
    every base and with a known refund; the clause comes on top (the ADR states no claim with
    the clause)."""
    for base, refund in itertools.product(BASES, (None, REFUND)):
        built = make(name, base=base, refund_eur=refund)
        ctx = context_of(built.expected)
        ids = default_selection(ctx)
        assert selection_problems(ids, ctx) == []
        for lang in LANGS:
            final = render_selection(ids, built.decision, base, lang, refund)
            message = final.split("\n\n")[0]
            assert word_count(message) <= MAX_CUSTOMER_WORDS, (name, base, lang)


def test_selection_bounds_match_the_contract() -> None:
    assert (MIN_SENTENCES, MAX_SENTENCES) == (2, 2 + MAX_EXTRAS)
    assert set(DEFAULT_SELECTION) == ALL_KINDS


def test_render_selection_appends_the_clause_once() -> None:
    built = make("Q4_cluster")
    final = render_selection(["OPEN_THANKS", "CLOSE_REGARDS"], built.decision, BASE, "it")
    assert final.endswith("\n\n" + CLAUSES["withdrawal"]["it"])
    assert final.count(CLAUSES["withdrawal"]["it"]) == 1
