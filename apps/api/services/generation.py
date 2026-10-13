"""Drafts of a decision version through llm/ (ADR-14, ADR-15 section 4 "Draft"; spec 8.7; rules
1, 2 and 10; US-304, US-305, US-901).

llm.case.build_case of the engine's decision of the version (s.proposal_decision): the code-built
facts, the score factors shown on the card (llm.case.factor_entity), the cluster the engine was
given; then llm.generate.generate with:

- LLM_MODE=mock: MockClient, the deterministic model of llm/client.py;
- LLM_MODE=vllm: VllmClient on the local vLLM (rule 2), guided JSON;
- DEMO_MODE=cached: the output vLLM produced on the MI300X for the same llm_case, read from
  SERVING_DIR/llm_cache/<sha256 of the llm_case>.json (python -m apps.api.batch_generate,
  ADR-03), validated again by llm.validator, served as cached. An llm_case the cache does not
  hold (or holds an invalid output for) gets the fixed template, served live and labelled with
  its path (template) and fallback reason (unavailable).

The validated output becomes a DraftView (DraftView.build composes the messages with
llm.phrasebook), stored in llm_generations with the llm_case it answered, and logged
(draft_generated or draft_regenerated, GenerationAuditState).
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.audit import record
from apps.api.services.ids import generation_id
from apps.api.settings import Settings
from contracts.sql import decisions, llm_generations
from contracts.vocab import LLM_MODES, AuditAction, GenerationKind, Lang
from llm.case import ClusterSummary, ScoreFactor, build_case, factor_entity
from llm.client import Completion, LlmClient, LlmUnavailable, MockClient, VllmClient, VllmConfig
from llm.decision import ExpectedDecision, serialize_decision
from llm.generate import Generation, generate
from llm.prompts import SYSTEM_PROMPT
from llm.text import LANGS
from llm.validator import LlmOutput, validate
from policy.types import EvidenceQuality, ReturnRequest
from policy.types import Path as ReturnPath

CACHE_FORMAT: Final = "returnring.llm_cache.v1"
PROMPT_VERSION: Final = "prompts-" + hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]
MOCK_MODEL: Final = "mock"
# The merchant's return policy, given to the model as data (spec 8.7; get_policy_text of 8.8).
POLICY_TEXT: Final = (
    "Navigli Fashion return policy (demo): returns within the window of the order terms, refund "
    "within 14 days of the notice, the legal guarantee for defects."
)
_ACTIONS: Final[dict[GenerationKind, AuditAction]] = {
    "case_draft": "draft_generated",
    "regenerate": "draft_regenerated",
}


def input_digest(case: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of an llm_case: the key of the MI300X cache."""
    text = json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DraftInputs:
    """What a draft explains: a decision version, its request, and what the card shows with it."""

    view: s.DecisionView
    request: ReturnRequest
    factors: Sequence[s.ScoreContribution]  # the score factors shown with the version
    cluster: ClusterSummary | None  # the cluster the engine was given, with its size
    customer_text: str | None = None
    languages: Sequence[Lang] = LANGS


@dataclass(frozen=True)
class LlmCase:
    case: dict[str, Any]
    expected: ExpectedDecision
    digest: str


def llm_case(inputs: DraftInputs) -> LlmCase:
    """llm.case.build_case of the engine's decision of the version (ADR-15 Consequences)."""
    view = inputs.view
    decision = s.proposal_decision(view)
    base = view.inputs.base_terms.to_policy()
    scored = view.path is ReturnPath.WITHDRAWAL and (
        view.evidence_quality is EvidenceQuality.SUFFICIENT
    )
    clustered = scored and inputs.cluster is not None
    factors = [
        ScoreFactor(f.feature, _number(f), f.phrase, factor_entity(f.feature, clustered))
        for f in (inputs.factors if scored else ())
    ]
    case = build_case(
        decision,
        inputs.request,
        base,
        policy_text=POLICY_TEXT,
        scores=view.inputs.scores() if scored else None,
        factors=factors,
        cluster=inputs.cluster if clustered else None,
        missing_evidence=view.missing_evidence,
        customer_text=inputs.customer_text,
        nature=view.inputs.provenance.nature,
        languages=list(inputs.languages),
    )
    _, expected = serialize_decision(decision, base)
    return LlmCase(case, expected, input_digest(case))


def _number(factor: s.ScoreContribution) -> int | float:
    value = factor.feature_value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"the score factor {factor.feature} of a draft is a number")
    return value


@dataclass(frozen=True)
class Outcome:
    """A validated generation: the draft and what llm_generations stores of it."""

    kind: GenerationKind
    draft: s.DraftView
    case: dict[str, Any]
    digest: str
    completion_tokens: int | None

    @property
    def info(self) -> s.GenerationInfo:
        return self.draft.generation


class _Unavailable:
    """The model is off: llm.generate falls back to the template (cached mode, cache miss)."""

    def complete(self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()) -> Completion:
        raise LlmUnavailable("no cached output for this case and no model in cached mode")


class _Recording:
    """A client that keeps the completions it returned (tokens for llm_generations)."""

    def __init__(self, inner: LlmClient) -> None:
        self.inner = inner
        self.completions: list[Completion] = []

    def complete(self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()) -> Completion:
        completion = self.inner.complete(case, retry_codes=retry_codes)
        self.completions.append(completion)
        return completion


def cache_path(cache_dir: Path, digest: str) -> Path:
    return cache_dir / f"{digest}.json"


def cache_entry(
    item: LlmCase, result: Generation, model: str, created_at: datetime, latency_ms: int | None,
    completion_tokens: int | None,
) -> dict[str, Any]:  # fmt: skip
    """The cache entry of a vLLM generation (written by python -m apps.api.batch_generate)."""
    return {
        "format": CACHE_FORMAT,
        "input_digest": item.digest,
        "case_id": item.case["case_id"],
        "model": model,
        "generation_path": result.path.value,
        "fallback_reason": result.fallback_reason,
        "model_calls": result.model_calls,
        "validator_codes": list(dict.fromkeys(i.code.value for i in result.issues)),
        "output": result.output.model_dump(mode="json"),
        "latency_ms": latency_ms,
        "completion_tokens": completion_tokens,
        "prompt_version": PROMPT_VERSION,
        "created_at": created_at.isoformat(),
    }


class Generator:
    """Makes drafts in the mode of the settings (see the module docstring)."""

    def __init__(self, settings: Settings, client: LlmClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._vllm: VllmClient | None = None
        self.cache_dir = settings.serving_dir / "llm_cache"

    def client(self) -> LlmClient:
        """The injected client, else one VllmClient (LLM_MODE=vllm, kept for the process), else
        a fresh MockClient (stateless: its default answer is derived from the case)."""
        if self._client is not None:
            return self._client
        if self.settings.llm_mode == "vllm":
            if self._vllm is None:
                self._vllm = VllmClient(
                    VllmConfig(
                        base_url=self.settings.vllm_base_url or "",
                        api_key=self.settings.vllm_api_key or "",
                        model=self.settings.gemma_model,
                    )
                )
            return self._vllm
        return MockClient()

    @property
    def model_name(self) -> str:
        if self._client is not None and not isinstance(self._client, VllmClient):
            return MOCK_MODEL
        return self.settings.gemma_model if self.settings.llm_mode == "vllm" else MOCK_MODEL

    def generate(
        self,
        inputs: DraftInputs,
        *,
        generation_id: str,
        created_at: datetime,
        kind: GenerationKind = "case_draft",
    ) -> Outcome:
        item = llm_case(inputs)
        if self.settings.cached:
            cached = self._from_cache(item, inputs, generation_id, created_at, kind)
            if cached is not None:
                return cached
            return self._run(item, inputs, _Unavailable(), generation_id, created_at, kind)
        return self._run(item, inputs, self.client(), generation_id, created_at, kind)

    def _run(
        self,
        item: LlmCase,
        inputs: DraftInputs,
        client: LlmClient,
        gen_id: str,
        created_at: datetime,
        kind: GenerationKind,
    ) -> Outcome:
        recorder = _Recording(client)
        started = time.perf_counter()
        result = generate(item.case, item.expected, recorder)
        measured = self.settings.llm_mode == "vllm" and result.model_calls > 0
        latency = round((time.perf_counter() - started) * 1000) if measured else None
        tokens = [c.completion_tokens for c in recorder.completions if c.completion_tokens]
        info = s.GenerationInfo.from_generation(
            result,
            generation_id=gen_id,
            llm_mode=self.settings.llm_mode,
            served_from="live",
            model=self.model_name,
            created_at=created_at,
            latency_ms=latency,
        )
        return self._outcome(kind, info, result.output, item, inputs, sum(tokens) or None)

    def _from_cache(
        self,
        item: LlmCase,
        inputs: DraftInputs,
        gen_id: str,
        created_at: datetime,
        kind: GenerationKind,
    ) -> Outcome | None:
        path = cache_path(self.cache_dir, item.digest)
        if not path.is_file():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry.get("format") != CACHE_FORMAT or entry.get("input_digest") != item.digest:
                return None
            checked = validate(entry["output"], item.expected, item.case)  # validated again
            if checked.output is None:
                return None
            info = s.GenerationInfo(
                generation_id=gen_id,
                generation_path=entry["generation_path"],
                fallback_reason=entry["fallback_reason"],
                llm_mode="vllm",
                served_from="cached",
                model=str(entry["model"]),
                model_calls=int(entry["model_calls"]),
                validator_codes=list(entry["validator_codes"]),
                created_at=created_at,
                latency_ms=entry.get("latency_ms"),
            )
            return self._outcome(
                kind, info, checked.output, item, inputs, entry.get("completion_tokens")
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None  # a broken entry is a miss: the template answers, labelled

    @staticmethod
    def _outcome(
        kind: GenerationKind,
        info: s.GenerationInfo,
        output: LlmOutput,
        item: LlmCase,
        inputs: DraftInputs,
        tokens: int | None,
    ) -> Outcome:
        view = inputs.view
        draft = s.DraftView.build(
            info,
            output,
            item.case,
            s.proposal_decision(view),
            view.inputs.base_terms.to_policy(),
            view.inputs.provenance,
        )
        return Outcome(kind, draft, copy.deepcopy(item.case), item.digest, tokens)


async def next_generation_id(conn: AsyncConnection, return_id: str) -> str:
    count = await conn.scalar(
        select(func.count())
        .select_from(llm_generations)
        .where(llm_generations.c.return_id == return_id)
    )
    return generation_id(return_id, int(count or 0) + 1)


async def store(
    conn: AsyncConnection,
    outcome: Outcome,
    view: s.DecisionView,
    *,
    actor: str = "system",
    request_id: str | None = None,
) -> s.AuditEntry:
    """Store a generation, make it the draft of its version and log it."""
    info = outcome.info
    tokens = outcome.completion_tokens
    latency = info.latency_ms
    await conn.execute(
        insert(llm_generations).values(
            generation_id=info.generation_id,
            kind=outcome.kind,
            return_id=view.return_id,
            decision_id=view.decision_id,
            model=info.model,
            llm_mode=info.llm_mode,
            served_from=info.served_from,
            generation_path=info.generation_path,
            fallback_reason=info.fallback_reason,
            model_calls=info.model_calls,
            prompt_version=PROMPT_VERSION,
            input_digest=outcome.digest,
            llm_case=outcome.case,
            output=outcome.draft.llm_output(),
            validator_codes=list(info.validator_codes),
            latency_ms=latency,
            prompt_tokens=None,
            completion_tokens=tokens,
            tokens_per_second=(tokens / (latency / 1000)) if tokens and latency else None,
            created_at=info.created_at,
        )
    )
    await conn.execute(
        update(decisions)
        .where(decisions.c.decision_id == view.decision_id)
        .values(generation_id=info.generation_id, generation_path=info.generation_path)
    )
    return await record(
        conn,
        at=info.created_at,
        actor=actor,
        action=_ACTIONS[outcome.kind],
        entity_type="llm_generation",
        entity_id=info.generation_id,
        return_id=view.return_id,
        decision_id=view.decision_id,
        after=s.GenerationAuditState.from_info(info, outcome.kind, view.decision_id),
        rules_version=view.rules_version,
        model_versions=view.model_versions,
        assumptions_version=view.assumptions_version,
        request_id=request_id,
    )


def info_of(row: Mapping[str, Any]) -> s.GenerationInfo:
    if row["llm_mode"] not in LLM_MODES:
        raise ValueError(f"unknown llm_mode {row['llm_mode']!r}")
    return s.GenerationInfo(
        generation_id=row["generation_id"],
        generation_path=row["generation_path"],
        fallback_reason=row["fallback_reason"],
        llm_mode=row["llm_mode"],
        served_from=row["served_from"],
        model=row["model"],
        model_calls=row["model_calls"],
        validator_codes=list(row["validator_codes"]),
        created_at=row["created_at"],
        latency_ms=row["latency_ms"],
    )


def draft_of(row: Mapping[str, Any], view: s.DecisionView) -> s.DraftView:
    """The draft stored in an llm_generations row, for the version `view` it explains: the
    stored output on the stored llm_case, the messages composed again by llm.phrasebook."""
    output = LlmOutput.model_validate(row["output"])
    return s.DraftView.build(
        info_of(row),
        output,
        row["llm_case"],
        s.proposal_decision(view),
        view.inputs.base_terms.to_policy(),
        view.inputs.provenance,
    )


async def load_draft(conn: AsyncConnection, view: s.DecisionView) -> s.DraftView | None:
    if view.generation_id is None:
        return None
    row = (
        (
            await conn.execute(
                select(llm_generations).where(llm_generations.c.generation_id == view.generation_id)
            )
        )
        .mappings()
        .first()
    )
    return None if row is None else draft_of(dict(row), view)
