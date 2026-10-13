"""Lexical checks on model text (docs/spec.md 8.7; review findings LLM-1, LLM-5, LLM-6, LLM-10 and
the adversarial reviews of 13 Oct, rounds 1 and 2).

Standard library only. Word checks run on folded forms of the text, so look-alike letters,
invisible characters, accents, dashes and letter spacing do not hide a word:

- `fold`: NFKC, casefold, no combining marks, common Cyrillic and Greek look-alikes mapped to
  Latin letters; format characters (soft hyphen, zero-width space) are removed, or replaced.
- `forms`: letter-only forms of the folded text. Format characters are removed in one form and
  read as spaces in another; inner joiners (any dash, dot, apostrophe) are read as word breaks
  in one form and removed in another; runs of three or more single letters ("f r o d e") are
  joined. A word is found if it appears in any form.
- `words`: the first form, as a list of lowercase letter runs.

These are blocklists: they reduce risk, they do not prove a text is safe. The validator applies
them to the text Gemma still writes (operator statements, agent answers); the tests apply them to
every sentence of the customer phrasebook (llm/phrasebook.py). One check is an allowlist instead:
`foreign_letters` accepts only the letters a-z and the Italian accented vowels, so look-alike
letters of other scripts never reach the blocklists (round 2, finding R5).
"""

from __future__ import annotations

import functools
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


def _add(found: list[str], items: Iterable[str]) -> list[str]:
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
    "|person[ae]|individu[oi]|soggett[oi]|client[ei]|utent[ei]|acquirent[ei]|titolar[ei]"
    "|intestatari[oae]?|proprietari[oae]?|entita|identita|consumator[ei]|consumatric[ei]"
    "|comprator[ei]|compratric[ei]|utilizzator[ei]|utilizzatric[ei]|esser[ei] uman[oi]"
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
)
# "un cliente", "una persona": an article as often as a numeral, so only in a sentence that also
# talks about accounts ("Un cliente controlla tutti gli account collegati").
_ONE_PERSON_RE = re.compile(rf"\bun[ao]? (?:{_PERSON_NOUNS})\b")
_ACCOUNTS_RE = re.compile(r"\b(?:accounts?|profil[oi]|collegat[oaie]|linked)\b")
_SENTENCE_RE = re.compile(r"[.!?;:\n]+")
# Ownership of all the accounts is the same claim ("the customer owns all the linked accounts",
# "gli account collegati sono tutti tuoi"): an ownership word in a sentence about all accounts.
_TOTALITY_RE = re.compile(
    r"\b(?:all|every|each|both|whole|tutti|tutte|ogni|entrambi|entrambe|ciascun[oa]?)\b"
)
_OWNERSHIP_RE = re.compile(
    r"(?<!its )(?<!their )(?<!your )(?<!his )(?<!her )(?<!our )(?<!my )\bown(?:s|ed|ing)?\b"
    r"|\bcontrol(?:s|led|ling)?\b|\bbehind\b|\bbelong(?:s|ed|ing)? to (?:you|him|her|me|us)\b"
    r"|\b(?:run|operated|managed|held) by\b|\b(?:yours|theirs|hers|mine)\b"
    r"|\b(?:tuoi|tue|suoi|sue|vostri|vostre|miei|mie)\b|\b(?:tutti|tutte) di\b"
    r"|\b(?:appartien[ei]|appartengono|controll[ao]\w*|gestit[oaie]|gestisc\w+|possedut[oaie]"
    r"|possied\w+|intestat[oaie])\b"
)


def _same_person(text: str) -> list[str]:
    found: list[str] = []
    for form in forms(text, leet=True):
        _add(found, (m.group() for p in _SAME_PERSON_RES for m in p.finditer(form)))
    for sentence in _SENTENCE_RE.split(fold(text, " ").translate(_LEET)):
        form = _letters_only(sentence)
        if _ACCOUNTS_RE.search(form):
            _add(found, (m.group() for m in _ONE_PERSON_RE.finditer(form)))
            if _TOTALITY_RE.search(form):
                _add(found, (m.group() for m in _OWNERSHIP_RE.finditer(form)))
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


# ---- Script allowlist (round 2, finding R5) ---------------------------------------------------

ITALIAN_VOWELS = "àáèéìíòóùúÀÁÈÉÌÍÒÓÙÚ"


def foreign_letters(text: str) -> list[str]:
    """Letters outside a-z and the Italian accented vowels, after NFKC (which already folds
    full-width, mathematical and ligature forms), and combining marks that NFKC could not
    compose into such a letter. Model text with any of them is regenerated, so a look-alike
    letter missing from `_CONFUSABLES` cannot hide a word."""
    found: list[str] = []
    for ch in unicodedata.normalize("NFKC", text):
        foreign = (ch.isalpha() and not (ch.isascii() or ch in ITALIAN_VOWELS)) or (
            unicodedata.category(ch).startswith("M")
        )
        if foreign and ch not in found:
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
# Words that share a stem: denim, scontrino (receipt), scontento (unhappy).
TERMS_ALLOWED: tuple[str, ...] = ("denim", "scontr", "scontent")
_TERMS_ALLOWED_PHRASES = re.compile(r"\bfeel free\b")


def decision_wording(text: str) -> list[str]:
    """Words that state a decision (approve, reject, block, investigate...)."""
    found: list[str] = []
    for form in forms(text, leet=True):
        _add(found, _stem_hits(form, DECISION_STEMS, allowed=TERMS_ALLOWED))
        _add(found, (m.group() for m in _DECISION_PHRASES.finditer(form)))
    return found


def terms_wording(text: str) -> list[str]:
    """Words that state return terms (free, instant, shipping, coupons, window...) or a decision.
    The customer message is connective text only: terms come from {terms}."""
    found: list[str] = []
    for form in forms(text, leet=True):
        form = _TERMS_ALLOWED_PHRASES.sub(" ", form)
        _add(found, _stem_hits(form, TERMS_STEMS, allowed=TERMS_ALLOWED, whole=TERMS_WORDS))
        _add(found, (m.group() for p in _TERMS_PHRASES for m in p.finditer(form)))
    return _add(found, decision_wording(text))


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
# "sei" alone is also "you are"; "sei giorni" is still caught by the unit check.
_IT_NOT_NUMBERS = frozenset({"sei"})

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
_EN_OTHER = word_set(
    "zero dozen dozens fortnight fortnights fortnightly twice thrice half halves quarter "
    "quarters double triple tenner tenners fiver fivers sennight sennights"
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
_NOT_GLUED = word_set("tend tremo")


def _cardinal(word: str) -> bool:
    return word in _EN_CARDINALS or _IT_NUMBER_RE.fullmatch(word) is not None


@functools.lru_cache(maxsize=4096)
def _plain_number(word: str) -> bool:
    """A cardinal, ordinal, fraction or multiple of either language, written as one word."""
    if _cardinal(word) or word in _EN_ORDINALS or word in _EN_OTHER:
        return True
    if word in _IT_ORDINALS or word in _IT_OTHER or word.startswith(_IT_STEMS):
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
    "quattordicigiorni", "tenhrs"), or a long cardinal at the start of a longer word
    ("twentyish")."""
    if word in _NOT_GLUED:
        return False
    for end in range(len(word) - 1, 2, -1):
        head, rest = word[:end], word[end:]
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
    or multiple, or a number glued to a unit. Italian numbers are checked in both languages,
    except "due", which is an English word ("due by")."""
    if word in _IT_NOT_NUMBERS or (word == "due" and lang == "en"):
        return False
    return _plain_number(word) or _glued_number(word)


def numeric_characters(text: str) -> list[str]:
    """Characters with a numeric value (digits of any script, fractions, Roman numerals), in the
    raw text and in its NFKC form. Roman numerals written with ASCII letters are not detected."""
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


def numbers_in_text(text: str) -> list[Decimal]:
    """Every number written with ASCII digits in the folded text (NFKC, no format characters),
    negative when a dash or minus sign comes before it, spaces aside."""
    folded = fold(text)
    found: list[Decimal] = []
    for match in _NUMBER_RE.finditer(folded):
        value = parse_number(match.group())
        before = folded[: match.start()].rstrip()[-1:]
        negative = before != "" and (before in _MINUS_SIGNS or is_dash(before))
        found.append(-value if negative else value)
    return found


# A number with a letter or a percent sign right after it ("42.1k", "0.87%"), a scale or unit
# word after it ("42.1 M", "42 mln", "42.1 grand", "42.1 kEUR", "42.1 percent"), a sign around
# it ("(42.1)", "±42.1", "42.1-"), or a sign word in a text with a number ("minus 42.1", "a loss
# of 42.1", "42.1 EUR owed"): its value is not the value written, so it cannot be grounded.
_SUFFIXED_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*(?:[^\W\d_]+|[%‰‱×])")
_SCALED_RE = re.compile(
    r"[0-9]\s*(?:k|m|b|mln|mld|mrd|mn|bn|mio|mil|mill|grand|[kmb]eur|percent|per cent|percento"
    r"|per cento|bps?|pp|basis points?|punti base|punti percentuali)\b"
)
_SIGNED_RE = re.compile(
    r"\(\s*[0-9]+(?:[.,][0-9]+)*\s*\)|[±∓]\s*[0-9]|[0-9][-−➖˗‐‑‒–—―⁻](?![0-9a-z])"
)
_SIGN_WORDS_RE = re.compile(
    r"\b(?:minus|meno|negativ[eoai]|loss|losses|lossmaking|perdit[ae]|deficit|disavanz[oi]"
    r"|in the red|in rosso|owed|owes|owing|debit(?! cards?\b)|debits|debts?|debit[oi]"
    r"|shortfall|lost|unprofitable)\b"
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


# ---- Quadrant labels (adversarial review of 13 Oct, finding 3) --------------------------------

_QUADRANT_RE = re.compile(r"(?<![a-z0-9])q[\s_‐‑‒–—-]*([1-4])(?![0-9])")
_QUADRANT_ROMAN_RE = re.compile(r"(?<![a-z0-9])q[\s_‐‑‒–—-]?(iv|iii|ii|i)(?![a-z0-9])")
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


def quadrant_labels(text: str) -> list[str]:
    """Quadrants named in the text ("Q4", "QIV", "quadrant 4", "fourth quadrant"), in order."""
    folded = fold(text, " ")
    found: list[str] = []
    patterns = (_QUADRANT_RE, _QUADRANT_ROMAN_RE, _QUADRANT_WORD_RE, _QUADRANT_ORDINAL_RE)
    matches = sorted((m for p in patterns for m in p.finditer(folded)), key=lambda m: m.start())
    return _add(found, (_QUADRANT_OF[m.group(1)] for m in matches))


# Quadrants described by their axes ("the high-risk, low-value quadrant") or by their place in the
# matrix ("top left"); restrictions named in words (round 2, finding R2).
_HIGH = "high|higher|highest|elevated|alt[oaie]|elevat[oaie]"
_LOW = "low|lower|lowest|bass[oaie]|scars[oaie]"
_AXIS = {"risk": "risk", "rischio": "risk", "value": "value", "valore": "value"}
_AXIS_RE = re.compile(
    rf"\b(?:(?P<level>{_HIGH}|{_LOW}) (?P<axis>risk|value|rischio|valore)"
    rf"|(?P<axis2>risk|value|rischio|valore) (?:is |e )?(?P<level2>{_HIGH}|{_LOW}))\b"
)
QUADRANT_AXES: dict[str, frozenset[str]] = {
    "Q1": frozenset({"risk:low", "value:high"}),
    "Q2": frozenset({"risk:low", "value:low"}),
    "Q3": frozenset({"risk:high", "value:high"}),
    "Q4": frozenset({"risk:high", "value:low"}),
}
_POSITION_RE = re.compile(
    r"\b(?:top|bottom|upper|lower) (?:left|right)\b"
    r"|\b(?:in )?(?:alto|basso) a (?:destra|sinistra)\b"
)
RESTRICTION_STEMS: tuple[str, ...] = ("restrict", "restrizion", "restritt")
_NEGATION_WORDS = word_set("no not t non without senza nessun nessuna nessuno never neither nor")
_CLAUSE_RE = re.compile(r"[.!?;:,\n]+")


def quadrant_axes(text: str) -> list[str]:
    """Risk and value levels named in the text, as "risk:high", "value:low"..."""
    found: list[str] = []
    for match in _AXIS_RE.finditer(_letters_only(fold(text, " "))):
        axis = _AXIS[match["axis"] or match["axis2"]]
        level = match["level"] or match["level2"]
        _add(found, [f"{axis}:{'high' if re.fullmatch(_HIGH, level) else 'low'}"])
    return found


def quadrant_positions(text: str) -> list[str]:
    """Places in the matrix named in the text ("top left", "in alto a destra")."""
    return [m.group() for m in _POSITION_RE.finditer(_letters_only(fold(text, " ")))]


def restriction_words(text: str) -> list[str]:
    """Words for a restriction ("restrictive", "restricted", "restrizioni") in a clause without a
    negation ("no restrictive levers", "without a score or restrictive levers" and
    "restrictions do not apply" are not claims of a restriction)."""
    found: list[str] = []
    for clause in _CLAUSE_RE.split(fold(text, " ")):
        tokens = _letters_only(clause).split()
        if not any(t in _NEGATION_WORDS for t in tokens):
            _add(found, (t for t in tokens if t.startswith(RESTRICTION_STEMS)))
    return found


def strip_tokens(text: str, tokens: Iterable[str]) -> str:
    """Remove whole-token occurrences of known identifiers (case-insensitive) from the text."""
    out = unicodedata.normalize("NFKC", text)
    for token in sorted({t for t in tokens if t}, key=len, reverse=True):
        out = re.sub(rf"(?<![\w-]){re.escape(token)}(?![\w-])", " ", out, flags=re.IGNORECASE)
    return out


def word_count(text: str) -> int:
    return len(text.split())
