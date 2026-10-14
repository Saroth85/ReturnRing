"""The read-only investigator agent behind POST /api/agent/ask (US-701, US-702, spec 8.8, flow
F5; ADR-15 section 4 "Agent"; rules 1, 2, 11 and 12; LLM-07).

The agent is llm.agent: MockAgent with LLM_MODE=mock, VllmAgent (Gemma tool calling on the local
vLLM) with LLM_MODE=vllm. Its five tools read a CaseReader, here ApiCaseReader: the case as the
API serves it (the latest decision version, from the database), the customer's orders with
their terms snapshots and the clusters of linked accounts, from the serving snapshot, with the
link disputes of the database. The reader is built before the agent runs and has no write
method; the agent can change nothing.

- The comparison reference "rossi" (US-702) is the graph cluster of the Rossi accounts in the
  snapshot the case was decided on: an opaque CL-... id, never a truth family id (rule 12).
- The stream (sse_frame, AgentStream) is built from what the agent did: one tool_call and
  tool_result pair per tool call, in order (at most 5), the answer as token deltas, exactly one
  answer or refusal, then done. Every step is computed before the response starts, so the whole
  stream is validated (AgentStream) and an unavailable model is a 503, never a broken stream.
- VllmAgent: an answer it cannot verify is asked once more, then replaced by its fallback
  listing the evidence consulted (spec 8.7: regenerate once, then the template; an answer that
  states a decision goes to the template at once). GenerationInfo records the path, the
  attempts and the llm.validator codes the public checks of llm/ find in the refused answer.
- A refusal is decided by code (llm.agent.is_write_request) before any model call, in every
  mode: path template, fallback write_request, no model call (1.5.0, H8).
- No model call runs inside a database transaction (H20): the case is read in one, the agent
  runs on the model-call pool of the runtime, the answer is stored in a second one.
- Rate limit (spec 11, "Sicurezza"): AGENT_RATE_LIMIT questions per person per
  AGENT_RATE_WINDOW_S; the next one is a 429.
- Every answer is kept in llm_generations (kind agent_answer) with the question, the evidence
  consulted and the answer: the operator's question is data, never an instruction.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final, Literal, cast

import pandas as pd
from fastapi import FastAPI
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import llm_unavailable, rate_limited
from apps.api.ratelimit import AGENT_RATE_LIMIT, AGENT_RATE_WINDOW_S, RateLimiter
from apps.api.services import decisions as store
from apps.api.services.cases import record_of
from apps.api.services.generation import POLICY_TEXT
from apps.api.services.other.common import line_prices, next_number
from apps.api.services.provenance import short_origin
from apps.api.serving import Serving
from apps.api.settings import Settings
from contracts.sql import link_disputes, llm_generations
from contracts.vocab import FallbackReason, GenerationPath, ValidatorCode
from llm.agent import (
    MAX_TOOL_CALLS,
    REFUSAL,
    TOOL_NAMES,
    AgentAnswer,
    CaseRecord,
    ClusterRecord,
    FactorRecord,
    LinkRecord,
    MockAgent,
    OrderRecord,
    PolicyRecord,
    VllmAgent,
    answer_problems,
    to_json,
)
from llm.client import ChatClient, LlmUnavailable
from llm.prompts import agent_system_prompt
from pipeline.generator.golden import ROSSI_ACCOUNTS
from policy.types import EvidenceQuality as Evidence
from policy.types import Path as ReturnPath
from policy.types import ReturnTerms

MAX_ANSWER_CHARS: Final = 4000  # AnswerEvent.text
MAX_TOKEN_CHARS: Final = 400  # TokenEvent.text
TOKEN_WORDS: Final = 12
MOCK_MODEL: Final = "mock"
TEMPLATE_MODEL: Final = "template"  # no model call: the refusal written by code
SHORTENED: Final = "\n(shortened)"
_LABEL: Final = re.compile(r"^(?P<name>\w+)\((?P<arg>.*)\)$")
_REFUSED_TOOL: Final = re.compile(r"^(?P<name>.+): refused$")


# ---- Rate limit (spec 11) ------------------------------------------------------------------------


def limiter_of(app: FastAPI) -> RateLimiter:
    """The rate limiter of an app (kept on app.state, so each app counts on its own)."""
    found = getattr(app.state, "agent_rate_limiter", None)
    if isinstance(found, RateLimiter):
        return found
    limiter = RateLimiter()
    app.state.agent_rate_limiter = limiter
    return limiter


# ---- The read-only data access (llm.agent.CaseReader) --------------------------------------------


@dataclass(frozen=True)
class CaseData:
    """Everything the five tools can read about one case, read before the agent runs."""

    case: CaseRecord
    customer_id: str
    decision_id: str
    orders: tuple[OrderRecord, ...]
    clusters: Mapping[str, ClusterRecord]
    factors: tuple[FactorRecord, ...]
    policy: PolicyRecord
    references: Mapping[str, str]


@dataclass
class Call:
    tool: str
    argument: str
    result: Any


@dataclass
class ApiCaseReader:
    """llm.agent.CaseReader over the data of one case; it records each call (for the stream)."""

    data: CaseData
    calls: list[Call] = field(default_factory=list)

    def _log(self, tool: str, argument: str, result: Any) -> Any:
        self.calls.append(Call(tool, argument, result))
        return result

    def get_case(self, case_id: str) -> CaseRecord | None:
        found = self.data.case if case_id == self.data.case.case_id else None
        return cast(CaseRecord | None, self._log("get_case", case_id, found))

    def get_orders(self, case_id: str) -> Sequence[OrderRecord]:
        found = self.data.orders if case_id == self.data.case.case_id else ()
        return cast(Sequence[OrderRecord], self._log("get_orders", case_id, found))

    def get_cluster(self, cluster_id: str) -> ClusterRecord | None:
        return cast(
            ClusterRecord | None,
            self._log("get_cluster", cluster_id, self.data.clusters.get(cluster_id)),
        )

    def get_score_factors(self, case_id: str) -> Sequence[FactorRecord]:
        found = self.data.factors if case_id == self.data.case.case_id else ()
        return cast(Sequence[FactorRecord], self._log("get_score_factors", case_id, found))

    def get_policy_text(self, case_id: str) -> PolicyRecord | None:
        found = self.data.policy if case_id == self.data.case.case_id else None
        return cast(PolicyRecord | None, self._log("get_policy_text", case_id, found))


def _day(value: Any) -> date:
    moment: datetime = pd.Timestamp(value).to_pydatetime()
    return moment.date()


def order_records(serving: Serving, customer_id: str) -> tuple[OrderRecord, ...]:
    """The customer's orders in the store, oldest first, with the terms snapshot of each."""
    orders = serving.frame("store/orders")
    mine = orders.loc[orders["customer_id"] == customer_id].sort_values(["event_at", "order_id"])
    out = []
    for row in mine.itertuples(index=False):
        snap = serving.by_key("store/terms_snapshots", str(row.terms_snapshot_id))
        if snap is None:
            continue
        out.append(
            OrderRecord(
                order_id=str(row.order_id),
                placed_on=_day(row.event_at),
                terms_snapshot_id=str(row.terms_snapshot_id),
                snapshot_source=str(snap["source"]),
                terms=ReturnTerms(
                    return_window_days=int(snap["return_window_days"]),
                    free_return_shipping=bool(snap["free_return_shipping"]),
                    instant_refund=bool(snap["instant_refund"]),
                    free_size_exchange=bool(snap["free_size_exchange"]),
                    coupons_enabled=bool(snap["coupons_enabled"]),
                ),
            )
        )
    return tuple(out)


def cluster_record(
    serving: Serving, cluster_id: str, instant: datetime, disputed: set[str]
) -> ClusterRecord | None:
    """A cluster of linked accounts of the graph at `instant`, its links and features."""
    frame = serving.at("features/clusters", instant)
    rows = frame.loc[frame["cluster_id"] == cluster_id]
    if rows.empty:
        return None
    first = rows.iloc[0]
    members = serving.cluster_members(cluster_id, instant)
    links = []
    for r in serving.cluster_links(members, instant).itertuples(index=False):
        lid = s.link_id(str(r.customer_a), str(r.customer_b))
        links.append(
            LinkRecord(
                link_id=lid,
                account_a=str(r.customer_a),
                account_b=str(r.customer_b),
                kinds=tuple(str(k) for k in r.kinds),
                first_shared_on=_day(r.first_shared_at),
                last_shared_on=_day(r.last_shared_at),
                n_shared_events=int(r.n_shared_events),
                weight=round(float(r.weight), 4),
                disputed=bool(r.disputed) or lid in disputed,
            )
        )
    rate = first["cluster_return_rate"]
    nature = serving.provenance["features/clusters"].nature
    return ClusterRecord(
        cluster_id=cluster_id,
        size=len(members),
        links=tuple(links),
        n_distinct_cards=int(first["n_distinct_cards"]),
        creation_spread_days=round(float(first["creation_spread_days"]), 2),
        welcome_coupons_per_account=round(float(first["welcome_coupons_per_account"]), 4),
        cluster_return_rate=None if pd.isna(rate) else round(float(rate), 4),
        strong_id_share=round(float(first["strong_id_share"]), 4),
        nature=nature,
        as_of=instant.date(),
    )


def reference_clusters(serving: Serving, instant: datetime) -> dict[str, str]:
    """The comparison references of US-702 at `instant`: the graph cluster that holds the Rossi
    accounts (a cluster of two or more accounts), if the snapshot has one."""
    frame = serving.at("features/clusters", instant)
    found = frame.loc[frame["customer_id"].isin(ROSSI_ACCOUNTS) & frame["cluster_id"].notna()]
    found = found.loc[found["cluster_size"] >= 2]
    if found.empty:
        return {}
    return {"rossi": str(found["cluster_id"].value_counts().sort_index().idxmax())}


async def case_data(
    conn: AsyncConnection, serving: Serving, return_id: str, at: datetime
) -> CaseData | None:
    """The data of the five tools for a return in the queue, or None when it is not there."""
    ret = await store.return_row(conn, return_id)
    if ret is None:
        return None
    prices = await line_prices(conn, serving, [return_id])
    found = await store.latest_view(conn, return_id, at, line_price=prices.get)
    if found is None:
        return None
    view, row = found
    record = record_of(serving, ret)
    context = serving.customer_context(record.customer_id, record.known_at)
    instant = context.as_of if not context.live else serving.feature_instants[-1]
    disputes = {str(r) for r in (await conn.execute(select(link_disputes.c.link_id))).scalars()}
    references = reference_clusters(serving, instant)
    clusters: dict[str, ClusterRecord] = {}
    for cluster_id in {view.inputs.cluster_id, *references.values()} - {None}:
        assert cluster_id is not None
        cluster = cluster_record(serving, cluster_id, instant, disputes)
        if cluster is not None:
            clusters[cluster_id] = cluster
    # rule 4, US-307: score factors exist only for a scored case
    scored = view.path is ReturnPath.WITHDRAWAL and view.evidence_quality is Evidence.SUFFICIENT
    factors = tuple(
        FactorRecord(f.feature, float(f.feature_value), f.phrase)
        for f in store.factors_of(row)
        if isinstance(f.feature_value, int | float) and not isinstance(f.feature_value, bool)
    )
    case = CaseRecord(
        case_id=return_id,
        order_id=record.order_id,
        path=view.path.value,
        evidence_quality=view.evidence_quality.value,
        quadrant=None if view.quadrant is None else view.quadrant.value,
        lever_ids=tuple(view.lever_ids),
        cluster_id=view.inputs.cluster_id,
        nature=view.inputs.provenance.nature,
        as_of=view.inputs.snapshot_on,  # the customer snapshot the engine read (rule 11)
    )
    return CaseData(
        case=case,
        customer_id=record.customer_id,
        decision_id=view.decision_id,
        orders=order_records(serving, record.customer_id),
        clusters=clusters,
        factors=factors if scored else (),
        policy=PolicyRecord(version=view.rules_version, text=POLICY_TEXT),
        references=references,
    )


# ---- Running the agent ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    """An answer of llm.agent, the tool calls it made and how it was produced."""

    answer: AgentAnswer
    calls: list[Call]
    path: GenerationPath
    fallback_reason: FallbackReason | None
    model_calls: int
    validator_codes: list[ValidatorCode]
    llm_mode: Literal["mock", "vllm"]
    model: str
    latency_ms: int | None


class _Watched:
    """A ChatClient that keeps the last answer text and whether the model was unavailable."""

    def __init__(self, inner: ChatClient) -> None:
        self.inner = inner
        self.unavailable = False
        self.last_content = ""

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        try:
            message = self.inner.chat(messages, tools=tools, tool_choice=tool_choice)
        except LlmUnavailable:
            self.unavailable = True
            raise
        self.last_content = str(message.get("content") or "")
        return message


def answer_codes(
    content: str,
    calls: Sequence[Call],
    case_id: str,
    references: Mapping[str, str] | None = None,
) -> list[ValidatorCode]:
    """The llm.validator codes of an answer VllmAgent refused, found with the checks VllmAgent
    runs (llm.agent.answer_problems; an empty answer is off the schema)."""
    if not content.strip():
        return ["SCHEMA"]
    results = [to_json(c.result) for c in calls]
    codes: list[ValidatorCode] = list(answer_problems(content, results, case_id, references))
    return codes or ["SCHEMA"]


def refusal_run(answer: AgentAnswer, llm_mode: Literal["mock", "vllm"]) -> Run:
    """A refusal written by code (llm.agent.is_write_request) before any model call."""
    return Run(answer, [], "template", "write_request", 0, [], llm_mode, TEMPLATE_MODEL, None)


def run_mock(data: CaseData, question: str) -> Run:
    reader = ApiCaseReader(data)
    answer = MockAgent(reader, data.references).ask(data.case.case_id, question)
    if answer.refused:
        return refusal_run(answer, "mock")
    return Run(answer, reader.calls, "model", None, 1, [], "mock", MOCK_MODEL, None)


def run_vllm(client: ChatClient, model: str, data: CaseData, question: str) -> Run:
    """VllmAgent, asked once more after an answer it could not verify (see the docstring).
    Raises LlmUnavailable when the model does not answer."""
    codes: list[ValidatorCode] = []
    started = time.perf_counter()
    case_id = data.case.case_id
    for attempt in (1, 2):
        watched = _Watched(client)
        reader = ApiCaseReader(data)
        answer = VllmAgent(watched, reader, data.references).ask(case_id, question)
        if answer.refused:
            return refusal_run(answer, "vllm")
        if watched.unavailable:
            raise LlmUnavailable("the local vLLM did not answer the agent")
        latency = round((time.perf_counter() - started) * 1000)
        if answer.source == "model":
            path: GenerationPath = "model" if attempt == 1 else "regenerated"
            return Run(answer, reader.calls, path, None, attempt, codes, "vllm", model, latency)
        found = answer_codes(watched.last_content, reader.calls, case_id, data.references)
        codes = list(dict.fromkeys([*codes, *found]))
        if "DECISION_ECHO" in found or attempt == 2:
            reason: FallbackReason = (
                "decision_echo" if "DECISION_ECHO" in found else "invalid_twice"
            )
            return Run(
                answer, reader.calls, "template", reason, attempt, codes, "vllm", model, latency
            )
    raise AssertionError("unreachable: the second attempt returns")  # pragma: no cover


# ---- The stream (AgentStream) --------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    tool: str
    argument: str
    called: bool
    result: Any


def steps_of(answer: AgentAnswer, calls: Sequence[Call]) -> tuple[list[Step], list[str]]:
    """The tool calls of an answer in order, from the ToolBox labels (llm.agent) and the reader
    calls; a call the ToolBox refused (an unknown tool) is only named in the missing evidence."""
    steps: list[Step] = []
    refused: list[str] = []
    pending = list(calls)
    for label in answer.consulted:
        match = _LABEL.match(label)
        if match is None or match["name"] not in TOOL_NAMES:
            bad = _REFUSED_TOOL.match(label)
            name = bad["name"] if bad else label
            refused.append(f"tool {name}: refused, the agent only has its five read-only tools")
            continue
        name, argument = match["name"], match["arg"]
        if pending and (pending[0].tool, pending[0].argument) == (name, argument):
            call = pending.pop(0)
            steps.append(Step(name, argument, True, call.result))
        else:
            steps.append(Step(name, argument, False, None))
    return steps, refused


def _ref(kind: s.EntityKind, entity: str) -> s.EntityRef:
    return s.EntityRef(kind=kind, id=entity)


def _unique_refs(refs: Sequence[s.EntityRef]) -> list[s.EntityRef]:
    seen: dict[tuple[str, str], s.EntityRef] = {}
    for ref in refs:
        seen.setdefault((ref.kind, ref.id), ref)
    return list(seen.values())


def tool_result(step: Step, data: CaseData) -> tuple[bool, str, list[s.EntityRef]]:
    """ok, summary and refs of one tool call, written by code from the record it returned."""
    result = step.result
    if step.tool == "get_case":
        if not isinstance(result, CaseRecord):
            return False, f"Case {step.argument} not found", []
        refs = [_ref("return", result.case_id), _ref("order", result.order_id)]
        text = (
            f"Case {result.case_id}: {result.path} path, {result.quadrant or 'no quadrant'}, "
            f"evidence {result.evidence_quality}; order {result.order_id}"
        )
        if result.cluster_id is not None:
            refs.append(_ref("cluster", result.cluster_id))
            text += f"; cluster {result.cluster_id}"
        return True, text, refs
    if step.tool == "get_orders":
        orders = tuple(result or ())
        if not orders:
            return False, "No order of the case customer on file", []
        refs = [
            ref
            for o in orders
            for ref in (_ref("order", o.order_id), _ref("terms_snapshot", o.terms_snapshot_id))
        ]
        return (
            True,
            f"{len(orders)} orders of {data.customer_id}, each with its terms snapshot",
            refs,
        )
    if step.tool == "get_cluster":
        if not step.called:
            return (
                False,
                f"Cluster {step.argument}: not readable (only the case cluster and the references)",
                [],
            )
        if not isinstance(result, ClusterRecord):
            return False, f"Cluster {step.argument} not found", []
        refs = [_ref("cluster", result.cluster_id)] + [
            _ref("link", x.link_id) for x in result.links
        ]
        text = (
            f"Cluster {result.cluster_id}: {result.size} linked accounts and {len(result.links)} "
            f"links ({result.nature} data, as of {result.as_of})"
        )
        return True, text, refs
    if step.tool == "get_score_factors":
        factors = tuple(result or ())
        if not factors:
            return False, "No score factors: the case is not scored", []
        names = ", ".join(f.feature for f in factors)
        return (
            True,
            f"Top contributions to the risk score, not causes: {names}",
            [_ref("return", data.case.case_id)],
        )
    if not isinstance(result, PolicyRecord):
        return False, "The merchant return policy is not on file", []
    return True, f"Merchant return policy, rules version {result.version}", []


def tokens_of(text: str) -> list[str]:
    """The answer as token deltas: groups of words of at most MAX_TOKEN_CHARS characters whose
    concatenation is the answer."""
    words = re.findall(r"\s*\S+\s*", text) or [text]
    chunks: list[str] = []
    current = ""
    count = 0
    for word in words:
        while len(word) > MAX_TOKEN_CHARS:
            if current:
                chunks.append(current)
                current, count = "", 0
            chunks.append(word[:MAX_TOKEN_CHARS])
            word = word[MAX_TOKEN_CHARS:]
        if count >= TOKEN_WORDS or len(current) + len(word) > MAX_TOKEN_CHARS:
            chunks.append(current)
            current, count = "", 0
        current += word
        count += 1
    if current:
        chunks.append(current)
    return chunks


def shorten(text: str, missing: list[str]) -> str:
    """An answer within MAX_ANSWER_CHARS, cut at a line and said so (in the missing evidence)."""
    if len(text) <= MAX_ANSWER_CHARS:
        return text
    head = text[: MAX_ANSWER_CHARS - len(SHORTENED)]
    cut = head.rfind("\n")
    missing.append("The answer was shortened to fit the panel: ask about one link at a time")
    return (head[:cut] if cut > 0 else head).rstrip() + SHORTENED


def stream(run: Run, data: CaseData, info: s.GenerationInfo) -> s.AgentStream:
    """The AgentStream of a run (ADR-15 section 4, Agent: order and framing)."""
    events: list[dict[str, Any]] = []
    answer = run.answer
    if answer.refused:
        text = short_origin(REFUSAL)
        events.append({"type": "refusal", "seq": 0, "reason": "write_request", "text": text})
        events.append({"type": "done", "seq": 1, "steps_used": 0,
                       "generation": info.model_dump(mode="json")})  # fmt: skip
        return s.AgentStream.model_validate(events)
    steps, refused = steps_of(answer, run.calls)
    consulted: list[s.EntityRef] = []
    for number, step in enumerate(steps[:MAX_TOOL_CALLS], start=1):
        argument = "cluster_id" if step.tool == "get_cluster" else "return_id"
        ok, summary, refs = tool_result(step, data)
        events.append({"type": "tool_call", "seq": len(events), "step": number,
                       "tool": step.tool, "arguments": {argument: step.argument}})  # fmt: skip
        events.append({"type": "tool_result", "seq": len(events), "step": number,
                       "tool": step.tool, "ok": ok, "summary": short_origin(summary),
                       "refs": [r.model_dump() for r in _unique_refs(refs)]})  # fmt: skip
        if ok and step.tool == "get_cluster":
            consulted.append(_ref("cluster", step.argument))
        elif ok and step.tool == "get_orders":
            consulted.append(_ref("customer", data.customer_id))
        elif ok and step.tool in ("get_case", "get_score_factors"):
            consulted.append(_ref("return", data.case.case_id))
    missing = [short_origin(m) for m in (*answer.missing, *refused)]
    cited = _unique_refs([_ref(x.kind, x.id) for x in answer.links])
    text = shorten(answer.text, missing)
    for chunk in tokens_of(text):
        events.append({"type": "token", "seq": len(events), "text": chunk})
    events.append({
        "type": "answer",
        "seq": len(events),
        "text": text,
        "evidence_consulted": [r.model_dump() for r in _unique_refs(consulted)],
        "evidence_missing": missing,
        "refs": [r.model_dump() for r in cited],
    })  # fmt: skip
    used = min(len(steps), MAX_TOOL_CALLS)
    events.append({"type": "done", "seq": len(events), "steps_used": used,
                   "generation": info.model_dump(mode="json")})  # fmt: skip
    return s.AgentStream.model_validate(events)


# ---- POST /api/agent/ask -------------------------------------------------------------------------


def _digest(document: Mapping[str, Any]) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prompt_version(references: Mapping[str, str]) -> str:
    prompt = agent_system_prompt(MAX_TOOL_CALLS, references)
    return "agent-" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def check_available(settings: Settings, client_error: str | None) -> None:
    """503 llm_unavailable when no model can answer: the GPU is off in cached mode, or the
    vLLM configuration is refused (rule 2, H15)."""
    if settings.cached:
        raise llm_unavailable(
            "the agent needs the model: the GPU is off in cached mode (US-901); the case card "
            "shows the precomputed explanation"
        )
    if settings.llm_mode == "vllm" and client_error is not None:
        raise llm_unavailable(f"the local vLLM is not configured: {client_error}")


async def run(
    settings: Settings,
    client_of: Callable[[], object],
    call: Callable[..., Awaitable[Run]],
    data: CaseData,
    question: str,
) -> Run:
    """The agent's answer, with no database transaction open: `call` runs a blocking function
    on the runtime's model-call pool. 503 when vLLM does not answer."""
    if settings.llm_mode == "mock":
        return await call(run_mock, data, question)
    client = client_of()
    if not hasattr(client, "chat"):
        raise llm_unavailable("the configured model client has no chat (tool calling)")
    chat = cast(ChatClient, client)
    try:
        return await call(run_vllm, chat, settings.gemma_model, data, question)
    except LlmUnavailable as exc:
        raise llm_unavailable(f"the local vLLM did not answer: {exc}") from None


async def save(
    conn: AsyncConnection, body: s.AgentAskRequest, data: CaseData, answered: Run, at: datetime
) -> s.AgentStream:
    """The whole stream of one answer, stored in llm_generations (kind agent_answer)."""
    number = await next_number(
        conn, "GEN-AGENT", llm_generations, llm_generations.c.kind == "agent_answer"
    )
    info = s.GenerationInfo(
        generation_id=f"GEN-AGENT-{number:06d}",
        generation_path=answered.path,
        fallback_reason=answered.fallback_reason,
        llm_mode=answered.llm_mode,
        served_from="live",
        model=answered.model,
        model_calls=answered.model_calls,
        validator_codes=answered.validator_codes,
        created_at=at,
        latency_ms=answered.latency_ms,
    )
    events = stream(answered, data, info)
    question = {
        "kind": "agent_question",
        "case_id": body.return_id,
        "question": body.question,
        "lang": body.lang,
        "asked_by": body.asked_by,
        "references": dict(data.references),
    }
    answer = answered.answer
    await conn.execute(
        insert(llm_generations).values(
            generation_id=info.generation_id,
            kind="agent_answer",
            return_id=body.return_id,
            decision_id=data.decision_id,
            model=info.model,
            llm_mode=info.llm_mode,
            served_from=info.served_from,
            generation_path=info.generation_path,
            fallback_reason=info.fallback_reason,
            model_calls=info.model_calls,
            prompt_version=prompt_version(data.references),
            input_digest=_digest(question),
            llm_case=question,
            output={
                "source": answer.source,
                "text": answer.text,
                "consulted": list(answer.consulted),
                "missing": list(answer.missing),
                "links": [{"kind": x.kind, "id": x.id} for x in answer.links],
            },
            validator_codes=list(info.validator_codes),
            latency_ms=info.latency_ms,
            prompt_tokens=None,
            completion_tokens=None,
            tokens_per_second=None,
            created_at=at,
        )
    )
    return events


def check_rate(app: FastAPI, person: str, client: str | None = None) -> None:
    """429 rate_limited past AGENT_RATE_LIMIT questions of one person in the window. On the
    public read-only demo (`client`: roles.client_key) the count is per client and person:
    every visitor names one of three demo users, so one anonymous client must not spend the
    questions of all."""
    key = person if client is None else f"{client} {person}"
    wait = limiter_of(app).retry_after(key)
    if wait is not None:
        raise rate_limited(
            f"at most {AGENT_RATE_LIMIT} questions to the agent per person every "
            f"{AGENT_RATE_WINDOW_S:.0f} s",
            wait,
        )
