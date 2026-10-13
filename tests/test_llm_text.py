"""Lexical checks of llm/text.py: parametrized probes for review findings LLM-1, LLM-5, LLM-6, the
language check of LLM-10 (docs/spec.md 8.7 and 16) and the adversarial review of 13 Oct (findings
1, 2, 4 and 5: terms and decisions in words, numbers in letters, same-person synonyms, signs; round
2, findings R2 to R7: quadrant descriptions, ownership, the script allowlist, glued numbers)."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llm.text import (
    banned_terms,
    decision_wording,
    fold,
    foreign_letters,
    language_of,
    legal_wording,
    literal_numbers,
    literal_units,
    number_modifiers,
    number_word,
    number_words,
    numbers_in_text,
    other_numeric_characters,
    parse_number,
    quadrant_axes,
    quadrant_labels,
    quadrant_positions,
    restriction_words,
    strip_tokens,
    terms_wording,
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
        "same\u200bperson",  # zero-width space read as a space
        "same\u00adperson",  # soft hyphen
    ],
)
def test_same_person_phrases_are_found(text: str) -> None:
    assert banned_terms(f"They are {text}.")


# Adversarial review of 13 Oct, finding 4: synonyms of "same person" (rule 11).
@pytest.mark.parametrize(
    "text",
    [
        "These linked accounts are one person.",
        "These linked accounts are the same customer.",
        "One customer controls all linked accounts.",
        "Lo stesso cliente ha più account collegati.",
        "You are the same customer as your linked accounts.",
        "We noticed that your linked accounts behave like a single customer.",
        "The accounts belong to the same buyer.",
        "The accounts belong to one single individual.",
        "The linked accounts have the same account holder.",
        "Same shopper behind every account.",
        "A single human uses these accounts.",
        "The same real person opened them.",
        "Gli account collegati appartengono a un unico utente.",
        "Gli account hanno lo stesso intestatario.",
        "Gli account sono della medesima acquirente.",
        "Un solo titolare per tutti gli account.",
        "Un cliente controlla tutti gli account collegati.",
        "Una persona gestisce questi profili.",
        "One entity controls these accounts.",
        "It is the same guy behind every account.",
    ],
)
def test_same_person_synonyms_are_found(text: str) -> None:
    assert banned_terms(text)


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
        "Sei un cliente prezioso per noi.",
        "Una persona del nostro team ti risponderà.",
        "Siamo felici di averti tra i nostri clienti.",
        "The return terms of your orders stay the same as always.",
        "The links share the same user agent and the same address.",
        "A customer wrote to us.",
        # Round 2, finding R3: ownership and group words that are not a same-person claim
        "Each linked account has its own card.",
        "All linked accounts use their own devices.",
        "The linked accounts may share a household; check the links.",
        "All linked accounts belong to cluster R-MARCO.",
        "One of the customers returned the item.",
        "The accounts all have different owners.",
        "Gli account collegati sono tutti nel cluster R-MARCO.",
        "Unlike the Rossi family, the linked accounts share a card.",
        "A common card is shared by the linked accounts.",
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
        # Adversarial review of 13 Oct, finding 2: ordinals, zero, fractions and multiples
        ("first", "en"),
        ("second", "en"),
        ("fourteenth", "en"),
        ("twentieth", "en"),
        ("thirds", "en"),
        ("zero", "en"),
        ("half", "en"),
        ("quarter", "en"),
        ("double", "en"),
        ("fortnightly", "en"),
        ("primo", "it"),
        ("secondo", "it"),
        ("terzo", "it"),
        ("quattordicesimo", "it"),
        ("ventunesima", "it"),
        ("trentatreesimi", "it"),
        ("tredicesima", "it"),
        ("centesimo", "it"),
        ("ventun", "it"),
        ("trentun", "it"),
        ("zero", "it"),
        ("metà", "it"),
        ("mezzo", "it"),
        ("mezz'ora", "it"),
        ("doppio", "it"),
        ("paio", "it"),
        # glued to a unit, or a long number at the start of a word
        ("fourteendays", "en"),
        ("quattordicigiorni", "it"),
        ("seigiorni", "it"),
        ("twentyish", "en"),
        ("quindicinale", "it"),
    ],
)
def test_number_words_are_literal_numbers(word: str, lang: str) -> None:
    assert literal_numbers(f"x {word} y", lang)  # type: ignore[arg-type]


# Adversarial review of 13 Oct, finding 2: each probe passed literal_numbers and literal_units.
@pytest.mark.parametrize(
    "text, lang",
    [
        ("Refund in fourteen\u200bdays.", "en"),  # zero-width space glued number and unit
        ("Refund in fourteen\u00addays.", "en"),  # soft hyphen
        ("Ti rimborsiamo in quattordici\u200bgiorni.", "it"),
        ("Rimborso: quattordici\u2060giorni.", "it"),  # word joiner
        ("Ti rimborsiamo quattor\u2011dici notti dopo.", "it"),  # non-breaking hyphen
        ("Ti rimborsiamo quattor\u2e3adici notti dopo.", "it"),  # two-em dash (Pd)
        ("Ti rimborsiamo quattor\u301cdici notti dopo.", "it"),  # wave dash (Pd)
        ("Refund on the fourteenth.", "en"),
        ("Rimborso il quattordicesimo.", "it"),
        ("Refund at zero cost.", "en"),
        ("Ti rimborsiamo la metà.", "it"),
        ("We refund half of it.", "en"),
        ("Refund by Friday.", "en"),
        ("Rimborso venerdì.", "it"),
        ("Refund on October the first.", "en"),
        ("Rimborso a ottobre.", "it"),
        ("Refund tomorrow.", "en"),
        ("Refund today.", "en"),
        ("Rimborso domani.", "it"),
        ("Rimborso dopodomani.", "it"),
        ("Ti scriviamo stasera.", "it"),
        ("Ti rimborsiamo tra un paio di notti.", "it"),
        ("We refund you over the weekend.", "en"),
        ("We refund you this afternoon.", "en"),
        ("Same day refund.", "en"),
        ("Ti rimborsiamo in giornata.", "it"),
        ("Ti rimborsiamo nel weekend.", "it"),
    ],
)
def test_finding2_numbers_dates_and_units_in_letters_are_found(text: str, lang: str) -> None:
    assert literal_numbers(text, lang) + literal_units(text)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text, lang",
    [
        ("Se sei soddisfatto, rispondi", "it"),  # "sei" is "you are"
        ("your refund is due by Friday", "en"),
        ("settembre ottobre novembre dicembre", "it"),
        ("centro centrale treno ventaglio novella", "it"),
        ("someone anyone often tone", "en"),
        ("teniamo molto a te, tengono conto, novità, trend, tremendo, duetto", "it"),
        ("online, oneself, tennis, tender, attend, none of them", "en"),
        ("giacca doppiopetto, abito scamiciato, primavera, quartiere", "it"),
        ("prima di ogni acquisto", "it"),
        ("il medesimo ordine, un battesimo", "it"),
        ("ventilato, trentino, millennio, centimetri", "it"),
        # round 2, finding R6: words that start with a short cardinal
        ("customers tend to reply, trench coat, treccia", "en"),
        ("ti tremo, ottone, tenda", "it"),
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
        "notti",
        "nights",
        "Friday",
        "venerdì",
        "October",
        "gennaio",
        "today",
        "domani",
        "ieri",
    ],
)
def test_units_of_time_and_money_are_found(text: str) -> None:
    assert literal_units(f"x {text} y")


@pytest.mark.parametrize(
    "text",
    [
        "buona giornata",
        "have a nice day",
        "ora",
        "orario",
        "you may reply",
        "buon weekend",
        "Buon giorno!",
        "buona serata",
        "good morning",
        "Have a great day!",
        "buon pomeriggio",
    ],
)
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
        "you are entitled to a refund",
        "ti spetta il rimborso",
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


def test_numbers_in_text_reads_any_dash_before_a_digit_as_a_sign() -> None:
    found = numbers_in_text("-0.12, 3-5, x-12, (−4) and +3; ٣ is not ASCII; ²")
    assert found == [Decimal("-0.12"), 3, -5, -12, -4, 3, 2]
    assert other_numeric_characters("٣ and 4") == ["٣"]


# Adversarial review of 13 Oct, finding 5: a sign flip read as the positive fact.
@pytest.mark.parametrize(
    "text, value",
    [
        ("margin: –42.1 EUR", "-42.1"),  # en dash
        ("margin: —42.1 EUR", "-42.1"),  # em dash
        ("margin:-42.1 EUR", "-42.1"),  # no space before the sign
        ("margin ‑42.1", "-42.1"),  # non-breaking hyphen
        ("margin ⁻42.1", "-42.1"),  # superscript minus
        ("margin ➖42.1", "-42.1"),  # heavy minus sign
        ("margin -\u200b42.1", "-42.1"),  # a format character between sign and digits
        ("risk .87", "0.87"),  # a leading separator is still a number
        ("risk ,87", "0.87"),
        # round 2, finding R7: a sign with spaces before the digits
        ("margin: − 42.1 EUR", "-42.1"),
        ("weight - 0.98", "-0.98"),
        ("margin ➖  42.1", "-42.1"),
    ],
)
def test_numbers_in_text_signs_and_leading_separators(text: str, value: str) -> None:
    assert numbers_in_text(text) == [Decimal(value)]


@pytest.mark.parametrize(
    "text",
    [
        "42.1k EUR",
        "42.1M EUR",
        "42.1 M EUR",
        "42,1 mln",
        "42.1 k",
        "0.87%",
        "7x",
        "42.1\u200bk",
        "minus 42.1 EUR",
        "meno 42,1 euro",
        "negative 42.1",
        "negativo: 42.1",
        "MINUS 42.1",
        "a loss of 42.1 EUR",
        "un margine negativo di 42,1 euro",
        "42.1 EUR in the red",
        "una perdita di 42,1",
    ],
)
def test_finding5_scaled_or_worded_signs_are_found(text: str) -> None:
    assert number_modifiers(text)


@pytest.mark.parametrize(
    "text",
    [
        "42.1 EUR",
        "0.87; the largest",
        "7 linked accounts",
        "the margin is negative",
        "Q4",
        # round 2, finding R7: not a sign, a scale or a unit
        "a 14-day window",
        "7 linked accounts share a debit card",
        "weight 0.98 (cluster of 7 linked accounts)",
        "0.87 – the largest",
    ],
)
def test_plain_numbers_have_no_modifiers(text: str) -> None:
    assert number_modifiers(text) == []


def test_strip_tokens_removes_whole_identifiers_only() -> None:
    text = strip_tokens("Case RET-1, ret-1 and RET-12, cluster R-MARCO", ["RET-1", "R-MARCO", ""])
    assert numbers_in_text(text) == [-12]  # an unknown identifier still carries a number
    assert "MARCO" not in text


# ---- Terms and decisions in words (adversarial review of 13 Oct, finding 1) --------------------


@pytest.mark.parametrize(
    "text",
    [
        "La spedizione del reso resta gratuita.",
        "Il reso è gratis.",
        "Returns are free.",
        "Rimborso istantaneo.",
        "Instant refund.",
        "Ti rimborsiamo immediatamente.",
        "We refund you immediately.",
        "Ti rimborsiamo subito.",
        "Ti rimborsiamo rapidamente.",
        "We refund you quickly.",
        "Rimborso veloce.",
        "La spedizione del reso.",
        "Puoi spedire il pacco.",
        "Return shipping.",
        "Il coupon resta attivo.",
        "Your coupons stay active.",
        "Lo sconto resta.",
        "Gli sconti restano.",
        "A discount for you.",
        "Il cambio taglia.",
        "I cambi taglia.",
        "Uno scambio di taglia.",
        "A size exchange.",
        "La finestra di reso.",
        "The return window.",
        "Il reso è a tuo carico.",
        "Returns at your expense.",
        "Il reso è a tue spese.",
        "Senza costi per te.",
        "At no cost.",
        "Un voucher per te.",
        "Abbiamo approvato il tuo rimborso.",
        "We approved your refund.",
        "Abbiamo accettato il reso.",
        "We accepted your return.",
        "Abbiamo rifiutato il reso.",
        "Il reso è stato respinto.",
        "Il rimborso è negato.",
        "We rejected your return.",
        "Your refund was denied.",
        "We deny the refund.",
        "We refused the refund.",
        "We declined your return.",
        "Abbiamo bloccato il tuo account.",
        "We blocked your account.",
        "You are on our blacklist.",
        "È in corso un'indagine.",
        "We are investigating your orders.",
        "Il reso è autorizzato.",
        "Your return is authorized.",
        "Il tuo account è sospeso.",
        "Your account is suspended.",
        "Fr\u200bee return.",
        "Spe-dizione gratuita.",
        "ɡratuita",  # Latin script g
        "Feel free to return it free of charge.",
        "Il reso non ti costa nulla.",
        "Returns cost you nothing.",
        "No fees for you.",
        "You get a full refund.",
        "Riceverai un rimborso completo.",
        "Ti rimborsiamo l'intero importo.",
        "Rimborso parziale.",
        "Returns at no extra cost.",
        "Without any fees.",
        "Senza alcun costo.",
        "Nessun addebito.",
        "Return shipping is complimentary.",
        "Shipping is on the house.",
        "Il tuo reso è stato accolto.",
        "Your refund was granted.",
        "Your return was turned down.",
        "We gave your return the green light.",
        "Abbiamo dato il via libera al reso.",
        "Il reso è andato a buon fine.",
        "La richiesta ha avuto esito positivo.",
    ],
)
def test_terms_and_decision_words_are_found(text: str) -> None:
    assert terms_wording(text)


@pytest.mark.parametrize(
    "text",
    [
        "Abbiamo registrato la tua richiesta di reso e il rimborso è previsto per il",
        "Per i prossimi ordini le condizioni di reso cambiano e ti verranno mostrate prima",
        "For your next orders the return terms change and you will see them before each",
        "Ti scriveremo presto con i prossimi passi.",
        "We will write to you soon with the next steps.",
        "Feel free to reply to this message.",
        "Il tuo jeans in denim.",
        "Conserva lo scontrino, ci dispiace che tu sia scontento.",
        "These terms take into account the returns on your previous orders.",
        "Se ti serve un'altra taglia, rispondi pure a questo messaggio.",
    ],
)
def test_connective_text_has_no_terms_or_decisions(text: str) -> None:
    assert terms_wording(text) == []


@pytest.mark.parametrize(
    "text, found",
    [
        ("You should reject this return.", True),
        ("Sara approved it.", True),
        ("The coupons are disabled for the cluster.", False),  # a term, not a decision verb
        ("Link L-101 shares a device and a card.", False),
    ],
)
def test_decision_wording(text: str, found: bool) -> None:
    assert bool(decision_wording(text)) is found


# ---- Quadrant labels (adversarial review of 13 Oct, finding 3) ---------------------------------


@pytest.mark.parametrize(
    "text, labels",
    [
        ("The rules engine placed the case in Q1, so no restriction applies.", ["Q1"]),
        ("Q4 with instant_refund:false", ["Q4"]),
        ("ｑ３ and q-2", ["Q3", "Q2"]),
        ("q\u20114", ["Q4"]),
        ("quadrant 2", ["Q2"]),
        ("quadrante quattro", ["Q4"]),
        ("in quadrant IV", ["Q4"]),
        ("quadrant: one", ["Q1"]),
        ("the fourth quadrant", ["Q4"]),
        ("nel terzo quadrante", ["Q3"]),
        ("the 2nd quadrant", ["Q2"]),
        ("no quadrant is assigned", []),
        ("FAQ 1 and Q12", []),
        # round 2, finding R2: Roman numerals after Q
        ("The case sits in QI.", ["Q1"]),
        ("The case is in QIV.", ["Q4"]),
        ("q-iii and Q II", ["Q3", "Q2"]),
        ("FAQ I, qiv8 and quinto", []),
    ],
)
def test_quadrant_labels(text: str, labels: list[str]) -> None:
    assert quadrant_labels(text) == labels


def test_number_words_skip_and_forms() -> None:
    assert number_words("one or two", "en", skip={"one"}) == ["two"]
    assert number_words("quattor-dici e quattor‑dici", "it") == ["quattordici"]


# ---- Quadrant descriptions and restrictions (round 2, finding R2) ------------------------------


@pytest.mark.parametrize(
    "text, axes",
    [
        ("the high-risk, low-value quadrant", ["risk:high", "value:low"]),
        ("Value is high and risk is low.", ["value:high", "risk:low"]),
        ("rischio alto, valore basso", ["risk:high", "value:low"]),
        ("un cliente di alto valore e basso rischio", ["value:high", "risk:low"]),
        ("Risk score 0.87 from the model.", []),
        ("the highest contribution to the score", []),
    ],
)
def test_quadrant_axes(text: str, axes: list[str]) -> None:
    assert quadrant_axes(text) == axes


def test_quadrant_positions() -> None:
    assert quadrant_positions("the top-left quadrant, then in basso a destra") == [
        "top left",
        "in basso a destra",
    ]
    assert quadrant_positions("the left side of the dashboard") == []


@pytest.mark.parametrize(
    "text, found",
    [
        ("Restrictive levers apply.", ["restrictive"]),
        ("The account is restricted; restrizioni attive.", ["restricted", "restrizioni"]),
        ("No restrictive levers.", []),
        ("Without a risk score or restrictive levers.", []),
        ("Restrictions do not apply.", []),
        ("The terms are standard, restrictive levers apply.", ["restrictive"]),
    ],
)
def test_restriction_words(text: str, found: list[str]) -> None:
    assert restriction_words(text) == found


# ---- Script allowlist (round 2, finding R5) ----------------------------------------------------

_ALLOWED_NAME = re.compile(r"LATIN (?:SMALL|CAPITAL) LETTER (?:[A-Z]|[AEIOU] WITH (?:GRAVE|ACUTE))")


def _oracle(text: str) -> set[str]:
    """Independent reading by Unicode name: after NFKC, a letter is allowed only if it is a Latin
    letter A to Z or a vowel with a grave or acute accent; a combining mark is never allowed."""
    return {
        ch
        for ch in unicodedata.normalize("NFKC", text)
        if (ch.isalpha() and not _ALLOWED_NAME.fullmatch(unicodedata.name(ch, "")))
        or unicodedata.category(ch).startswith("M")
    }


def _block(first: int, last: int) -> list[str]:
    return [chr(c) for c in range(first, last + 1) if unicodedata.category(chr(c)) != "Cn"]


MIXED_SCRIPTS = (
    list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ àèéìíòóùúÀÈÉÌÒÙ .,;:-'")
    + _block(0x0100, 0x024F)  # Latin Extended-A and B
    + _block(0x0250, 0x02FF)  # IPA, modifier letters
    + _block(0x0300, 0x036F)  # combining marks
    + _block(0x0370, 0x03FF)  # Greek
    + _block(0x0400, 0x04FF)  # Cyrillic
    + _block(0x0530, 0x058F)  # Armenian
    + _block(0x13A0, 0x13F4)  # Cherokee
    + _block(0x1D00, 0x1D7F)  # phonetic extensions, small capitals
    + _block(0x2100, 0x214F)  # letterlike symbols
    + _block(0xFF21, 0xFF5A)  # full width
    + _block(0x1D400, 0x1D4FF)  # mathematical alphanumerics
)
FOREIGN_LETTERS = [ch for ch in MIXED_SCRIPTS if ch.isalpha() and _oracle(ch)]


@settings(max_examples=500, deadline=None)
@given(st.text(alphabet=st.sampled_from(MIXED_SCRIPTS), max_size=40))
def test_r5_foreign_letters_agree_with_the_unicode_names(text: str) -> None:
    assert set(foreign_letters(text)) == _oracle(text)


@settings(max_examples=500, deadline=None)
@given(
    word=st.sampled_from(["fraud", "frode", "scam", "truffa", "abuse", "suspicious", "approved"]),
    index=st.integers(min_value=0, max_value=9),
    letter=st.sampled_from(FOREIGN_LETTERS),
)
def test_r5_a_disguised_word_never_passes_both_checks(word: str, index: int, letter: str) -> None:
    """Any letter of another script swapped into a banned or decision word is caught: by the
    script allowlist, or by the blocklists once folded."""
    position = index % len(word)
    disguised = word[:position] + letter + word[position + 1 :]
    text = f"This is {disguised}."
    assert foreign_letters(text) or banned_terms(text) or decision_wording(text)


@pytest.mark.parametrize(
    "text, found",
    [
        ("This is frօud.", ["օ"]),
        ("This is frᴀud.", ["ᴀ"]),
        ("This is ғraud.", ["ғ"]),
        ("a scɑm, a fraʋd", ["ɑ", "ʋ"]),
        ("perché è così, città", []),
        ("Ｆｕｌｌ ｗｉｄｔｈ and 𝐛𝐨𝐥𝐝 and ﬁne", []),  # NFKC folds them to a-z
        ("frau\u0301d", []),  # composes to ú
        ("x\u0301", ["\u0301"]),  # a mark that composes to nothing allowed
        ("naïve", ["ï"]),
        ("İstanbul", ["İ"]),
        ("€ 42.1, 7%", []),
    ],
)
def test_r5_foreign_letters(text: str, found: list[str]) -> None:
    assert foreign_letters(text) == found
