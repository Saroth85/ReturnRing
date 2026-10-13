"""Read-only investigator agent (US-701, US-702, docs/spec.md 8.8, test LLM-07).

- Five read-only tools over an injected data-access protocol (CaseReader): get_case, get_orders,
  get_cluster, get_score_factors, get_policy_text. The protocol has no write method.
- At most MAX_TOOL_CALLS tool calls per question, counted by the ToolBox; unknown tools count too.
- A request to change anything is refused before any tool or model call. Each sentence of the
  question is checked on its own ("What is this case? Approve it." is a request), and so is each
  clause that opens with a write verb ("Tell me what this case is and approve it"), also with
  digits read as letters, dashes and dots joined ("appr0ve", "dis-able", "dis.able"), Italian
  clitics ("Disattivali") and formal imperatives ("Approvi il reso"). A write-verb noun after a
  determiner or before a noun it qualifies is not a request ("Tell me about the refund",
  "Refund status?").
- Every answer lists the evidence consulted, the evidence missing and the ids it cites (orders,
  links, clusters), all collected by code from the tool results, never from the model.
- MockAgent answers the three questions of US-702 deterministically from tool results only.
- VllmAgent uses Gemma tool calling with the same tools and limits. An answer is replaced by a
  list of the evidence consulted when it names a banned term (same-person synonyms and ownership
  of the linked accounts included), a decision verb (approve, reject, block...), a
  recommendation, an action (an imperative, an obligation such as "must be disabled" or "make
  sure", a claim that something was done such as "done", "as requested" or a first-person
  subject with a write verb), a character outside the allowlist, a number written in letters or
  Roman numerals, or a number absent from the tool results (integers exactly, decimals possibly
  rounded to the digits written, no sign flip or scale suffix). A number in a clause about a
  record (a link, a cluster, an order, the case) must be a value of that record, and a number
  next to a field word ("7 linked accounts", "weight 0.6") that field. Adversarial review of
  13 Oct, finding 7; round 2, finding R4; round 3, findings T6, T9 and T12 to T16.
"""

from __future__ import annotations

import dataclasses
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Protocol

from llm.client import ChatClient, LlmUnavailable
from llm.prompts import agent_system_prompt
from llm.text import (
    PRONOUN_NUMBERS,
    banned_terms,
    decision_wording,
    fold,
    foreign_characters,
    forms,
    number_modifiers,
    number_words,
    roman_numerals,
    signed_numbers,
    strip_tokens,
    word_set,
    words,
)
from policy.types import ReturnTerms

MAX_TOOL_CALLS = 5
# Number words an answer may use: pronoun "one", and "first" of the tool field first_shared_on.
ANSWER_SKIPPED_NUMBER_WORDS = PRONOUN_NUMBERS | {"first"}
TOOL_NAMES = ("get_case", "get_orders", "get_cluster", "get_score_factors", "get_policy_text")
DEFAULT_REFERENCES: Mapping[str, str] = {"rossi": "F-ROSSI"}  # spec 7: the Rossi family
Nature = Literal["real", "synthetic", "simulated"]


# ---- Records returned by the data-access protocol -----------------------------------------------


@dataclass(frozen=True)
class CaseRecord:
    case_id: str
    order_id: str
    path: str
    evidence_quality: str
    quadrant: str | None
    lever_ids: tuple[str, ...]
    cluster_id: str | None
    nature: Nature
    as_of: date


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    placed_on: date
    terms_snapshot_id: str
    snapshot_source: str  # "base" or the decision that produced the terms (ADR-05)
    terms: ReturnTerms


@dataclass(frozen=True)
class LinkRecord:
    link_id: str
    account_a: str
    account_b: str
    kinds: tuple[str, ...]
    first_shared_on: date
    last_shared_on: date
    n_shared_events: int
    weight: float
    disputed: bool = False


@dataclass(frozen=True)
class ClusterRecord:
    cluster_id: str
    size: int
    links: tuple[LinkRecord, ...]
    n_distinct_cards: int
    creation_spread_days: float
    welcome_coupons_per_account: float
    cluster_return_rate: float | None
    strong_id_share: float
    nature: Nature
    as_of: date


@dataclass(frozen=True)
class FactorRecord:
    feature: str
    value: float
    phrase: str


@dataclass(frozen=True)
class PolicyRecord:
    version: str
    text: str


class CaseReader(Protocol):
    """Read-only data access, injected by the API (store, features and scores)."""

    def get_case(self, case_id: str) -> CaseRecord | None: ...
    def get_orders(self, case_id: str) -> Sequence[OrderRecord]: ...
    def get_cluster(self, cluster_id: str) -> ClusterRecord | None: ...
    def get_score_factors(self, case_id: str) -> Sequence[FactorRecord]: ...
    def get_policy_text(self, case_id: str) -> PolicyRecord | None: ...


# ---- Answers ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    kind: Literal["order", "link", "cluster"]
    id: str


Source = Literal["refusal", "mock", "model", "fallback"]


@dataclass(frozen=True)
class AgentAnswer:
    question: str
    text: str
    source: Source
    tool_calls: int
    consulted: tuple[str, ...]
    missing: tuple[str, ...]
    links: tuple[Ref, ...]

    @property
    def refused(self) -> bool:
        return self.source == "refusal"


REFUSAL = (
    "I can only read case data. I cannot change, approve or reject a decision, a lever, a link, "
    "a coupon or a message, and I do not recommend decisions: an operator does that in the case "
    "view, with a reason in the audit log."
)

# ---- Write requests (LLM-07; adversarial review of 13 Oct, finding 7; rounds 2 and 3) ---------

_EN_WRITE_VERBS = (
    "approve reject deny change modify edit update set apply override delete remove add create "
    "insert cancel block unblock enable disable grant revoke refund send email dispute mark "
    "unmark assign lower raise increase decrease reset close reopen write save confirm flag "
    "unflag restore merge unlink ban allow waive issue move force ignore bypass forget turn "
    "switch stop toggle activate deactivate suspend revert undo rollback dismiss split notify "
    "accept blacklist shut kill proceed keep freeze unfreeze lock unlock restrict lift release "
    "process drop limit whitelist unban erase purge wipe authorize authorise execute finalize "
    "finalise validate pay reimburse"
)
_IT_WRITE_VERBS = (
    "approva approvare rifiuta rifiutare nega negare cambia cambiare modifica modificare aggiorna "
    "aggiornare imposta impostare applica applicare cancella cancellare elimina eliminare rimuovi "
    "rimuovere aggiungi aggiungere crea creare annulla annullare blocca bloccare sblocca "
    "sbloccare attiva attivare disattiva disattivare concedi concedere revoca revocare rimborsa "
    "rimborsare invia inviare manda mandare contesta contestare segna segnare assegna assegnare "
    "abbassa abbassare alza alzare aumenta aumentare diminuisci diminuire riduci ridurre chiudi "
    "chiudere riapri riaprire scrivi scrivere salva salvare conferma confermare ripristina "
    "ripristinare sposta spostare forza forzare togli togliere metti mettere ignora ignorare "
    "dimentica dimenticare spegni spegnere accendi accendere ferma fermare sospendi sospendere "
    "riducete accetta accettare respingi respingere leva levare procedi procedere mantieni "
    "mantenere autorizza autorizzare emetti emettere rilascia rilasciare congela congelare "
    "limita limitare riduciamo"
)
_ITALIAN_INFINITIVES = [w for w in _IT_WRITE_VERBS.split() if w.endswith(("are", "ere", "ire"))]
_IT_INFINITIVES = frozenset([*_ITALIAN_INFINITIVES, "ridurre"])
# Imperatives in the second person plural ("disattivate", "togliete", "rimuovete").
_IT_PLURAL_IMPERATIVES = " ".join(
    w[:-3] + {"a": "ate", "e": "ete", "i": "ite"}[w[-3]] for w in _ITALIAN_INFINITIVES
)


def _stem(infinitive: str) -> str:
    """The stem, with an "h" after c or g of an -are verb ("blocch", "negh")."""
    stem = infinitive[:-3]
    return stem + "h" if infinitive.endswith("are") and stem.endswith(("c", "g")) else stem


def _first_plural(infinitive: str) -> str:
    """The first person plural, also an imperative ("blocchiamo", "togliamo", "disattiviamo")."""
    stem = _stem(infinitive)
    return stem + ("amo" if stem.endswith("i") else "iamo")


def _formal(infinitive: str, plural: bool = False) -> str:
    """The present subjunctive, singular (the formal "Lei" imperative: "approvi", "blocchi",
    "rimuova") or third plural ("disattivino", "rimuovano", as in "si disattivino")."""
    stem = _stem(infinitive)
    if infinitive.endswith("are"):
        ending = "i" if not stem.endswith("i") else ""
        return stem + ending + ("no" if plural else "")
    return stem + ("ano" if plural else "a")


_IT_FIRST_PLURALS = " ".join(_first_plural(w) for w in _ITALIAN_INFINITIVES)
_WRITE_VERBS = word_set(
    f"{_EN_WRITE_VERBS} {_IT_WRITE_VERBS} {_IT_PLURAL_IMPERATIVES} {_IT_FIRST_PLURALS}"
)
# Formal imperatives count only at the start of a clause ("Disattivi i coupon"): elsewhere the
# same words are often nouns or adjectives ("i coupon sono attivi"). Round 3, finding T12.
_FORMAL_IMPERATIVES = word_set(
    " ".join(_formal(w) for w in _ITALIAN_INFINITIVES)
    + " tolga faccia mantenga riduca spenga diminuisca"
)
# Third plural subjunctives, an obligation in an answer ("Si disattivino i coupon").
_SUBJUNCTIVE_PLURALS = word_set(
    " ".join(_formal(w, plural=True) for w in _ITALIAN_INFINITIVES)
    + " tolgano facciano mantengano riducano spengano diminuiscano"
)
# Italian clitic pronouns glued to an imperative or an infinitive ("approvalo", "disattivali",
# "toglili", "mandagli", "disattivarli"), longest first.
_CLITICS = tuple(
    sorted(
        word_set(
            "glielo gliela glieli gliele gliene gli lo la li le ne mi ti ci vi si me te ce ve se"
        ),
        key=lambda clitic: (-len(clitic), clitic),
    )
)


def _write_verb(token: str) -> str | None:
    """The write verb a token is, also with up to two clitic pronouns glued to it."""
    if token in _WRITE_VERBS:
        return token
    stem = token
    for _ in range(2):
        clitic = next((c for c in _CLITICS if stem.endswith(c) and len(stem) - len(c) >= 4), None)
        if clitic is None:
            return None
        stem = stem[: -len(clitic)]
        for candidate in (stem, stem + "e"):  # "disattivar" + "li": the infinitive "disattivare"
            if candidate in _WRITE_VERBS:
                return candidate
    return None


# "okay" is a verb only with an object ("okay the return"), not as "Okay, what is...".
_OBJECT_VERBS = word_set("okay")
_DETERMINERS = word_set(
    "the this that these those it them all every il lo la i gli le questo questa questi queste"
)
# A write verb or participle right after a determiner or a preposition is a noun or an adjective
# ("the refund", "the blocked accounts", "il blocco"), and so is one followed by "of" or by a
# noun it qualifies ("refund status", "merge history"). Italian infinitives stay verbs ("di
# spegnere", "a disattivare"); the article "i" is left out, since it is also the English pronoun
# ("Should I approve"). Round 3, finding T16.
_NOUN_MARKERS = word_set(
    "the a an this that these those its their his her my your our any some no about of for on "
    "in with from at into regarding il lo la gli le l un una uno del della dello dei degli "
    "delle dell al alla allo ai agli alle all sul sulla sullo sui sugli sulle nel nella nello "
    "nei negli nelle questo questa questi queste quel quella quello quei quelle suo sua suoi sue "
    "tuo tua tuoi tue di su con"
)
# Only nouns that are hardly ever the object of a write verb: "Change type" or "Reset count"
# stay requests.
_NOUN_HEADS = word_set(
    "status deadline deadlines date dates amount amounts history reason reasons timing details "
    "detail info information of"
)


def _noun_use(tokens: Sequence[str], index: int) -> bool:
    token = tokens[index]
    if token in _IT_INFINITIVES:
        return False
    before = tokens[index - 1] if index > 0 else ""
    following = tokens[index + 1] if index + 1 < len(tokens) else ""
    return before in _NOUN_MARKERS or following in _NOUN_HEADS


# Past participles ask for a change ("Sara wants coupons disabled") unless a state auxiliary comes
# right before them ("were disabled", "è stato approvato"): then they describe the case.
_EN_PARTICIPLES = (
    "approved rejected denied changed modified updated overridden deleted removed cancelled "
    "canceled blocked unblocked enabled disabled granted revoked refunded reset lowered raised "
    "reopened flagged unflagged restored merged unlinked banned waived forced bypassed turned "
    "switched stopped toggled activated deactivated suspended reverted dismissed notified "
    "accepted blacklisted okayed killed frozen unfrozen whitelisted unbanned purged wiped erased "
    "authorized authorised"
)
# Italian participles of the -are and -ire verbs ("disattivati", "approvato"), and a few irregular.
_IT_PARTICIPLES = " ".join(
    w[:-3] + {"a": "at", "i": "it"}[w[-3]] + ending
    for w in _ITALIAN_INFINITIVES
    if w[-3] in "ai"
    for ending in "oaie"
)
_IT_IRREGULAR_PARTICIPLES = (
    "rimosso rimossa rimossi rimosse concesso concessa concessi concesse tolto tolta tolti tolte "
    "sospeso sospesa sospesi sospese spento spenta spenti spente respinto respinta respinti "
    "respinte emesso emessa emessi emesse ridotto ridotta ridotti ridotte"
)
_WRITE_PARTICIPLES = word_set(f"{_EN_PARTICIPLES} {_IT_PARTICIPLES} {_IT_IRREGULAR_PARTICIPLES}")
_STATE_AUXILIARIES = word_set(
    "was were is are been being has have had got e stato stata stati state sono era erano ha "
    "hanno ho abbiamo viene vengono"
)
_REQUEST_MARKERS = re.compile(
    r"\b(?:can you|could you|would you|will you|please|i want you|i need you|is it possible|"
    r"can we|could we|let s|puoi|potresti|riesci|per favore|per piacere|vorrei che|devi|"
    r"dovresti|e possibile|si puo)\b"
)
_QUESTION_WORDS = word_set(
    "what which why how who when where whose does did is are was were has have had "
    "cosa che quale quali perche come chi quando dove quanto quanti quante qual cos"
)
# Auxiliaries open a question only in a sentence that ends with "?": "Have the coupons been
# disabled?" is a question, "Have the coupons disabled." is a request.
_AUX_QUESTION_WORDS = word_set("does did is are was were has have had")
# Sentences end at a terminator that is not a dot between two letters ("dis.able" is one word):
# round 3, finding T15.
_QUESTION_SENTENCE_RE = re.compile(r"(?:[^.?!;\n]|(?<=[^\W\d_])\.(?=[^\W\d_]))+[.?!;\n]*")
# Clauses of a sentence: a write verb that opens one is a request whatever question word came
# before ("Tell me what this case is and approve it", "What is this case - approve it"). Split
# before accents are folded, so "è" is not "e". Round 3, finding T12.
_QUESTION_CLAUSE_RE = re.compile(
    r"[,:;]|\s[-‐‑‒–—―]+\s|[–—―]|\b(?:and|then|so|also|e|poi|quindi|anche)\b", re.IGNORECASE
)


# The agent never recommends a decision (spec 8.8): advice in an answer is not verified.
_ADVICE_RE = re.compile(
    r"\b(?:recommend\w*|suggest\w*|advis\w*|you should|you ought|consigli\w*|suggerisc\w*"
    r"|raccomand\w*|dovresti|dovreste)\b"
)


def advice(text: str) -> list[str]:
    """Recommendations in an answer ("you should", "I recommend", "consiglio")."""
    return [m.group() for m in _ADVICE_RE.finditer(" ".join(words(text)))]


def _first_write_cue(tokens: Sequence[str]) -> int | None:
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if _noun_use(tokens, index):
            continue
        if _write_verb(token) is not None:
            return index
        if token in _OBJECT_VERBS and following in _DETERMINERS:
            return index
        if token in _WRITE_PARTICIPLES and (
            index == 0 or tokens[index - 1] not in _STATE_AUXILIARIES
        ):
            return index
    return None


def _sentence_requests(tokens: Sequence[str], asked: bool) -> bool:
    cue = _first_write_cue(tokens)
    if cue is None:
        return False
    joined = " ".join(tokens)
    marker = max(
        (
            joined[: m.end()].count(" ")
            for m in _REQUEST_MARKERS.finditer(joined)
            if joined[: m.end()].count(" ") < cue
        ),
        default=-1,
    )
    question_word = max(
        (
            i
            for i in range(cue)
            if tokens[i] in _QUESTION_WORDS and (asked or tokens[i] not in _AUX_QUESTION_WORDS)
        ),
        default=-1,
    )
    return question_word < 0 or marker >= question_word


_SOFTENERS = word_set(
    "please now just simply kindly also then so ok okay go ahead per favore adesso ora quindi "
    "allora anche subito"
)


def _opening_verb(tokens: Sequence[str]) -> str | None:
    """The write verb that opens a clause, after softeners: an imperative or an infinitive,
    also formal or with clitics ("Approvalo", "Disattivi i coupon"); never a noun use."""
    start = 0
    while start < len(tokens) and tokens[start] in _SOFTENERS:
        start += 1
    if start >= len(tokens) or _noun_use(tokens, start):
        return None
    token = tokens[start]
    following = tokens[start + 1] if start + 1 < len(tokens) else ""
    if _write_verb(token) is not None or token in _FORMAL_IMPERATIVES:
        return token
    return token if token in _OBJECT_VERBS and following in _DETERMINERS else None


def is_write_request(question: str) -> bool:
    """True when a sentence of the question asks to change something (or to ignore the rules):
    it names a write verb, or a participle that asks for a change, and no question word comes
    between the last request marker ("can you", "please") and that verb; or a clause of it opens
    with a write verb, whatever came before ("What is this case, then approve it"). "Why was it
    disabled?", "Can you tell me why it was disabled?" and "Tell me about the refund" are
    questions; "Turn off coupons", "Can the coupons be turned off?", "Sara wants coupons
    disabled", "What is this case? Approve it.", "Disattivali" and "Approvi il reso" are
    requests. Every letter form is checked: digits read as letters, dashes and dots joined."""
    text = unicodedata.normalize("NFKC", question)
    for match in _QUESTION_SENTENCE_RE.finditer(text):
        sentence = match.group()
        asked = "?" in sentence
        if any(_sentence_requests(form.split(), asked) for form in forms(sentence, leet=True)):
            return True
        for clause in _QUESTION_CLAUSE_RE.split(sentence):
            if any(_opening_verb(form.split()) for form in forms(clause, leet=True)):
                return True
    return False


# ---- Actions in an answer (round 2, finding R4; round 3, findings T13 and T15) ----------------

# An answer never acts, asks to act or says it acted: a write verb that opens a clause ("Turn off
# coupons", "Go ahead and refund", "Disattivali"), a write verb after a modal ("you can
# disable"), an obligation ("must be disabled", "are to be disabled", "need disabling", "make
# sure", "sono da disattivare", "si disattivino"), a completion ("Done", "now off", "as
# requested", "carried out") or a first-person subject with a write verb or participle anywhere
# after it in the sentence ("I have already disabled", "I went ahead and froze", "Li ho appena
# disattivati") sends the answer to the fallback.
_ANSWER_STOPS = r"[;:!?\n]|(?<![^\W\d_])\.|\.(?![^\W\d_])"
_ANSWER_SENTENCE_SPLIT_RE = re.compile(rf"(?:{_ANSWER_STOPS})+")
_ANSWER_CLAUSE_RE = re.compile(
    rf"(?:{_ANSWER_STOPS}|,)+|\s[-‐‑‒–—―]+\s|\b(?:and|then|e|poi)\b", re.IGNORECASE
)
_MODALS = word_set(
    "to can could may might will would shall should must let lets puoi potete possiamo devi "
    "dovete dobbiamo"
)
_OBLIGATION_RE = re.compile(
    r"\b(?:must|need to|needs to|needed to|have to|has to|had to|should|ought|required to"
    r"|better to|bisogna|occorre|vanno|meglio|deve|devono|dovrebbe|dovrebbero|necessari[oa]"
    r"|andrebbe|andrebbero|conviene|converrebbe|make sure|ensure|assicura\w*|fai in modo"
    r"|fate in modo|ricorda\w* di|(?:need|needs|needed|require|requires|required)"
    r" (?!nothing\b|something\b|anything\b|everything\b)\w+ing)\b"
)
_COMPLETION_RE = re.compile(
    r"\b(?:done|fatto|fatta|completed|completat[oaie]|eseguit[oaie]|as requested|as you asked"
    r"|as asked|carried out|went ahead|gone ahead|all set|come richiesto|come chiesto"
    r"|come da richiesta|provveduto|riuscit[oaie]"
    r"|now (?:off|on|disabled|enabled|active|inactive|removed|blocked|unblocked|approved|rejected)"
    r"|ora (?:disattivat|attivat|spent|accesi|rimoss|bloccat|approvat)\w*)\b"
)
# Past forms an agent could claim, beyond the participles that ask for a change.
_EN_PAST_FORMS = word_set(
    "froze sent paid issued set closed applied added created saved confirmed marked assigned "
    "locked unlocked restricted lifted released processed dropped limited executed finalized "
    "finalised validated reimbursed emailed disputed kept shut edited moved inserted unmarked "
    "increased decreased written undone split ignored cleared"
)
_EN_ING_FORMS = frozenset(
    form
    for verb in _EN_WRITE_VERBS.split()
    for form in (verb + "ing", verb[:-1] + "ing", verb + verb[-1] + "ing")
)
_FIRST_PERSON = word_set("i we")
# Words that follow "I" or "we" as a subject ("I have", "I've", "we just"), never the Italian
# article "i" ("i coupon").
_FIRST_PERSON_NEXT = word_set(
    "have ve had d ll will would just already went did do am m also then now successfully "
    "personally can could was were"
)
_ITALIAN_FIRST_PERSON = word_set("ho abbiamo")


def _acted(token: str) -> bool:
    """A write verb, participle or past form, in either language."""
    return (
        _write_verb(token) is not None
        or token in _WRITE_PARTICIPLES
        or token in _EN_PAST_FORMS
        or token in _EN_ING_FORMS
    )


def _clause_actions(tokens: Sequence[str]) -> list[str]:
    found: list[str] = []
    opening = _opening_verb(tokens)
    if opening is not None:
        found.append(opening)
    for index in range(1, len(tokens)):
        token, before = tokens[index], tokens[index - 1]
        if before in _MODALS and _write_verb(token) is not None:
            found.append(f"{before} {token}")
    return found


def _sentence_actions(tokens: Sequence[str]) -> list[str]:
    """A first-person subject with a write form anywhere after it, "to be" with a participle,
    "da" with a write infinitive, a third plural subjunctive, and "now" with a participle."""
    found: list[str] = []
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        subject = token in _ITALIAN_FIRST_PERSON or (
            token in _FIRST_PERSON and (following in _FIRST_PERSON_NEXT or _acted(following))
        )
        verb = (
            next((j for j in range(index + 1, len(tokens)) if _acted(tokens[j])), None)
            if subject
            else None
        )
        if verb is not None:
            span = tokens[index : verb + 1]
            found.append(" ".join(span) if len(span) <= 4 else f"{token} ... {tokens[verb]}")
        after = tokens[index + 1 : index + 4]
        if token == "to" and after[:1] == ["be"] and after[1:2] and _acted(after[1]):
            found.append(f"to be {after[1]}")
        if token == "da" and after[:1] and after[0] in _IT_INFINITIVES:
            found.append(f"da {after[0]}")
        if token in _SUBJUNCTIVE_PLURALS:
            found.append(token)
        rest = [t for t in after if t not in ("been", "be", "is", "are")]
        if token == "now" and rest[:1] and rest[0] in _WRITE_PARTICIPLES:
            found.append(f"now {rest[0]}")
    return found


def answer_actions(text: str) -> list[str]:
    """Imperatives, obligations and claims of an action taken, in every letter form; dots
    between two letters join a word ("Dis.able")."""
    found: list[str] = []
    normal = unicodedata.normalize("NFKC", text)
    for clause in _ANSWER_CLAUSE_RE.split(normal):
        for form in forms(clause, leet=True):
            found += [cue for cue in _clause_actions(form.split()) if cue not in found]
    for sentence in _ANSWER_SENTENCE_SPLIT_RE.split(normal):
        for form in forms(sentence, leet=True):
            found += [cue for cue in _sentence_actions(form.split()) if cue not in found]
    for form in forms(text, leet=True):
        for pattern in (_OBLIGATION_RE, _COMPLETION_RE):
            found += [m.group() for m in pattern.finditer(form) if m.group() not in found]
    return found


# ---- Tools --------------------------------------------------------------------------------------


class ToolLimitReached(RuntimeError):
    pass


def to_json(value: Any) -> Any:
    """Tool results as JSON-ready data: dataclasses as objects, dates in ISO format."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [to_json(v) for v in value]
    return value


class ToolBox:
    """The five read-only tools bound to one case, with the call budget and the evidence log."""

    def __init__(self, reader: CaseReader, case_id: str, references: Mapping[str, str]) -> None:
        self._reader = reader
        self.case_id = case_id
        self.references = dict(references)
        self.calls = 0
        self.consulted: list[str] = []
        self.missing: list[str] = []
        self.refs: list[Ref] = []
        self._clusters: set[str] = set(self.references.values())

    @property
    def remaining(self) -> int:
        return MAX_TOOL_CALLS - self.calls

    def _use(self, label: str) -> None:
        if self.calls >= MAX_TOOL_CALLS:
            raise ToolLimitReached(f"At most {MAX_TOOL_CALLS} tool calls")
        self.calls += 1
        self.consulted.append(label)

    def miss(self, what: str) -> None:
        if what not in self.missing:
            self.missing.append(what)

    def _ref(self, ref: Ref) -> None:
        if ref not in self.refs:
            self.refs.append(ref)

    def get_case(self) -> CaseRecord | None:
        self._use(f"get_case({self.case_id})")
        record = self._reader.get_case(self.case_id)
        if record is None:
            self.miss(f"case {self.case_id}: not found")
        elif record.cluster_id is not None:
            self._clusters.add(record.cluster_id)
        return record

    def get_orders(self) -> tuple[OrderRecord, ...]:
        self._use(f"get_orders({self.case_id})")
        orders = tuple(self._reader.get_orders(self.case_id))
        if not orders:
            self.miss(f"orders of case {self.case_id}: none on file")
        for order in orders:
            self._ref(Ref("order", order.order_id))
        return orders

    def get_cluster(self, cluster_id: str) -> ClusterRecord | None:
        self._use(f"get_cluster({cluster_id})")
        if cluster_id not in self._clusters:
            self.miss(f"cluster {cluster_id}: not readable (only the case cluster and references)")
            return None
        record = self._reader.get_cluster(cluster_id)
        if record is None:
            self.miss(f"cluster {cluster_id}: not found")
            return None
        self._ref(Ref("cluster", record.cluster_id))
        for link in record.links:
            self._ref(Ref("link", link.link_id))
        return record

    def get_score_factors(self) -> tuple[FactorRecord, ...]:
        self._use(f"get_score_factors({self.case_id})")
        factors = tuple(self._reader.get_score_factors(self.case_id))
        if not factors:
            self.miss(f"score factors of case {self.case_id}: none (not scored)")
        return factors

    def get_policy_text(self) -> PolicyRecord | None:
        self._use(f"get_policy_text({self.case_id})")
        record = self._reader.get_policy_text(self.case_id)
        if record is None:
            self.miss("merchant policy text: not found")
        return record

    def dispatch(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Run a tool requested by the model; anything else is refused but still counted."""
        if name not in TOOL_NAMES:
            self._use(f"{name}: refused")
            return {"error": "unknown tool: this agent can only read case data"}
        if name == "get_cluster":
            return to_json(self.get_cluster(str(arguments.get("cluster_id", ""))))
        tool = getattr(self, name)
        return to_json(tool())


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Read-only. {description}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }
    for name, description, properties in (
        ("get_case", "The decided case: path, quadrant, levers, order and cluster.", {}),
        ("get_orders", "Orders of the case customer with their terms snapshot.", {}),
        (
            "get_cluster",
            "A cluster of linked accounts with its links: the case cluster or a reference.",
            {"cluster_id": {"type": "string"}},
        ),
        ("get_score_factors", "The top 3 contributions to the risk score.", {}),
        ("get_policy_text", "The merchant return policy text and version.", {}),
    )
]


# ---- Deterministic answers (mock agent) ---------------------------------------------------------


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def describe_terms(terms: ReturnTerms) -> str:
    shipping = "free" if terms.free_return_shipping else "paid by the customer"
    return (
        f"return window {terms.return_window_days} days; return shipping {shipping}; "
        f"instant refund {_yes_no(terms.instant_refund)}; "
        f"free size exchange {_yes_no(terms.free_size_exchange)}; "
        f"coupons {'enabled' if terms.coupons_enabled else 'disabled'}"
    )


def _number(value: float | int | None) -> str:
    return "not available" if value is None else f"{value:g}"


_COMPARED: tuple[tuple[str, str], ...] = (
    ("size", "linked accounts"),
    ("n_distinct_cards", "distinct cards"),
    ("creation_spread_days", "spread of account creation dates (days)"),
    ("welcome_coupons_per_account", "welcome coupons per account"),
    ("cluster_return_rate", "cluster return rate"),
    ("strong_id_share", "share of strong identifiers shared"),
)


def _link_evidence(tb: ToolBox) -> tuple[str, list[Ref]]:
    case = tb.get_case()
    if case is None:
        return f"Case {tb.case_id} was not found.", []
    if case.cluster_id is None:
        tb.miss("linked accounts: none on file for this case")
        return "No linked accounts are on file for this case, so no link evidence applies.", []
    cluster = tb.get_cluster(case.cluster_id)
    if cluster is None:
        return f"Cluster {case.cluster_id} could not be read.", []
    lines = [
        f"Cluster {cluster.cluster_id}: {cluster.size} linked accounts "
        f"({cluster.nature} data, as of {cluster.as_of})."
    ]
    for link in cluster.links:
        disputed = " Disputed by an operator." if link.disputed else ""
        lines.append(
            f"- Link {link.link_id}: {link.account_a} and {link.account_b} share "
            f"{', '.join(link.kinds)}; first shared on {link.first_shared_on}, last on "
            f"{link.last_shared_on}; {link.n_shared_events} shared events; weight "
            f"{link.weight:g}.{disputed}"
        )
    if not cluster.links:
        tb.miss(f"links of cluster {cluster.cluster_id}: none on file")
    lines.append("A link shows identifiers shared by accounts: evidence to verify, not a verdict.")
    return "\n".join(lines), [Ref("link", link.link_id) for link in cluster.links]


def _compare_reference(tb: ToolBox, name: str) -> tuple[str, list[Ref]]:
    reference_id = tb.references[name]
    case = tb.get_case()
    if case is None:
        return f"Case {tb.case_id} was not found.", []
    if case.cluster_id is None:
        tb.miss("linked accounts: none on file for this case")
        return (
            f"This case has no linked accounts, so there is no cluster to compare with the "
            f"{name.title()} reference ({reference_id}).",
            [],
        )
    mine = tb.get_cluster(case.cluster_id)
    reference = tb.get_cluster(reference_id)
    if mine is None or reference is None:
        return "The comparison needs both clusters; see the missing evidence.", []
    lines = [
        f"Cluster {mine.cluster_id} ({mine.nature} data, as of {mine.as_of}) compared with the "
        f"{name.title()} reference cluster {reference.cluster_id} ({reference.nature} data, as "
        f"of {reference.as_of}):"
    ]
    for attribute, label in _COMPARED:
        here, there = getattr(mine, attribute), getattr(reference, attribute)
        lines.append(f"- {label}: {_number(here)} here, {_number(there)} in {reference_id}")
    lines.append(
        "These are cluster features of the risk model: they describe the accounts and "
        "their links, they are not a verdict."
    )
    return "\n".join(lines), [Ref("cluster", mine.cluster_id), Ref("cluster", reference_id)]


def _purchase_terms(tb: ToolBox) -> tuple[str, list[Ref]]:
    case = tb.get_case()
    if case is None:
        return f"Case {tb.case_id} was not found.", []
    order = next((o for o in tb.get_orders() if o.order_id == case.order_id), None)
    if order is None:
        tb.miss(f"terms snapshot of order {case.order_id}: not found")
        return f"The terms snapshot of order {case.order_id} is not on file.", []
    policy = tb.get_policy_text()
    text = (
        f"Order {order.order_id}, placed on {order.placed_on}, was sold with terms snapshot "
        f"{order.terms_snapshot_id} (source: {order.snapshot_source}): "
        f"{describe_terms(order.terms)}. These terms hold for this order whatever is decided "
        "now: restrictive levers apply only to future orders, shown at checkout."
    )
    if policy is not None:
        text += f" Merchant policy version: {policy.version}."
    return text, [Ref("order", order.order_id)]


def _summary(tb: ToolBox) -> tuple[str, list[Ref]]:
    case = tb.get_case()
    if case is None:
        return f"Case {tb.case_id} was not found.", []
    factors = tb.get_score_factors()
    quadrant = case.quadrant or "no quadrant"
    lines = [
        f"Case {case.case_id}: {case.path} path, {quadrant}, evidence {case.evidence_quality} "
        f"({case.nature} data, as of {case.as_of})."
    ]
    lines += [f"- Score factor {f.feature} = {f.value:g}: {f.phrase}" for f in factors]
    lines.append(
        "I can also answer which evidence supports the link, how this case differs from a "
        "reference cluster, and which terms applied when the order was bought."
    )
    return "\n".join(lines), []


def _reference_named(question: str, references: Mapping[str, str]) -> str | None:
    tokens = set(words(question))
    return next((name for name in references if name in tokens), None)


def _intent(question: str) -> str:
    tokens = words(question)
    terms = any(t.startswith(("condizion", "termin", "terms", "snapshot")) for t in tokens)
    bought = any(
        t.startswith(("acquist", "comprat", "bought", "purchas", "ordin", "order")) for t in tokens
    )
    if terms and bought:
        return "purchase_terms"
    if any(t.startswith(("collegament", "legam", "link")) for t in tokens):
        return "link_evidence"
    return "summary"


def _refusal(question: str) -> AgentAnswer:
    return AgentAnswer(question, REFUSAL, "refusal", 0, (), (), ())


class MockAgent:
    """Deterministic agent for LLM_MODE=mock and the demo: tool results only, no model."""

    def __init__(
        self, reader: CaseReader, references: Mapping[str, str] = DEFAULT_REFERENCES
    ) -> None:
        self._reader = reader
        self._references = dict(references)

    def ask(self, case_id: str, question: str) -> AgentAnswer:
        if is_write_request(question):
            return _refusal(question)
        tb = ToolBox(self._reader, case_id, self._references)
        reference = _reference_named(question, self._references)
        if reference is not None:
            text, links = _compare_reference(tb, reference)
        else:
            intent = _intent(question)
            if intent == "purchase_terms":
                text, links = _purchase_terms(tb)
            elif intent == "link_evidence":
                text, links = _link_evidence(tb)
            else:
                text, links = _summary(tb)
        return AgentAnswer(
            question, text, "mock", tb.calls, tuple(tb.consulted), tuple(tb.missing), tuple(links)
        )


# ---- Gemma tool calling (vLLM) ------------------------------------------------------------------


def _parse_call(call: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    function = call.get("function")
    if not isinstance(function, Mapping):
        return "", {}
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except (TypeError, ValueError):
        arguments = {}
    return str(function.get("name", "")), arguments if isinstance(arguments, dict) else {}


def _collect(value: Any, numbers: list[Decimal], ids: list[str]) -> None:
    """Numbers and identifier-like strings (no space, at most 64 characters, such as ids and
    ISO dates) of JSON-ready tool results; booleans and nulls carry neither."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            _collect(item, numbers, ids)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        numbers.append(Decimal(str(value)))
    elif isinstance(value, str) and len(value) <= 64 and " " not in value:
        ids.append(value)


# Records of the tool results, by the first key of their JSON object: a number in a clause that
# names a record id must be a value of that record (round 2, finding R4: "Link L-102 has 1 shared
# event" is not grounded by the cluster's 1.0 welcome coupons per account).
_RECORD_KINDS = {
    "case_id": "case",
    "order_id": "order",
    "cluster_id": "cluster",
    "link_id": "link",
    "feature": "factor",
    "version": "policy",
}
# Words that bring a kind of record into a clause, and whether they name one record ("the
# disputed link", "the cluster") or several ("the links", "linked accounts"). A clause with only
# a kind word is scoped to the records of that kind (round 3, finding T14).
_KIND_WORDS: dict[str, tuple[str, bool]] = {
    "case": ("case", True),
    "caso": ("case", True),
    "order": ("order", True),
    "ordine": ("order", True),
    "orders": ("order", False),
    "ordini": ("order", False),
    "terms": ("order", False),
    "cluster": ("cluster", True),
    "clusters": ("cluster", False),
    "account": ("cluster", False),
    "accounts": ("cluster", False),
    "link": ("link", True),
    "links": ("link", False),
    "collegamento": ("link", True),
    "collegamenti": ("link", False),
    "legame": ("link", True),
    "legami": ("link", False),
    "factor": ("factor", True),
    "factors": ("factor", False),
    "score": ("factor", True),
    "policy": ("policy", True),
}
# Words that bind a number to a field of a record: the number written next to them must be that
# field of a record in scope ("7 linked accounts" is a cluster size, "weight 0.6" a link weight).
# Round 3, finding T14.
_CUES: tuple[tuple[tuple[str, ...], str], ...] = tuple(
    sorted(
        [
            (tuple(cue.split()), field)
            for field, cues in (
                ("size", ("linked accounts", "accounts linked", "account collegati", "accounts")),
                ("n_distinct_cards", ("distinct cards", "carte distinte", "cards", "carte")),
                (
                    "n_shared_events",
                    ("shared events", "shared event", "eventi condivisi", "events", "event"),
                ),
                ("weight", ("weight", "peso")),
                ("_days", ("days", "day", "giorni", "giorno")),
                ("return_rate", ("return rate", "tasso di reso")),
            )
            for cue in cues
        ],
        key=lambda item: -len(item[0]),
    )
)
# Words a cue may stand before a number with ("weight of 0.6", "accounts linked: 9").
_CUE_FILLERS = frozenset([":", "=", "of", "is", "are", "was", "were", "di", "e", "at", "pari", "a"])
_CUE_STOPS = frozenset(",;.()[]")
_ANSWER_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_GROUNDING_CLAUSE_RE = re.compile(r"([;:()\[\]]|,(?=\s))")
# A sentence that opens with a pronoun goes on about the record of the sentence before.
_PRONOUN_RE = re.compile(r"\s*(?:it|its|they|their|esso|essa|essi|esse)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Record:
    kind: str
    id: str | None
    aliases: tuple[str, ...]  # a link is also named by its two accounts
    fields: tuple[tuple[str, Decimal], ...]  # its own numbers, by field name

    @property
    def numbers(self) -> tuple[Decimal, ...]:
        return tuple(value for _, value in self.fields)

    def values(self, field: str) -> list[Decimal]:
        """The values of a field; a field starting with "_" matches any field ending with it."""
        return [v for name, v in self.fields if name == field or name.endswith(field)]

    def named_in(self, clause: str) -> bool:
        if self.id is not None and _names(clause, self.id):
            return True
        return bool(self.aliases) and all(_names(clause, alias) for alias in self.aliases)


def _record_kind(value: Any) -> str | None:
    return _RECORD_KINDS.get(next(iter(value), "")) if isinstance(value, dict) else None


def _own_fields(
    value: Any, key: str, fields: list[tuple[str, Decimal]], *, nested: bool = False
) -> None:
    """Numbers of a record by field name, without those of the records nested in it (a
    cluster's links); nested plain objects (an order's terms) count as its own."""
    if nested and _record_kind(value) is not None:
        return
    if isinstance(value, dict):
        for name, item in value.items():
            _own_fields(item, name, fields, nested=True)
    elif isinstance(value, list):
        for item in value:
            _own_fields(item, key, fields, nested=True)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        fields.append((key, Decimal(str(value))))


def _records(value: Any, found: list[_Record]) -> None:
    kind = _record_kind(value)
    if kind is not None:
        key = next(iter(value))
        fields: list[tuple[str, Decimal]] = []
        _own_fields(value, key, fields)
        accounts = (value.get("account_a"), value.get("account_b")) if kind == "link" else ()
        found.append(
            _Record(
                kind,
                str(value[key]) if key.endswith("_id") else None,
                tuple(str(a) for a in accounts if isinstance(a, str)),
                tuple(fields),
            )
        )
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            _records(item, found)


def _matches(written: Decimal, numbers: Sequence[Decimal]) -> bool:
    exponent = written.as_tuple().exponent
    if isinstance(exponent, int) and exponent < 0:
        step = Decimal(1).scaleb(exponent)
        return any(n.quantize(step, rounding=ROUND_HALF_UP) == written for n in numbers)
    return any(n == written for n in numbers)


def _names(clause: str, record_id: str) -> bool:
    pattern = rf"(?<![\w-]){re.escape(record_id)}(?![\w-])"
    return re.search(pattern, unicodedata.normalize("NFKC", clause), re.IGNORECASE) is not None


def _cue_after(rest: str) -> str | None:
    match = re.match(r"\s*((?:[^\W\d_]+\s+){0,2}[^\W\d_]+)", rest)
    following = tuple(match.group(1).split()) if match else ()
    return next((f for cue, f in _CUES if following[: len(cue)] == cue), None)


def _cue_before(text: str) -> str | None:
    tokens = re.findall(r"[^\W\d_]+|[^\w\s]", text)
    end, skipped = len(tokens), 0
    while end > 0 and tokens[end - 1] in _CUE_FILLERS and skipped < 2:
        end, skipped = end - 1, skipped + 1
    if end == 0 or tokens[end - 1] in _CUE_STOPS:
        return None
    before = tuple(tokens[:end])
    return next((f for cue, f in _CUES if before[len(before) - len(cue) :] == cue), None)


@dataclass(frozen=True)
class _Evidence:
    numbers: tuple[Decimal, ...]
    ids: tuple[str, ...]
    records: tuple[_Record, ...]


def _evidence(results: Sequence[Any], case_id: str) -> _Evidence:
    numbers: list[Decimal] = []
    ids: list[str] = [case_id]
    records: list[_Record] = []
    for result in results:
        _collect(result, numbers, ids)
        _records(result, records)
    return _Evidence(tuple(numbers), tuple(ids), tuple(records))


class _Scope:
    """The records the clauses of a sentence talk about, and, when they talk about one record,
    the records that can still be it after the numbers bound to fields so far."""

    def __init__(self) -> None:
        self.records: list[_Record] | None = None  # None: any number of the results
        self.single = False
        self.candidates: set[int] | None = None

    def update(self, clause: str, plain: str, evidence: _Evidence) -> None:
        """A clause that names records, or kinds of record the results have, sets the scope."""
        named = [r for r in evidence.records if r.named_in(clause)]
        read = {r.kind for r in evidence.records}
        kinds: dict[str, bool] = {}
        for word in words(plain):
            if word in _KIND_WORDS and _KIND_WORDS[word][0] in read:
                kind, one = _KIND_WORDS[word]
                kinds[kind] = kinds.get(kind, False) or one
        for record in named:
            kinds.pop(record.kind, None)
        if named or kinds:
            self.records = named + [r for r in evidence.records if r.kind in kinds]
            self.single = (len(named) == 1 and not kinds) or (
                not named and len(kinds) == 1 and all(kinds.values())
            )
            self.candidates = None

    def grounds(self, value: Decimal, field: str | None, evidence: _Evidence) -> bool:
        """A number next to a field word is that field of a record in scope (of any record when
        none in scope has the field); a field no record has does not bind the number."""
        scope = list(evidence.records) if self.records is None else self.records
        if field is not None and not any(r.values(field) for r in evidence.records):
            field = None
        if field is None:
            pool = (
                evidence.numbers if self.records is None else [n for r in scope for n in r.numbers]
            )
            return _matches(value, pool)
        in_scope = [r for r in scope if r.values(field)]
        candidates = in_scope or [r for r in evidence.records if r.values(field)]
        hits = {id(r) for r in candidates if _matches(value, r.values(field))}
        if not hits:
            return False
        if self.single and in_scope:
            self.candidates = hits if self.candidates is None else self.candidates & hits
            return bool(self.candidates)
        return True


def grounded_in(answer: str, results: Sequence[Any], case_id: str) -> bool:
    """Every number in the answer is a number of the tool results: an integer as written must
    be equal to one, a decimal may be one rounded to the decimals written. Identifiers and dates
    of the results are removed first; a number with a scale suffix, a sign or a sign word, or
    written in letters or Roman numerals, is never grounded. From the first clause of a sentence
    that names a record (a link by its id or its two accounts, a cluster, an order, the case) or
    a kind of record read ("the cluster", "the disputed link"), numbers must be values of those
    records; a sentence that opens with a pronoun goes on with them. A number next to a field
    word ("7 linked accounts", "weight 0.6", "9 distinct cards", "4 shared events", "9 days",
    "return rate 0.9") must be that field of such a record, and of one same record when the
    clauses talk about one ("The disputed link has weight 0.6 and 4 shared events" is not)."""
    evidence = _evidence(results, case_id)
    text = strip_tokens(answer, evidence.ids)
    if number_modifiers(text) or number_words(text, "en", skip=ANSWER_SKIPPED_NUMBER_WORDS):
        return False
    if roman_numerals(text):
        return False
    scope = _Scope()
    for sentence in _ANSWER_SENTENCE_RE.split(answer):
        if _PRONOUN_RE.match(sentence) is None:
            scope = _Scope()
        before = ""
        parts = _GROUNDING_CLAUSE_RE.split(sentence)
        for clause, separator in zip(parts[::2], [*parts[1::2], ""], strict=True):
            plain = fold(strip_tokens(clause, evidence.ids))
            scope.update(clause, plain, evidence)
            for value, start, end in signed_numbers(plain):
                field = _cue_after(plain[end:]) or _cue_before(before + plain[:start])
                if not scope.grounds(value, field, evidence):
                    return False
            before += plain + separator
    return True


class VllmAgent:
    """Gemma tool calling over the same read-only tools, with the same limits."""

    def __init__(
        self,
        client: ChatClient,
        reader: CaseReader,
        references: Mapping[str, str] = DEFAULT_REFERENCES,
    ) -> None:
        self._client = client
        self._reader = reader
        self._references = dict(references)

    def ask(self, case_id: str, question: str) -> AgentAnswer:
        if is_write_request(question):
            return _refusal(question)
        tb = ToolBox(self._reader, case_id, self._references)
        results: list[Any] = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": agent_system_prompt(MAX_TOOL_CALLS, self._references)},
            {"role": "user", "content": f"Case {case_id}. Question: {question}"},
        ]
        content: str | None = None
        # Bounded: every turn either gives the answer or runs at least one tool call through
        # the ToolBox, which allows MAX_TOOL_CALLS; after that, tool_choice "none" asks for
        # the answer. So there are at most MAX_TOOL_CALLS + 1 turns.
        while content is None:
            final = tb.remaining == 0
            try:
                message = self._client.chat(
                    messages, tools=TOOLS, tool_choice="none" if final else "auto"
                )
            except LlmUnavailable:
                return self._fallback(question, tb, "the model is unavailable")
            raw_calls = message.get("tool_calls") or []
            calls = (
                [c for c in raw_calls if isinstance(c, Mapping)]
                if isinstance(raw_calls, list)
                else []
            )
            if final or not calls:
                content = str(message.get("content") or "")
                continue
            messages.append(
                {"role": "assistant", "content": message.get("content") or "", "tool_calls": calls}
            )
            for call in calls:
                if tb.remaining == 0:
                    result: Any = {"error": f"limit of {MAX_TOOL_CALLS} tool calls reached"}
                else:
                    name, arguments = _parse_call(call)
                    result = tb.dispatch(name, arguments)
                    results.append(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id", "")),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        if not content.strip():
            return self._fallback(question, tb, "the model gave no answer")
        if (
            banned_terms(strip_tokens(content, _evidence(results, case_id).ids))
            or decision_wording(content)
            or advice(content)
            or answer_actions(content)
            or foreign_characters(content)
            or not grounded_in(content, results, case_id)
        ):
            return self._fallback(question, tb, "the answer could not be verified")
        links = tuple(ref for ref in tb.refs if ref.id in content)
        return AgentAnswer(
            question, content, "model", tb.calls, tuple(tb.consulted), tuple(tb.missing), links
        )

    @staticmethod
    def _fallback(question: str, tb: ToolBox, reason: str) -> AgentAnswer:
        consulted = ", ".join(tb.consulted) or "none"
        text = f"No verified answer: {reason}. Evidence consulted: {consulted}."
        return AgentAnswer(
            question,
            text,
            "fallback",
            tb.calls,
            tuple(tb.consulted),
            tuple(tb.missing),
            tuple(tb.refs),
        )
