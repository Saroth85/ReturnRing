"""Lexical checks on model text (docs/spec.md 8.7; review findings LLM-1, LLM-5, LLM-6, LLM-10 and
the adversarial reviews of 13 Oct, rounds 1 to 5).

Standard library only. Word checks run on folded forms of the text, so look-alike letters,
invisible characters, accents, dashes and letter spacing do not hide a word:

- `fold`: NFKC, casefold, no combining marks, common Cyrillic and Greek look-alikes mapped to
  Latin letters; format characters (soft hyphen, zero-width space) are removed, or replaced.
- `forms`: letter-only forms of the folded text. Format characters are removed in one form and
  read as spaces in another; inner joiners (any dash, dot, apostrophe) are read as word breaks
  in one form and removed in another (any ASCII punctuation but , ; : ! ? joins too: "fr|aud");
  runs of three or more single letters ("f r o d e") are joined; with `leet`, digits and
  ASCII-art letters ("pers()n", "sc^m") are read as letters in more forms. A word is found if
  it appears in any form.
- `words`: the first form, as a list of lowercase letter runs.

Most checks are blocklists: they reduce risk, they do not prove a text is safe. The validator
applies them to the text Gemma still writes (operator statements, agent answers); the tests apply
them to every sentence of the customer phrasebook (llm/phrasebook.py). On the operator's free
text they are tuned for precision (round 5, ADR-14 decision 11): the operator also sees the
engine decision, so rejecting correct output is worse than missing a contrived paraphrase, and
every check must let the must-accept corpus of tests/test_llm_corpus.py pass. Three checks are
allowlists instead (round 2, finding R5; round 3, findings T7, T9 and T11):

- `foreign_characters`: on the raw text, before NFKC, only printable ASCII, the Italian accented
  vowels and a short typographic list are allowed, so no symbol or letter of another script can
  stand in for a letter of a banned word;
- `operator_topics`: an operator statement does not talk about levers, terms, refunds,
  restrictions, decisions or the risk and value levels of the quadrant at all (the interface
  shows the engine decision), so no lever value has to be read from the words;
- `grounding_problems`: a number of an operator statement is grounded only with the unit of its
  fact (or none) after it, a plain word or separator before it, and no unit, currency, rate or
  approximation of another kind anywhere in the statement.
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
# Digits and symbols used as letters, for banned-term matching only; ASCII-art letters first
# ("pers()n", "fr/\\ud", "sc^m", "gui|ty": round 4, finding U11).
_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
_LEET_SEQUENCES = (("()", "o"), ("/\\", "a"), ("^", "a"), ("|", "l"))

_SPACED_LETTERS_RE = re.compile(r"\b[a-z](?: [a-z]\b){2,}")
_NON_LETTERS_RE = re.compile(r"[^a-z]+")
_INNER_MARKS_RE = re.compile(r"(?<=[a-z])[^a-z0-9\s]+(?=[a-z])")
# Joiners: dots, apostrophes, asterisks and the like, every dash (Unicode category Pd), and any
# ASCII punctuation but , ; : ! ? ("fr|aud", "fr/aud", "fr{a}ud": round 4, finding U11).
_JOINERS = "_.·*'’"
_ASCII_JOINERS = frozenset(chr(c) for c in range(0x21, 0x7F) if not chr(c).isalnum()) - set(",;:!?")


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
    """Remove runs of joiners (dashes, dots, apostrophes, ASCII punctuation but , ; : ! ?)
    between two letters ("quattor-dici", "fr|aud")."""

    def join(match: re.Match[str]) -> str:
        marks = match.group()
        joined = all(ch in _JOINERS or ch in _ASCII_JOINERS or is_dash(ch) for ch in marks)
        return "" if joined else marks

    return _INNER_MARKS_RE.sub(join, folded)


def _leet(folded: str) -> str:
    for sequence, letter in _LEET_SEQUENCES:
        folded = folded.replace(sequence, letter)
    return folded.translate(_LEET)


def _letters_only(folded: str) -> str:
    spaced = _NON_LETTERS_RE.sub(" ", folded).strip()
    return _SPACED_LETTERS_RE.sub(lambda m: m.group().replace(" ", ""), spaced)


def forms(text: str, *, leet: bool = False) -> list[str]:
    """Letter-only forms of the text, without repeats: format characters removed or read as
    spaces, inner joiners read as word breaks or removed; with `leet`, also the forms with
    digits and ASCII-art letters read as letters ("fr|aud" is found joined, "gui|ty" as leet)."""
    found: list[str] = []
    for format_chars in ("", " "):
        folded = fold(text, format_chars)
        for base in (folded, _leet(folded)) if leet else (folded,):
            for variant in (base, _join_inner(base)):
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
    # Returns-abuse verdicts (round 4, finding U8): "wardrobing" and "serial returner" are the
    # industry words for the behaviour of a Q4 cluster case, so a model is likely to use them.
    "wardrobing",
    "wardrober",
    "serialreturn",
    "fake",
    "bogus",
    "exploit",
    "misus",
    "malicio",
    "decept",
    "deceiv",
    "untrustworth",
    "badactor",
    "illicit",
    "illecit",
    "furbett",
    "approfitt",
    "impostor",
    "imposter",
    "offender",
    "culprit",
    "perpetrat",
    "crimin",
    "illegal",
    "illegit",
    "swindl",
    "grifter",
    "sockpuppet",
    "alterego",
    # Round 5, finding V15: verdicts on the accounts ("predatory", "throwaway", "burner accounts").
    "predator",
    "throwaway",
    "burner",
)
# Verdicts as whole words ("sham", not "shampoo"; "crook", not "a crooked seam") and phrases
# (round 4, findings U7 and U8; round 5, findings V4, V10 and V15): duplicate accounts, clones,
# multi-accounting, gaming anything.
BANNED_WORDS = frozenset({"sham", "shams", "crook", "crooks"})
_BANNED_PHRASES = re.compile(
    r"\bserial returns?(?:er|ers|ing)?\b|\bbad actors?\b|\bsock puppets?\b|\balter egos?\b"
    r"|\bgam(?:e|es|ed|ing) the (?:system|rules|policy|returns?)\b|\bgam(?:es|ed|ing) the [a-z]+"
    r"|\bduplicat\w* (?:accounts?|profil\w*|identit\w*|users?|customers?)\b"
    r"|\baccounts?\b(?: [a-z]+){0,3} duplicates?\b|\bmulti ?accounting\b|\bclon(?:e|es|ed|ing)\b"
)
# Farming and harvesting, in a sentence about accounts or coupons ("account farming", "created to
# farm welcome coupons"): round 5, finding V15.
_FARMING_RE = re.compile(r"\b(?:farm|farms|farmed|farming|farmer|farmers|harvest\w*)\b")
_FARMED_RE = re.compile(r"\b(?:accounts?|coupons?|codes?|vouchers?|profil\w*|clusters?|offers?)\b")
# Innocent fashion and retail words that share a stem: scambio (exchange), scamosciato (suede),
# scampolo (remnant), scamiciato (pinafore dress), truffle (a colour); "wardrobe" is not a stem
# above, and "fake fur" (or leather, suede, pearls) is a material.
ALLOWED_PREFIXES: tuple[str, ...] = ("scambi", "scamosci", "scamp", "scamiciat", "truffle")
_ALLOWED_BANNED_PHRASES = re.compile(r"\bfake (?:furs?|leather|suede|pearls?)\b")
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
    # "the person is the same", "the account holder is the same for both" (round 5, V10)
    re.compile(
        rf"\b(?:{_PERSON_NOUNS})\b(?: (?:here|there|also|too|involved|again|behind|of|across|in|on"
        r"|for|both|all|these|those|the|linked|accounts?|account|them|it|dietro|degli|dei|di))"
        r"{0,3} (?:is|are|was|were|seems?|appears?|looks?|remains?|stays?|e|sono|era|erano|sembra"
        r"|sembrano|resta|rimane) (?:(?:always|still|clearly|probably|likely|really|just|all"
        r"|sempre|chiaramente|probabilmente|forse) )*(?:(?:the|lo|la|il|l) )?(?:same|identical"
        r"|selfsame|stess[aeoi]|medesim[aeoi]|identic[aoi])\b"
    ),
    re.compile(
        rf"\bcondivid\w* (?:(?:un|una|uno|il|lo|la|lo stesso|la stessa) )?(?:{_PERSON_NOUNS})\b"
    ),
)
# "un cliente", "una persona": an article as often as a numeral, so only in a sentence that also
# talks about accounts ("Un cliente controlla tutti gli account collegati").
_ONE_PERSON_RE = re.compile(rf"\bun[ao]? (?:{_PERSON_NOUNS})\b")
_ACCOUNTS_RE = re.compile(r"\b(?:accounts?|profil[oi]|collegat[oaie]|linked)\b")
_SENTENCE_RE = re.compile(r"[.!?;:\n]+")
# Ownership and identity in a statement about the linked accounts (round 2, finding R3; round 3,
# finding T6; round 4, finding U7; round 5, findings V2, V3, V10 and V18). The checks run on each
# sentence that talks about the accounts (split only at . ! ? and line breaks: a dot between two
# letters joins a word), clause by clause (split at , ; :), on every letter form, so inner joiners
# ("con-trols", "man.ages") hide nothing. Precision first (ADR-14 decision 11):
# - a control verb whose object is the accounts, whatever the subject ("they / the family / Marco
#   controls, runs, manages, operates, owns the linked accounts", "gestisce", "controlla"), and
#   its passive with any agent but an identifier ("run by them", "gestiti da Marco"; "controlled
#   by a shared device" passes);
# - a person noun as the subject of a verb of possession or use from a short list, followed
#   directly by the accounts (determiners and numbers aside): "the customer holds / has / uses /
#   logs into / set up the linked accounts", "il cliente usa gli account"; checked on the forms
#   without digits read as letters, so "7 linked accounts" never reads as a verb "t". A negation
#   ("has no linked accounts", "non ha account collegati") or any other word between the verb and
#   the accounts ("the customer's cluster has 7 linked accounts", "is in a cluster of",
#   "should review the linked accounts") is not a claim;
# - a person or group noun, or a pronoun, as the agent of a passive about the accounts
#   ("registered to the customer", "opened by the household"), their predicate ("the linked
#   accounts are the customer's"), a role of them ("the owner of the linked accounts"), a
#   possessive before them ("the customer's other linked accounts", "his linked accounts", "i suoi
#   account"), "in the customer's name", "belongs to" anything but a cluster or group (named in at
#   most three words: "the Rossi family cluster", round 6, finding W6), "traces back to", "behind
#   the accounts", aliases, alter egos and sock puppets.
# Intransitive uses pass ("the linked accounts operate from a shared address", "orders from the
# linked accounts ran in rapid succession"), and so do "its own", "their own", "differ in name" and
# Italian possessives of a cluster ("il cluster R-MARCO ... i suoi legami").
_OWNERSHIP_SENTENCE_RE = re.compile(r"[!?\n]+|(?<![^\W\d_])\.+|\.+(?![^\W\d_])")
_CLAUSE_RE = re.compile(r"[,;:]+")
_LINKED_RE = re.compile(
    r"\b(?:accounts|linked|collegat[oaie]|profili|clusters?"
    r"|(?:gli|degli|questi|tutti gli) account)\b"
)
_OWNERS = (
    "customers?|clients?|users?(?! agents?\\b)|persons?|people|individuals?|buyers?|shoppers?"
    "|consumers?|purchasers?|owners?|holders?|proprietors?|someone|somebody|whoever|he|she"
    "|client[ei]|utent[ei]|person[ae]|titolar[ei]|intestatari[oae]?|proprietari[oae]?"
    "|acquirent[ei]|individu[oi]|soggett[oi]|qualcuno|chi"
)
# Agents of a passive: the owners, and groups and pronouns ("run by them", "opened by the family").
_AGENTS = f"{_OWNERS}|they|them|famil(?:y|ies)|households?|part(?:y|ies)|entit(?:y|ies)|loro"
_AGENTS += "|famigli[ae]|nucle[oi]"
_ROLES = (
    "owners?|holders?|proprietors?|controllers?|creators?|operators?|managers?|titolar[ei]"
    "|proprietari[oae]?|intestatari[oae]?|gestor[ei]|creator[ei]"
)
_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|due|tre|quattro|cinque|sei"
    "|sette|otto|nove|dieci"
)
_DETERMINERS = (
    "the|all|these|those|both|of|its|his|her|their|other|several|multiple|many|every|each|a|an"
    "|any|some|same|gli|degli|i|tutti|tutte|questi|quegli|altri|dei|agli|negli|sugli|ai|un|uno|una"
    f"|piu|diversi|vari|molti|{_NUMBER_WORDS}"
)
_ACCOUNT_ADJECTIVES = (
    "linked|other|connected|related|additional|extra|secondary|multiple|several|different"
    "|separate|collegati|altri"
)
_THE_ACCOUNTS = (
    rf"(?:(?:{_DETERMINERS}) )*(?:(?:{_ACCOUNT_ADJECTIVES}) )?(?:accounts?|profil[oi])\b"
)
_CONTROL_VERBS = (
    r"controls?|controlled|controlling|runs?|ran|running|manages?|managed|managing|operates?"
    r"|operated|operating|owns?|owned|owning|administers?|administered|gestisc\w*|gestiv\w*"
    r"|gestit\w*|controll\w*|possied\w*|possed\w*|amministr\w*"
)
_CONTROL_PARTICIPLES = (
    r"controlled|managed|run|operated|owned|administered|gestit[oaie]|controllat[oaie]"
    r"|possedut[oaie]|amministrat[oaie]"
)
# What may control the accounts without a claim about a person: an identifier or the merchant.
_NOT_AGENTS = (
    r"devices?|address\w*|cards?|links?|identifiers?|ids?|ip|phones?|emails?|e mails?|cookies?"
    r"|fingerprints?|dispositiv\w*|indirizz\w*|cart[ae]|legam\w*|collegament\w*|identificativ\w*"
    r"|telefon\w*|merchant|store|shop|team|warehouse|system|model|engine|negozio|magazzino"
)
_ARTICLES = (
    r"the|a|an|one|same|shared|single|common|il|lo|la|l|un|una|uno|stesso|stessa|condivis\w*"
)
_GROUPS = "clusters?|groups?|rings?|networks?|components?|sets?|graphs?|communit(?:y|ies)"
_PASSIVE_BY = r"by|da|dal|dalla|dallo|dai|dagli|dall"
# Verbs of possession or use with a person as subject (a short list: "review", "resemble" or
# "belong to a cluster" are not claims).
_OWNER_VERBS = (
    r"has|have|had|holds?|held|holding|uses?|used|using|utili[sz]e[sd]?|utili[sz]ing|creates?"
    r"|created|creating|opens?|opened|opening|registers?|registered|registering|maintains?"
    r"|maintained|keeps?|kept|handles?|handled|possess(?:es|ed)?|accesses|accessed|accessing"
    r"|access|controls?|runs?|ran|manages?|managed|operates?|operated|owns?|owned|juggles?"
    r"|juggled|employs?|employed|makes?|made|builds?|built|switches between|switched between"
    r"|rotates between|rotated between|logs? (?:into|in to|onto|on to)|logged (?:into|in to|onto"
    r"|on to)|sets? up|setting up|signs? up|signed up|usa|usano|utilizza|utilizzano|ha|hanno"
    r"|possiede|possiedono|detiene|detengono|crea|creano|apre|aprono|registra|registrano"
    r"|mantiene|mantengono|accede a|accedono a|creato|aperto|registrato|usato|utilizzato"
)
_AUXILIARIES = (
    r"has|have|had|ha|hanno|aveva|avevano|will|would|can|could|may|might|must|does|did|do|also"
    r"|still|probably|likely|clearly|apparently|actually|just|really|evidently|then|e|sono|era"
    r"|is|are|was|were"
)
_OWNERSHIP_RE = re.compile(
    # a control verb with the accounts as object, whatever the subject (V2)
    rf"\b(?:{_CONTROL_VERBS}) (?:(?:{_THE_ACCOUNTS})|them\b)"
    # its passive, with any agent but an identifier or the merchant (V2)
    rf"|\b(?:accounts?|profil[oi]|them)\b(?: [a-z]+){{0,4}} (?:{_CONTROL_PARTICIPLES})"
    rf" (?:{_PASSIVE_BY}) (?!(?:(?:{_ARTICLES}) )*(?:{_NOT_AGENTS})\b)[a-z]+"
    r"|(?<!its )(?<!their )(?<!your )(?<!his )(?<!her )(?<!our )(?<!my )\bown(?:s|ed|ing)?\b"
    # the agent of a passive about the accounts
    r"|\b(?:accounts?|profil[oi])\b(?: [a-z]+){0,6} (?:[a-z]+(?:ed|en|able|ible|at[oaie]|it[oaie]"
    r"|ut[oaie]|ert[oaie]|ess[oaie]|os[oaie])|held|made|built|kept|set up|signed up) (?:by|to|da"
    r"|dal|dalla|dallo|dai|dagli|al|alla|allo|ai|agli) (?:(?:the|this|that|a|an|one|same|il|lo|la"
    rf"|l|un|una|uno|stesso|stessa) )?(?:{_AGENTS})\b(?! s\b)"
    # predicate of the accounts
    rf"|\b(?:accounts?|profil[oi])\b(?: [a-z]+){{0,3}} (?:are|is|were|was|seem|appear|sono|e"
    r"|erano|era|sembrano) (?:(?:all|both|each|just|simply|really|clearly|probably|likely|in fact"
    rf"|actually|basically|tutti|tutte) )*(?:(?:the|a|an|one|del|della|dello|dell|di|il|la|lo) )?"
    rf"(?:{_OWNERS})\b"
    # a role of the accounts, a possessive before them, in the name of
    rf"|\b(?:{_ROLES}) (?:of|di|degli|dei|del|della|delle) (?:{_THE_ACCOUNTS})"
    rf"|\bin charge of (?:{_THE_ACCOUNTS})|\bresponsabil[ei] (?:di|degli|dei) (?:{_THE_ACCOUNTS})"
    rf"|\b(?:{_OWNERS}) s (?:(?:{_ACCOUNT_ADJECTIVES}|{_NUMBER_WORDS}|own) ){{0,2}}(?:accounts"
    rf"|profiles|profili)\b"
    rf"|\b(?:his|her) (?:(?:{_ACCOUNT_ADJECTIVES}|{_NUMBER_WORDS}|own) ){{0,2}}accounts\b"
    rf"|\bin (?:his|her|their|the (?:{_OWNERS})(?: s)?|(?:{_OWNERS}) s) name\b|\bin the name of\b"
    r"|\ba (?:suo|loro) nome\b|\ba nome (?:del|della|di)\b|\bproprieta\b|\bproprietari\w*"
    r"|\bproprietors?\b"
    r"|\bbelong(?:s|ed|ing)? to\b(?! ?$| (?:(?:the|a|an|this|that|these|those|same|one"
    rf"|different|another|other|separate) )*(?:(?!(?:{_FUNCTION_WORDS})\b)[a-z]+ ){{0,3}}"
    rf"(?:{_GROUPS})\b)"
    r"|\b(?:trace[sd]?|tracing|leads?|led|leading) back\b|\btraceable to\b|\btraced to\b"
    rf"|\baccounts? (?:[a-z]+ )?of (?:the|this|that|a|one) (?:{_PERSON_NOUNS})\b"
    rf"|\bbehind (?:(?:{_DETERMINERS}) )*(?:(?:{_ACCOUNT_ADJECTIVES}) )?(?:accounts?|profil\w*"
    rf"|them|(?:{_GROUPS})\b(?! (?:s|score|scores|size|risk|rate|returns?)\b))\b"
    r"|\bdietro (?:(?:a|ad|agli|gli|questi|tutti|i|ai|al|il|lo|la) )*(?:account|profil\w*"
    r"|cluster)\b"
    r"|\b(?:aliases|alias|alts?|puppets?)\b|\bunder (?:[a-z]+ ){0,3}control\b"
    r"|\b(?:yours|theirs|hers|mine)\b"
    # Italian possessives of the accounts, not of a cluster ("i suoi legami"): V18
    r"|\b(?:tuoi|tue|suoi|sue|vostri|vostre|miei|mie|loro)(?= (?:(?:altri|diversi|vari|molti"
    r"|nuovi|vecchi) )?(?:account|profil[oi])\b)"
    r"|\b(?:sono|siano|sembrano|erano)(?: [a-z]+){0,2} (?:tuoi|tue|suoi|sue|vostri|vostre|miei"
    r"|mie)\b|\b(?:tutti|tutte) di\b"
    r"|\b(?:account|profil[oi])\b(?: [a-z]+){0,2} (?:del|dello|della|dell) (?:cliente|utente"
    r"|persona|titolare|consumatore|consumatrice|acquirente|compratore|compratrice"
    r"|proprietari[oa])\b"
    r"|\b(?:appartien\w*|appartengono|apparten\w+)\b(?! (?:(?:a|al|allo|alla|ai|agli|all|lo|la"
    r"|il|stesso|stessa|un|uno|una|altro|altri|diversi|diverso) )*(?:cluster|grupp[oi]|ret[ei]"
    r"|anell[oi]|insiem[ei])\b)"
    r"|\b(?:possedut[oaie]|possied\w+|intestat[oaie]|riconducibil\w+)\b"
    r"|\bfa(?:nno)? capo\b|\b(?:creat|apert)[oaie] da\b"
)
# A person as the subject of a verb of possession or use whose object follows it directly (V3).
_SUBJECT_RE = re.compile(
    rf"\b(?:{_OWNERS}) (?:(?:{_AUXILIARIES}) ){{0,2}}(?:{_OWNER_VERBS}) (?:{_THE_ACCOUNTS})"
)


def _same_person(text: str) -> list[str]:
    found: list[str] = []
    for form in forms(text, leet=True):
        _add(found, (m.group() for p in _SAME_PERSON_RES for m in p.finditer(form)))
    for sentence in _SENTENCE_RE.split(fold(text, " ").translate(_LEET)):
        form = _letters_only(sentence)
        if _ACCOUNTS_RE.search(form):
            _add(found, (m.group() for m in _ONE_PERSON_RE.finditer(form)))
    for sentence in _OWNERSHIP_SENTENCE_RE.split(unicodedata.normalize("NFKC", text)):
        if not any(_LINKED_RE.search(form) for form in forms(sentence, leet=True)):
            continue
        for clause in _CLAUSE_RE.split(sentence):
            for form in forms(clause, leet=True):
                _add(found, (m.group().strip() for m in _OWNERSHIP_RE.finditer(form)))
            for form in forms(clause):
                _add(found, (m.group().strip() for m in _SUBJECT_RE.finditer(form)))
    return found


def banned_terms(text: str) -> list[str]:
    """Banned stems, words and phrases, same-person phrases and ownership of the linked accounts
    found in the text (empty when clean)."""
    found: list[str] = []
    for form in forms(text, leet=True):
        _add(found, (m.group() for m in _BANNED_PHRASES.finditer(form)))
        if _FARMED_RE.search(form):
            _add(found, (m.group() for m in _FARMING_RE.finditer(form)))
        form = _ALLOWED_BANNED_PHRASES.sub(" ", form)
        hits = _stem_hits(
            form,
            BANNED_STEMS,
            allowed=ALLOWED_PREFIXES,
            whole=BANNED_WORDS,
            word_prefixes=_WORD_PREFIXES,
        )
        _add(found, hits)
    return _add(found, _same_person(text))


# ---- Character allowlist (round 2, finding R5; round 3, finding T9) ---------------------------

ITALIAN_VOWELS = "àáèéìíòóùúÀÁÈÉÌÍÒÓÙÚ"
# Typographic characters a model may use: curly quotes, hyphen, non-breaking hyphen, en and em
# dash, ellipsis, euro sign, the no-break, thin and narrow no-break spaces (round 4, U10).
TYPOGRAPHIC_CHARACTERS = "’‘“”‐‑–—…€\u00a0\u2009\u202f"
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


# ---- Topics of the operator statements (round 3, findings T7 and T8; round 4, finding U6) -----

# Restrictions in words; "limited" alone is left out ("limited evidence" is a plain observation),
# and so are "tight" and "tough" ("the dress is too tight"), "withdrawal path" and "right of
# withdrawal" (round 5, finding V4).
RESTRICTION_STEMS: tuple[str, ...] = (
    "restrict",
    "restrizion",
    "restritt",
    "strict",
    "tighter",
    "tighten",
    "tougher",
    "toughen",
    "harsh",
    "limitation",
    "limitazion",
    "limitat",
    "penal",
    "sanction",
    "sanzion",
    "withh",
    "withdr",
)
# The state of a lever and its scope (round 4, finding U6): every lever is about future orders
# and every fact about the past, so "future", "enabled", "paused", "eligible" and "priority" are
# topics, whatever they qualify. Keeps, remains, available, offered, paid, gone and "no longer"
# are topics only next to a lever noun (`_LEVER_STATE_RE`): "the customer kept the S", "no photo
# is available" and "the item no longer fits" are observations (round 5, findings V4 and V17).
LEVER_STATE_STEMS: tuple[str, ...] = (
    "futur",
    "enabl",
    "disabl",
    "paus",
    "eligib",
    "priorit",
    "payout",
    "loyalt",
    "reward",
)
# The terms stems of the customer gate, restrictions, refunds and their paraphrases (repay,
# reimburse, postage, prepaid, payouts, money), promotions and perks, levers and terms, the
# state and scope of a lever.
OPERATOR_TOPIC_STEMS: tuple[str, ...] = (
    TERMS_STEMS
    + RESTRICTION_STEMS
    + LEVER_STATE_STEMS
    + ("refund", "rimbors", "reimburs", "repay", "repaid", "postage", "prepaid", "promo", "perk")
    + ("privileg", "concession", "lever", "condizion", "transparenc", "trasparenz")
)
OPERATOR_TOPIC_WORDS = TERMS_WORDS | word_set(
    "terms termini leva leve policy policies limit limits lose loses charged money payer"
)
# Decisions and verdicts an operator statement never states (round 5, finding V15): monitor,
# scrutiny, "no further action", "can be closed", "warrants a review", "a red flag".
STATEMENT_DECISION_WORDS = word_set(
    "monitor monitors monitored monitoring scrutiny scrutinise scrutinize scrutinised "
    "scrutinized scrutinising scrutinizing"
)
_STATEMENT_DECISION_RE = re.compile(
    r"\bno (?:further )?action\b|\bcan be closed\b|\bwarrant(?:s|ed|ing)? (?:a |an )?(?:[a-z]+ )?"
    r"(?:review|check|look)\b|\bred flags?\b"
)
# Flag and caution are decisions only about a person or the case (round 6, finding W5): "flag
# this customer", "flag it for review", "the case was flagged", "treat this customer with
# caution", "proceed with caution". The model flags a factor ("the model flags
# cluster_welcome_coupons (7) as the main contribution") and a score is read with caution ("read
# the risk score with caution"): those are observations about the score.
_FLAGGED = (
    r"customers?|clients?|users?|buyers?|shoppers?|persons?|people|cases?|returns?|orders?"
    r"|accounts?|clusters?|refunds?"
)
_FLAG_RE = re.compile(
    r"\bflag(?:s|ged|ging)? (?:(?:the|this|that|these|those|a|an|its|their|his|her|all|both|each"
    rf"|every|linked|other|same|whole) )*(?:{_FLAGGED}|it|them|him|her|for)\b"
    rf"|\b(?:{_FLAGGED})\b(?: (?:is|are|was|were|be|been|being|has|have|had|should|must|may"
    r"|might|could|would|will|can|now|already|also|not|then|therefore|thus|so))+ flagged\b"
)
# A subject that makes "flags" a description of the score ("the model flags").
_MODEL_SUBJECT_RE = re.compile(
    r"\b(?:model|models|score|scores|algorithm)(?: (?:also|clearly|strongly|mainly|only))? $"
)
_CAUTION_RE = re.compile(r"\bcaution(?:s|ed|ing|ary)?\b|\bcautious(?:ly)?\b")
_CAUTION_OBJECTS_RE = re.compile(rf"\b(?:{_FLAGGED}|proceed\w*)\b")
# Words of reading the score or the evidence: caution about them is a hedge, not a decision.
_READING_RE = re.compile(
    r"\b(?:scores?|estimates?|models?|read\w*|interpret\w*|figures?|values?|signals?|results?"
    r"|data|evidence|factors?|contributions?|outputs?|predictions?)\b"
)
_STATEMENT_CLAUSE_RE = re.compile(r"[,;:.!?\n]+")


def _flags_and_cautions(text: str) -> list[str]:
    """Flag and caution about a person or the case, clause by clause (round 6, finding W5)."""
    found: list[str] = []
    for clause in _STATEMENT_CLAUSE_RE.split(fold(text, " ")):
        for form in forms(clause, leet=True):
            _add(
                found,
                (
                    m.group()
                    for m in _FLAG_RE.finditer(form)
                    if not _MODEL_SUBJECT_RE.search(form[: m.start()])
                ),
            )
            if _CAUTION_OBJECTS_RE.search(form) and not _READING_RE.search(form):
                _add(found, (m.group() for m in _CAUTION_RE.finditer(form)))
    return found


# Observed phrases that share a stem with a lever word: the welcome coupons of a cluster are
# data, not the coupons lever (round 3, finding T7); a free-text reason, a shipping address,
# orders shipped to an address, coupons and codes used, an order with a coupon, an expensive
# item, a closed return window of past order lines, the withdrawal path (round 5, findings V4
# and V17). The state words are checked first (`_LEVER_STATE_RE`), so "welcome coupons are
# disabled" or "the cluster keeps its welcome coupons" are still topics.
_OPERATOR_ALLOWED_PHRASES = re.compile(
    r"\bfeel free\b|\bwelcome coupons?\b|\bfree ?text\b|\bin terms of\b"
    r"|\bin (?:quick|rapid) succession\b|\bshipping ?address(?:es)?\b"
    r"|\bindirizz[oi] di spedizione\b|\bship(?:ped|s|ping)? to\b|\bshipped\b|\bshipments?\b"
    r"|\bcoupons? (?:use|usage|uses|used|redemptions?|redeemed|history|sequence|pattern)\b"
    r"|\b(?:used|redeemed|redeeming|using|use of|usage of|redemption of)(?: (?:the|a|an|their"
    r"|its|each|one|same|welcome|first))* coupons?\b"
    r"|\b(?:promo\w*|referral|discount|welcome)(?:(?: and | or | )(?:promo\w*|referral|discount"
    r"|welcome))* (?:codes?|coupons?|vouchers?) (?:used|redeemed)\b"
    r"|\borders? (?:with|using) (?:a |an )?(?:coupons?|codes?|vouchers?)\b"
    r"|\b(?:in)?expensive(?:ly)?\b|\bclosed return windows?\b"
    r"|\breturn windows? (?:has |have |had )?(?:closed|ended|expired)\b"
    r"|\bwithdrawal path\b|\bright of withdrawal\b|\bwithdrawal rights?\b|\bwithdrawal periods?\b"
)
_OPERATOR_PHRASES = re.compile(
    r"\b(?:size|sizing|fit) (?:advice|guidance|help|tips?|recommendations?)\b"
    r"|\bconsigli[oi]? (?:di |sulla |per la )?taglia\b|\bmoney back\b"
    r"|\bstore credit\b|\bgift cards?\b|\breturns? periods?\b|\bsend\w* (?:[a-z]+ ){0,2}back\b"
    # the scope of a lever (round 4, finding U6)
    r"|\bfrom now on\b|\bgoing forward\b|\bd ora in poi\b"
    r"|\b(?:next|upcoming) (?:orders?|purchases?)\b|\bhenceforth\b"
    r"|\bhereafter\b|\bfrom here on\b|\bprossim[oi] (?:ordin[ei]|acquist[oi])\b"
    # the state of a lever, and a welcome coupon withheld
    r"|\b(?:switch|switches|switched|switching|turn|turns|turned|turning) (?:off|on)\b"
    r"|\b(?:is|are|was|were|be|been|being|stays?|stayed|now|get|gets|got|go|goes|went) off\b"
    r"|\b(?:no|without|zero|lose|loses|lost|losing|denied|deny) welcome coupons?\b"
    # who pays for returns
    r"|\bpa(?:y|ys|id|ying) (?:for )?(?:the |their |his |her |own |any )*(?:returns?|return"
    r" shipping)\b"
)
# A state word next to a lever noun, in either order, within three words (round 5, findings V4
# and V17): "the cluster keeps its welcome coupons", "welcome coupons remain available", "size
# changes are no longer offered", "coupons are gone".
_STATE_WORDS = (
    r"keep|keeps|kept|keeping|retain\w*|remain\w*|stay|stays|stayed|staying|avail\w*|unavail\w*"
    r"|offer|offers|offered|offering|lose|loses|lost|losing|gone|still|pay|pays|paid|paying"
    r"|no longer|non piu|disponibil\w*|mantien\w*|mantengono|rimane|rimangono|resta|restano"
    r"|offert\w*|tolt\w*"
)
_LEVER_NOUNS = (
    r"coupons?|vouchers?|discounts?|codes?|shipping|postage|refunds?|exchanges?|swaps?"
    r"|size changes?|windows?|terms|conditions|perks?|benefits?|privileges?|rewards?|services?"
    r"|advice|guidance"
    r"|sconti|sconto|codici|spedizion\w*|rimbors\w*|cambi|cambio|condizioni|vantaggi"
)
_LEVER_STATE_RE = re.compile(
    rf"\b(?:{_STATE_WORDS})(?: [a-z]+){{0,2}} (?:{_LEVER_NOUNS})\b"
    rf"|\b(?:{_LEVER_NOUNS})(?: [a-z]+){{0,2}} (?:{_STATE_WORDS})\b"
)
# The word "quadrant" without a label next to it describes a place in the matrix in words ("the
# generous quadrant", "the last quadrant"): round 4, finding U5. A label next to it is read by
# `quadrant_labels`; "no quadrant" is an observation when the engine assigned none.
_QUADRANT_NOUN_RE = re.compile(r"\bquadrant[ei]?\b")
_LABEL = r"q[\s_‐‑‒–—-]*(?:[1-4]|iv|iii|ii|i|one|two|three|four|uno|due|tre|quattro)"
_LABELLED_QUADRANT_RE = re.compile(rf"\b{_LABEL}\s+quadrant[ei]?\b|\bquadrant[ei]?\s+{_LABEL}\b")
_NO_QUADRANT_RE = re.compile(r"\bno quadrant\b")


def unlabelled_quadrants(text: str, *, none_assigned: bool = False) -> list[str]:
    """The word "quadrant" with no quadrant label next to it; "no quadrant" is left out when the
    engine assigned none (`none_assigned`)."""
    folded = fold(text, " ")
    for pattern in (_LABELLED_QUADRANT_RE, _QUADRANT_WORD_RE, _QUADRANT_ORDINAL_RE):
        folded = pattern.sub(" ", folded)
    if none_assigned:
        folded = _NO_QUADRANT_RE.sub(" ", folded)
    return [m.group() for m in _QUADRANT_NOUN_RE.finditer(folded)]


# A count of coupons in digits ("7 coupons"), read as the welcome coupons of a cited fact.
_COUPON_COUNT_RE = re.compile(r"(?<![\w.,])[0-9]+\s+(?:welcome\s+)?coupons?\b", re.IGNORECASE)


def operator_topics(
    text: str, *, no_quadrant: bool = False, coupon_count: bool = False
) -> list[str]:
    """Words of a topic an operator statement never talks about: levers and return terms (the
    stems of the customer gate, refunds, promotions, "terms", "policy"), restrictions, the state
    and scope of a lever (enabled, future orders; available, kept or no longer next to a lever
    noun), decision words (also flag, monitor, scrutiny, caution, "no further action"), the
    risk and value levels of the quadrant, places in the matrix and "quadrant" without a label.
    The interface shows the engine decision and its levers from code, so the statement has
    nothing to say about them (round 3, finding T7; round 4, findings U5 and U6). Metric names
    are removed by the caller; the observed phrase "welcome coupon(s)" is removed here, after
    the phrases that withhold it ("no welcome coupon"). With `coupon_count` (a welcome-coupon fact
    is cited), a count of coupons in digits is that fact ("7 coupons across 7 linked accounts":
    round 6, finding W6). Flag and caution count only about a person or the case (finding
    W5)."""
    if coupon_count:
        text = _COUPON_COUNT_RE.sub(" welcome coupons ", text)
    found = _flags_and_cautions(text)
    for form in forms(text, leet=True):
        _add(found, (m.group() for m in _OPERATOR_PHRASES.finditer(form)))
        _add(found, (m.group() for m in _LEVER_STATE_RE.finditer(form)))
        _add(found, (m.group() for m in _STATEMENT_DECISION_RE.finditer(form)))
        _add(found, [word for word in form.split() if word in STATEMENT_DECISION_WORDS])
        form = _OPERATOR_ALLOWED_PHRASES.sub(" ", form)
        _add(found, _terms_hits(form, OPERATOR_TOPIC_STEMS, OPERATOR_TOPIC_WORDS))
        _add(found, _decision_hits(form))
    levels = [f"{axis} {named}" for kind, axis, _, named in level_mentions(text) if kind == "axis"]
    found = _add(_add(found, levels), quadrant_positions(text))
    return _add(found, unlabelled_quadrants(text, none_assigned=no_quadrant))


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
# Round 4, finding U12: nil, nought, naught, zilch, twain, brace, gross, umpteen, pony, monkey.
_EN_OTHER = word_set(
    "zero dozen dozens fortnight fortnights fortnightly twice thrice half halves halve halved "
    "halving quarter quarters tenner tenners fiver fivers sennight sennights couple couples pair "
    "pairs handful handfuls trio trios grand grands quid quids buck bucks nil nought noughts "
    "naught naughts zilch twain brace braces gross grosses umpteen umpteenth pony ponies monkey "
    "monkeys"
)
# A quantity word that is not one before these words ("gross margin").
_NOT_NUMBER_BEFORE: dict[str, frozenset[str]] = {
    "gross": word_set("margin margins value revenue revenues sales profit profits amount income"),
}
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
    """Numbers written in letters, in any form of the text ("gross" before "margin" is not)."""
    skipped = frozenset(skip)
    found: list[str] = []
    for form in forms(text):
        tokens = form.split()
        for index, word in enumerate(tokens):
            following = tokens[index + 1] if index + 1 < len(tokens) else ""
            if following in _NOT_NUMBER_BEFORE.get(word, frozenset()):
                continue
            if word not in skipped and number_word(word, lang):
                _add(found, [word])
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
# Round 3, finding T11: losing, costs, short, arrears, in passivo, in rimessa, deducted, written
# off. "Less" is a sign right before a number only ("less 42.1 EUR"), not anywhere ("contributes
# less to the score": round 5, finding V17); in an agent answer, which may be Italian, so is
# "meno" ("meno 3", not "meno carte distinte": finding V12).
_SIGN_BEFORE_RE = re.compile(r"\b(?:less|meno)\s*[0-9]")
# "Short" is a sign next to a number only ("42.1 EUR short", "short by 42.1", "short 42.1"), not
# anywhere ("redeemed within a short time", "short sleeves": round 6, finding W6).
_SHORT_RE = re.compile(
    r"[0-9]\s*(?:(?:eur|euros?|€|days?|giorni|accounts?|items?|orders?|returns?)\s+)?short\b"
    r"|\bshort\s+(?:(?:by|of)\s+)?[0-9]"
)
_SIGN_WORDS_RE = re.compile(
    r"\b(?:minus|meno|negativ[eoai]|loss|losses|lossmaking|perdit[ae]|deficit|disavanz[oi]"
    r"|in the red|in rosso|owed|owes|owing|debit(?! cards?\b)|debits|debts?|debit[oi]"
    r"|shortfall|lost|unprofitable|lose|loses|losing|cost|costs|costing|arrears"
    r"|passiv[oaie]|rimessa|deduct\w*|written off|writes? off|writeoffs?)\b"
)


def number_modifiers(text: str, labelled: str | None = None) -> list[str]:
    """Scale suffixes, unit words and signs around numbers, and sign words in a text with a
    number; empty when every number is plain. In `labelled` (the same text with identifiers
    read as a word), a number in parentheses right after a word is a label's value, not an
    accounting sign ("weight (0.98)", "returns_90d (4)": round 5, finding V12)."""
    found: list[str] = []
    patterns = ((fold(text), _SUFFIXED_RE), (fold(text, " "), _SCALED_RE))
    for folded, pattern in (*patterns, (fold(text, " "), _SIGN_BEFORE_RE)):
        _add(found, (m.group().strip() for m in pattern.finditer(folded)))
    signed = fold(labelled if labelled is not None else text)
    for match in _SIGNED_RE.finditer(signed):
        label = match.group().startswith("(") and signed[: match.start()].rstrip()[-1:].isalpha()
        if not label:
            _add(found, [match.group().strip()])
    if _NUMBER_RE.search(fold(text)):
        for form in forms(text):
            words_ = (m.group() for m in _SIGN_WORDS_RE.finditer(form))
            _add(found, (word for word in words_ if word != "meno"))
    return _add(found, _short_signs(text))


def other_numeric_characters(text: str) -> list[str]:
    """Numeric characters other than ASCII digits (left after NFKC): they cannot be grounded."""
    return [ch for ch in numeric_characters(text) if ch not in "0123456789"]


def _short_signs(text: str) -> list[str]:
    return ["short"] if _SHORT_RE.search(fold(text, " ")) else []


def sign_words(text: str) -> list[str]:
    """Words that give a number a sign ("minus", "a loss of", "owed", "in arrears", "short" next
    to a number)."""
    found: list[str] = []
    for form in forms(text):
        _add(found, (m.group() for m in _SIGN_WORDS_RE.finditer(form)))
    return _add(found, _short_signs(text))


# ---- The context of a grounded number (round 3, finding T11) ----------------------------------

# A number of an operator statement is grounded only by the value of a cited fact AND an allowed
# context, so no unit, scale, rate, comparator or sign can change what it means:
# - before it: the start of the text, a word that is not a comparator or a unit of another kind,
#   a separator (":", "=", ",", ";"), "(" after a word, or a dash with spaces around it after a
#   word ("margin — 42.1 EUR"); any other dash or minus right before the digits is a sign;
# - after it: the end of the text, punctuation, a spaced dash, a function word, or the unit of
#   the fact's metric (EUR or euro for money, days or giorni for day counts; for scores, rates
#   and counts no unit or a count noun), itself followed by the end, punctuation, a spaced dash
#   or a function word (a count noun by anything but a rate word);
# - a closing parenthesis is transparent ("(42.1) mil" is "42.1 mil": round 4, finding U1), a
#   function word followed by more, less, least, most, average, each, every... is a comparator
#   ("42.1 EUR or more", "at least", "on average"), so is a comparator before "of", "to" or
#   "than" ("in excess of", "close to"), and "within" is allowed before a day count only;
# - anywhere in the statement (round 4, finding U9): a unit, currency, scale word or ISO 4217
#   code of another kind than the facts its numbers ground ("Weeks since delivery: 6", "Margin in
#   USD: 42.1", "CHF 42.1"), a percent or currency sign but €, and a rate word ("per", "monthly",
#   "average") make the statement ungrounded. Metric names are stripped before, so
#   "value_eur_12m" or "days_since_delivery" never count.
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
# The word a stripped metric name leaves in a statement (round 4, finding U10; round 5, finding
# V1): "driven by cluster_welcome_coupons (7)" is read as "driven by metric (7)". The placeholder
# is a label, not a function word: after a number it is transparent ("42.1 value_eur_12m k" is
# "42.1 k"), and before a number or "(" it is allowed only after a word that is not a comparator
# (but "by"). Case, cluster and quadrant ids are stripped to a space, so "Q4 (42.1)" is "(42.1)".
IDENTIFIER_PLACEHOLDER = "metric"
_FUNCTION_AFTER = word_set(
    "and or but in on at of for from to with over across during since as is are was were be "
    "been has have had the this that these those which who while so because after before later "
    "earlier ago out e o ma di del della nel nella con da che su "
    # conjunctions and modal verbs (round 6, findings W5 and W6): "low (0.22) despite the shared
    # address", "the risk score of 0.18 should be read with caution"
    "despite although though whereas unlike yet should may might can could would will must"
)
# "Even" is a conjunction only before these ("(0.22) even though"); "42.1 EUR even" is exact.
_EVEN_CONJUNCTIONS = word_set("though if when")
# Verbs a closed parenthesis around the number may be followed by: "returns_90d (4) contributes
# to the score", "Recent returns (4) push the score up" (round 5, finding V16), and so may a
# number right after a metric name ("returns_90d = 4 raises the score"). An allowlist, so an
# unknown scale word ("(42.1) lakh") stays ungrounded.
_CONTRIBUTION_AFTER = word_set(
    "contribute contributes contributed contributing drive drives drove driving add adds added "
    "push pushes pushed raise raises raised weigh weighs weighed explain explains explained "
    "dominate dominates dominated lead leads led lift lifts lifted lower lowers lowered increase "
    "increases increased reduce reduces reduced affect affects affected shape shapes shaped "
    "count counts counted matter matters mattered "
    # round 6, findings W2 and W6: "(6) plays a minor role", "(7) outweighs", "(6) makes the risk
    # score slightly lower", "(6) pulls the risk score lower"
    "play plays played playing outweigh outweighs outweighed outranks outrank make makes made "
    "pull pulls pulled move moves moved nudge nudges nudged tip tips tipped bring brings brought "
    "carry carries carried tops ranks stands influence influences "
    "influenced decrease decreases decreased boost boosts boosted"
)
# Participles a count may follow with "by" ("the welcome coupons used by 7 linked accounts"):
# round 5, finding V17. Elsewhere "by" is a comparator ("short by 7").
_BY_PARTICIPLES = word_set(
    "used shared redeemed created opened placed made returned linked connected joined held"
)
# After a function word right after a number, these make it a comparator ("6 days or more").
_AFTER_STOPS = word_set(
    "more less fewer so least most average each every above below over under higher lower "
    "longer shorter up plus"
)
# Adjectives a count noun may follow ("5 recent returns", "9 distinct cards").
_COUNT_ADJECTIVES = word_set(
    "recent previous past prior distinct different separate unique active payment shared"
)
_RATE_WORDS = word_set("per a an every weekly monthly daily yearly annually hourly times x apiece")
_COMPARATORS = word_set(
    "about around approximately approx roughly nearly almost circa over under above below beyond "
    "less more than up least most max min maximum minimum exceeding exceeds within plus minus "
    "times x by sotto sopra oltre quasi almeno massimo minimo meno piu fino entro near some "
    "estimated excess upwards upward north close"
)
# A comparator before one of these is still a comparator ("in excess of", "close to").
_COMPARATOR_LINKS = word_set("of to than")
_WITHIN = word_set("within entro")  # "returned within 6 days" of a day count is the day count
_ANY_UNIT = UNIT_WORDS | _UNITS["money"] | _UNITS["days"]
_SEPARATORS = frozenset(":=,;.!?")
_CLOSERS = frozenset(".,;:!?(…")
# Statement-level units of another kind (round 4, finding U9).
_SCALE_WORDS = word_set(
    "mln mld mrd mio mil mill bln bn mn keur meur beur kilo kilos thou trillion trillions bps pp "
    "pct percentage percentages"
)
_CURRENCY_WORDS = word_set(
    "dollar dollars dollaro dollari pound pounds sterling sterlina sterline cent cents centesimo "
    "centesimi penny pennies pence franc francs franchi yen yuan renminbi rupee rupees krona "
    "kronor krone kroner zloty zlotys forint forints lira lire rouble roubles ruble rubles peso "
    "pesos rand dinar dinars dirham dirhams riyal riyals shekel shekels baht ringgit rupiah "
    "bitcoin bitcoins btc usd gbp chf jpy cny sek nok dkk pln czk huf bgn cad aud nzd hkd sgd inr "
    "brl mxn zar krw"
)
_STATEMENT_RATE_WORDS = word_set(
    "per monthly weekly daily yearly annually annual hourly quarterly average averages averaged "
    "avg apiece"
)
# ISO 4217 codes, read in upper case only ("CHF 42.1"; "ALL" or "TOP" in lower case are words).
_ISO_CURRENCIES = word_set(
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP "
    "BYN BZD CAD CDF CHF CLP CNY COP CRC CUC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB FJD FKP GBP "
    "GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR "
    "KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK "
    "MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR "
    "SBD SCR SDG SEK SGD SHP SLE SLL SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD "
    "TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL BTC XBT"
)
_UPPER_CODE_RE = re.compile(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])")
_APPROXIMATIONS = re.compile(
    r"\b(?:give or take|more or less|plus or minus|or thereabouts|ballpark|in the region of)\b"
)
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


def _comparator_at(tokens: Sequence[_Token], index: int) -> bool:
    """A comparator, or "of", "to", "than" right after one ("in excess of", "close to")."""
    token = tokens[index]
    linked = (
        token.text in _COMPARATOR_LINKS and index > 0 and tokens[index - 1].text in _COMPARATORS
    )
    return token.text in _COMPARATORS or linked


def _placeholder_ok(tokens: Sequence[_Token], index: int) -> bool:
    """A metric placeholder before a number or "(": at the start, after a separator, or after a
    word that is not a comparator ("driven by metric (7)" passes, "about metric 42.1" does not)."""
    if index == 0:
        return True
    before = tokens[index - 1]
    if before.word:
        return before.text == "by" or not _comparator_at(tokens, index - 1)
    return before.text in _SEPARATORS or before.text == "(" or before.dash


def _counted(tokens: Sequence[_Token], number: int) -> bool:
    """A count noun after the number, past up to two adjectives ("7 linked accounts")."""
    noun = number + 1
    while noun < min(len(tokens), number + 3) and tokens[noun].text in _COUNT_ADJECTIVES:
        noun += 1
    return noun < len(tokens) and tokens[noun].text in _COUNT_NOUNS


def _before_ok(tokens: Sequence[_Token], index: int, kind: UnitKind, number: int) -> bool:
    if index < 0:
        return True
    token = tokens[index]
    if token.text == IDENTIFIER_PLACEHOLDER:
        return _placeholder_ok(tokens, index)
    if token.word:
        if kind == "days" and token.text in _WITHIN:
            return True
        by_count = kind == "count" and token.text == "by" and _counted(tokens, number)
        if by_count and index > 0 and tokens[index - 1].text in _BY_PARTICIPLES:
            return True
        return not _comparator_at(tokens, index) and (
            token.text not in _ANY_UNIT or token.text in _UNITS[kind]
        )
    if token.text == "(":
        if index == 0:
            return False
        label = tokens[index - 1]
        if label.text == IDENTIFIER_PLACEHOLDER:
            return _placeholder_ok(tokens, index - 1)
        return label.word and label.text not in _COMPARATORS
    return token.text in _SEPARATORS or token.dash or token.text in _UNITS[kind]


def _comparing(tokens: Sequence[_Token], index: int) -> bool:
    """A function word followed by more, least, average, each... ("or more", "at least")."""
    return index + 1 < len(tokens) and tokens[index + 1].text in _AFTER_STOPS


def _end_ok(tokens: Sequence[_Token], index: int, opened: bool, verbs: bool = False) -> bool:
    """What follows a number (or its unit) is allowed. A closing parenthesis and a metric
    placeholder are transparent ("(42.1) mil" is "42.1 mil": round 4, finding U1; "42.1
    value_eur_12m k" is "42.1 k": round 5, finding V1). A contribution verb is allowed after the
    ")" that closes a "(" opened right before the number (finding V16), and right after a number
    that follows a metric name (`verbs`)."""
    if index >= len(tokens):
        return True
    token = tokens[index]
    if token.text == IDENTIFIER_PLACEHOLDER:
        return _end_ok(tokens, index + 1, opened, verbs)
    if token.text == ")":
        return _end_ok(tokens, index + 1, opened, verbs or opened)
    if token.word:
        if verbs and token.text in _CONTRIBUTION_AFTER:
            return True
        following = tokens[index + 1].text if index + 1 < len(tokens) else ""
        if token.text == "even" and following in _EVEN_CONJUNCTIONS:
            return True
        return token.text in _FUNCTION_AFTER and not _comparing(tokens, index)
    return token.text in _CLOSERS or (token.dash and token.start > tokens[index - 1].end)


def _labelled(tokens: Sequence[_Token], before: int) -> bool:
    """A number right after a metric name, also after "=" or ":" ("returns_90d = 4")."""
    if before >= 1 and tokens[before].text in ("=", ":"):
        before -= 1
    return before >= 0 and tokens[before].text == IDENTIFIER_PLACEHOLDER


def _after_ok(
    tokens: Sequence[_Token], index: int, kind: UnitKind, opened: bool, labelled: bool
) -> bool:
    following = index + 1
    if following < len(tokens) and tokens[following].text in _UNITS[kind]:
        return _end_ok(tokens, following + 1, opened, labelled)
    if kind == "count":
        noun = following
        while noun < min(len(tokens), following + 2) and tokens[noun].text in _COUNT_ADJECTIVES:
            noun += 1
        if noun < len(tokens) and tokens[noun].text in _COUNT_NOUNS:
            while noun < len(tokens) and tokens[noun].text in _COUNT_NOUNS:
                noun += 1
            if noun >= len(tokens):
                return True
            after = tokens[noun]
            comparing = after.text in _FUNCTION_AFTER and _comparing(tokens, noun)
            return after.text not in _RATE_WORDS and not comparing
    return _end_ok(tokens, following, opened, labelled)


def _unit_conflicts(text: str, tokens: Sequence[_Token], kinds: set[UnitKind]) -> list[str]:
    """Units, currencies, scales, ISO codes, signs and rate words anywhere in a statement that
    are not the units of the kinds of facts its numbers ground."""
    allowed = frozenset[str]().union(*(_UNITS[kind] for kind in kinds))
    found: list[str] = []
    for token in tokens:
        named = token.text in _ANY_UNIT or token.text in _SCALE_WORDS
        named = named or token.text in _CURRENCY_WORDS
        symbol = not token.word and not token.number
        symbol = symbol and (unicodedata.category(token.text[0]) == "Sc" or token.text in "%‰‱")
        other_unit = (named or symbol) and token.text not in allowed
        if other_unit or token.text in _STATEMENT_RATE_WORDS:
            _add(found, [token.text])
    codes = _UPPER_CODE_RE.findall(unicodedata.normalize("NFKC", text))
    _add(found, (code for code in codes if code in _ISO_CURRENCIES))
    return _add(found, (m.group() for m in _APPROXIMATIONS.finditer(_letters_only(fold(text)))))


def grounding_problems(text: str, cited: Sequence[tuple[str, Decimal]]) -> list[str]:
    """The numbers of an operator statement that are not grounded on the cited facts, given as
    (metric, value): a value no cited fact has, sign included, a value outside an allowed
    context, and sign words in a text with a number that is not negative (a negative fact
    written with its sign may say "negative")."""
    folded = fold(text)
    tokens = [_Token(m.group(), m.start(), m.end()) for m in _GROUND_TOKEN_RE.finditer(folded)]
    problems: list[str] = []
    values: list[Decimal] = []
    grounded: set[UnitKind] = set()
    for index, token in enumerate(tokens):
        if not token.number:
            continue
        value, before = parse_number(token.text), index - 1
        if before >= 0 and tokens[before].dash and not _spaced(tokens, before):
            value, before = -value, before - 1
        values.append(value)
        kinds = {unit_kind(metric) for metric, fact in cited if fact == value}
        grounded |= kinds
        opened = before >= 0 and tokens[before].text == "("
        labelled = _labelled(tokens, before)
        if not kinds:
            problems.append(str(value))
        elif not any(
            _before_ok(tokens, before, k, index) and _after_ok(tokens, index, k, opened, labelled)
            for k in kinds
        ):
            last = tokens[min(index + 2, len(tokens) - 1)]
            problems.append(folded[tokens[max(before, 0)].start : last.end])
    if values:
        problems += _unit_conflicts(text, tokens, grounded)
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
    "high higher highest highish elevated significant considerable substantial serious severe "
    "big alto alta alti alte elevato elevata elevati elevate maggiore notevole"
)
_LEVEL_LOW = word_set(
    "low lower lowest lowish negligible minimal modest small little minor basso bassa bassi "
    "basse scarso scarsa scarsi scarse ridotto ridotta minore trascurabile"
)
_ABOVE = word_set(
    "above over exceeds exceed exceeding exceeded beyond sopra oltre supera superiore"
)
_BELOW = word_set("below under beneath sotto inferiore")
# A threshold, or a typical level, compared with ("above the threshold", "below average").
_THRESHOLDS = word_set(
    "threshold thresholds cutoff average median normal typical soglia soglie media"
)
# Words that imply a level of an axis (round 4, finding U5): risky and dangerous (high risk), safe,
# trustworthy and reliable (low risk) are quadrant levels; valuable and profitable (high value),
# unprofitable (low value) are levels of the customer value, like a level of the net margin.
_RISKY = word_set(
    "risky riskier riskiest dangerous rischioso rischiosa rischiosi rischiose pericoloso "
    "pericolosa pericolosi pericolose"
)
_SAFE = word_set(
    "safe safer safest trustworthy reliable dependable harmless sicuro sicura sicuri sicure "
    "affidabile affidabili"
)
_VALUABLE = word_set("valuable profitable lucrative prezioso preziosa redditizio redditizia")
_UNPROFITABLE = word_set("unprofitable lossmaking")
# The customer value in other words: margin, profit, spend (round 4, finding U5), as whole words
# ("marginally" is not a margin: round 5, finding V5).
_VALUE_MEASURES = word_set(
    "margin margins profit profits profitability spend spending spent margine margini redditivita"
)
# Middle levels, read as a level of unknown side ("?"), never as the engine's level.
_LEVEL_MID = word_set(
    "moderate moderately medium mid middling borderline intermediate moderato moderata medio "
    "intermedio intermedia"
)
_NEGATIONS = word_set(
    "not never non hardly barely scarcely neither nor cannot mai nemmeno neanche mica"
)
# Negations that reach a level word from anywhere before it in the clause ("far from the
# high-risk range", "nowhere near high"): round 6, finding W3.
_FAR_FROM = frozenset({("far", "from"), ("nowhere", "near"), ("lontano", "da")})
# A clause is read in stretches that end at a comma, a colon, a parenthesis or a dash: a negation
# counts before the level word in its stretch ("not in the high range", "does not push the risk
# score into the high range"), never after it ("high (0.87): an estimate, not a cause").
_STRETCH_RE = re.compile(r"[,:()\[\]–—]")
# How large a contribution to the score is, not the level of the score (round 5, finding V11): a
# level word followed within two words by one of these nouns ("a significant contributor", "a
# small effect", "a substantial part", "a small change"), or right after one of these verbs
# ("contributes little").
_CONTRIBUTION_NOUNS = word_set(
    "contribution contributions contributor contributors factor factors driver drivers weight "
    "weights effect effects impact impacts part parts share shares influence influences signal "
    "signals role roles number count amount proportion fraction portion difference differences "
    "change changes step steps shift shifts contributo contributi fattore fattori peso effetto "
    "parte quota"
)
_CONTRIBUTION_VERBS = word_set(
    "contributes contribute contributed adds add added weighs weigh weighed counts count matters "
    "matter contribuisce contribuiscono pesa pesano"
)
# A comparative says a direction, not a level ("pushes the risk score higher", "the risk score
# is higher because of the shared card", "a higher risk score"), unless it compares with a
# threshold ("higher than the threshold", "lower than average") or qualifies an axis of the
# quadrant ("a higher-risk customer"): round 6, finding W2.
_COMPARATIVES = word_set("higher lower maggiore minore")
# Verbs that move the score: after one of them in the clause, a word that sizes the move is not
# a level ("lowers the risk score a little", "raises it by a considerable step"), while a level
# word still is ("pushes the risk score into the high range"). "A little" is a quantity anywhere.
# Round 6, finding W2.
_DIRECTION_VERBS = word_set(
    "push pushes pushed pushing pull pulls pulled pulling make makes made making drive drives "
    "drove driven driving move moves moved moving raise raises raised raising lift lifts lifted "
    "lifting lower lowers lowered lowering increase increases increased increasing reduce "
    "reduces reduced reducing bring brings brought bringing nudge nudges nudged nudging tip tips "
    "tipped tipping shift shifts shifted shifting"
)
_QUANTITY_WORDS = word_set(
    "little small minor modest slight negligible minimal significant considerable substantial big"
)
# "By a small margin", "by a wide margin": a quantity, not the customer's margin (round 6).
_BY_A_MARGIN_RE = re.compile(r"\bby an? (?:[^\W\d_]+ )?margins?\b")
_SCORES = word_set("score scores punteggio punteggi")
# "The value of the order", "a high-value item": the value of something else than the customer.
_ORDER_WORDS = word_set(
    "order orders item items product products basket cart purchase purchases ordine ordini "
    "articolo articoli prodotto prodotti capo capi carrello acquisto acquisti"
)
_DAMAGE_WORDS = word_set("damage damages error errors mistake mistakes danno danni errore errori")
# Clauses end at sentence ends and at "and", "but" ("e", "ma"), split before the accents are
# folded, so "è" never splits a clause. Numbers are replaced by a word first, so a decimal point
# does not end a clause ("The risk score (0.87) is low").
_LEVEL_CLAUSE_RE = re.compile(r"([.!?;\n]+|\b(?:and|but|while|whereas|e|ma|mentre)\b)")
_CARRY_SEPARATORS = frozenset({"and", "e"})
# "Over the period" is the period of a fact, not "above" (round 5, finding V5).
_PERIOD_WORDS = word_set("period year months weeks days time")


def _clause_tokens(clause: str) -> tuple[list[str], list[int]]:
    """The words of a clause and, for each, the number of its stretch (`_STRETCH_RE`)."""
    tokens: list[str] = []
    stretches: list[int] = []
    for number, stretch in enumerate(_STRETCH_RE.split(clause)):
        found = _letters_only(fold(stretch, " ")).split()
        tokens += found
        stretches += [number] * len(found)
    return tokens, stretches


def _negation_at(tokens: Sequence[str], stretches: Sequence[int], index: int) -> str | None:
    """The negation that governs the word at `index`, as written, or None: not, never, non,
    hardly, "n't"... before it in its stretch of the clause ("not high", "is not in the high
    range", "isn't high"), or "far from", "nowhere near" anywhere before it in the clause ("far
    from a high value"). A negation after the word does not count ("high (0.87): an estimate, not
    a cause": round 5, finding V5; round 6, finding W3)."""
    for first, second in zip(tokens[:index], tokens[1:index], strict=False):
        if (first, second) in _FAR_FROM:
            return f"{first} {second}"
    start = index
    while start > 0 and stretches[start - 1] == stretches[index]:
        start -= 1
    for position in range(start, index):
        token = tokens[position]
        if token in _NEGATIONS:
            return token
        if token == "t" and position > start and tokens[position - 1].endswith("n"):
            return f"{tokens[position - 1]} t"
    return None


def _describes_contribution(tokens: Sequence[str], index: int) -> bool:
    """A level word that says how large a contribution is (round 5, finding V11)."""
    after = tokens[index + 1 : index + 3]
    before = tokens[index - 1] if index > 0 else ""
    return any(t in _CONTRIBUTION_NOUNS for t in after) or before in _CONTRIBUTION_VERBS


def _not_a_level(tokens: Sequence[str], index: int, threshold: bool, moved: bool) -> bool:
    """A level word used for something else: the size of a contribution, a comparative that does
    not compare with a threshold, "a little", a quantity after a verb that moves the score."""
    token = tokens[index]
    if token in _COMPARATIVES:
        following = tokens[index + 1 : index + 3]
        axis = following[:1] in (["risk"], ["value"]) and not set(following[1:]) & _SCORES
        return not (axis or (threshold and following[:1] == ["than"]))
    if token == "little" and index > 0 and tokens[index - 1] == "a":
        return True
    return (moved and token in _QUANTITY_WORDS) or _describes_contribution(tokens, index)


def _above(tokens: Sequence[str], index: int) -> bool:
    """A word for "above", not "over the period" (round 5, finding V5)."""
    following = tokens[index + 1 : index + 3]
    period = (
        tokens[index] == "over"
        and following[:1] == ["the"]
        and following[1:]
        and (following[1] in _PERIOD_WORDS)
    )
    return tokens[index] in _ABOVE and not period


def _level(tokens: Sequence[str], stretches: Sequence[int]) -> tuple[str, str] | None:
    """The level a clause names, with the words that name it: high, low, "?" (a threshold without
    a side, both levels, a middle level or a negated level) or None (no level). Words that size a
    contribution or a move, and comparatives, are left out ("a significant contributor to the
    risk score", "pushes the risk score higher", "lowers it a little")."""
    threshold = [t for t in tokens if t in _THRESHOLDS]
    found: dict[str, list[int]] = {"high": [], "low": [], "mid": []}
    moved = False
    for index, token in enumerate(tokens):
        if _not_a_level(tokens, index, bool(threshold), moved):
            pass
        elif token in _LEVEL_HIGH or (threshold and _above(tokens, index)):
            found["high"].append(index)
        elif token in _LEVEL_LOW or (threshold and token in _BELOW):
            found["low"].append(index)
        elif token in _LEVEL_MID:
            found["mid"].append(index)
        moved = moved or token in _DIRECTION_VERBS
    high, low, mid = found["high"], found["low"], found["mid"]
    if not (high or low or mid or threshold):
        return None
    negations = {i: _negation_at(tokens, stretches, i) for i in high + low}
    named = [f"{negations[i]} {tokens[i]}" if negations.get(i) else tokens[i] for i in high + low]
    named += [tokens[i] for i in mid] if named or mid else threshold
    level = "high" if high else "low"
    if mid or bool(high) == bool(low) or any(negations.values()):
        level = "?"
    return level, " ".join(_add([], named))


# A level named in a text: (kind, axis, level, the words that name it).
Mention = tuple[str, str, str, str]


# Nouns of the evidence a word like "reliable" may qualify without naming a level of the customer
# ("a reliable estimate", "safe data"): round 5.
_EVIDENCE_NOUNS = word_set(
    "estimate estimates model models data evidence signal signals link links score scores "
    "measure measures figure figures result results history information source sources "
    "indicator indicators sample samples comparison conclusion stima stime dati modello"
)


def _implied_levels(tokens: Sequence[str], stretches: Sequence[int]) -> list[Mention]:
    """Levels a word implies: "risky" (high risk), "safe" (low risk), "profitable" (high value);
    of unknown side when a negation governs the word ("not safe"); none when the word qualifies
    the evidence ("a reliable estimate")."""
    found: list[Mention] = []
    for words_, kind, axis, level in (
        (_RISKY, "axis", "risk", "high"),
        (_SAFE, "axis", "risk", "low"),
        (_VALUABLE, "score", "value", "high"),
        (_UNPROFITABLE, "score", "value", "low"),
    ):
        hits = [
            i
            for i, t in enumerate(tokens)
            if t in words_ and not set(tokens[i + 1 : i + 2]) & _EVIDENCE_NOUNS
        ]
        if hits:
            negation = next(
                (n for n in (_negation_at(tokens, stretches, i) for i in hits) if n), None
            )
            named = f"{negation} {tokens[hits[0]]}" if negation else tokens[hits[0]]
            _add(found, [(kind, axis, "?" if negation else level, named)])
    return found


def _clause_axes(tokens: Sequence[str]) -> list[tuple[str, str]]:
    """The axes a clause names, as (kind, axis): the risk score and the customer value in other
    words (margin, profit, spend) are "score", the quadrant axes "axis"."""
    found: list[tuple[str, str]] = []
    other_value = any(t in _ORDER_WORDS for t in tokens)
    for index, token in enumerate(tokens):
        following = tokens[index + 1 : index + 3]
        if token in ("risk", "rischio"):
            damage = (
                following[:1] in (["of"], ["di"])
                and following[1:2]
                and (following[1] in _DAMAGE_WORDS)
            )
            if damage:
                continue
            score = bool(following[:1]) and following[0] in _SCORES
            score = score or (tokens[index - 2 : index] == ["punteggio", "di"])
            _add(found, [("score" if score else "axis", "risk")])
        elif token in ("value", "valore"):
            if not other_value and not any(t in _SCORES for t in tokens):
                _add(found, [("axis", "value")])
        elif token in _VALUE_MEASURES and not other_value:
            _add(found, [("score", "value")])
    return found


def level_mentions(text: str) -> list[Mention]:
    """Risk and value levels named in the text, as (kind, axis, level, words): kind "score" for
    a level of the risk score ("the risk score is high") or of the customer value in other words
    ("the net margin is low", "highly profitable"), "axis" for a level of the quadrant axes
    ("risk level is low", "high-risk", "il rischio è molto basso", "below the threshold",
    "risky", "safe"); `words` are the words of the text that name the level ("high", "not high",
    "far from high"), for the retry feedback. The value of an order or item, or of the score,
    and the risk of a damage or an error are not quadrant levels. A negated or middle level is
    "?" ("not high", "moderate"). A comparative, a word that sizes a contribution or a move, and
    "by a small margin" name no level. An axis without a level carries over "and" to the next
    clause ("the risk score and the margin are both low"). Numbers are replaced by a word before
    clauses are split (round 4, finding U5; round 6, findings W2 and W3)."""
    normal = _NUMBER_RE.sub(" num ", unicodedata.normalize("NFKC", text).casefold())
    normal = _BY_A_MARGIN_RE.sub(" by quantity ", normal)
    parts = _LEVEL_CLAUSE_RE.split(normal)
    found: list[Mention] = []
    carried: list[tuple[str, str]] = []
    for clause, separator in zip(parts[::2], [*parts[1::2], ""], strict=True):
        tokens, stretches = _clause_tokens(clause)
        _add(found, _implied_levels(tokens, stretches))
        axes = _clause_axes(tokens)
        named = _level(tokens, stretches)
        if named is None:
            carried = [*carried, *axes] if separator in _CARRY_SEPARATORS else []
            continue
        level, words_ = named
        _add(found, [(kind, axis, level, words_) for kind, axis in [*carried, *axes]])
        carried = []
    return found


def quadrant_levels(text: str) -> list[tuple[str, str, str]]:
    """The (kind, axis, level) of `level_mentions`, without repeats."""
    return _add([], ((kind, axis, level) for kind, axis, level, _ in level_mentions(text)))


_POSITION_RE = re.compile(
    r"\b(?:top|bottom|upper|lower) (?:left|right)\b"
    r"|\b(?:in )?(?:alto|basso) a (?:destra|sinistra)\b"
)


def quadrant_positions(text: str) -> list[str]:
    """Places in the matrix named in the text ("top left", "in alto a destra")."""
    return [m.group() for m in _POSITION_RE.finditer(_letters_only(fold(text, " ")))]


def strip_tokens(text: str, tokens: Iterable[str], replacement: str = " ") -> str:
    """Replace whole-token occurrences of known identifiers (case-insensitive) in the text by
    `replacement` (a space, or a placeholder word such as IDENTIFIER_PLACEHOLDER)."""
    out = unicodedata.normalize("NFKC", text)
    for token in sorted({t for t in tokens if t}, key=len, reverse=True):
        pattern = rf"(?<![\w-]){re.escape(token)}(?![\w-])"
        out = re.sub(pattern, lambda _: replacement, out, flags=re.IGNORECASE)
    return out


def word_count(text: str) -> int:
    return len(text.split())
