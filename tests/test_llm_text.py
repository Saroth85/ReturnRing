"""Lexical checks of llm/text.py: parametrized probes for review findings LLM-1, LLM-5, LLM-6 and
the language check of LLM-10 (docs/spec.md 8.7 and 16)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from llm.text import (
    banned_terms,
    fold,
    language_of,
    legal_wording,
    literal_numbers,
    literal_units,
    number_word,
    numbers_in_text,
    other_numeric_characters,
    parse_number,
    strip_tokens,
    word_count,
    word_set,
    words,
)

# ---- Banned terms (LLM-5, LLM-6) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "frode",
        "FRODE",
        "fròde",
        "fr­ode",  # soft hyphen
        "fr​ode",  # zero-width space
        "fr⁠ode",  # word joiner
        "frоde",  # Cyrillic o
        "fraυd",  # Greek upsilon
        "𝐟𝐫𝐨𝐝𝐞",  # mathematical bold
        "ＦＲＯＤＥ",  # full width
        "f r o d e",
        "f.r.o.d.e",
        "fro-de",
        "fr0de",
        "antifrode",
        "anti-fraud",
        "fraudulent",
        "truffa",
        "truffatore",
        "abuso",
        "abusivo",
        "abuser",
        "scam",
        "SCAMMER",
        "sospetto",
        "sospettiamo",
        "suspicious",
        "suspected",
        "imbroglio",
        "raggiro",
        "cheating",
        "colpevole",
        "guilty",
        "disonesto",
        "dishonest",
    ],
)
def test_banned_stems_are_found_whatever_the_disguise(text: str) -> None:
    assert banned_terms(f"Nota per il cliente: {text}.")


@pytest.mark.parametrize(
    "text",
    [
        "stessa persona",
        "la medesima persona",
        "medesimi individui",
        "lo stesso soggetto",
        "una sola persona",
        "un'unica persona",
        "same person",
        "SAME PERSON",
        "same people",
        "same individual",
        "same-person",
        "same\nperson",
        "same   person",
        "one and the same",
        "a single person",
        "ѕаmе реrѕоn",  # Cyrillic look-alikes
        "s a m e person",
    ],
)
def test_same_person_phrases_are_found(text: str) -> None:
    assert banned_terms(f"They are {text}.")


@pytest.mark.parametrize(
    "text",
    [
        "scambio taglia gratuito",
        "scambi di taglia",
        "giacca scamosciata",
        "scamosciato",
        "scampolo di tessuto",
        "abito scamiciato",
        "truffle brown",
        "Truffle",
        "linked accounts",
        "account collegati",
        "the same address",
        "lo stesso indirizzo",
        "sospensione",
        "suspended",
        "Abbiamo registrato la tua richiesta",
        "a person",
        "frontale",
    ],
)
def test_innocent_words_are_not_banned(text: str) -> None:
    assert banned_terms(text) == []


# ---- Numbers (LLM-1) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word, lang",
    [
        ("uno", "it"),
        ("due", "it"),
        ("tre", "it"),
        ("quattordici", "it"),
        ("QUATTORDICI", "it"),
        ("trenta", "it"),
        ("trentuno", "it"),
        ("ventotto", "it"),
        ("ventitré", "it"),
        ("cento", "it"),
        ("centouno", "it"),
        ("centotto", "it"),
        ("duecento", "it"),
        ("mille", "it"),
        ("duemila", "it"),
        ("milleduecento", "it"),
        ("milioni", "it"),
        ("quindicina", "it"),
        ("one", "en"),
        ("two", "en"),
        ("fourteen", "en"),
        ("thirty", "en"),
        ("hundred", "en"),
        ("thousand", "en"),
        ("fortnight", "en"),
        ("dozen", "en"),
        ("twice", "en"),
        ("fourteen", "it"),  # English words in an Italian message
        ("quattordici", "en"),  # and Italian words in an English one
    ],
)
def test_number_words_are_literal_numbers(word: str, lang: str) -> None:
    assert literal_numbers(f"x {word} y", lang)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text, lang",
    [
        ("Se sei soddisfatto, rispondi", "it"),  # "sei" is "you are"
        ("your refund is due by Friday", "en"),
        ("settembre ottobre novembre dicembre", "it"),
        ("centro centrale treno ventaglio novella", "it"),
        ("someone anyone often tone", "en"),
        ("tredicesima", "it"),
    ],
)
def test_words_that_are_not_numbers(text: str, lang: str) -> None:
    assert literal_numbers(text, lang) == []  # type: ignore[arg-type]


def test_due_is_a_number_in_italian_only() -> None:
    assert number_word("due", "it")
    assert not number_word("due", "en")


@pytest.mark.parametrize("text", ["14", "١٤", "½", "Ⅻ", "²", "①", "四", "۳"])
def test_any_numeric_character_is_a_literal_number(text: str) -> None:
    assert literal_numbers(f"entro {text}", "it")


@pytest.mark.parametrize(
    "text",
    [
        "€",
        "$",
        "£",
        "EUR",
        "euro",
        "giorni",
        "settimane",
        "mesi",
        "days",
        "weeks",
        "months",
        "%",
        "ore",
        "anni",
    ],
)
def test_units_of_time_and_money_are_found(text: str) -> None:
    assert literal_units(f"x {text} y")


@pytest.mark.parametrize("text", ["buona giornata", "have a nice day", "ora", "orario"])
def test_greetings_are_not_units(text: str) -> None:
    assert literal_units(text) == []


# ---- Legal wording (LLM-1) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "puoi esercitare il diritto di recesso",
        "puoi recedere dal contratto",
        "i tuoi diritti",
        "la garanzia legale",
        "rimborso garantito",
        "you can withdraw",
        "your withdrawal",
        "your rights",
        "you have the right to return it",
        "the legal guarantee",
        "our warranty",
        "Il rimborso arriverà entro breve.",
        "Entro breve riceverai il rimborso.",
        "You will be refunded within a short time.",
        "come previsto dalla legge",
        "by law",
        "secondo la direttiva",
    ],
)
def test_legal_wording_is_found(text: str) -> None:
    assert legal_wording(text)


@pytest.mark.parametrize(
    "text",
    [
        "the right size",
        "right away",
        "il rimborso è previsto per il",
        "Il rimborso è previsto. Entro domani ti scriviamo.",
        "your refund is due by",
        "within your next orders",
        "la taglia giusta",
        "precedente",
    ],
)
def test_ordinary_words_are_not_legal_wording(text: str) -> None:
    assert legal_wording(text) == []


# ---- Language (LLM-06, LLM-10) -------------------------------------------------------------------


def test_language_of_italian_and_english() -> None:
    assert language_of("Ciao, grazie per averci scritto: la tua richiesta è registrata.") == "it"
    assert language_of("Hello, thank you for writing to us: your request is registered.") == "en"


@pytest.mark.parametrize(
    "text",
    [
        "Ciao!",
        "OK",
        "",
        "Grazie per la tua richiesta di reso. Thank you for the request.",  # mixed
    ],
)
def test_language_cannot_be_told(text: str) -> None:
    assert language_of(text) is None


# ---- Folding, digits and identifiers ------------------------------------------------------------


def test_fold_and_words() -> None:
    assert fold("ＦＲÒ­DE") == "frode"
    assert words("Un'altra taglia, per-favore!") == ["un", "altra", "taglia", "per", "favore"]
    assert word_set("a b a") == frozenset({"a", "b"})
    assert word_count("  uno  due\ntre ") == 3


@pytest.mark.parametrize(
    "token, value",
    [
        ("7", "7"),
        ("42,1", "42.1"),
        ("0.87", "0.87"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1,000,000", "1000000"),
        ("1.000.000", "1000000"),
    ],
)
def test_parse_number(token: str, value: str) -> None:
    assert parse_number(token) == Decimal(value)


def test_numbers_in_text_keeps_signs_only_after_a_space() -> None:
    found = numbers_in_text("-0.12, 3-5, x-12, (−4) and +3; ٣ is not ASCII; ²")
    assert found == [Decimal("-0.12"), 3, 5, 12, -4, 3, 2]
    assert other_numeric_characters("٣ and 4") == ["٣"]


def test_strip_tokens_removes_whole_identifiers_only() -> None:
    text = strip_tokens("Case RET-1, ret-1 and RET-12, cluster R-MARCO", ["RET-1", "R-MARCO", ""])
    assert numbers_in_text(text) == [12]
    assert "MARCO" not in text
