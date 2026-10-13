"""Read-only investigator agent (US-701, US-702, docs/spec.md 8.8, test LLM-07).

- Five read-only tools over an injected data-access protocol (CaseReader): get_case, get_orders,
  get_cluster, get_score_factors, get_policy_text. The protocol has no write method.
- At most MAX_TOOL_CALLS tool calls per question, counted by the ToolBox; unknown tools count too.
- A request to change anything is refused before any tool or model call.
- Every answer lists the evidence consulted, the evidence missing and the ids it cites (orders,
  links, clusters), all collected by code from the tool results, never from the model.
- MockAgent answers the three questions of US-702 deterministically from tool results only.
- VllmAgent uses Gemma tool calling with the same tools and limits; an answer that names a banned
  term or a number absent from the tool results is replaced by a list of the evidence consulted.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Protocol

from llm.client import ChatClient, LlmUnavailable
from llm.prompts import agent_system_prompt
from llm.text import banned_terms, numbers_in_text, strip_tokens, word_set, words
from policy.types import ReturnTerms

MAX_TOOL_CALLS = 5
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

# ---- Write requests (LLM-07) ------------------------------------------------------------------

_WRITE_VERBS = word_set(
    "approve reject deny change modify edit update set apply override delete remove add create "
    "insert cancel block unblock enable disable grant revoke refund send email dispute mark "
    "unmark assign lower raise increase decrease reset close reopen write save confirm flag "
    "unflag restore merge unlink ban allow waive issue move force ignore bypass forget "
    "approva approvate approvare rifiuta rifiutare nega negare cambia cambiare modifica "
    "modificare aggiorna aggiornare imposta impostare applica applicare cancella cancellare "
    "elimina eliminare rimuovi rimuovere aggiungi aggiungere crea creare annulla annullare blocca "
    "bloccare sblocca sbloccare attiva attivare disattiva disattivare concedi concedere revoca "
    "revocare rimborsa rimborsare invia inviare manda mandare contesta contestare segna segnare "
    "assegna assegnare abbassa abbassare alza alzare aumenta aumentare diminuisci diminuire riduci "
    "ridurre chiudi chiudere riapri riaprire scrivi scrivere salva salvare conferma confermare "
    "ripristina ripristinare sposta spostare forza forzare togli togliere metti mettere ignora "
    "ignorare dimentica dimenticare"
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


def is_write_request(question: str) -> bool:
    """True when the question asks to change something (or to ignore the rules): it names a
    write verb and is a request or an imperative rather than a question about the case."""
    tokens = words(question)
    if not any(token in _WRITE_VERBS for token in tokens):
        return False
    if _REQUEST_MARKERS.search(" ".join(tokens)):
        return True
    return tokens[0] not in _QUESTION_WORDS


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


def grounded_in(answer: str, results: Sequence[Any], case_id: str) -> bool:
    """Every number in the answer is a number of the tool results, possibly rounded to the
    decimals written; identifiers and dates of the results are removed first."""
    numbers: list[Decimal] = []
    ids: list[str] = [case_id]
    for result in results:
        _collect(result, numbers, ids)
    for written in numbers_in_text(strip_tokens(answer, ids)):
        exponent = written.as_tuple().exponent
        places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
        step = Decimal(1).scaleb(-places)
        if not any(n.quantize(step, rounding=ROUND_HALF_UP) == written for n in numbers):
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
        if banned_terms(content) or not grounded_in(content, results, case_id):
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
