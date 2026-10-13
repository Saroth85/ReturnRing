"""Lexical checks on model text (docs/spec.md 8.7; review findings LLM-1, LLM-5, LLM-6, LLM-10).

Standard library only. Word checks run on a folded form of the text, so look-alike letters,
invisible characters, accents and letter spacing do not hide a word:

- `fold`: NFKC, casefold, no format characters (soft hyphen, zero-width space) or combining marks,
  common Cyrillic and Greek look-alikes mapped to Latin letters.
- `words`: the folded text as lowercase letter runs; hyphens, apostrophes and punctuation split
  words, and runs of three or more single letters ("f r o d e") are joined.

These are blocklists: they reduce risk, they do not prove a text is safe. The validator applies
them to model text only, never to fixed texts filled in by code.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from decimal import Decimal
from typing import Literal

Lang = Literal["it", "en"]
LANGS: tuple[Lang, ...] = ("it", "en")


def word_set(text: str) -> frozenset[str]:
    """A set of words written as one space-separated string."""
    return frozenset(text.split())


# ---- Folding ------------------------------------------------------------------------------------

# Cyrillic and Greek letters that look like Latin ones, after casefold.
_CONFUSABLES = str.maketrans(
    {
        # Cyrillic
        "а": "a",
        "в": "b",
        "е": "e",
        "ё": "e",
        "к": "k",
        "м": "m",
        "н": "h",
        "о": "o",
        "р": "p",
        "с": "c",
        "т": "t",
        "у": "y",
        "х": "x",
        "ѕ": "s",
        "і": "i",
        "ї": "i",
        "ј": "j",
        "ԁ": "d",
        "ԛ": "q",
        "ԝ": "w",
        "һ": "h",
        "ӏ": "l",
        # Greek
        "α": "a",
        "β": "b",
        "ε": "e",
        "η": "n",
        "ι": "i",
        "κ": "k",
        "ν": "v",
        "ο": "o",
        "ρ": "p",
        "τ": "t",
        "υ": "u",
        "χ": "x",
        "ω": "w",
        "ϲ": "c",
        # Latin script variants
        "ɡ": "g",
        "ı": "i",
    }
)
# Digits and symbols used as letters, for banned-term matching only.
_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)

_SPACED_LETTERS_RE = re.compile(r"\b[a-z](?: [a-z]\b){2,}")
_NON_LETTERS_RE = re.compile(r"[^a-z]+")
_INNER_JOINERS_RE = re.compile(r"(?<=[a-z])[-‐‑‒–—―_.·*'’](?=[a-z])")


def fold(text: str) -> str:
    """NFKC, casefold, no format characters or combining marks, Latin look-alikes."""
    folded = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text).casefold())
    kept = "".join(ch for ch in folded if unicodedata.category(ch) not in ("Mn", "Me", "Cf"))
    return kept.translate(_CONFUSABLES)


def _letters_only(folded: str) -> str:
    spaced = _NON_LETTERS_RE.sub(" ", folded).strip()
    return _SPACED_LETTERS_RE.sub(lambda m: m.group().replace(" ", ""), spaced)


def words(text: str) -> list[str]:
    """Lowercase letter runs of the folded text."""
    return _letters_only(fold(text)).split()


def _banned_forms(text: str) -> list[str]:
    """Two letter-only forms: inner hyphens and dots as word breaks, and removed ("fro-de")."""
    folded = fold(text).translate(_LEET)
    return [_letters_only(folded), _letters_only(_INNER_JOINERS_RE.sub("", folded))]


# ---- Banned terms (LLM-5, LLM-6, spec 8.7 and 16) ---------------------------------------------

# Word-start stems of accusatory or verdict words. A word matches if it starts with a stem, also
# after a prefix such as "anti" ("antifrode").
BANNED_STEMS: tuple[str, ...] = (
    "frod",
    "fraud",
    "truff",
    "abus",
    "scam",
    "sospett",
    "suspic",
    "suspect",
    "imbrogl",
    "raggir",
    "cheat",
    "colpevol",
    "guilty",
    "disonest",
    "dishonest",
)
# Innocent fashion and retail words that share a stem: scambio (exchange), scamosciato (suede),
# scampolo (remnant), scamiciato (pinafore dress), truffle (a colour).
ALLOWED_PREFIXES: tuple[str, ...] = ("scambi", "scamosci", "scamp", "scamiciat", "truffle")
_WORD_PREFIXES: tuple[str, ...] = ("anti", "non", "pre", "super", "ultra", "iper", "hyper")

# "Linked accounts", never "same person" (CLAUDE.md rule 11, spec 16).
_SAME_PERSON_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:stess[aeoi]|medesim[aeoi]) (?:person[ae]|individu[oi]|soggett[oi])\b"),
    re.compile(r"\bun[a]? (?:sola|unica|solo|unico) (?:persona|individuo|soggetto)\b"),
    re.compile(r"\bsame (?:person|persons|people|individual|individuals|human|user)\b"),
    re.compile(r"\bone and the same\b"),
    re.compile(r"\b(?:a|one) single (?:person|individual|human)\b"),
)


def _stem_hit(word: str) -> str | None:
    candidates = [word] + [word[len(p) :] for p in _WORD_PREFIXES if word.startswith(p)]
    for candidate in candidates:
        if candidate.startswith(ALLOWED_PREFIXES):
            continue
        for stem in BANNED_STEMS:
            if candidate.startswith(stem):
                return stem
    return None


def banned_terms(text: str) -> list[str]:
    """Banned stems and same-person phrases found in the text (empty when clean)."""
    found: list[str] = []
    for form in _banned_forms(text):
        for word in form.split():
            stem = _stem_hit(word)
            if stem is not None and stem not in found:
                found.append(stem)
        for pattern in _SAME_PERSON_RES:
            match = pattern.search(form)
            if match is not None and match.group() not in found:
                found.append(match.group())
    return found


# ---- Numbers written as words or symbols (LLM-1) ---------------------------------------------

_IT_UNITS = "uno|due|tre|quattro|cinque|sei|sette|otto|nove"
_IT_TEENS = (
    "dieci|undici|dodici|tredici|quattordici|quindici|sedici|diciassette|diciotto|diciannove"
)
_IT_TENS = "venti|trenta|quaranta|cinquanta|sessanta|settanta|ottanta|novanta"
_IT_TENS_ELIDED = "vent|trent|quarant|cinquant|sessant|settant|ottant|novant"
_IT_UNDER_100 = (
    f"(?:{_IT_TEENS}|(?:{_IT_TENS})(?:due|tre|quattro|cinque|sei|sette|nove)?"
    f"|(?:{_IT_TENS_ELIDED})(?:uno|otto)|{_IT_UNITS})"
)
_IT_UNDER_1000 = (
    f"(?:(?:due|tre|quattro|cinque|sei|sette|otto|nove)?cent(?:o(?:{_IT_UNDER_100})?"
    f"|{_IT_UNDER_100})|{_IT_UNDER_100})"
)
_IT_NUMBER_RE = re.compile(
    f"(?:(?:{_IT_UNDER_1000})?mila|mille)(?:{_IT_UNDER_1000})?|{_IT_UNDER_1000}"
    "|milion[ei]|miliard[oi]"
    "|(?:decin|dozzin|quindicin|ventin|trentin|quarantin|cinquantin|centinai|migliai)[ae]"
)
# "sei" alone is also "you are"; "sei giorni" is still caught by the unit check.
_IT_NOT_NUMBERS = frozenset({"sei"})

_EN_NUMBER_WORDS = frozenset(
    {
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "hundreds",
        "thousand",
        "thousands",
        "million",
        "millions",
        "billion",
        "dozen",
        "dozens",
        "fortnight",
        "fortnights",
        "twice",
        "thrice",
    }
)
# Pronoun uses ("this one") are too common in free operator text; the customer check keeps them.
PRONOUN_NUMBERS = frozenset({"one", "uno"})


def number_word(word: str, lang: Lang) -> bool:
    """True if a folded word is a number written in letters. Italian numbers are checked in both
    languages, except "due", which is an English word ("due by")."""
    if word in _EN_NUMBER_WORDS:
        return True
    if word in _IT_NOT_NUMBERS or (word == "due" and lang == "en"):
        return False
    return _IT_NUMBER_RE.fullmatch(word) is not None


def numeric_characters(text: str) -> list[str]:
    """Characters with a numeric value (digits of any script, fractions, Roman numerals), in the
    raw text and in its NFKC form. Roman numerals written with ASCII letters are not detected."""
    found: list[str] = []
    for ch in text + unicodedata.normalize("NFKC", text):
        if unicodedata.numeric(ch, None) is not None and ch not in found:
            found.append(ch)
    return found


def number_words(text: str, lang: Lang, *, skip: Iterable[str] = ()) -> list[str]:
    skipped = frozenset(skip)
    found: list[str] = []
    for word in words(text):
        if word not in skipped and number_word(word, lang) and word not in found:
            found.append(word)
    return found


def literal_numbers(text: str, lang: Lang) -> list[str]:
    """Numbers written by the model in a customer message: digits, numeric characters, words."""
    return numeric_characters(text) + number_words(text, lang)


# ---- Units of time and money (LLM-1) -----------------------------------------------------------

# "day" and "giornata" are left out: "have a nice day" and "buona giornata" are greetings, and a
# duration needs a number, which the number check catches.
UNIT_WORDS = frozenset(
    {
        "giorno",
        "giorni",
        "gg",
        "settimana",
        "settimane",
        "mese",
        "mesi",
        "anno",
        "anni",
        "ore",
        "minuti",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
        "hour",
        "hours",
        "minute",
        "minutes",
        "euro",
        "eur",
        "centesimi",
        "cent",
        "cents",
        "dollaro",
        "dollari",
        "dollar",
        "dollars",
        "usd",
        "gbp",
        "sterline",
        "pound",
        "pounds",
        "percento",
        "percent",
    }
)


def literal_units(text: str) -> list[str]:
    """Currency symbols, percent signs and words for units of time or money."""
    found: list[str] = []
    for ch in unicodedata.normalize("NFKC", text):
        if (unicodedata.category(ch) == "Sc" or ch in "%‰") and ch not in found:
            found.append(ch)
    for word in words(text):
        if word in UNIT_WORDS and word not in found:
            found.append(word)
    return found


# ---- Legal-rights wording (LLM-1): the fixed clause covers it ---------------------------------

LEGAL_STEMS: tuple[str, ...] = (
    "recess",
    "reced",
    "diritt",
    "garanzi",
    "garant",
    "withdraw",
    "guarant",
    "warrant",
    "legal",
    "direttiv",
    "directive",
    "normativ",
)
LEGAL_WORDS = frozenset({"legge", "law", "laws", "rights"})
_RIGHT_RE = re.compile(r"\bright (?:to|of)\b|\b(?:your|legal|statutory) right\b")
_SENTENCE_RE = re.compile(r"[.!?;:\n]+")
_REFUND_TIMING: tuple[tuple[str, str], ...] = (("rimbors", "entro"), ("refund", "within"))


def legal_wording(text: str) -> list[str]:
    """Words about rights, the guarantee, the law or refund timing written by the model."""
    found: list[str] = []
    form = _letters_only(fold(text))
    for word in form.split():
        hit = (
            word
            if word in LEGAL_WORDS
            else next((stem for stem in LEGAL_STEMS if word.startswith(stem)), None)
        )
        if hit is not None and hit not in found:
            found.append(hit)
    match = _RIGHT_RE.search(form)
    if match is not None:
        found.append(match.group())
    for sentence in _SENTENCE_RE.split(fold(text)):
        sentence_words = _letters_only(sentence).split()
        for refund, within in _REFUND_TIMING:
            if any(w.startswith(refund) for w in sentence_words) and within in sentence_words:
                found.append(f"{refund} ... {within}")
    return found


# ---- Language (LLM-06, LLM-10) -------------------------------------------------------------------

# Small function-word lists; words shared by both languages ("a", "in", "no", "come") are left out.
# Folded forms (no accents): "è" is "e". Words shared by both languages are removed below.
IT_STOPWORDS = word_set(
    "il lo la le gli i un una uno di del della dei delle degli al alla ai alle dal dalla da per "
    "con su tra fra e ed o che non ti ci si mi vi tuo tua tuoi tue nostro nostra nostri nostre "
    "questo questa questi queste sono sei siamo abbiamo hai ha stato stata ma anche se grazie "
    "ciao gentile ordine ordini prossimi puoi pure nel nella nei sul sulla"
)
EN_STOPWORDS = word_set(
    "the an of to and or for with on at by from your you we our us this that these those is are "
    "was were be been have has had will would can could not but if it its as thank thanks hello "
    "dear please just any next orders order"
)
_IT_ONLY = IT_STOPWORDS - EN_STOPWORDS
_EN_ONLY = EN_STOPWORDS - IT_STOPWORDS


def language_of(text: str) -> Lang | None:
    """The language of a text from its function words, or None when it cannot be told: fewer
    than two hits, or neither language at least twice as frequent as the other."""
    tokens = words(text)
    it = sum(1 for w in tokens if w in _IT_ONLY)
    en = sum(1 for w in tokens if w in _EN_ONLY)
    if max(it, en) < 2:
        return None
    if it >= 2 * en:
        return "it"
    if en >= 2 * it:
        return "en"
    return None


# ---- Numbers written with digits (grounding of the operator explanation) ----------------------

_NUMBER_RE = re.compile(
    r"(?:(?<![^\s(\[])(?P<sign>[-−+]))?(?<![0-9.,])(?P<digits>[0-9]+(?:[.,][0-9]+)*)"
)


def parse_number(token: str) -> Decimal:
    """A number written with "." or "," as decimal separator. With both, the last one is the
    decimal separator; a single kind used more than once is a thousands separator."""
    separators = {c for c in token if c in ".,"}
    if len(separators) == 2:
        decimal = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        return Decimal(token.replace(thousands, "").replace(decimal, "."))
    if separators:
        sep = separators.pop()
        if token.count(sep) > 1:
            return Decimal(token.replace(sep, ""))
        return Decimal(token.replace(sep, "."))
    return Decimal(token)


def numbers_in_text(text: str) -> list[Decimal]:
    """Every number written with ASCII digits in the NFKC form of the text, with its sign."""
    found: list[Decimal] = []
    for match in _NUMBER_RE.finditer(unicodedata.normalize("NFKC", text)):
        value = parse_number(match.group("digits"))
        found.append(-value if match.group("sign") in ("-", "−") else value)
    return found


def other_numeric_characters(text: str) -> list[str]:
    """Numeric characters other than ASCII digits (left after NFKC): they cannot be grounded."""
    return [ch for ch in numeric_characters(text) if ch not in "0123456789"]


def strip_tokens(text: str, tokens: Iterable[str]) -> str:
    """Remove whole-token occurrences of known identifiers (case-insensitive) from the text."""
    out = unicodedata.normalize("NFKC", text)
    for token in sorted({t for t in tokens if t}, key=len, reverse=True):
        out = re.sub(rf"(?<![\w-]){re.escape(token)}(?![\w-])", " ", out, flags=re.IGNORECASE)
    return out


def word_count(text: str) -> int:
    return len(text.split())
