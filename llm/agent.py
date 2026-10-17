"""Read-only investigator agent (US-701, US-702, docs/spec.md 8.8, test LLM-07).

- Five read-only tools over an injected data-access protocol (CaseReader): get_case, get_orders,
  get_cluster, get_score_factors, get_policy_text. The protocol has no write method.
- At most MAX_TOOL_CALLS tool calls per question, counted by the ToolBox; unknown tools count too.
- A request to change anything is refused before any tool or model call. Each sentence of the
  question is checked on its own ("What is this case? Approve it." is a request), and so is each
  clause that opens with a write verb ("Tell me what this case is and approve it", "..., but
  approve it anyway", "(approve it)"), each bare imperative with an object ("..., if none remove
  L-102") and "why don't you" act, also with digits read as letters, dashes, dots, ellipses and
  ASCII punctuation joined, format characters removed ("appr0ve", "dis-able", "dis…able"),
  Italian clitics ("Disattivali", "Li disattivi") and formal imperatives before an object
  ("Approvi il reso"). A write word is read as a noun only in a question, a sentence that ends
  with "?" or that follows a read word ("Tell me about the refund", "Refund status?", "Explain
  refund rules"); a participle before a noun is an adjective ("Show blocked accounts").
- Every answer lists the evidence consulted, the evidence missing and the ids it cites (orders,
  links, clusters), all collected by code from the tool results, never from the model.
- MockAgent answers the three questions of US-702 deterministically from tool results only.
- VllmAgent uses Gemma tool calling with the same tools and limits. List markers are removed
  first; an answer is then replaced by a list of the evidence consulted when it names a banned
  term (same-person synonyms and ownership of the linked accounts included), a decision verb
  (approve, reject, block...), a recommendation, an action (an imperative, an obligation such as
  "must be disabled" or "va disattivato", a claim that something was done such as "done", "by
  me" or a first-person subject with any past form but a read verb), a character outside the
  allowlist, a number written in letters or Roman numerals, or a number absent from the tool
  results (integers exactly, decimals possibly rounded to the digits written, no sign flip or
  scale suffix). A number in a clause about a record (a link, a cluster by its id or reference
  name, an order, the case) must be a value of that record, and a number next to a field word
  ("7 linked accounts", "weight 0.6") that field. Adversarial review of 13 Oct, finding 7; round
  2, finding R4; round 3, findings T6, T9 and T12 to T16; round 4, findings U13 to U19. Since
  round 5 (findings V12, V13, V18; ADR-14 decision 11) correct answers on the golden cases must
  keep the model path: a factor is named by its feature, "7 vs 3" binds the first number to the
  record of the clause and the second to the other one, "weight (0.98)" is a label's value. Round
  6 (findings W1 and W4): every "a vs b" pair of a clause (also "instead of", "invece di") binds to
  the records the clause compares, "(F-ROSSI: 3)" binds 3 to F-ROSSI and leaves the scope as it
  was, ordinals second to fifth rank the factors or links named after them in tool order, and the
  integer of an "id:value" lever of get_case is a field of the case.
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
# Number words an answer may use: pronoun "one", and "first" of the tool field first_shared_on,
# also in Italian ("il primo fattore", "il primo legame": rank 1 of the tool order).
ANSWER_SKIPPED_NUMBER_WORDS = PRONOUN_NUMBERS | {"first", "primo", "prima"}
# Ordinals an answer may use to rank the factors of the score or the links of a cluster in the
# order the tools list them ("the second is days_since_delivery", "il secondo legame L-102"):
# each one must stand before the record it ranks, in its sentence (round 6, finding W4).
RANK_ORDINALS: Mapping[str, int] = {
    **dict.fromkeys(("second", "secondo", "seconda"), 2),
    **dict.fromkeys(("third", "terzo", "terza"), 3),
    **dict.fromkeys(("fourth", "quarto", "quarta"), 4),
    **dict.fromkeys(("fifth", "quinto", "quinta"), 5),
}
_RANKED_KINDS = ("factor", "link")
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

# ---- Write requests (LLM-07; adversarial review of 13 Oct, finding 7; rounds 2 to 4) ----------

_EN_WRITE_VERBS = (
    "approve reject deny change modify edit update set apply override delete remove add create "
    "insert cancel block unblock enable disable grant revoke refund send email dispute mark "
    "unmark assign lower raise increase decrease reset close reopen write save confirm flag "
    "unflag restore merge unlink ban allow waive issue move force ignore bypass forget turn "
    "switch stop toggle activate deactivate suspend revert undo rollback dismiss split notify "
    "accept blacklist shut kill proceed keep freeze unfreeze lock unlock restrict lift release "
    "process drop limit whitelist unban erase purge wipe authorize authorise execute finalize "
    "finalise validate pay reimburse hold withhold delay postpone defer void clear pause decline "
    "refuse reduce extend shorten reclassify strip withdraw escalate"
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
    "limita limitare riduciamo disabilita disabilitare abilita abilitare riattiva riattivare "
    "scollega scollegare esegui eseguire effettua effettuare paga pagare avvisa avvisare "
    "notifica notificare ritarda ritardare trattieni trattenere rinvia rinviare"
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
_EN_BASE_FORMS = word_set(_EN_WRITE_VERBS)
# Italian imperatives in the second person, singular and plural (not the first plurals, which
# are also "we block": "Perché blocchiamo i coupon?").
_IT_IMPERATIVES = word_set(
    " ".join(w for w in _IT_WRITE_VERBS.split() if w not in _IT_INFINITIVES)
    + " "
    + _IT_PLURAL_IMPERATIVES
)
# Formal imperatives count only at the start of a clause and before an object ("Disattivi i
# coupon"): elsewhere, or before an adjective or "di", the same words are nouns ("Rimborsi
# precedenti?", "Blocchi attivi?", "Cambi di carta?"). Rounds 3 and 4, findings T12 and U18.
_FORMAL_IMPERATIVES = word_set(
    " ".join(_formal(w) for w in _ITALIAN_INFINITIVES)
    + " tolga faccia mantenga riduca spenga diminuisca trattenga"
)
# Third plural subjunctives, an obligation in an answer ("Si disattivino i coupon").
_SUBJUNCTIVE_PLURALS = word_set(
    " ".join(_formal(w, plural=True) for w in _ITALIAN_INFINITIVES)
    + " tolgano facciano mantengano riducano spengano diminuiscano trattengano"
)
# Italian first person singular of the write verbs ("Disattivo subito i coupon"), an action in
# an answer at the start of a clause (round 4, finding U15).
_IT_FIRST_SINGULARS = word_set(
    " ".join(w[:-3] + "o" for w in _ITALIAN_INFINITIVES) + " tolgo spengo metto faccio mantengo"
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
_PROCLITICS = word_set("lo la li le gli ne glielo gliela glieli gliele")


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
# Objects of an imperative: determiners, pronouns and Italian articles, clitics and a few
# adverbs that stand between a formal imperative and its object ("Rimborsi subito il cliente").
_OBJECTS = _DETERMINERS | word_set(
    "a an my your our his her its their any some both each him me us l un una uno quel quello "
    "quella quei quegli quelle tutti tutte tutto ne ci mi ti vi lui lei loro subito pure ora "
    "adesso immediatamente"
)
# A write word after a determiner, a preposition or an adjective is a noun ("the refund", "the
# blocked accounts", "instant refund"), and so is one before a noun of the attributes of a
# record ("refund status", "refund rules"). Since round 4 (finding U13) this reading holds only
# in a question: a sentence that ends with "?", or after a read verb or a question word
# ("Tell me about the refund", "Explain refund rules"). Otherwise a write word is a cue, as at
# e9d17e2: "Withhold the refund", "Get this approved", "Set status to Q1" are requests.
_NOUN_MARKERS = word_set(
    "the a an this that these those its their his her my your our any some no about of for on "
    "in with from at into regarding il lo la gli le l un una uno del della dello dei degli "
    "delle dell al alla allo ai agli alle all sul sulla sullo sui sugli sulle nel nella nello "
    "nei negli nelle questo questa questi queste quel quella quello quei quelle suo sua suoi sue "
    "tuo tua tuoi tue di su con instant immediate partial full late pending previous past recent "
    "current last next"
)
# Nouns of the attributes of a record: a write word before one is a noun in a question ("Refund
# status?", "Refund timeline?"); "Change type" or "Set status to Q1" stay requests.
_NOUN_HEADS = word_set(
    "status deadline deadlines date dates amount amounts history reason reasons timing details "
    "detail info information of timeline timelines window windows rules rule policy policies "
    "process period time times method methods options terms conditions count number rate log "
    "logs trail stato scadenza data importo storico motivo dettagli regole tempi termini "
    "condizioni finestra"
)
# Verbs that set a value: never a noun before a head ("Set status", "Change deadline").
_SETTERS = word_set(
    "set change update reset edit modify imposta cambia modifica aggiorna reimposta"
)
# Nouns a participle may qualify ("Show blocked accounts", "flagged links", "Le carte bloccate").
_NOUNS = _NOUN_HEADS | word_set(
    "accounts account links link coupons coupon refunds refund orders order items item returns "
    "return cards card devices device addresses address flags flag cases case customers customer "
    "clusters cluster levers lever codes code users profiles entries records transactions "
    "payments decisions requests messages notices events collegamenti collegamento legami legame "
    "rimborsi rimborso ordini ordine articoli articolo resi reso carte carta dispositivi "
    "indirizzi casi caso clienti cliente leve leva codici messaggi"
)
# Words that open a read question: a write word after one of them, in the question, may be a noun.
_READ_WORDS = word_set(
    "tell show explain list summarize summarise describe display give what which why how who "
    "when where whose dimmi mostrami spiegami elenca mostra spiega descrivi riassumi quale quali "
    "cosa perche come chi quando dove"
)

# Past participles ask for a change ("Sara wants coupons disabled") unless a state auxiliary comes
# right before them ("were disabled", "è stato approvato"), a noun right after them ("blocked
# accounts"), or the sentence is a question with no request marker ("Coupons disabled?").
_EN_PARTICIPLES = (
    "approved rejected denied changed modified updated overridden deleted removed cancelled "
    "canceled blocked unblocked enabled disabled granted revoked refunded reset lowered raised "
    "reopened flagged unflagged restored merged unlinked banned waived forced bypassed turned "
    "switched stopped toggled activated deactivated suspended reverted dismissed notified "
    "accepted blacklisted okayed killed frozen unfrozen whitelisted unbanned purged wiped erased "
    "authorized authorised held withheld delayed postponed deferred voided cleared paused "
    "declined refused reduced extended shortened reclassified stripped withdrawn escalated"
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
    "respinte emesso emessa emessi emesse ridotto ridotta ridotti ridotte trattenuto trattenuta "
    "trattenuti trattenute messo messa messi messe chiuso chiusa chiusi chiuse"
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
# "Why don't you disable...?", "Perché non lo approvi?": a request (round 4, finding U14).
_WHY_NOT_RE = re.compile(
    r"\b(?:why (?:don t|dont|do not|not)(?: you| we)?|perche non(?: (?:lo|la|li|le|gli|ne|ci))?"
    r"|(?:how|what) about)(?: just)? ([a-z]+)"
)
# Sentences end at a terminator that is not a dot between two letters ("dis.able" is one word):
# round 3, finding T15. Format characters are removed and a run of dots or an ellipsis between
# two letters becomes one dot before the split ("dis…able", "ap.​prove"): round 4, finding U19.
_QUESTION_SENTENCE_RE = re.compile(r"(?:[^.?!;\n]|(?<=[^\W\d_])\.(?=[^\W\d_]))+[.?!;\n]*")
_DOT_RUN_RE = re.compile(r"(?<=[^\W\d_])\.{2,}(?=[^\W\d_])")
# Clauses of a sentence: a write verb that opens one is a request whatever question word came
# before ("Tell me what this case is and approve it", "What is this case - approve it", "...,
# but approve it anyway", "What is this case (approve it)?"). Split before accents are folded, so
# "è" is not "e". Rounds 3 and 4, findings T12 and U14.
_QUESTION_CLAUSE_RE = re.compile(
    r"[,:;()\[\]]|\s[-‐‑‒–—―]+\s|[–—―]|\b(?:and|then|so|also|but|or|otherwise|else|afterwards"
    r"|finally|anyway|instead|e|poi|quindi|anche|ma|o|oppure|altrimenti|infine|dopodich[eé]"
    r"|comunque|invece)\b",
    re.IGNORECASE,
)
# A label before ":" at the start of a question ("Refund: when is it due?") is a topic.
_QUESTION_LABEL_RE = re.compile(r"^\s*[^\W\d_]+\s*:\s*(?=\S)")
# Words before a bare imperative that do not make it a third-person verb: connectives, softeners,
# question words. Anything else right before it is a subject ("Which accounts block the card?",
# "Why do we block it?", "how to approve"). Round 4, finding U14.
_NOT_SUBJECTS = word_set(
    "and or but then so also plus otherwise else afterwards finally anyway instead if not no yes "
    "none just simply now please kindly pls plz ok okay what why how when where whether e o ma "
    "poi quindi anche oppure altrimenti infine dopodiche comunque invece se non si solo ora "
    "adesso subito perche come quando dove allora pure"
)
_ID_AFTER_RE = r"\s+[a-z]{1,6}-[a-z0-9]"


# The agent never recommends a decision (spec 8.8): advice in an answer is not verified.
_ADVICE_RE = re.compile(
    r"\b(?:recommend\w*|suggest\w*|advis\w*|you should|you ought|consigli\w*|suggerisc\w*"
    r"|raccomand\w*|dovresti|dovreste)\b"
)


def advice(text: str) -> list[str]:
    """Recommendations in an answer ("you should", "I recommend", "consiglio")."""
    return [m.group() for m in _ADVICE_RE.finditer(" ".join(words(text)))]


def _clean(text: str) -> str:
    """NFKC, format characters removed, runs of dots between two letters made one dot."""
    normal = unicodedata.normalize("NFKC", text)
    normal = "".join(ch for ch in normal if unicodedata.category(ch) != "Cf")
    return _DOT_RUN_RE.sub(".", normal)


@dataclass(frozen=True)
class _Question:
    """What a sentence of a question says about the write words in it."""

    asked: bool  # it ends with "?"
    marker: bool  # it has a request marker ("can you", "please")
    read_before: bool  # a read verb or question word came in an earlier sentence
    folded: str  # the folded sentence, to find an identifier after a verb


def _participle(tokens: Sequence[str], index: int) -> bool:
    """A participle, also an Italian -ate/-ite form after a state auxiliary or a noun (equal to
    the plural imperative: "sono state bloccate", "le carte bloccate")."""
    token = tokens[index]
    if token not in _WRITE_PARTICIPLES:
        return False
    before = tokens[index - 1] if index > 0 else ""
    return _write_verb(token) is None or before in _STATE_AUXILIARIES or before in _NOUNS


def _participle_cue(tokens: Sequence[str], index: int, q: _Question | None) -> bool:
    """A participle that asks for a change: not after a state auxiliary, not before a noun it
    qualifies, not in a question without a request marker (unless after "be": "Can the coupons be
    turned off?")."""
    before = tokens[index - 1] if index > 0 else ""
    after = tokens[index + 1] if index + 1 < len(tokens) else ""
    if before in _STATE_AUXILIARIES or after in _NOUNS:
        return False
    return not (q is not None and q.asked and not q.marker and before != "be")


def _noun_use(tokens: Sequence[str], index: int, q: _Question | None) -> bool:
    """A write word read as a noun. In a question (`q`), only in a sentence that ends with "?"
    or after a read word; in an answer (`q` None), wherever a noun marker or head is next to it."""
    token = tokens[index]
    if token in _IT_INFINITIVES:
        return False
    if q is not None and not (
        q.asked or q.read_before or any(t in _READ_WORDS for t in tokens[:index])
    ):
        return False
    before = tokens[index - 1] if index > 0 else ""
    after = tokens[index + 1] if index + 1 < len(tokens) else ""
    if before in _NOUN_MARKERS:
        return True
    if after not in _NOUN_HEADS or (q is not None and token in _SETTERS):
        return False
    value = tokens[index + 2] if index + 2 < len(tokens) else ""
    return q is None or value not in ("to", "as", "a", "in")


def _first_write_cue(tokens: Sequence[str], q: _Question) -> int | None:
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if _participle(tokens, index):
            if _participle_cue(tokens, index, q):
                return index
            continue
        if _write_verb(token) is not None and not _noun_use(tokens, index, q):
            return index
        if token in _OBJECT_VERBS and following in _DETERMINERS:
            return index
    return None


def _bare_imperative(tokens: Sequence[str], q: _Question) -> bool:
    """A bare imperative with an object anywhere in the sentence, whatever question word came
    earlier, unless a subject, an auxiliary, "to" or a noun marker comes right before it ("Which
    evidence supports the link, if none remove L-102"). Round 4, finding U14."""
    for index, token in enumerate(tokens):
        if token not in _EN_BASE_FORMS and token not in _IT_IMPERATIVES:
            continue
        after = tokens[index + 1] if index + 1 < len(tokens) else ""
        has_id = re.search(rf"\b{token}{_ID_AFTER_RE}", q.folded) is not None
        if after not in _OBJECTS and not has_id:
            continue
        before = tokens[index - 1] if index > 0 else ""
        if index == 0 or before in _NOT_SUBJECTS:
            return True
    return False


def _sentence_requests(tokens: Sequence[str], q: _Question) -> bool:
    match = _WHY_NOT_RE.search(" ".join(tokens))
    verb = match[1] if match else ""
    if _write_verb(verb) or verb in _FORMAL_IMPERATIVES or verb in _EN_ING_FORMS:
        return True
    if _bare_imperative(tokens, q):
        return True
    cue = _first_write_cue(tokens, q)
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
            if tokens[i] in _QUESTION_WORDS and (q.asked or tokens[i] not in _AUX_QUESTION_WORDS)
        ),
        default=-1,
    )
    return question_word < 0 or marker >= question_word


_SOFTENERS = word_set(
    "please now just simply kindly also then so ok okay go ahead per favore adesso ora quindi "
    "allora anche subito pls plz do better"
)


def _softener(token: str) -> bool:
    """A softener, or an Italian adverb in -mente ("Cortesemente approvi il reso")."""
    return token in _SOFTENERS or (token.endswith("mente") and len(token) > 6)


def _opening_verb(tokens: Sequence[str], q: _Question | None = None) -> str | None:
    """The write verb that opens a clause, after softeners: an imperative or an infinitive,
    also with clitics ("Approvalo"), a formal imperative before its object ("Disattivi i
    coupon") or after a proclitic ("Li disattivi"); never a noun use."""
    start = 0
    while start < len(tokens) and _softener(tokens[start]):
        start += 1
    if start >= len(tokens):
        return None
    token = tokens[start]
    following = tokens[start + 1] if start + 1 < len(tokens) else ""
    if token in _PROCLITICS and (following in _FORMAL_IMPERATIVES or _write_verb(following)):
        return f"{token} {following}"
    if token in _FORMAL_IMPERATIVES and token not in _WRITE_VERBS:
        return token if following in _OBJECTS or (q is not None and _has_id(token, q)) else None
    if _participle(tokens, start) or _noun_use(tokens, start, q):
        return None
    if _write_verb(token) is not None:
        return token
    return token if token in _OBJECT_VERBS and following in _DETERMINERS else None


def _has_id(token: str, q: _Question) -> bool:
    return re.search(rf"\b{token}{_ID_AFTER_RE}", q.folded) is not None


def is_write_request(question: str) -> bool:
    """True when a sentence of the question asks to change something (or to ignore the rules):
    it names a write verb, or a participle that asks for a change, and no question word comes
    between the last request marker ("can you", "please") and that verb; or a clause of it opens
    with a write verb, whatever came before ("What is this case, then approve it", "..., but
    approve it anyway"); or a bare imperative with an object comes anywhere in it, unless a
    subject comes right before it; or it asks "why don't you" act. "Why was it disabled?", "Can
    you tell me why it was disabled?", "Tell me about the refund", "Refund timeline?" and "Show
    blocked accounts" are questions; "Turn off coupons", "Can the coupons be turned off?",
    "Sara wants coupons disabled", "Withhold the refund", "Get this approved", "Set status to
    Q1", "Disattivali" and "Approvi il reso" are requests. Every letter form is checked: digits
    read as letters, dashes, dots and ASCII punctuation joined."""
    text = _clean(question)
    read_before = False
    for match in _QUESTION_SENTENCE_RE.finditer(text):
        sentence = match.group()
        asked = "?" in sentence
        if asked:
            label = _QUESTION_LABEL_RE.match(sentence)
            rest = sentence[label.end() :] if label else ""
            if label and words(rest)[:1] and words(rest)[0] in _READ_WORDS:
                sentence = rest
        folded = fold(sentence, " ")
        marker = _REQUEST_MARKERS.search(" ".join(words(sentence))) is not None
        q = _Question(asked, marker, read_before, folded)
        if any(_sentence_requests(form.split(), q) for form in forms(sentence, leet=True)):
            return True
        read_in_sentence = False
        for clause in _QUESTION_CLAUSE_RE.split(sentence):
            clause_q = dataclasses.replace(q, read_before=read_before or read_in_sentence)
            if any(_opening_verb(form.split(), clause_q) for form in forms(clause, leet=True)):
                return True
            read_in_sentence = read_in_sentence or any(t in _READ_WORDS for t in words(clause))
        read_before = read_before or read_in_sentence
    return False


# ---- Actions in an answer (round 2, finding R4; rounds 3 and 4, findings T13, T15 and U15) ----

# An answer never acts, asks to act or says it acted: a write verb that opens a clause ("Turn off
# coupons", "Go ahead and refund", "Disattivali", "Li disattivi", "Disattivo subito i coupon",
# "Switching off coupons"), a write verb after a modal or a softener ("you can disable", "do
# disable", "don't refund"), "let me" with a write verb, a causative ("have coupons disabled"),
# an obligation ("must be disabled", "are to be disabled", "need disabling", "make sure", "va
# disattivato", "bisognerebbe", "serve disattivare", "it is essential that"), a completion
# ("Done", "now off", "as requested", "carried out", "taken care of", "by me") or a first-person
# subject with a write form or any past form but a read verb after it ("I have already disabled",
# "I went ahead and froze", "I've taken", "I put", "Ho chiuso", "Ho messo"; "I checked" and "Ho
# verificato" are reads) sends the answer to the fallback. Round 4 reverses the first-person
# test (finding U15): the agent never acts, so a claim of any action is suspect.
_ANSWER_STOPS = r"[;:!?\n]|(?<![^\W\d_])\.|\.(?![^\W\d_])"
_ANSWER_SENTENCE_SPLIT_RE = re.compile(rf"(?:{_ANSWER_STOPS})+")
_ANSWER_CLAUSE_RE = re.compile(
    rf"(?:{_ANSWER_STOPS}|,|[()\[\]])+|\s[-‐‑‒–—―]+\s|\b(?:and|then|but|or|e|poi|ma|o)\b",
    re.IGNORECASE,
)
_MODALS = word_set(
    "to can could may might will would shall should must let lets puoi potete possiamo devi "
    "dovete dobbiamo pls plz please"
)
_OBLIGATION_RE = re.compile(
    r"\b(?:must|need to|needs to|needed to|have to|has to|had to|should|ought|required to"
    r"|better to|bisogna|bisogn\w+|occorr\w+|opportuno|opportuna|vanno|meglio|deve|devono"
    r"|dovrebbe|dovrebbero|necessari[oa]|andrebbe|andrebbero|conviene|converrebbe|make sure"
    r"|ensure|assicura\w*|fai in modo|fate in modo|ricorda\w* di|chied\w* di|dimentic\w* di"
    r"|(?:essential|imperative|necessary|vital|crucial|important|mandatory|required) that"
    r"|(?:need|needs|needed|require|requires|required)"
    r" (?!nothing\b|something\b|anything\b|everything\b)\w+ing)\b"
)
_COMPLETION_RE = re.compile(
    r"\b(?:done|fatto|fatta|completed|completat[oaie]|eseguit[oaie]|as requested|as you asked"
    r"|as asked|carried out|went ahead|gone ahead|all set|come richiesto|come chiesto"
    r"|come da richiesta|provveduto|riuscit[oaie]|(?:take|takes|taking|took|taken) care of"
    r"|(?<!not )by me|(?<!non )da me|(?:at|on|per) your request|su (?:tua|sua|vostra) richiesta"
    r"|(?:off|on|disabled|enabled|removed|blocked) (?:as of|from) now"
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
# Irregular English past forms, any verb: after "I" or "we" they claim an action.
_EN_IRREGULAR_PAST = word_set(
    "put took taken made gave given got gotten did done sent set cut held withheld kept froze "
    "frozen shut struck hit let left brought built bought paid ran run wrote written told begun "
    "began went gone stood laid"
)
# Read verbs: after "I" or "we" they are what the agent did, not an action on the case.
_READ_PAST = word_set(
    "checked read found looked reviewed consulted saw seen noticed observed examined inspected "
    "verified analysed analyzed compared queried retrieved fetched searched counted called used "
    "loaded gathered collected listed summarized summarised identified traced noted learned "
    "learnt understood considered computed received obtained accessed requested asked "
    "consultato letto "
    "trovato verificato controllato visto esaminato notato osservato analizzato cercato guardato "
    "confrontato rilevato riscontrato"
)
_FIRST_PERSON = word_set("i we")
# Words between "I" or "we" and the verb ("I have", "I've", "we just", "I went ahead and").
_FIRST_PERSON_SKIP = word_set(
    "have ve had d ll will would just already went did do am m also then now successfully "
    "personally can could was were ahead and gone simply quickly immediately"
)
# Words that follow "I" or "we" as a subject ("I have", "I've", "we just"), never the Italian
# article "i" ("i coupon"); a real "I" must be in the sentence, not a digit read as a letter.
_FIRST_PERSON_NEXT = word_set(
    "have ve had d ll will would just already went did do am m also then now successfully "
    "personally can could was were"
)
_ITALIAN_FIRST_PERSON = word_set("ho abbiamo")
_ITALIAN_SKIP = word_set("appena gia anche ora subito ormai gia personalmente")
_ADJECTIVE_PARTICIPLES = word_set("linked shared connected related distinct based combined")
_IT_PAST_RE = re.compile(r"[a-z]{2,}(?:at|it|ut|s|t)[oaie]")
# "Let me disable", "lasciami disattivare", "procedo a disattivare" (round 4, finding U15).
_LET_ME = word_set("let lasciami fammi")
_I_GO_TO = word_set("procedo provvedo vado passo")
_CAUSATIVES = word_set("have get make")
_PARTICLES = word_set("off on out up down")
_I_RE = re.compile(r"(?<![\w-])[Ii](?![\w-])")


def _past(token: str, italian: bool) -> bool:
    """A past form or participle: any -ed or irregular English past, an Italian participle."""
    if token in _READ_PAST or token in _ADJECTIVE_PARTICIPLES:
        return False
    if italian:
        return _IT_PAST_RE.fullmatch(token) is not None or token in _WRITE_PARTICIPLES
    return token in _EN_IRREGULAR_PAST or (token.endswith("ed") and len(token) > 4)


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
    start = 0
    while start < len(tokens) and _softener(tokens[start]):
        start += 1
    rest = tokens[start:]
    following = rest[1] if len(rest) > 1 else ""
    if rest[:1] and rest[0] in _IT_FIRST_SINGULARS and following in _OBJECTS:
        found.append(rest[0])
    acting = following in _OBJECTS or following in _PARTICLES
    acting = acting or (following in _NOUNS and following not in _NOUN_HEADS)
    if rest[:1] and rest[0] in _EN_ING_FORMS and acting:
        found.append(rest[0])
    if rest[:1] and rest[0] in _CAUSATIVES and any(t in _WRITE_PARTICIPLES for t in rest[1:]):
        found.append(f"{rest[0]} ... {next(t for t in rest[1:] if t in _WRITE_PARTICIPLES)}")
    for index in range(1, len(tokens)):
        token, before = tokens[index], tokens[index - 1]
        if before in _MODALS and _write_verb(token) is not None:
            found.append(f"{before} {token}")
        if before == "t" and index > 1 and tokens[index - 2] == "don" and _write_verb(token):
            found.append(f"don t {token}")
        if before == "not" and index > 1 and tokens[index - 2] == "do" and _write_verb(token):
            found.append(f"do not {token}")
        infinitive = tokens[index + 1] if index + 1 < len(tokens) else ""
        if before in _I_GO_TO and token == "a" and _write_verb(infinitive):
            found.append(f"{before} a {infinitive}")
    return found


def _first_person_verb(tokens: Sequence[str], index: int) -> int | None:
    """The first write form after a first-person subject at `index` (not right after a state
    auxiliary), or its main verb when that is a past form but a read verb."""
    italian = tokens[index] in _ITALIAN_FIRST_PERSON
    skip = _ITALIAN_SKIP if italian else _FIRST_PERSON_SKIP
    main = index + 1
    while main < len(tokens) and tokens[main] in skip:
        main += 1
    if main < len(tokens) and tokens[main] in _READ_PAST:
        main = len(tokens)  # a read verb: only a write form later counts
    elif main < len(tokens) and _past(tokens[main], italian):
        return main
    for j in range(index + 1, len(tokens)):
        if _acted(tokens[j]) and tokens[j - 1] not in _STATE_AUXILIARIES:
            return j
    return None


def _sentence_actions(tokens: Sequence[str], has_i: bool) -> list[str]:
    """A first-person subject with a write form or a past form after it, "let me" with a write
    verb, "to be" with a participle, "da" with a write infinitive, "va" with a participle, "serve"
    with a write infinitive, a third plural subjunctive, and "now" with a participle."""
    found: list[str] = []
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        subject = token in _ITALIAN_FIRST_PERSON or (
            token in _FIRST_PERSON
            and (token != "i" or has_i)
            and (following in _FIRST_PERSON_NEXT or _acted(following) or _past(following, False))
        )
        verb = _first_person_verb(tokens, index) if subject else None
        if verb is not None:
            span = tokens[index : verb + 1]
            found.append(" ".join(span) if len(span) <= 4 else f"{token} ... {tokens[verb]}")
        after = tokens[index + 1 : index + 4]
        if (
            token in _LET_ME
            and after[:1] in (["me"], ["us"], [])
            and any(_write_verb(t) for t in after[1:3])
        ):
            found.append(f"{token} {' '.join(after[:2])}")
        if token in _LET_ME and token != "let" and after[:1] and _write_verb(after[0]):
            found.append(f"{token} {after[0]}")
        if token == "to" and after[:1] == ["be"] and after[1:2] and _acted(after[1]):
            found.append(f"to be {after[1]}")
        if token == "da" and after[:1] and after[0] in _IT_INFINITIVES:
            found.append(f"da {after[0]}")
        if token == "va" and after[:1] and after[0] in _WRITE_PARTICIPLES:
            found.append(f"va {after[0]}")
        if token in ("serve", "servirebbe") and after[:1] and after[0] in _IT_INFINITIVES:
            found.append(f"{token} {after[0]}")
        if token in _SUBJUNCTIVE_PLURALS:
            found.append(token)
        rest = [t for t in after if t not in ("been", "be", "is", "are")]
        if token == "now" and rest[:1] and rest[0] in _WRITE_PARTICIPLES:
            found.append(f"now {rest[0]}")
    return found


def answer_actions(text: str) -> list[str]:
    """Imperatives, obligations and claims of an action taken, in every letter form; dots
    between two letters join a word ("Dis.able", "Dis…able")."""
    found: list[str] = []
    normal = _clean(text)
    for clause in _ANSWER_CLAUSE_RE.split(normal):
        for form in forms(clause, leet=True):
            found += [cue for cue in _clause_actions(form.split()) if cue not in found]
    for sentence in _ANSWER_SENTENCE_SPLIT_RE.split(normal):
        has_i = _I_RE.search(sentence) is not None
        for form in forms(sentence, leet=True):
            found += [cue for cue in _sentence_actions(form.split(), has_i) if cue not in found]
    for form in forms(normal, leet=True):
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
    # No first person: "I can also answer ... which terms applied" reads as a claimed action to
    # the answer check (round 5, finding V13).
    lines.append(
        "Other questions answered here: which evidence supports the link, how this case differs "
        "from a reference cluster, and which terms applied when the order was bought."
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
# a kind word is scoped to the records of that kind (round 3, finding T14). A singular kind word
# with two or more records of that kind read names none of them ("the cluster" after R-MARCO and
# F-ROSSI), except "link" and "factor", which may be any one link of the cluster or factor of the
# score, and "order", which is the case's order (round 4, finding U16; round 5, finding V13).
# "Score" is not a kind word: in an answer it nearly always means the risk score.
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
    "fattore": ("factor", True),
    "fattori": ("factor", False),
    "policy": ("policy", True),
}
_ANY_ONE = frozenset({"link", "factor"})  # kinds whose singular word may stand for any one record
# Kinds whose singular word is the case's own record when several were read: "the order" of a
# return case is the returned order (the orders of the customer are all read at once).
_CASE_DEFAULT = frozenset({"order"})
# Words that bind a number to a field of a record: the number written next to them must be that
# field of a record in scope ("7 linked accounts" is a cluster size, "weight 0.6" a link weight).
# Up to two adjectives may stand before the field word ("9 distinct linked accounts", "9 payment
# cards"). Rounds 3 and 4, findings T14 and U16.
_CUES: tuple[tuple[tuple[str, ...], str], ...] = tuple(
    sorted(
        [
            (tuple(cue.split()), field)
            for field, cues in (
                (
                    "size",
                    (
                        "linked accounts",
                        "accounts linked",
                        "account collegati",
                        "accounts",
                        "account",
                        "members",
                        "membri",
                    ),
                ),
                ("n_distinct_cards", ("distinct cards", "carte distinte", "cards", "carte")),
                (
                    "n_shared_events",
                    ("shared events", "shared event", "eventi condivisi", "events", "event"),
                ),
                ("weight", ("weight", "peso")),
                ("_days", ("days", "day", "giorni", "giorno")),
                ("return_rate", ("return rate", "tasso di reso")),
                ("return_window_days", ("return window", "finestra di reso")),
            )
            for cue in cues
        ],
        key=lambda item: -len(item[0]),
    )
)
# Words a cue may stand before a number with ("weight of 0.6", "accounts linked: 9").
_CUE_FILLERS = frozenset([":", "=", "of", "is", "are", "was", "were", "di", "e", "at", "pari", "a"])
_CUE_STOPS = frozenset(",;.()[]")
# A cue after these is a rate, not a field ("1.0 coupons per account").
_CUE_RATES = frozenset(["per", "each", "every", "ogni"])
_CUE_WORDS_RE = re.compile(r"\s*((?:[^\W\d_]+\s+){0,4}[^\W\d_]+)")
_NOT_ADJECTIVES = word_set(
    "and or of in on at for to the a an with from by as is are was were vs versus e o di da in "
    "con per tra fra il la lo i gli le"
)
_LIST_MARKER_RE = re.compile(r"^[ \t]*[-*•–—][ \t]+", re.MULTILINE)
_LINE_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_GROUNDING_CLAUSE_RE = re.compile(r"([;:()\[\]]|,(?=\s))")
# When a clause names two records of one kind, it is split at these words and each part binds
# its numbers to the record it names ("F-ROSSI has 7 linked accounts while R-MARCO has 3").
_SUBCLAUSE_RE = re.compile(
    r"\b(?:and|while|whereas|but|versus|vs|against|compared (?:with|to)|e|mentre|ma|contro"
    r"|rispetto a)\b",
    re.IGNORECASE,
)
# Two numbers compared ("7 vs 3 linked accounts", "7 linked accounts instead of 3", "2 carte
# distinte contro 3"): the first is a value of the first record of the comparison, the second of
# the second (round 5, finding V12; round 6, finding W1). Up to three field words may stand
# between the first number and the comparison word.
_VERSUS_WORDS = (
    r"vs\.?|versus|against|contro|compared (?:with|to)|instead of|rather than|invece (?:di|che)"
    r"|anziche"
)
_VERSUS_RE = re.compile(rf"^\s*(?:(?!qz)[^\W\d_]+\s+){{0,3}}(?:{_VERSUS_WORDS})\s*$")
# A record named right after one of these is the reference of a comparison, the second record of
# its pair ("compared with the Rossi family this case's cluster has 7 vs 3 linked accounts").
_COMPARED_RE = re.compile(
    r"(?:\bcompared (?:with|to)|\bthan|\bunlike|\bversus|\bvs|\bagainst|\bdiffer(?:s|ent)? from"
    r"|\brispetto (?:a|al|alla|allo|ai|agli)|\bcontro|\binvece di|\bdivers[oa] da)"
    r"(?: (?:the|il|la|lo|l|family|famiglia|cluster))*\s*$"
)
# A sentence that opens with a pronoun or a demonstrative goes on about the record of the
# sentence before ("It has", "This link has", "The same link", "Questo legame").
_PRONOUN_RE = re.compile(
    r"\s*(?:it|its|they|their|esso|essa|essi|esse|this|that|these|those|the same|questo|questa"
    r"|questi|queste|quel|quello|quella|lo stesso|la stessa|lo stesso)\b",
    re.IGNORECASE,
)
# The case cluster in words, and "here" for the case cluster in a comparison.
_CASE_CLUSTER_RE = re.compile(
    r"\bcase(?: s)? cluster\b|\bcluster of (?:the|this) case\b|\bcluster del caso\b"
    r"|\bcluster di questo caso\b"
)
_CASE_ORDER_RE = re.compile(
    r"\b(?:returned|case(?: s)?|this case s) order\b|\border of (?:the|this) case\b"
    r"|\bordine (?:del caso|reso)\b"
)
_HERE = frozenset(["here", "qui", "qua"])
_BIND_PREPOSITIONS = frozenset(
    ["in", "for", "of", "at", "on", "di", "del", "della", "nel", "nella", "per", "su", "da"]
)
_BIND_FILLERS = frozenset(["the", "cluster", "link", "order", "il", "la", "lo", "l", "reference"])
_FLAG_WORDS = {"disputed": ("disputed", True), "undisputed": ("disputed", False)}
_LEVER_NUMBER_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def strip_list_markers(text: str) -> str:
    """The text without list markers at the start of a line ("- ", "* ", "• ", "– "): a
    bullet is not a minus sign or a symbol (round 4, finding U17)."""
    return _LIST_MARKER_RE.sub("", text)


@dataclass(frozen=True)
class _Record:
    kind: str
    id: str | None
    aliases: tuple[str, ...]  # a link is also named by its two accounts
    fields: tuple[tuple[str, Decimal], ...]  # its own numbers, by field name
    names: tuple[str, ...] = ()  # other names, any one names it ("Rossi" for F-ROSSI)
    flags: frozenset[tuple[str, bool]] = frozenset()  # its booleans ("disputed", True)
    refers: tuple[str, ...] = ()  # ids of other records it names (a case: its cluster, order)

    @property
    def numbers(self) -> tuple[Decimal, ...]:
        return tuple(value for _, value in self.fields)

    def values(self, field: str) -> list[Decimal]:
        """The values of a field, also of a field ending with it ("return_rate":
        cluster_return_rate); a field starting with "_" also matches a field with that word in its
        name ("_days": days_since_delivery, the field of a factor of that feature: round 6,
        finding W4)."""
        word = field[1:] if field.startswith("_") else None
        return [
            v
            for name, v in self.fields
            if name == field or name.endswith(field) or word in name.split("_")
        ]


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


def lever_values(value: Any) -> list[tuple[str, Decimal]]:
    """The numbers of the "id:value" lever strings of a case record ("return_window_days:14" is
    the field return_window_days, 14): round 6, finding W4."""
    found: list[tuple[str, Decimal]] = []
    levers = value.get("lever_ids") if isinstance(value, dict) else None
    for lever in levers if isinstance(levers, list) else []:
        name, _, number = str(lever).partition(":")
        if _LEVER_NUMBER_RE.fullmatch(number):
            found.append((name, Decimal(number)))
    return found


def _records(value: Any, found: list[_Record], references: Mapping[str, str]) -> None:
    kind = _record_kind(value)
    if kind is not None:
        key = next(iter(value))
        fields: list[tuple[str, Decimal]] = []
        _own_fields(value, key, fields)
        if kind == "case":
            fields += lever_values(value)
        if kind == "factor":  # its value is also the field of its feature ("6 days" of x_days)
            fields += [(str(value[key]), number) for name, number in fields if name == "value"]
        accounts = (value.get("account_a"), value.get("account_b")) if kind == "link" else ()
        # A factor is named by its feature ("cluster_welcome_coupons = 7"): round 5, V13.
        record_id = str(value[key]) if key.endswith("_id") or kind == "factor" else None
        refers = (value.get("cluster_id"), value.get("order_id")) if kind == "case" else ()
        found.append(
            _Record(
                kind,
                record_id,
                tuple(str(a) for a in accounts if isinstance(a, str)),
                tuple(fields),
                tuple(name for name, cluster in references.items() if cluster == record_id),
                frozenset((k, v) for k, v in value.items() if isinstance(v, bool)),
                tuple(str(r) for r in refers if isinstance(r, str)),
            )
        )
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            _records(item, found, references)


def _matches(written: Decimal, numbers: Sequence[Decimal]) -> bool:
    exponent = written.as_tuple().exponent
    if isinstance(exponent, int) and exponent < 0:
        step = Decimal(1).scaleb(exponent)
        return any(n.quantize(step, rounding=ROUND_HALF_UP) == written for n in numbers)
    return any(n == written for n in numbers)


def _cue_after(rest: str) -> str | None:
    match = _CUE_WORDS_RE.match(rest)
    following = tuple(match.group(1).split()) if match else ()
    for skip in range(3):
        if any(word in _NOT_ADJECTIVES for word in following[:skip]):
            return None
        at = following[skip:]
        field = next((f for cue, f in _CUES if at[: len(cue)] == cue), None)
        if field is not None:
            return field
    return None


def _cue_before(text: str) -> str | None:
    tokens = re.findall(r"[^\W\d_]+|[^\w\s]", text)
    if tokens[-1:] == ["("]:  # "linked accounts (3)": the parenthesis right before the number
        tokens = tokens[:-1]
    end, skipped = len(tokens), 0
    while end > 0 and tokens[end - 1] in _CUE_FILLERS and skipped < 2:
        end, skipped = end - 1, skipped + 1
    if end == 0 or tokens[end - 1] in _CUE_STOPS:
        return None
    before = tuple(tokens[:end])
    for cue, field in _CUES:
        if before[len(before) - len(cue) :] == cue:
            rate = len(before) > len(cue) and before[len(before) - len(cue) - 1] in _CUE_RATES
            return None if rate else field
    return None


@dataclass(frozen=True)
class _Evidence:
    numbers: tuple[Decimal, ...]
    ids: tuple[str, ...]
    records: tuple[_Record, ...]
    # the place of a factor or link among those of its tool result, by "kind:id" ("factor:x": 2)
    ranks: Mapping[str, int] = dataclasses.field(default_factory=dict)

    def case_record(self, kind: str) -> _Record | None:
        """The record of a kind the case names (its cluster, its order), if it was read."""
        refers = {r for record in self.records if record.kind == "case" for r in record.refers}
        return next((r for r in self.records if r.kind == kind and r.id in refers), None)


def _evidence(
    results: Sequence[Any], case_id: str, references: Mapping[str, str] | None = None
) -> _Evidence:
    numbers: list[Decimal] = []
    ids: list[str] = [case_id]
    records: list[_Record] = []
    ranks: dict[str, int] = {}
    for result in results:
        _collect(result, numbers, ids)
        numbers += [value for _, value in lever_values(result)]
        start = len(records)
        _records(result, records, references or {})
        for kind in _RANKED_KINDS:
            mine = [r for r in records[start:] if r.kind == kind]
            for place, record in enumerate(mine, start=1):
                ranks.setdefault(f"{kind}:{record.id}", place)
    return _Evidence(tuple(numbers), tuple(ids), tuple(records), ranks)


def _letters(index: int) -> str:
    """A three-letter code for an index ("aaa", "aab"...), so a token never holds a digit."""
    return "".join(chr(ord("a") + index // 26**power % 26) for power in (2, 1, 0))


class _Marked:
    """A clause with each record name replaced by a token of letters ("qzrb"), each account of a
    link by its own token, and every other identifier of the results removed."""

    def __init__(self, clause: str, evidence: _Evidence) -> None:
        tokens: dict[str, str] = {}
        for index, record in enumerate(evidence.records):
            for name in (record.id, *record.names):
                if name:
                    tokens[name] = f" qzr{_letters(index)} "
        accounts = sorted({a for r in evidence.records for a in r.aliases})
        for index, account in enumerate(accounts):
            tokens[account] = f" qza{_letters(index)} "
        out = unicodedata.normalize("NFKC", clause)
        for name in sorted(tokens, key=len, reverse=True):
            out = strip_tokens(out, [name], tokens[name])
        self.plain = fold(strip_tokens(out, evidence.ids))
        self.words = words(self.plain)
        letters = " ".join(self.words)
        # The records named, in the order of their names in the clause: an id or reference
        # name, the two accounts of a link (at the first of them), "the case cluster", "the
        # returned order" (round 5, finding V12: "This case's cluster has more linked accounts
        # than the Rossi family" names R-MARCO first).
        at: list[tuple[int, _Record]] = []
        first_account: dict[str, int] = {}
        for match in re.finditer(r"\bqz([ra])([a-z]+)\b", letters):
            kind, code = match.groups()
            if kind == "r":
                at.append((match.start(), evidence.records[_decode(code)]))
            else:
                first_account.setdefault(accounts[_decode(code)], match.start())
        for record in evidence.records:
            if record.aliases and set(record.aliases) <= set(first_account):
                at.append((min(first_account[a] for a in record.aliases), record))
        for kind, pattern in (("cluster", _CASE_CLUSTER_RE), ("order", _CASE_ORDER_RE)):
            own = evidence.case_record(kind)
            phrase = pattern.search(letters)
            if own is not None and phrase is not None:
                at.append((phrase.start(), own))
        self.named: list[_Record] = []
        compared: list[_Record] = []
        for start, record in sorted(at, key=lambda item: item[0]):
            if record not in self.named:
                self.named.append(record)
                if _COMPARED_RE.search(letters[:start]):
                    compared.append(record)
        # The reference of a comparison comes after the other records (round 6, finding W1).
        self.named = [r for r in self.named if r not in compared] + compared

    def record_token(self, word: str, evidence: _Evidence) -> _Record | None:
        match = re.fullmatch(r"qzr([a-z]+)", word)
        return evidence.records[_decode(match[1])] if match else None


def _decode(code: str) -> int:
    index = 0
    for ch in code:
        index = index * 26 + ord(ch) - ord("a")
    return index


class _Scope:
    """The records the clauses of a sentence talk about, in the order they are named, the kinds
    of record that stand for one record (and the records that can still be it after the numbers
    bound to fields so far), and the kinds named ambiguously (they ground nothing)."""

    def __init__(self, records: Sequence[_Record] | None = None) -> None:
        self.records: list[_Record] | None = None if records is None else list(records)
        self.order: list[_Record] = list(records or [])
        self.single: set[str] = (
            {records[0].kind} if records is not None and len(records) == 1 else set()
        )
        self.candidates: dict[str, set[int]] = {}
        self.ambiguous: set[str] = set()

    def copy(self) -> _Scope:
        new = _Scope()
        new.records = None if self.records is None else list(self.records)
        new.order, new.single = list(self.order), set(self.single)
        new.candidates = {kind: set(ids) for kind, ids in self.candidates.items()}
        new.ambiguous = set(self.ambiguous)
        return new

    def restore(self, saved: _Scope) -> None:
        """The scope of `saved`: a parenthesis that names a record ("(F-ROSSI: 3)") does not
        change the scope of the sentence around it (round 6, finding W1)."""
        copy = saved.copy()
        self.records, self.order, self.single = copy.records, copy.order, copy.single
        self.candidates, self.ambiguous = copy.candidates, copy.ambiguous

    def update(self, marked: _Marked, evidence: _Evidence, keep: bool) -> None:
        """A clause that names records, or kinds of record the results have, sets the scope;
        with `keep` (a sentence that opens with a pronoun or a demonstrative), a kind word the
        scope already has goes on with its records."""
        named = marked.named
        read = {r.kind for r in evidence.records}
        kinds: dict[str, tuple[bool, tuple[str, bool] | None]] = {}
        for index, word in enumerate(marked.words):
            if word in _KIND_WORDS and _KIND_WORDS[word][0] in read:
                kind, one = _KIND_WORDS[word]
                flag = _FLAG_WORDS.get(marked.words[index - 1]) if index else None
                before = kinds.get(kind, (False, None))
                kinds[kind] = (before[0] or one, flag or before[1])
        for record in named:
            kinds.pop(record.kind, None)
        current = self.records or []
        # A kind word of the records already in scope goes on with them ("Rossi: 7 linked
        # accounts" is about F-ROSSI, not every cluster).
        for kind in [k for k in kinds if any(r.kind == k for r in current)]:
            kinds.pop(kind)
        if not (named or kinds):
            return
        records = list(named)
        single = {r.kind for r in named if sum(n.kind == r.kind for n in named) == 1}
        ambiguous: set[str] = set()
        for kind, (one, flag) in kinds.items():
            pool = [r for r in evidence.records if r.kind == kind]
            if flag is not None:
                pool = (
                    [r for r in pool if (flag[0], True) in r.flags]
                    if flag[1]
                    else [r for r in pool if (flag[0], True) not in r.flags]
                )
            own = evidence.case_record(kind)
            if one and len(pool) > 1 and flag is None and kind in _CASE_DEFAULT and own:
                pool = [own]
            elif one and len(pool) > 1 and flag is None and kind not in _ANY_ONE:
                ambiguous.add(kind)
                continue
            records += pool
            if one:
                single.add(kind)
        if keep:
            records = [*[r for r in current if r not in records], *records]
            single |= self.single
        self.records, self.order = records, list(named)
        self.single, self.ambiguous, self.candidates = single, ambiguous, {}

    def grounds(
        self, value: Decimal, field: str | None, evidence: _Evidence, bound: _Record | None = None
    ) -> bool:
        """A number next to a field word is that field of a record in scope (of any record when
        none in scope has the field, unless the kind is ambiguous); a field no record has does
        not bind the number. A number bound to a record ("3 in F-ROSSI", "7 here") is a value of
        that record."""
        if field is not None and not any(r.values(field) for r in evidence.records):
            field = None
        if bound is not None:
            return _matches(value, bound.values(field) if field else list(bound.numbers))
        scope = list(evidence.records) if self.records is None else self.records
        if field is None:
            pool = (
                evidence.numbers if self.records is None else [n for r in scope for n in r.numbers]
            )
            return _matches(value, pool)
        in_scope = [r for r in scope if r.values(field)]
        others = [r for r in evidence.records if r.values(field) and r.kind not in self.ambiguous]
        candidates = in_scope or others
        hits = [r for r in candidates if _matches(value, r.values(field))]
        if not hits:
            return False
        for kind in {r.kind for r in in_scope} & self.single:
            kind_hits = {id(r) for r in hits if r.kind == kind}
            before = self.candidates.get(kind)
            self.candidates[kind] = kind_hits if before is None else before & kind_hits
            if not self.candidates[kind]:
                return False
        return True


def _bound(marked: _Marked, end: int, evidence: _Evidence) -> _Record | None:
    """The record a number is bound to by the words after it: "here" (the case cluster) or a
    preposition before a record name ("3 in F-ROSSI", "4 shared events on L-101")."""
    following = re.findall(r"[^\W\d_]+|\S", marked.plain[end:])[:6]
    for index, word in enumerate(following[:4]):
        if word in _HERE:
            return evidence.case_record("cluster")
        if word in _BIND_PREPOSITIONS:
            rest = [w for w in following[index + 1 :] if w not in _BIND_FILLERS]
            return marked.record_token(rest[0], evidence) if rest else None
        if not word.isalpha() or word.startswith("qz"):
            return None
    return None


def _versus_pairs(marked: _Marked, numbers: Sequence[tuple[Decimal, int, int]]) -> list[int]:
    """The indices of the first numbers of the pairs compared in a clause ("7 vs 3 linked
    accounts and 2 vs 3 distinct cards": 0 and 2)."""
    pairs: list[int] = []
    index = 0
    while index + 1 < len(numbers):
        between = marked.plain[numbers[index][2] : numbers[index + 1][1]]
        if _VERSUS_RE.match(between):
            pairs.append(index)
            index += 1
        index += 1
    return pairs


def _other_record(record: _Record, seen: Sequence[_Record], evidence: _Evidence) -> _Record | None:
    """The record a "vs" compares `record` with: the one other record of its kind named earlier
    in the sentence, else the one other record of its kind read (round 5, finding V12)."""
    for pool in (seen, evidence.records):
        others = [r for r in pool if r.kind == record.kind and r is not record]
        unique = list({id(r): r for r in others}.values())
        if len(unique) == 1:
            return unique[0]
    return None


def _pair_records(
    candidates: Sequence[_Record], field: str | None, seen: Sequence[_Record], evidence: _Evidence
) -> tuple[_Record, _Record] | None:
    """The two records of a compared pair: the two records of one kind named, in order (the
    reference of a comparison last), or the one record named and the other record of its kind
    (round 5, finding V12; round 6, finding W1). The records named in the clause come first,
    then those of the scope. None when it is not clear."""
    pool = [r for r in candidates if field is None or r.values(field)] or list(candidates)
    if len(pool) == 2 and pool[0].kind == pool[1].kind:
        return pool[0], pool[1]
    if len(pool) == 1:
        other = _other_record(pool[0], seen, evidence)
        return (pool[0], other) if other is not None else None
    return None


def _clause_grounded(
    marked: _Marked,
    before: str,
    scope: _Scope,
    evidence: _Evidence,
    seen: Sequence[_Record] = (),
) -> bool:
    numbers = signed_numbers(marked.plain)
    paired: set[int] = set()
    for index in _versus_pairs(marked, numbers):
        (first, start, first_end), (second, second_start, end) = numbers[index : index + 2]
        field = (
            _cue_after(marked.plain[end:])
            or _cue_before(before + marked.plain[:start])
            or _cue_after(marked.plain[first_end:second_start])
        )
        pair = _pair_records(marked.named or scope.order, field, seen, evidence)
        if pair is None:
            ok = scope.grounds(first, field, evidence) and scope.grounds(second, field, evidence)
        else:
            ok = scope.grounds(first, field, evidence, pair[0]) and scope.grounds(
                second, field, evidence, pair[1]
            )
        if not ok:
            return False
        paired |= {index, index + 1}
    for index, (value, start, end) in enumerate(numbers):
        if index in paired:
            continue
        field = _cue_after(marked.plain[end:]) or _cue_before(before + marked.plain[:start])
        if not scope.grounds(value, field, evidence, _bound(marked, end, evidence)):
            return False
    return True


def _header(line: str, evidence: _Evidence) -> _Scope | None:
    """The scope of a line that ends with ":", for the list lines under it: every record it
    names, in order ("R-MARCO vs F-ROSSI:", "Link L-102:")."""
    if not line.rstrip().endswith(":"):
        return None
    named = _Marked(line, evidence).named
    return _Scope(named) if named else None


def grounded_in(
    answer: str,
    results: Sequence[Any],
    case_id: str,
    references: Mapping[str, str] | None = None,
) -> bool:
    """Every number in the answer is a number of the tool results: an integer as written must
    be equal to one, a decimal may be one rounded to the decimals written. Identifiers and dates
    of the results are removed first, and so are list markers ("- ", "• "); a number with a scale
    suffix, a sign or a sign word, or written in letters or Roman numerals, is never grounded.
    From the first clause of a sentence that names a record (a link by its id or its two
    accounts, a cluster by its id or its reference name, "the case cluster", an order, the case)
    or a kind of record read ("the cluster", "the disputed link"), numbers must be values of
    those records; a sentence that opens with a pronoun or a demonstrative goes on with them, and
    so do the list lines under a line that names records and ends with ":". A clause that names
    two records of one kind is split at and, while, but, vs... and each part binds its own
    numbers. A number next to a field word ("7 linked accounts", "weight 0.6", "9 distinct
    cards", "4 shared events", "9 days", "return rate 0.9") must be that field of such a record,
    and of one same record when the clauses talk about one ("The disputed link has weight 0.6 and
    4 shared events" is not). "3 in F-ROSSI" and "7 here" bind a number to F-ROSSI and to the case
    cluster; "7 vs 3 linked accounts" under "R-MARCO vs F-ROSSI:" binds 7 to R-MARCO, 3 to
    F-ROSSI. A singular kind word with two records of that kind read ("the cluster" after both
    clusters) grounds no number (rounds 3 and 4, findings T14, U16 and U17)."""
    evidence = _evidence(results, case_id, references)
    text = strip_tokens(strip_list_markers(answer), evidence.ids)
    labelled = strip_tokens(strip_list_markers(answer), evidence.ids, " qzid ")
    skipped = ANSWER_SKIPPED_NUMBER_WORDS | ranked_ordinals(answer, evidence)
    if number_modifiers(text, labelled) or number_words(text, "en", skip=skipped):
        return False
    if roman_numerals(text):
        return False
    scope, header = _Scope(), None
    for line in answer.split("\n"):
        bullet = _LIST_MARKER_RE.match(line) is not None
        line = strip_list_markers(line)
        if not bullet:
            header = None
        for index, sentence in enumerate(_LINE_SENTENCE_RE.split(line)):
            listed = index == 0 and bullet and header is not None
            keep = _PRONOUN_RE.match(sentence) is not None
            if listed and header is not None:
                scope = _Scope(header.order)
            elif not keep:
                scope = _Scope()
            if not _sentence_grounded(sentence, scope, evidence, keep, listed):
                return False
        header = _header(line, evidence) if not bullet else header
    return True


def _sentence_grounded(
    sentence: str, scope: _Scope, evidence: _Evidence, keep: bool, listed: bool
) -> bool:
    """The clauses of a sentence, in order; `keep` goes on with the scope of the sentence before
    (a pronoun), `listed` with the scope of the header for every clause (a list line). A clause
    that names two records of one kind is split at and, while, vs... when it has numbers that are
    not compared in pairs ("F-ROSSI has 3 linked accounts while R-MARCO has 7"); otherwise its
    records are the scope, in order ("R-MARCO vs F-ROSSI: 7 vs 3 linked accounts"). A parenthesis
    that names a record after a clause that named one ("R-MARCO has 7 linked accounts (F-ROSSI:
    3)") binds its numbers to that record and leaves the scope of the sentence as it was; after a
    kind word it names the record of the clause ("the cluster (R-MARCO)"): round 6, finding W1."""
    before = ""
    seen: list[_Record] = []  # the records named so far in the sentence, in order
    saved: _Scope | None = None  # the scope before an open parenthesis
    named_inside = False
    parts = _GROUNDING_CLAUSE_RE.split(sentence)
    for clause, separator in zip(parts[::2], [*parts[1::2], ""], strict=True):
        marked = _Marked(clause, evidence)
        kinds = [r.kind for r in marked.named]
        pieces = [clause]
        numbers = signed_numbers(marked.plain)
        if any(kinds.count(kind) > 1 for kind in kinds) and (
            numbers and not _versus_pairs(marked, numbers)
        ):
            pieces = _SUBCLAUSE_RE.split(clause)
        for piece in pieces:
            marked = _Marked(piece, evidence) if len(pieces) > 1 else marked
            scope.update(marked, evidence, keep or listed)
            keep = False
            named_inside = named_inside or bool(marked.named)
            seen += [r for r in marked.named if r not in seen]
            if not _clause_grounded(marked, before, scope, evidence, seen):
                return False
            before += marked.plain + " "
        before += separator
        if separator == "(":
            saved, named_inside = scope.copy(), False
        elif separator == ")" and saved is not None:
            # "(F-ROSSI: 3)" after a clause about R-MARCO is an aside; "the cluster (R-MARCO)"
            # names the record of the clause.
            if named_inside and saved.order:
                scope.restore(saved)
            saved = None
    return True


def ranked_ordinals(answer: str, evidence: _Evidence) -> set[str]:
    """The ordinals of RANK_ORDINALS that rank a record in every place they stand: the first
    factor or link named after the ordinal in its sentence is that one in the order of its tool
    result ("The first factor is x = 7, the second days_since_delivery = 6"). Any other ordinal
    stays a number in words (round 6, finding W4)."""
    good: set[str] = set()
    bad: set[str] = set()
    for sentence in _ANSWER_SENTENCE_SPLIT_RE.split(strip_list_markers(answer)):
        marked = _Marked(sentence, evidence)
        for index, word in enumerate(marked.words):
            rank = RANK_ORDINALS.get(word)
            if rank is None:
                continue
            records = (marked.record_token(w, evidence) for w in marked.words[index + 1 :])
            ranked = next((r for r in records if r is not None and r.kind in _RANKED_KINDS), None)
            place = evidence.ranks.get(f"{ranked.kind}:{ranked.id}") if ranked else None
            (good if place == rank else bad).add(word)
    return good - bad


AnswerProblem = Literal["BANNED_TERM", "DECISION_ECHO", "SCRIPT", "UNGROUNDED_NUMBER"]


def answer_problems(
    content: str,
    results: Sequence[Any],
    case_id: str,
    references: Mapping[str, str] | None = None,
) -> list[AnswerProblem]:
    """Why VllmAgent replaces a model answer by the evidence consulted, as llm.validator codes;
    empty when the answer is kept. List markers are removed first: a banned term outside the
    identifiers of the tool results, a decision word, a recommendation or an action
    (DECISION_ECHO), a character outside the allowlist (SCRIPT), a number the tool results do not
    ground (`grounded_in`). The API records these codes with a fallback answer (ADR-15)."""
    checked = strip_list_markers(content)
    problems: list[AnswerProblem] = []
    if banned_terms(strip_tokens(checked, _evidence(results, case_id).ids)):
        problems.append("BANNED_TERM")
    if decision_wording(checked) or advice(checked) or answer_actions(checked):
        problems.append("DECISION_ECHO")
    if foreign_characters(checked):
        problems.append("SCRIPT")
    if not grounded_in(content, results, case_id, references):
        problems.append("UNGROUNDED_NUMBER")
    return problems


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
        if answer_problems(content, results, case_id, self._references):
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
