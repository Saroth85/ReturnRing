"""Read-only investigator agent (US-701, US-702, docs/spec.md 8.8, test LLM-07).

- Five read-only tools over an injected data-access protocol (CaseReader): get_case, get_orders,
  get_cluster, get_score_factors, get_policy_text. The protocol has no write method.
- At most MAX_TOOL_CALLS tool calls per question, counted by the ToolBox; unknown tools count too.
- A request to change anything is refused before any tool or model call. Each sentence of the
  question is checked on its own ("What is this case? Approve it." is a request), also with
  digits read as letters and dashes joined ("appr0ve", "dis-able").
- Every answer lists the evidence consulted, the evidence missing and the ids it cites (orders,
  links, clusters), all collected by code from the tool results, never from the model.
- MockAgent answers the three questions of US-702 deterministically from tool results only.
- VllmAgent uses Gemma tool calling with the same tools and limits. An answer is replaced by a
  list of the evidence consulted when it names a banned term (same-person synonyms included), a
  decision verb (approve, reject, block...), a recommendation, an action (an imperative, an
  obligation such as "must be disabled", a claim that something was done such as "done" or
  "now off"), a letter of another script, a number written in letters, or a number absent from
  the tool results (integers exactly, decimals possibly rounded to the digits written, no sign
  flip or scale suffix). A number in a clause that names a record (a link, a cluster, an order,
  the case) must be a value of that record. Adversarial review of 13 Oct, finding 7, and round
  2, finding R4.
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
    foreign_letters,
    forms,
    number_modifiers,
    number_words,
    numbers_in_text,
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

# ---- Write requests (LLM-07; adversarial review of 13 Oct, finding 7; round 2, finding R4) ----

_EN_WRITE_VERBS = (
    "approve reject deny change modify edit update set apply override delete remove add create "
    "insert cancel block unblock enable disable grant revoke refund send email dispute mark "
    "unmark assign lower raise increase decrease reset close reopen write save confirm flag "
    "unflag restore merge unlink ban allow waive issue move force ignore bypass forget turn "
    "switch stop toggle activate deactivate suspend revert undo rollback dismiss split notify "
    "accept blacklist shut kill proceed keep"
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
    "mantenere"
)
_ITALIAN_INFINITIVES = [w for w in _IT_WRITE_VERBS.split() if w.endswith(("are", "ere", "ire"))]
# Imperatives in the second person plural ("disattivate", "togliete", "rimuovete").
_IT_PLURAL_IMPERATIVES = " ".join(
    w[:-3] + {"a": "ate", "e": "ete", "i": "ite"}[w[-3]] for w in _ITALIAN_INFINITIVES
)


def _first_plural(infinitive: str) -> str:
    """The first person plural, also an imperative ("blocchiamo", "togliamo", "disattiviamo")."""
    stem = infinitive[:-3]
    if infinitive.endswith("are") and stem.endswith(("c", "g")):
        stem += "h"
    return stem + ("amo" if stem.endswith("i") else "iamo")


_IT_FIRST_PLURALS = " ".join(_first_plural(w) for w in _ITALIAN_INFINITIVES)
_WRITE_VERBS = word_set(
    f"{_EN_WRITE_VERBS} {_IT_WRITE_VERBS} {_IT_PLURAL_IMPERATIVES} {_IT_FIRST_PLURALS}"
)
# "okay" is a verb only with an object ("okay the return"), not as "Okay, what is...".
_OBJECT_VERBS = word_set("okay")
_DETERMINERS = word_set(
    "the this that these those it them all every il lo la i gli le questo questa questi queste"
)
# Past participles ask for a change ("Sara wants coupons disabled") unless a state auxiliary comes
# right before them ("were disabled", "è stato approvato"): then they describe the case.
_EN_PARTICIPLES = (
    "approved rejected denied changed modified updated overridden deleted removed cancelled "
    "canceled blocked unblocked enabled disabled granted revoked refunded reset lowered raised "
    "reopened flagged unflagged restored merged unlinked banned waived forced bypassed turned "
    "switched stopped toggled activated deactivated suspended reverted dismissed notified "
    "accepted blacklisted okayed killed"
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
    "respinte"
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
_QUESTION_SENTENCE_RE = re.compile(r"[^.?!;\n]+[.?!;\n]*")


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
        if token in _WRITE_VERBS:
            return index
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
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


def is_write_request(question: str) -> bool:
    """True when a sentence of the question asks to change something (or to ignore the rules):
    it names a write verb, or a participle that asks for a change, and no question word comes
    between the last request marker ("can you", "please") and that verb. "Why was it disabled?"
    and "Can you tell me why it was disabled?" are questions; "Turn off coupons", "Can the
    coupons be turned off?", "Sara wants coupons disabled" and "What is this case? Approve it."
    are requests. Every letter form is checked: digits read as letters, dashes joined."""
    text = unicodedata.normalize("NFKC", question)
    for match in _QUESTION_SENTENCE_RE.finditer(text):
        sentence = match.group()
        asked = "?" in sentence
        if any(_sentence_requests(form.split(), asked) for form in forms(sentence, leet=True)):
            return True
    return False


# ---- Actions in an answer (round 2, finding R4) -----------------------------------------------

# An answer never acts, asks to act or says it acted: a write verb that opens a clause ("Turn off
# coupons", "Go ahead and refund"), a write verb after a modal ("you can disable"), an obligation
# ("must be disabled", "è meglio togliere") or a completion ("Done", "now off", "I have
# disabled") sends the answer to the fallback.
_ANSWER_CLAUSE_RE = re.compile(r"[.;:!?\n,]+|\b(?:and|then|e|poi)\b", re.IGNORECASE)
_SOFTENERS = word_set(
    "please now just simply kindly also then so ok okay go ahead per favore adesso ora quindi "
    "allora anche subito"
)
_MODALS = word_set(
    "to can could may might will would shall should must let lets puoi potete possiamo devi "
    "dovete dobbiamo"
)
_OBLIGATION_RE = re.compile(
    r"\b(?:must|need to|needs to|needed to|have to|has to|had to|should|ought|required to"
    r"|better to|bisogna|occorre|vanno|meglio|deve|devono|dovrebbe|dovrebbero|necessari[oa])\b"
)
_COMPLETION_RE = re.compile(
    r"\b(?:done|fatto|fatta|completed|completat[oaie]|eseguit[oaie]"
    r"|now (?:off|on|disabled|enabled|active|inactive|removed|blocked|unblocked|approved|rejected)"
    r"|ora (?:disattivat|attivat|spent|accesi|rimoss|bloccat|approvat)\w*)\b"
)
_FIRST_PERSON = word_set("i we")
_PERFECT_AUXILIARIES = word_set("have ve had just")
_ITALIAN_PERFECT = word_set("ho abbiamo")


def _clause_actions(tokens: Sequence[str]) -> list[str]:
    found: list[str] = []
    start = 0
    while start < len(tokens) and tokens[start] in _SOFTENERS:
        start += 1
    if start < len(tokens) and tokens[start] in _WRITE_VERBS:
        found.append(tokens[start])
    for index in range(1, len(tokens)):
        token, before = tokens[index], tokens[index - 1]
        if token in _WRITE_VERBS and before in _MODALS:
            found.append(f"{before} {token}")
        first_person = before in _ITALIAN_PERFECT or (
            before in _PERFECT_AUXILIARIES and index > 1 and tokens[index - 2] in _FIRST_PERSON
        )
        if token in _WRITE_PARTICIPLES and first_person:
            found.append(f"{before} {token}")
    return found


def answer_actions(text: str) -> list[str]:
    """Imperatives, obligations and claims of an action taken, in every letter form."""
    found: list[str] = []
    for clause in _ANSWER_CLAUSE_RE.split(unicodedata.normalize("NFKC", text)):
        for form in forms(clause, leet=True):
            found += [cue for cue in _clause_actions(form.split()) if cue not in found]
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
# Words that bring another kind of record into a clause already about a named record.
_KIND_WORDS = {
    "case": "case",
    "order": "order",
    "orders": "order",
    "terms": "order",
    "cluster": "cluster",
    "clusters": "cluster",
    "account": "cluster",
    "accounts": "cluster",
    "link": "link",
    "links": "link",
    "factor": "factor",
    "factors": "factor",
    "score": "factor",
    "policy": "policy",
}
_ANSWER_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_GROUNDING_CLAUSE_RE = re.compile(r"[;:()\[\]]|,(?=\s)")


@dataclass(frozen=True)
class _Record:
    kind: str
    id: str | None
    numbers: tuple[Decimal, ...]


def _record_kind(value: Any) -> str | None:
    return _RECORD_KINDS.get(next(iter(value), "")) if isinstance(value, dict) else None


def _own_numbers(value: Any, numbers: list[Decimal]) -> None:
    """Numbers of a record, without those of the records nested in it (a cluster's links)."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            if _record_kind(item) is None:
                _own_numbers(item, numbers)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        numbers.append(Decimal(str(value)))


def _records(value: Any, found: list[_Record]) -> None:
    kind = _record_kind(value)
    if kind is not None:
        key = next(iter(value))
        numbers: list[Decimal] = []
        _own_numbers(value, numbers)
        found.append(
            _Record(kind, str(value[key]) if key.endswith("_id") else None, tuple(numbers))
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


def grounded_in(answer: str, results: Sequence[Any], case_id: str) -> bool:
    """Every number in the answer is a number of the tool results: an integer as written must
    be equal to one, a decimal may be one rounded to the decimals written. Identifiers and dates
    of the results are removed first; a number with a scale suffix, a sign or a sign word, or
    written in letters, is never grounded. Within a sentence, from the first clause that names a
    record (a link, a cluster, an order, the case), numbers must be values of the records the
    clause names, or of the kinds of record it mentions ("cluster of 7 linked accounts")."""
    numbers: list[Decimal] = []
    ids: list[str] = [case_id]
    records: list[_Record] = []
    for result in results:
        _collect(result, numbers, ids)
        _records(result, records)
    text = strip_tokens(answer, ids)
    if number_modifiers(text) or number_words(text, "en", skip=ANSWER_SKIPPED_NUMBER_WORDS):
        return False
    for sentence in _ANSWER_SENTENCE_RE.split(answer):
        scope: list[Decimal] | None = None  # None: any number of the results
        for clause in _GROUNDING_CLAUSE_RE.split(sentence):
            named = [r for r in records if r.id is not None and _names(clause, r.id)]
            kinds = {_KIND_WORDS[w] for w in words(strip_tokens(clause, ids)) if w in _KIND_WORDS}
            kinds -= {r.kind for r in named}
            if named or (kinds and scope is not None):
                chosen = named + [r for r in records if r.kind in kinds]
                scope = [n for r in chosen for n in r.numbers]
            pool = numbers if scope is None else scope
            if not all(_matches(n, pool) for n in numbers_in_text(strip_tokens(clause, ids))):
                return False
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
            banned_terms(content)
            or decision_wording(content)
            or advice(content)
            or answer_actions(content)
            or foreign_letters(content)
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
