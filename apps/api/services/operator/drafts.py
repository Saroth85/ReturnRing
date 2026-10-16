"""A new draft of the latest version: POST /api/returns/{return_id}/regenerate (US-304, US-305;
ADR-15 section 4 "Draft" and "Regeneration"; rules 1, 2 and 10).

llm.generate runs again on the case of the version (services.generation: llm.case.build_case of
the engine's decision, the score factors shown on the card and the cluster the engine was
given, as the case card rebuilds them), in the languages asked for, with no free-text
instruction and no tone (NFR-11, ADR-15 round 4, E14). The output passes llm.validator before
anyone sees it; it is stored with its llm_case, becomes the draft of the version and is logged
(draft_regenerated, by the person who asked).

503 llm_unavailable when no model can answer a regeneration (ADR-15 section 2): in cached mode
the model is off (US-901: the cache holds the drafts of the reset, not new ones), and in live
vLLM mode with vLLM unreachable llm.generate could only give the template again. The stored
draft stays and nothing is written. In mock mode the mock model answers.

The model call runs outside any transaction, on the runtime's model-call pool: the case is
read in one, the draft stored in a second one that checks the version is still the latest (409
stale_decision otherwise) and gives it its id. A vLLM configuration the client refuses is an
unavailable model: 503, never a 500 (1.5.0, H15).
"""

from __future__ import annotations

from datetime import datetime

from apps.api import schemas as s
from apps.api.errors import llm_unavailable, stale_decision
from apps.api.runtime import Runtime
from apps.api.services import decisions as store
from apps.api.services.cases import cluster_summary, policy_request
from apps.api.services.generation import DraftInputs
from apps.api.services.generation import store as store_generation
from apps.api.services.operator.graph import view_for
from apps.api.services.operator.shown import return_of, shown
from policy.types import EvidenceQuality
from policy.types import Path as ReturnPath

CACHED_OFF = "The language model is off in cached mode: no new draft can be generated (US-901)"
VLLM_DOWN = "vLLM is unreachable: no new draft can be generated now; the stored draft stays"


async def regenerate(
    rt: Runtime,
    return_id: str,
    body: s.RegenerateRequest,
    now: datetime,
    request_id: str | None = None,
) -> s.RegenerateResponse:
    generator = rt.generator
    serving = rt.require_serving()
    async with rt.transaction() as conn:
        case = await shown(conn, serving, return_id, body.decision_id, now)
        graph = await view_for(conn, serving, case.row)
    if rt.settings.cached:
        raise llm_unavailable(CACHED_OFF)
    if rt.settings.llm_mode != "mock":
        generator.client()
        if generator.client_error is not None:
            raise llm_unavailable(f"{VLLM_DOWN} (the local vLLM is not configured)")
    view, record = case.view, case.record
    context = graph.customer_context(record.customer_id, record.known_at)
    scored = view.path is ReturnPath.WITHDRAWAL and (
        view.evidence_quality is EvidenceQuality.SUFFICIENT
    )
    inputs = DraftInputs(
        view=view,
        request=policy_request(record),
        factors=store.factors_of(case.row) if scored else [],
        cluster=cluster_summary(graph, context),
        customer_text=record.customer_text,
        languages=list(body.languages),
    )
    outcome = await rt.model_call(generator.generate, inputs, created_at=now, kind="regenerate")
    info = outcome.info
    if rt.settings.llm_mode != "mock" and info.fallback_reason == "unavailable":
        raise llm_unavailable(VLLM_DOWN)
    async with rt.transaction() as conn:
        await return_of(conn, return_id, lock=True)
        rows = await store.decision_rows(conn, return_id)
        if rows[-1]["decision_id"] != view.decision_id:
            raise stale_decision(view.decision_id, rows[-1]["decision_id"])
        _, outcome = await store_generation(
            conn, outcome, view, actor=body.requested_by, request_id=request_id
        )
        row = await store.decision_row_by_id(conn, view.decision_id)
        assert row is not None
        served = await store.view_of(conn, row, now, line_price=case.line_price)
    return s.RegenerateResponse(return_id=return_id, decision=served, draft=outcome.draft)
