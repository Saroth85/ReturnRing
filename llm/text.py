"""Lexical checks on model text (docs/spec.md 8.7; review findings LLM-1, LLM-5, LLM-6, LLM-10 and
the adversarial reviews of 13 Oct, rounds 1 to 3).

Standard library only. Word checks run on folded forms of the text, so look-alike letters,
invisible characters, accents, dashes and letter spacing do not hide a word:

- `fold`: NFKC, casefold, no combining marks, common Cyrillic and Greek look-alikes mapped to
  Latin letters; format characters (soft hyphen, zero-width space) are removed, or replaced.
- `forms`: letter-only forms of the folded text. Format characters are removed in one form and
  read as spaces in another; inner joiners (any dash, dot, apostrophe) are read as word breaks
  in one form and removed in another; runs of three or more single letters ("f r o d e") are
  joined. A word is found if it appears in any form.
- `words`: the first form, as a list of lowercase letter runs.

Most checks are blocklists: they reduce risk, they do not prove a text is safe. The validator
applies them to the text Gemma still writes (operator statements, agent answers); the tests apply
them to every sentence of the customer phrasebook (llm/phrasebook.py). Three checks are
allowlists instead (round 2, finding R5; round 3, findings T7, T9 and T11):

- `foreign_characters`: on the raw text, before NFKC, only printable ASCII, the Italian accented
  vowels and a short typographic list are allowed, so no symbol or letter of another script can
  stand in for a letter of a banned word;
- `operator_topics`: an operator statement does not talk about levers, terms, refunds,
  restrictions, decisions or the risk and value levels of the quadrant at all (the interface
  shows the engine decision), so no lever value has to be read from the words;
- `grounding_problems`: a number of an operator statement is grounded only with the unit of its
  fact (or none) after it and a plain word or separator before it.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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
_INNER_MARKS_RE = re.compile(r"(?<=[a-z])[^a-z0-9\s]+(?=[a-z])")
_JOINERS = "_.·*'’"  # and every dash (Unicode category Pd)


def fold(text: str, format_chars: str = "") -> str:
    """NFKC, casefold, no combining marks, Latin look-alikes; each format character (Cf) is
    replaced by `format_chars`, so it is removed by default."""
    folded = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text).casefold())
    kept: list[str] = []
    for ch in folded:
        category = unicodedata.category(ch)
        if category == "Cf":
            kept.append(format_chars)
        elif category not in ("Mn", "Me"):
            kept.append(ch)
    return "".join(kept).translate(_CONFUSABLES)


def is_dash(ch: str) -> bool:
    """Any dash or hyphen (Unicode category Pd)."""
    return unicodedata.category(ch) == "Pd"


def _join_inner(folded: str) -> str:
    """Remove runs of joiners (dashes, dots, apostrophes) between two letters ("quattor-dici")."""

    def join(match: re.Match[str]) -> str:
        marks = match.group()
        return "" if all(ch in _JOINERS or is_dash(ch) for ch in marks) else marks

    return _INNER_MARKS_RE.sub(join, folded)


def _letters_only(folded: str) -> str:
    spaced = _NON_LETTERS_RE.sub(" ", folded).strip()
    return _SPACED_LETTERS_RE.sub(lambda m: m.group().replace(" ", ""), spaced)


def forms(text: str, *, leet: bool = False) -> list[str]:
    """Letter-only forms of the text, without repeats: format characters removed or read as
    spaces, inner joiners read as word breaks or removed; digits read as letters with `leet`."""
    found: list[str] = []
    for format_chars in ("", " "):
        folded = fold(text, format_chars)
        if leet:
            folded = folded.translate(_LEET)
        for variant in (folded, _join_inner(folded)):
            form = _letters_only(variant)
            if form not in found:
                found.append(form)
    return found


def words(text: str) -> list[str]:
    """Lowercase letter runs of the folded text."""
    return _letters_only(fold(text)).split()


def _stem_hits(
    form: str,
    stems: tuple[str, ...],
    *,
    allowed: tuple[str, ...] = (),
    whole: frozenset[str] = frozenset(),
    word_prefixes: tuple[str, ...] = (),
) -> list[str]:
    """Stems that start a word of the form (also after a prefix such as "anti"), and whole
    words; words that start with an allowed prefix are skipped."""
    found: list[str] = []
    for word in form.split():
        candidates = [word] + [word[len(p) :] for p in word_prefixes if word.startswith(p)]
        for candidate in candidates:
            if candidate.startswith(allowed):
                continue
            hit = (
                candidate
                if candidate in whole
                else next((stem for stem in stems if candidate.startswith(stem)), None)
            )
            if hit is not None and hit not in found:
                found.append(hit)
    return found


def _add[T](found: list[T], items: Iterable[T]) -> list[T]:
    for item in items:
        if item not in found:
            found.append(item)
    return found


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

# "Linked accounts", never "same person" (CLAUDE.md rule 11, spec 16), with its synonyms: a
# qualifier (same, one, single, sole, common, stesso, singolo, unico...), at most two more words
# that are not function words ("the same very real customer"), and a noun for a person, an
# identity or a household (adversarial review of 13 Oct, finding 4; round 2, finding R3).
_PERSON_NOUNS = (
    "persons?|people|individuals?|customers?|clients?|buyers?|shoppers?|owners?|holders?"
    "|users?(?! agents?\\b)|humans?|entity|entities|guys?|man|men|woman|women"
    "|consumers?|purchasers?|members?|subscribers?|part(?:y|ies)|identit(?:y|ies)"
    "|actors?|operators?|somebody|someone"
    "|person[ae]|individu[oi]|soggett[oi]|client[ei]|utent[ei]|acquirent[ei]|titolar[ei]"
    "|intestatari[oae]?|proprietari[oae]?|entita|identita|consumator[ei]|consumatric[ei]"
    "|comprator[ei]|compratric[ei]|utilizzator[ei]|utilizzatric[ei]|esser[ei] uman[oi]"
    "|attor[ei]|operator[ei]|qualcuno"
)
# A household is several people: only with a qualifier ("the same household"), never "a family".
_GROUP_NOUNS = "households?|famil(?:y|ies)|famigli[ae]|nucle[oi]"
_QUALIFIERS = (
    "same|single|one|sole|selfsame|identical|common|stess[aeoi]|medesim[aeoi]|singol[aeoi]"
    "|unic[aeoi]|comun[ei]"
)
_FUNCTION_WORDS = (
    "of|or|and|the|a|an|to|for|in|on|with|as|by|from|at|than|di|del|della|dei|degli|delle|e|o"
    "|per|con|tra|fra|da|su|al|alla|che"
)
_GAP = rf"(?: (?!(?:{_FUNCTION_WORDS})\b)[a-z]+){{0,2}}"
_SAME_PERSON_RES: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b(?:{_QUALIFIERS}){_GAP} (?:{_PERSON_NOUNS}|{_GROUP_NOUNS})\b"),
    re.compile(rf"\bun[ao]? (?:sol[aeoi]|unic[aeoi]){_GAP} (?:{_PERSON_NOUNS})\b"),
    re.compile(r"\bone and the same\b"),
    # "the one and only customer", "lo stesso e unico cliente" (round 3, finding T6)
    re.compile(
        rf"\b(?:{_QUALIFIERS}) (?:and|e) (?:only|sole|single|same|selfsame|unic[aeoi]|sol[aeoi]"
        rf"|stess[aeoi]|singol[aeoi]){_GAP} (?:{_PERSON_NOUNS})\b"
    ),
    # "the linked accounts share an owner", "condividono un titolare"
    re.compile(rf"\bshar(?:e|es|ed|ing) (?:(?:a|an|one|the same|the) )?(?:{_PERSON_NOUNS})\b"),
    re.compile(
        rf"\bcondivid\w* (?:(?:un|una|uno|il|lo|la|lo stesso|la stessa) )?(?:{_PERSON_NOUNS})\b"
    ),
)
# "un cliente", "una persona": an article as often as a numeral, so only in a sentence that also
# talks about accounts ("Un cliente controlla tutti gli account collegati").
_ONE_PERSON_RE = re.compile(rf"\bun[ao]? (?:{_PERSON_NOUNS})\b")
_ACCOUNTS_RE = re.compile(r"\b(?:accounts?|profil[oi]|collegat[oaie]|linked)\b")
_SENTENCE_RE = re.compile(r"[.!?;:\n]+")
# Ownership and agency in a sentence about the linked accounts (round 2, finding R3; round 3,
# finding T6): owning, controlling, running, managing or creating them, being behind them, the
# accounts belonging to anything but a cluster, tracing back to someone, "the customer's
# accounts", Italian possessives and "del cliente". "Its own" and "their own" are not ownership,
# and a participle without an agent ("accounts created within 9 days") is not agency.
_LINKED_RE = re.compile(
    r"\b(?:accounts|linked|collegat[oaie]|profili|clusters?"
    r"|(?:gli|degli|questi|tutti gli) account)\b"
)
_OBJECT_STARTS = (
    "the|these|those|this|that|all|both|every|each|its|their|his|her|them|it|a|an|several"
    "|multiple|many|other|linked|accounts|account|new|separate|different"
)
_PASSIVE = "(?<!was )(?<!were )(?<!is )(?<!are )(?<!be )(?<!been )(?<!being )(?<!get )(?<!got )"
_GROUPS = "clusters?|groups?|rings?|networks?|components?|sets?|graphs?|communit(?:y|ies)"
_OWNERSHIP_RE = re.compile(
    r"(?<!its )(?<!their )(?<!your )(?<!his )(?<!her )(?<!our )(?<!my )\bown(?:s|ed|ing)?\b"
    r"|\b(?:controls|controlling|operates|operating|runs|running|ran|manages|managing|creates"
    r"|creating|opens|behind|control|operate|manage)\b"
    r"|\b(?:owned|controlled|operated|run|managed|created|opened|held|set up) by\b"
    rf"|{_PASSIVE}\b(?:controlled|operated|managed|created|opened)(?= (?:{_OBJECT_STARTS})\b)"
    r"|\bbelong(?:s|ed|ing)? to\b(?! ?$| (?:(?:the|a|an|this|that|these|those|same|one"
    rf"|different|another|other|separate) )*(?:{_GROUPS})\b)"
    r"|\b(?:trace[sd]?|tracing|leads?|led|leading) back\b|\btraceable to\b"
    r"|\b(?:customer|client|buyer|shopper|user|consumer|person|owner|holder) s (?:[a-z]+ )?"
    r"accounts\b"
    rf"|\baccounts? (?:[a-z]+ )?of (?:the|this|that|a|one) (?:{_PERSON_NOUNS})\b"
    r"|\b(?:yours|theirs|hers|mine)\b"
    r"|\b(?:tuoi|tue|suoi|sue|vostri|vostre|miei|mie)\b|\b(?:tutti|tutte) di\b"
    r"|\b(?:del|dello|della|dell) (?:cliente|utente|persona|titolare|consumatore|consumatrice"
    r"|acquirente|compratore|compratrice|proprietari[oa])\b"
    r"|\b(?:appartien\w*|appartengono|apparten\w+|controll\w+|gestit[oaie]|gestisc\w+"
    r"|possedut[oaie]|possied\w+|intestat[oaie]|riconducibil\w+)\b"
    r"|\bfa(?:nno)? capo\b|\b(?:creat|apert)[oaie] da\b"
)


def _same_person(text: str) -> list[str]:
    found: list[str] = []
    for form in forms(text, leet=True):
        _add(found, (m.group() for p in _SAME_PERSON_RES for m in p.finditer(form)))
    for sentence in _SENTENCE_RE.split(fold(text, " ").translate(_LEET)):
        form = _letters_only(sentence)
        if _ACCOUNTS_RE.search(form):
            _add(found, (m.group() for m in _ONE_PERSON_RE.finditer(form)))
        if _LINKED_RE.search(form):
            _add(found, (m.group().strip() for m in _OWNERSHIP_RE.finditer(form)))
    return found


def banned_terms(text: str) -> list[str]:
    """Banned stems and same-person phrases found in the text (empty when clean)."""
    found: list[str] = []
    for form in forms(text, leet=True):
        hits = _stem_hits(
            form, BANNED_STEMS, allowed=ALLOWED_PREFIXES, word_prefixes=_WORD_PREFIXES
        )
        _add(found, hits)
    return _add(found, _same_person(text))


# ---- Character allowlist (round 2, finding R5; round 3, finding T9) ---------------------------

ITALIAN_VOWELS = "àáèéìíòóùúÀÁÈÉÌÍÒÓÙÚ"
# Typographic characters a model may use: curly quotes, en and em dash, ellipsis, euro sign and
# the no-break space.
TYPOGRAPHIC_CHARACTERS = "’‘“”–—…€ "
ALLOWED_CHARACTERS = frozenset(
    [chr(code) for code in range(0x20, 0x7F)] + list("\t\n\r" + ITALIAN_VOWELS)
) | frozenset(TYPOGRAPHIC_CHARACTERS)


def foreign_characters(text: str) -> list[str]:
    """Characters of the raw text, before NFKC, outside the allowlist: printable ASCII, tab and
    line breaks, the Italian accented vowels and TYPOGRAPHIC_CHARACTERS. Model text with any of
    them is regenerated, so no letter of another script, enclosed or full-width letter, flag
    letter or symbol that looks like a letter ("pers°n", "sc∀m") can hide a word."""
    found: list[str] = []
    for ch in text:
        if ch not in ALLOWED_CHARACTERS and ch not in found:
            found.append(ch)
    return found


# ---- Terms and decisions in words (adversarial review of 13 Oct, finding 1) -------------------

# Customer-message words for the terms of the lever catalogue (contracts/levers.py): the terms
# come only from the {terms} field, written by code (rule 10).
TERMS_STEMS: tuple[str, ...] = (
    "gratuit",
    "gratis",
    "free",
    "istantane",
    "instant",
    "immediat",
    "subito",
    "rapid",
    "veloc",
    "quick",
    "spedi",
    "ship",
    "coupon",
    "voucher",
    "scont",
    "discount",
    "cambio",
    "scambi",
    "exchange",
    "finestra",
    "window",
    "expens",
)
TERMS_WORDS = frozenset({"cambi", "spese", "fee", "fees", "complimentary"})
_TERMS_PHRASES: tuple[re.Pattern[str], ...] = (
    # who pays
    re.compile(r"\ba (?:tuo|tuoi|tue|vostro|suo|loro) (?:carico|spese)\b"),
    re.compile(
        r"\b(?:no|nessun[oa]?|without|senza) (?:[a-z]+ )?"
        r"(?:cost[oi]?|costs|charges?|spes[ae]|fees?|addebit[oi])\b"
    ),
    re.compile(r"\bcosts? (?:you )?nothing\b|\bcost(?:a|era) (?:nulla|niente)\b"),
    re.compile(r"\bon the house\b"),
    # how much is refunded: the amount comes only from {amount}
    re.compile(r"\b(?:full|partial|total|entire|whole) (?:refund|amount|price)\b"),
    re.compile(r"\brimborso (?:complet|total|inter|integral|parzial)[a-z]*\b"),
    re.compile(r"\b(?:intero|totale) importo\b|\bprezzo pieno\b|\btutto quanto\b"),
)
# Decisions in words: the decision is the engine's, shown by code (rules 1 and 3).
DECISION_STEMS: tuple[str, ...] = (
    "approv",
    "accett",
    "accept",
    "rifiut",
    "respint",
    "negat",
    "reject",
    "deni",
    "deny",
    "refus",
    "declin",
    "blocc",
    "block",
    "blacklist",
    "indagin",
    "investigat",
    "autorizz",
    "authoriz",
    "authoris",
    "sospes",
    "sospend",
    "sospens",
    "suspend",
    "accolt",
    "grant",
)
_DECISION_PHRASES = re.compile(
    r"\bturned down\b|\bgreen light\b|\bvia libera\b|\bbuon fine\b"
    r"|\besito (?:positivo|negativo|favorevole|sfavorevole)\b"
)
# Words that share a stem: denim, scontrino (receipt), scontento (unhappy), negative (not
# "negato", denied).
TERMS_ALLOWED: tuple[str, ...] = ("denim", "scontr", "scontent", "negativ")
_TERMS_ALLOWED_PHRASES = re.compile(r"\bfeel free\b")


def _decision_hits(form: str) -> list[str]:
    found = _stem_hits(form, DECISION_STEMS, allowed=TERMS_ALLOWED)
    return _add(found, (m.group() for m in _DECISION_PHRASES.finditer(form)))


def _terms_hits(form: str, stems: tuple[str, ...], whole: frozenset[str]) -> list[str]:
    found = _stem_hits(form, stems, allowed=TERMS_ALLOWED, whole=whole)
    return _add(found, (m.group() for p in _TERMS_PHRASES for m in p.finditer(form)))


def decision_wording(text: str) -> list[str]:
    """Words that state a decision (approve, reject, block, investigate...)."""
    found: list[str] = []
    for form in forms(text, leet=True):
        _add(found, _decision_hits(form))
    return found


def terms_wording(text: str) -> list[str]:
    """Words that state return terms (free, instant, shipping, coupons, window...) or a decision.
    The customer message is connective text only: terms come from {terms}."""
    found: list[str] = []
    for form in forms(text, leet=True):
        _add(found, _terms_hits(_TERMS_ALLOWED_PHRASES.sub(" ", form), TERMS_STEMS, TERMS_WORDS))
    return _add(found, decision_wording(text))


# ---- Topics of the operator statements (round 3, findings T7 and T8) ---------------------------

# Restrictions in words; "limited" alone is left out ("limited evidence" is a plain observation).
RESTRICTION_STEMS: tuple[str, ...] = (
    "restrict",
    "restrizion",
    "restritt",
    "strict",
    "tighten",
    "harsh",
    "limitation",
    "limitazion",
    "limitat",
    "penal",
    "sanction",
    "sanzion",
    "withh",
)
# The terms stems of the customer gate, restrictions, refunds and their paraphrases (repay,
# reimburse, postage, prepaid), promotions and perks, levers and terms.
OPERATOR_TOPIC_STEMS: tuple[str, ...] = (
    TERMS_STEMS
    + RESTRICTION_STEMS
    + ("refund", "rimbors", "reimburs", "repay", "repaid", "postage", "prepaid", "promo", "perk")
    + ("privileg", "concession", "lever", "condizion", "transparenc", "trasparenz")
)
OPERATOR_TOPIC_WORDS = TERMS_WORDS | word_set(
    "terms termini leva leve policy policies limit limits lose loses charged"
)
# Observed phrases that share a stem with a lever word: the welcome coupons of a cluster are
# data, not the coupons lever (round 3, finding T7).
_OPERATOR_ALLOWED_PHRASES = re.compile(
    r"\bfeel free\b|\bwelcome coupons?\b|\bfree text\b|\bin terms of\b"
    r"|\bin (?:quick|rapid) succession\b"
)
_OPERATOR_PHRASES = re.compile(
    r"\bsize advice\b|\bconsigli[oi]? (?:di |sulla |per la )?taglia\b|\bmoney back\b"
    r"|\bstore credit\b|\bgift cards?\b"
)


def operator_topics(text: str) -> list[str]:
    """Words of a topic an operator statement never talks about: levers and return terms (the
    stems of the customer gate, refunds, promotions, "terms", "policy"), restrictions, decision
    words, the risk and value levels of the quadrant, and places in the matrix. The interface
    shows the engine decision and its levers from code, so the statement has nothing to say
    about them (round 3, finding T7). Metric names and the observed phrase "welcome coupon(s)"
    are removed by the caller and here."""
    found: list[str] = []
    for form in forms(text, leet=True):
        form = _OPERATOR_ALLOWED_PHRASES.sub(" ", form)
        _add(found, _terms_hits(form, OPERATOR_TOPIC_STEMS, OPERATOR_TOPIC_WORDS))
        _add(found, (m.group() for m in _OPERATOR_PHRASES.finditer(form)))
        _add(found, _decision_hits(form))
    levels = [f"{axis} {level}" for kind, axis, level in quadrant_levels(text) if kind == "axis"]
    return _add(_add(found, levels), quadrant_positions(text))


# ---- Numbers written as words or symbols (LLM-1; adversarial review of 13 Oct, finding 2) ------

_IT_UNITS = "uno|due|tre|quattro|cinque|sei|sette|otto|nove"
_IT_TEENS = (
    "dieci|undici|dodici|tredici|quattordici|quindici|sedici|diciassette|diciotto|diciannove"
)
_IT_TENS = "venti|trenta|quaranta|cinquanta|sessanta|settanta|ottanta|novanta"
_IT_TENS_ELIDED = "vent|trent|quarant|cinquant|sessant|settant|ottant|novant"
_IT_UNDER_100 = (
    f"(?:{_IT_TEENS}|(?:{_IT_TENS})(?:due|tre|quattro|cinque|sei|sette|nove)?"
    f"|(?:{_IT_TENS_ELIDED})(?:uno|un|otto)|{_IT_UNITS})"
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
# Ordinals in -esimo: the root is a cardinal without its last vowel ("undicesimo", "ventesimo").
_IT_ESIMO_RE = re.compile(r"([a-z]+)esim[oaie]")
_IT_ORDINALS = word_set(
    "primo primi secondo seconda secondi seconde terzo terza terzi terze quarto quarta quarti "
    "quarte quinto quinta quinti quinte sesto sesta sesti seste settimo settima settimi settime "
    "ottavo ottava ottavi ottave nono nona noni decimo decima decimi decime"
)
# Fractions and multiples; "mezz" starts mezzo, mezza, mezzanotte, mezzogiorno, mezz'ora.
_IT_OTHER = word_set("zero meta paio doppio doppia doppi doppie triplo tripla tripli triple")
_IT_STEMS = ("mezz",)
# "sei" alone is also "you are" and "siete" "you are" in the plural; "due" is an English word
# ("due by"). "sei giorni" is still caught by the unit check.
_NOT_NUMBERS: dict[Lang, frozenset[str]] = {
    "it": frozenset({"sei", "siete"}),
    "en": frozenset({"sei", "due"}),
}

_EN_CARDINALS = word_set(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety "
    "hundred hundreds thousand thousands million millions billion billions"
)
_EN_ORDINALS = word_set(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth "
    "thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth thousandth "
    "millionth billionth"
)
# Fractions, multiples, slang amounts and quantity words (round 3, finding T10).
_EN_OTHER = word_set(
    "zero dozen dozens fortnight fortnights fortnightly twice thrice half halves halve halved "
    "halving quarter quarters tenner tenners fiver fivers sennight sennights couple couples pair "
    "pairs handful handfuls trio trios grand grands quid quids buck bucks"
)
# Multiplicatives: double, doubled, triple, quadrupled, septuple...
_EN_STEMS = ("doubl", "tripl", "quadrupl", "quintupl", "sextupl", "septupl", "octupl", "decupl")
# French, Spanish and German cardinals from 1 to 12, without those that are Italian or English
# words or articles ("un", "une", "una", "uno", "once", "ein").
_FOREIGN_CARDINALS = word_set(
    "deux trois quatre cinq six sept huit neuf dix onze douze "
    "dos tres cuatro cinco seis siete ocho nueve diez doce "
    "eins zwei drei vier funf fuenf sechs sieben acht neun zehn elf zwolf zwoelf"
)
# Pronoun uses ("this one") are too common in free operator text; the customer check keeps them.
PRONOUN_NUMBERS = frozenset({"one", "uno"})
# A cardinal this long at the start of a longer word is read as a number ("twentyish").
_LONG_CARDINAL = 6
# Abbreviated units a short cardinal may be glued to ("tenhrs", "sixd", "dueh", "duesett"), and
# the words that only look like one ("tend", "tremo"). Round 2, finding R6.
_UNIT_ABBREVIATIONS = word_set(
    "h hr hrs d dd g gg w wk wks m mo mos mth mths y yr yrs min mins sec secs sett"
)
# A cardinal of three or more letters with one of these endings ("sevenfold", "tenish",
# "twelvish", "sevens"); "ones" is a pronoun and "dues" an English word.
_GLUED_ENDINGS = ("fold", "ish", "s")
_NOT_GLUED = word_set("tend tremo ones dues")


def _cardinal(word: str) -> bool:
    return word in _EN_CARDINALS or _IT_NUMBER_RE.fullmatch(word) is not None


@functools.lru_cache(maxsize=4096)
def _plain_number(word: str) -> bool:
    """A cardinal, ordinal, fraction or multiple of either language, or a French, Spanish or
    German cardinal up to twelve, written as one word."""
    if _cardinal(word) or word in _EN_ORDINALS or word in _EN_OTHER:
        return True
    if word in _IT_ORDINALS or word in _IT_OTHER or word in _FOREIGN_CARDINALS:
        return True
    if word.startswith(_IT_STEMS) or word.startswith(_EN_STEMS):
        return True
    if word.endswith("s") and word[:-1] in _EN_ORDINALS:  # "thirds"
        return True
    esimo = _IT_ESIMO_RE.fullmatch(word)
    return esimo is not None and any(
        _IT_NUMBER_RE.fullmatch(esimo.group(1) + vowel) for vowel in ("", "a", "e", "i", "o")
    )


@functools.lru_cache(maxsize=4096)
def _glued_number(word: str) -> bool:
    """A number glued to a unit, an abbreviated unit or another number ("fourteendays",
    "quattordicigiorni", "tenhrs"), a long cardinal at the start of a longer word
    ("twentyish"), or a cardinal with -fold, -ish or -s ("sevenfold", "twelvish", "sevens")."""
    if word in _NOT_GLUED:
        return False
    for end in range(len(word) - 1, 2, -1):
        head, rest = word[:end], word[end:]
        if rest in _GLUED_ENDINGS and (_cardinal(head) or _cardinal(head + "e")):
            return True
        if _plain_number(head) and (
            (len(head) >= _LONG_CARDINAL and _cardinal(head))
            or rest in UNIT_WORDS
            or rest in _UNIT_ABBREVIATIONS
            or _plain_number(rest)
        ):
            return True
    return False


def number_word(word: str, lang: Lang) -> bool:
    """True if a folded word is a number written in letters: a cardinal, an ordinal, a fraction
    or multiple, a quantity word, a number glued to a unit or an ending. Numbers of every
    language are checked in both languages, except the words of `_NOT_NUMBERS`."""
    if word in _NOT_NUMBERS[lang]:
        return False
    return _plain_number(word) or _glued_number(word)


# Roman numerals of two or more upper-case letters, in canonical form ("VII", "XLII", "XIV"),
# in statements and answers (round 3, finding T10). "XL" is a clothing size; a numeral right
# after "Q" is a quadrant label, read by `quadrant_labels`.
_ROMAN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[MDCLXVI]{2,}(?![A-Za-z0-9]))"
    r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})(?![A-Za-z0-9])"
)
_NOT_ROMAN = frozenset({"XL"})
_QUADRANT_PREFIX_RE = re.compile(r"[Qq][\s_\-‐‑‒–—]?$")


def roman_numerals(text: str) -> list[str]:
    """Upper-case Roman numerals of the NFKC text, other than "XL" and quadrant labels."""
    normal = unicodedata.normalize("NFKC", text)
    found: list[str] = []
    for match in _ROMAN_RE.finditer(normal):
        quadrant = _QUADRANT_PREFIX_RE.search(normal[: match.start()]) is not None
        if match.group() not in _NOT_ROMAN and not quadrant:
            _add(found, [match.group()])
    return found


def numeric_characters(text: str) -> list[str]:
    """Characters with a numeric value (digits of any script, fractions, Roman numeral signs), in
    the raw text and in its NFKC form. Roman numerals written with ASCII letters are found by
    `roman_numerals`."""
    found: list[str] = []
    for ch in text + unicodedata.normalize("NFKC", text):
        if unicodedata.numeric(ch, None) is not None and ch not in found:
            found.append(ch)
    return found


def number_words(text: str, lang: Lang, *, skip: Iterable[str] = ()) -> list[str]:
    """Numbers written in letters, in any form of the text."""
    skipped = frozenset(skip)
    found: list[str] = []
    for form in forms(text):
        _add(found, (w for w in form.split() if w not in skipped and number_word(w, lang)))
    return found


def literal_numbers(text: str, lang: Lang) -> list[str]:
    """Numbers written by the model in a customer message: digits, numeric characters, words."""
    return numeric_characters(text) + number_words(text, lang)


# ---- Units of time and money, calendar words (LLM-1; adversarial review of 13 Oct) ----------

UNIT_WORDS = word_set(
    "giorni giornate gg notti settimana settimane mese mesi anno anni ore minuto minuti "
    "secondi trimestre semestre days nights week weeks month months year years hour hours minute "
    "minutes seconds euro euros eur centesimo centesimi cent cents dollaro dollari dollar dollars "
    "usd gbp sterlina sterline pound pounds percento percent penny pennies pence "
    "h hr hrs wk wks mo yr yrs min mins sec secs sett"
)
# Dates, deadlines, holidays and periods in words. "may" is left out (a modal verb).
DATE_WORDS = word_set(
    "gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre "
    "dicembre january february march april june july august september october november "
    "december lunedi martedi mercoledi giovedi venerdi sabato domenica monday tuesday wednesday "
    "thursday friday saturday sunday oggi domani dopodomani ieri stasera stanotte stamattina "
    "stamani mezzanotte mezzogiorno today tomorrow tonight yesterday midnight noon "
    "domattina posdomani indomani stamane overnight eod cob alba dawn sunrise sunset tramonto "
    "cena pranzo christmas xmas easter natale pasqua ferragosto capodanno finesettimana "
    "decade decades century centuries decennio decenni secolo secoli lustro lustri biennio "
    "bienni bimestre bimestri quadrimestre quadrimestri"
)
# Units written as two words: "un'ora" ("ora" alone is "now"), "end of business".
_UNIT_PHRASES = re.compile(r"\b(?:un|una|mezz|mezza) ora\b|\b(?:end|close) of business\b")


# Times of day are units too ("over the weekend", "entro la giornata"), except in a greeting:
# right after a greeting adjective ("have a nice day", "buona giornata", "buon weekend").
TIME_OF_DAY_WORDS = word_set(
    "day giorno giornata weekend notte night sera serata evening mattina mattinata morning "
    "pomeriggio afternoon"
)
_GREETING_ADJECTIVES = word_set(
    "buon buona buonissima buonissimo bel bella bellissima ottima felice nice good great lovely "
    "happy wonderful pleasant fantastic splendid"
)


def _unit_words(form: str) -> list[str]:
    tokens = form.split()
    found = [
        word
        for index, word in enumerate(tokens)
        if word in UNIT_WORDS
        or word in DATE_WORDS
        or (
            word in TIME_OF_DAY_WORDS
            and (index == 0 or tokens[index - 1] not in _GREETING_ADJECTIVES)
        )
    ]
    return found + [m.group() for m in _UNIT_PHRASES.finditer(form)]


def literal_units(text: str) -> list[str]:
    """Currency symbols, percent signs, words for units of time or money, calendar words and
    times of day outside greetings."""
    found: list[str] = []
    for ch in unicodedata.normalize("NFKC", text):
        if (unicodedata.category(ch) == "Sc" or ch in "%‰") and ch not in found:
            found.append(ch)
    for form in forms(text):
        _add(found, _unit_words(form))
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
    "entitle",
)
LEGAL_WORDS = frozenset({"legge", "law", "laws", "rights"})
_RIGHT_RE = re.compile(
    r"\bright (?:to|of)\b|\b(?:your|legal|statutory) right\b|\b(?:ti|vi|le|gli) spett(?:a|ano)\b"
)
_REFUND_TIMING: tuple[tuple[str, str], ...] = (("rimbors", "entro"), ("refund", "within"))


def legal_wording(text: str) -> list[str]:
    """Words about rights, the guarantee, the law or refund timing written by the model."""
    found: list[str] = []
    for form in forms(text):
        _add(found, _stem_hits(form, LEGAL_STEMS, whole=LEGAL_WORDS))
        _add(found, (m.group() for m in _RIGHT_RE.finditer(form)))
    for format_chars in ("", " "):
        for sentence in _SENTENCE_RE.split(fold(text, format_chars)):
            sentence_words = _letters_only(sentence).split()
            for refund, within in _REFUND_TIMING:
                if any(w.startswith(refund) for w in sentence_words) and within in sentence_words:
                    _add(found, [f"{refund} ... {within}"])
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

# A leading separator is part of the number (".87"); any dash or minus sign before the digits,
# also with spaces between ("- 42.1"), is a sign (adversarial review of 13 Oct, finding 5; round
# 2, finding R7).
_NUMBER_RE = re.compile(r"(?<![0-9])(?:[.,](?=[0-9]))?[0-9]+(?:[.,][0-9]+)*")
_MINUS_SIGNS = "−➖˗"


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


def signed_numbers(folded: str) -> list[tuple[Decimal, int, int]]:
    """Every number written with ASCII digits in an already folded text, with its span; negative
    when a dash or minus sign comes before it, spaces aside."""
    found: list[tuple[Decimal, int, int]] = []
    for match in _NUMBER_RE.finditer(folded):
        value = parse_number(match.group())
        before = folded[: match.start()].rstrip()[-1:]
        negative = before != "" and (before in _MINUS_SIGNS or is_dash(before))
        found.append((-value if negative else value, match.start(), match.end()))
    return found


def numbers_in_text(text: str) -> list[Decimal]:
    """Every number written with ASCII digits in the folded text (NFKC, no format characters),
    negative when a dash or minus sign comes before it, spaces aside."""
    return [value for value, _, _ in signed_numbers(fold(text))]


# A number with a letter or a percent sign right after it ("42.1k", "0.87%"), a scale or unit
# word after it ("42.1 M", "42 mln", "42.1 grand", "42.1 kEUR", "42.1 percent"), a sign around
# it ("(42.1)", "±42.1", "42.1-"), or a sign word in a text with a number ("minus 42.1", "a loss
# of 42.1", "42.1 EUR owed"): its value is not the value written, so it cannot be grounded.
# This blocklist checks the agent's answers; operator statements use the stricter allowlist of
# `grounding_problems` (round 3, finding T11).
_SUFFIXED_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*(?:[^\W\d_]+|[%‰‱×])")
_SCALED_RE = re.compile(
    r"[0-9]\s*(?:k|m|b|mln|mld|mrd|mn|bn|mio|mil|mill|grand|[kmb]eur|percent|per cent|percento"
    r"|per cento|bps?|pp|basis points?|punti base|punti percentuali)\b"
)
_SIGNED_RE = re.compile(
    r"\(\s*[0-9]+(?:[.,][0-9]+)*\s*\)|[±∓]\s*[0-9]|[0-9][-−➖˗‐‑‒–—―⁻](?![0-9a-z])"
)
# Round 3, finding T11: losing, costs, short, arrears, in passivo, in rimessa, less, deducted,
# written off.
_SIGN_WORDS_RE = re.compile(
    r"\b(?:minus|meno|negativ[eoai]|loss|losses|lossmaking|perdit[ae]|deficit|disavanz[oi]"
    r"|in the red|in rosso|owed|owes|owing|debit(?! cards?\b)|debits|debts?|debit[oi]"
    r"|shortfall|lost|unprofitable|lose|loses|losing|cost|costs|costing|short|arrears"
    r"|passiv[oaie]|rimessa|less|deduct\w*|written off|writes? off|writeoffs?)\b"
)


def number_modifiers(text: str) -> list[str]:
    """Scale suffixes, unit words and signs around numbers, and sign words in a text with a
    number; empty when every number is plain."""
    found: list[str] = []
    patterns = ((fold(text), _SUFFIXED_RE), (fold(text, " "), _SCALED_RE), (fold(text), _SIGNED_RE))
    for folded, pattern in patterns:
        _add(found, (m.group().strip() for m in pattern.finditer(folded)))
    if _NUMBER_RE.search(fold(text)):
        for form in forms(text):
            _add(found, (m.group() for m in _SIGN_WORDS_RE.finditer(form)))
    return found


def other_numeric_characters(text: str) -> list[str]:
    """Numeric characters other than ASCII digits (left after NFKC): they cannot be grounded."""
    return [ch for ch in numeric_characters(text) if ch not in "0123456789"]


def sign_words(text: str) -> list[str]:
    """Words that give a number a sign ("minus", "a loss of", "owed", "in arrears")."""
    found: list[str] = []
    for form in forms(text):
        _add(found, (m.group() for m in _SIGN_WORDS_RE.finditer(form)))
    return found


# ---- The context of a grounded number (round 3, finding T11) ----------------------------------

# A number of an operator statement is grounded only by the value of a cited fact AND an allowed
# context, so no unit, scale, rate, comparator or sign can change what it means:
# - before it: the start of the text, a word that is not a comparator or a unit of another kind,
#   a separator (":", "=", ",", ";"), "(" after a word, or a dash with spaces around it after a
#   word ("margin — 42.1 EUR"); any other dash or minus right before the digits is a sign;
# - after it: the end of the text, punctuation, a spaced dash, a function word, or the unit of
#   the fact's metric (EUR or euro for money, days or giorni for day counts; for scores, rates
#   and counts no unit or a count noun), itself followed by the end, punctuation, a spaced dash
#   or a function word (a count noun by anything but a rate word).
UnitKind = Literal["money", "days", "count"]
_UNITS: dict[UnitKind, frozenset[str]] = {
    "money": word_set("eur euro euros €"),
    "days": word_set("day days giorni giorno"),
    "count": frozenset(),
}
_COUNT_NOUNS = word_set(
    "linked accounts account returns return orders order items item events event links link "
    "cards card devices device addresses address coupons coupon welcome purchases purchase resi "
    "reso ordini ordine articoli articolo collegati eventi carte"
)
_FUNCTION_AFTER = word_set(
    "and or but in on at of for from to with over across during since as is are was were be "
    "been has have had the this that these those which who while so because after before later "
    "earlier ago e o ma di del della nel nella con da che su"
)
_RATE_WORDS = word_set("per a an every weekly monthly daily yearly annually hourly times x apiece")
_COMPARATORS = word_set(
    "about around approximately approx roughly nearly almost circa over under above below beyond "
    "less more than up least most max min maximum minimum exceeding exceeds within plus minus "
    "times x by sotto sopra oltre quasi almeno massimo minimo meno piu fino entro"
)
_ANY_UNIT = UNIT_WORDS | _UNITS["money"] | _UNITS["days"]
_SEPARATORS = frozenset(":=,;.!?")
_CLOSERS = frozenset(".,;:!?)(…")
_GROUND_TOKEN_RE = re.compile(r"(?<![0-9])(?:[.,](?=[0-9]))?[0-9]+(?:[.,][0-9]+)*|[^\W\d_]+|\S")


def unit_kind(metric: str) -> UnitKind:
    """The kind of unit of a fact: money for *_eur*, days for *_days*, a count otherwise."""
    parts = metric.split("_")
    if "eur" in parts:
        return "money"
    return "days" if "days" in parts else "count"


@dataclass(frozen=True)
class _Token:
    text: str
    start: int
    end: int

    @property
    def word(self) -> bool:
        return self.text[0].isalpha()

    @property
    def number(self) -> bool:
        return self.text[-1].isdigit()

    @property
    def dash(self) -> bool:
        return len(self.text) == 1 and (is_dash(self.text) or self.text in _MINUS_SIGNS)


def _spaced(tokens: Sequence[_Token], index: int) -> bool:
    """A dash with spaces on both sides, right after a word: punctuation, not a sign."""
    dash = tokens[index]
    return (
        dash.text not in _MINUS_SIGNS
        and 0 < index < len(tokens) - 1
        and tokens[index - 1].word
        and tokens[index - 1].end < dash.start
        and dash.end < tokens[index + 1].start
    )


def _before_ok(tokens: Sequence[_Token], index: int, kind: UnitKind) -> bool:
    if index < 0:
        return True
    token = tokens[index]
    if token.word:
        return token.text not in _COMPARATORS and (
            token.text not in _ANY_UNIT or token.text in _UNITS[kind]
        )
    if token.text == "(":
        return index > 0 and tokens[index - 1].word and tokens[index - 1].text not in _COMPARATORS
    return token.text in _SEPARATORS or token.dash or token.text in _UNITS[kind]


def _end_ok(tokens: Sequence[_Token], index: int) -> bool:
    if index >= len(tokens):
        return True
    token = tokens[index]
    if token.word:
        return token.text in _FUNCTION_AFTER
    return token.text in _CLOSERS or (token.dash and token.start > tokens[index - 1].end)


def _after_ok(tokens: Sequence[_Token], index: int, kind: UnitKind) -> bool:
    following = index + 1
    if following < len(tokens) and tokens[following].text in _UNITS[kind]:
        return _end_ok(tokens, following + 1)
    if kind == "count" and following < len(tokens) and tokens[following].text in _COUNT_NOUNS:
        while following < len(tokens) and tokens[following].text in _COUNT_NOUNS:
            following += 1
        return following >= len(tokens) or tokens[following].text not in _RATE_WORDS
    return _end_ok(tokens, following)


def grounding_problems(text: str, cited: Sequence[tuple[str, Decimal]]) -> list[str]:
    """The numbers of an operator statement that are not grounded on the cited facts, given as
    (metric, value): a value no cited fact has, sign included, a value outside an allowed
    context, and sign words in a text with a number that is not negative (a negative fact
    written with its sign may say "negative")."""
    folded = fold(text)
    tokens = [_Token(m.group(), m.start(), m.end()) for m in _GROUND_TOKEN_RE.finditer(folded)]
    problems: list[str] = []
    values: list[Decimal] = []
    for index, token in enumerate(tokens):
        if not token.number:
            continue
        value, before = parse_number(token.text), index - 1
        if before >= 0 and tokens[before].dash and not _spaced(tokens, before):
            value, before = -value, before - 1
        values.append(value)
        kinds = {unit_kind(metric) for metric, fact in cited if fact == value}
        if not kinds:
            problems.append(str(value))
        elif not any(_before_ok(tokens, before, k) and _after_ok(tokens, index, k) for k in kinds):
            last = tokens[min(index + 2, len(tokens) - 1)]
            problems.append(folded[tokens[max(before, 0)].start : last.end])
    if any(value >= 0 for value in values):
        problems += sign_words(text)
    return problems


# ---- Quadrant labels (adversarial review of 13 Oct, finding 3) --------------------------------

_QUADRANT_RE = re.compile(r"(?<![a-z0-9])q[\s_‐‑‒–—-]*([1-4])(?![0-9])")
_QUADRANT_ROMAN_RE = re.compile(r"(?<![a-z0-9])q[\s_‐‑‒–—-]?(iv|iii|ii|i)(?![a-z0-9])")
# "Q-one", "q quattro" (round 3, finding T7)
_QUADRANT_NUMBER_RE = re.compile(
    r"(?<![a-z0-9])q[\s_‐‑‒–—-]*(one|two|three|four|uno|due|tre|quattro)\b"
)
_QUADRANT_WORD_RE = re.compile(
    r"\bquadrant[ei]?[\s:#]*(?:no\.?\s*|number\s+)?"
    r"([1-4]|one|two|three|four|uno|due|tre|quattro|iv|iii|ii|i)\b"
)
_QUADRANT_ORDINAL_RE = re.compile(
    r"\b(first|second|third|fourth|primo|secondo|terzo|quarto|1st|2nd|3rd|4th) quadrant[ei]?\b"
)
_QUADRANT_OF = {
    **{str(n): f"Q{n}" for n in range(1, 5)},
    **dict.fromkeys(("one", "uno", "i", "first", "primo", "1st"), "Q1"),
    **dict.fromkeys(("two", "due", "ii", "second", "secondo", "2nd"), "Q2"),
    **dict.fromkeys(("three", "tre", "iii", "third", "terzo", "3rd"), "Q3"),
    **dict.fromkeys(("four", "quattro", "iv", "fourth", "quarto", "4th"), "Q4"),
}
_QUADRANT_PATTERNS = (
    _QUADRANT_RE,
    _QUADRANT_ROMAN_RE,
    _QUADRANT_NUMBER_RE,
    _QUADRANT_WORD_RE,
    _QUADRANT_ORDINAL_RE,
)


def quadrant_labels(text: str) -> list[str]:
    """Quadrants named in the text ("Q4", "QIV", "Q-one", "quadrant 4", "fourth quadrant"), in
    order."""
    folded = fold(text, " ")
    found: list[str] = []
    matches = (m for p in _QUADRANT_PATTERNS for m in p.finditer(folded))
    return _add(found, (_QUADRANT_OF[m.group(1)] for m in sorted(matches, key=lambda m: m.start())))


# ---- Risk and value levels, places in the matrix (round 2, finding R2; round 3, T7 and T8) ----

QUADRANT_AXES: dict[str, frozenset[str]] = {
    "Q1": frozenset({"risk:low", "value:high"}),
    "Q2": frozenset({"risk:low", "value:low"}),
    "Q3": frozenset({"risk:high", "value:high"}),
    "Q4": frozenset({"risk:high", "value:low"}),
}
_AXES = {"risk": "risk", "rischio": "risk", "value": "value", "valore": "value"}
_LEVEL_HIGH = word_set(
    "high higher highest elevated significant considerable substantial serious severe big alto "
    "alta alti alte elevato elevata elevati elevate maggiore notevole"
)
_LEVEL_LOW = word_set(
    "low lower lowest negligible minimal modest small little minor basso bassa bassi basse "
    "scarso scarsa scarsi scarse ridotto ridotta minore trascurabile"
)
_ABOVE = word_set(
    "above over exceeds exceed exceeding exceeded beyond sopra oltre supera superiore"
)
_BELOW = word_set("below under beneath sotto inferiore")
# A threshold, or a typical level, compared with ("above the threshold", "below average").
_THRESHOLDS = word_set(
    "threshold thresholds cutoff average median normal typical soglia soglie media"
)
_RISKY = word_set("risky riskier riskiest rischioso rischiosa rischiosi rischiose")
_SCORES = word_set("score scores punteggio punteggi")
# "The value of the order", "a high-value item": the value of something else than the customer.
_ORDER_WORDS = word_set(
    "order orders item items product products basket cart purchase purchases ordine ordini "
    "articolo articoli prodotto prodotti capo capi carrello acquisto acquisti"
)
_DAMAGE_WORDS = word_set("damage damages error errors mistake mistakes danno danni errore errori")
_LEVEL_CLAUSE_RE = re.compile(r"[.!?;\n]+|\b(?:and|but|while|whereas|e|ma|mentre)\b")


def _level(tokens: Sequence[str]) -> str | None:
    """The level a clause names: high, low, "?" (a threshold without a side, or both levels) or
    None (no level)."""
    threshold = any(t in _THRESHOLDS for t in tokens)
    high = any(t in _LEVEL_HIGH for t in tokens) or (threshold and any(t in _ABOVE for t in tokens))
    low = any(t in _LEVEL_LOW for t in tokens) or (threshold and any(t in _BELOW for t in tokens))
    if high != low:
        return "high" if high else "low"
    return "?" if high or threshold else None


def quadrant_levels(text: str) -> list[tuple[str, str, str]]:
    """Risk and value levels named in the text, as (kind, axis, level): kind "score" when the
    level is that of the risk score ("the risk score is high"), "axis" for a level of the
    quadrant axes ("risk level is low", "high-risk", "il rischio è molto basso", "below the
    threshold", "risky"). The value of an order or item, or of the score, and the risk of a
    damage or an error are not quadrant levels. Clauses are split at sentence ends and at
    "and", "but" ("e", "ma") before the accents are folded, so "è" never splits a clause."""
    found: list[tuple[str, str, str]] = []
    for clause in _LEVEL_CLAUSE_RE.split(unicodedata.normalize("NFKC", text).casefold()):
        tokens = _letters_only(fold(clause, " ")).split()
        if any(t in _RISKY for t in tokens):
            _add(found, [("axis", "risk", "high")])
        level = _level(tokens)
        if level is None:
            continue
        for index, token in enumerate(tokens):
            axis = _AXES.get(token)
            following = tokens[index + 1 : index + 3]
            if axis is None:
                continue
            if axis == "risk":
                score = following[:1] and following[0] in _SCORES
                score = score or (tokens[index - 2 : index] == ["punteggio", "di"])
                damage = (
                    following[:1] in (["of"], ["di"])
                    and following[1:]
                    and (following[1] in _DAMAGE_WORDS)
                )
                if damage:
                    continue
                _add(found, [("score" if score else "axis", axis, level)])
            elif not any(t in _SCORES or t in _ORDER_WORDS for t in tokens):
                _add(found, [("axis", axis, level)])
    return found


_POSITION_RE = re.compile(
    r"\b(?:top|bottom|upper|lower) (?:left|right)\b"
    r"|\b(?:in )?(?:alto|basso) a (?:destra|sinistra)\b"
)


def quadrant_positions(text: str) -> list[str]:
    """Places in the matrix named in the text ("top left", "in alto a destra")."""
    return [m.group() for m in _POSITION_RE.finditer(_letters_only(fold(text, " ")))]


def strip_tokens(text: str, tokens: Iterable[str]) -> str:
    """Remove whole-token occurrences of known identifiers (case-insensitive) from the text."""
    out = unicodedata.normalize("NFKC", text)
    for token in sorted({t for t in tokens if t}, key=len, reverse=True):
        out = re.sub(rf"(?<![\w-]){re.escape(token)}(?![\w-])", " ", out, flags=re.IGNORECASE)
    return out


def word_count(text: str) -> int:
    return len(text.split())
