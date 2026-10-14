"""POST /api/agent/ask (US-701, US-702, spec 8.8, flow F5; ADR-15 section 4 "Agent"; LLM-07;
rules 1, 11 and 12).

The stream is framed as the examples (sse_frame), in the order of AgentStream: tool calls and
results (at most 5, read-only), token deltas, exactly one answer or refusal, done. The three
questions of US-702 work on the golden cases; a write request is refused before any tool or
model call; cached mode and an unavailable vLLM are 503; past the rate limit, 429.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from sqlalchemy import select

from apps.api.services.other.agent import RateLimiter, tokens_of
from contracts.sql import llm_generations
from llm.agent import REFUSAL
from llm.client import LlmUnavailable
from pipeline.generator.golden import ELENA, GIULIA, ROSSI_ACCOUNTS
from tests.api_harness import Api, golden_return, open_api, settings_for, validate_body
from tests.test_api_other_support import marco_return, parse_sse

LINK = "Which evidence supports the link between these linked accounts?"
ROSSI = "What makes this case different from the Rossi family?"
TERMS = "Which terms applied when the order was placed?"
WRITE = "Approve the refund and remove the restriction."


def _ask(api: Api, return_id: str, question: str, who: str = "sara") -> Any:
    body = {"return_id": return_id, "question": question, "lang": "en", "asked_by": who}
    return api.client.post("/api/agent/ask", json=body)


def _events(api: Api, return_id: str, question: str) -> list[dict[str, Any]]:
    response = _ask(api, return_id, question)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["X-ReturnRing-Mode"] == "live"
    events = parse_sse(response.text)
    validate_body("agent_stream", events)
    return events


def _error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    validate_body("error", body)
    assert body["code"] == code
    return body


def _of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


def _rossi_cluster(api: Api) -> str | None:
    clusters = api.serving.frame("features/clusters")
    rows = clusters.loc[
        clusters["customer_id"].isin(ROSSI_ACCOUNTS) & (clusters["cluster_size"] >= 2)
    ]
    return None if rows.empty else str(rows["cluster_id"].iloc[-1])


# ---- The three questions of US-702 (mock agent) -------------------------------------------------


def test_which_evidence_supports_the_link(api: Api) -> None:
    return_id, customer = marco_return(api)
    events = _events(api, return_id, LINK)
    calls = _of(events, "tool_call")
    assert [c["tool"] for c in calls] == ["get_case", "get_cluster"]
    assert calls[0]["arguments"] == {"return_id": return_id}
    cluster_id = calls[1]["arguments"]["cluster_id"]
    assert cluster_id.startswith("CL-")
    results = _of(events, "tool_result")
    assert all(r["ok"] for r in results) and len(results) == len(calls) <= 5
    (answer,) = _of(events, "answer")
    assert answer["text"] == "".join(t["text"] for t in _of(events, "token"))
    links = [r for r in answer["refs"] if r["kind"] == "link"]
    assert links and all(r["id"] in answer["text"] for r in links)
    assert all(customer in r["id"] or "~" in r["id"] for r in links)
    assert {"kind": "cluster", "id": cluster_id} in answer["evidence_consulted"]
    assert "same person" not in answer["text"].lower()  # rule 11
    done = events[-1]
    assert done["type"] == "done" and done["steps_used"] == 2
    assert (
        done["generation"]["llm_mode"] == "mock"
        and done["generation"]["generation_path"] == "model"
    )


def test_what_makes_the_case_different_from_the_rossi_family(api: Api) -> None:
    """The reference is the graph cluster of the Rossi accounts, an opaque id: never the truth
    family id (rule 12)."""
    reference = _rossi_cluster(api)
    return_id, _ = marco_return(api)
    events = _events(api, return_id, ROSSI)
    calls = [c for c in _of(events, "tool_call") if c["tool"] == "get_cluster"]
    (answer,) = _of(events, "answer")
    assert "F-ROSSI" not in json.dumps(events)
    if reference is None:
        pytest.skip("no cluster of the Rossi accounts in the tiny store")
    assert [c["arguments"]["cluster_id"] for c in calls][-1] == reference
    clusters = {r["id"] for r in answer["refs"] if r["kind"] == "cluster"}
    assert reference in clusters and len(clusters) == 2
    assert reference in answer["text"]


def test_which_terms_applied_when_the_order_was_placed(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    record = api.serving.store_return(return_id)
    assert record is not None
    events = _events(api, return_id, TERMS)
    assert [c["tool"] for c in _of(events, "tool_call")] == [
        "get_case",
        "get_orders",
        "get_policy_text",
    ]
    (answer,) = _of(events, "answer")
    assert record.order_id in answer["text"] and record.terms_snapshot_id in answer["text"]
    assert {"kind": "order", "id": record.order_id} in answer["refs"]
    orders = next(r for r in _of(events, "tool_result") if r["tool"] == "get_orders")
    assert {"kind": "terms_snapshot", "id": record.terms_snapshot_id} in orders["refs"]


def test_a_guarantee_case_has_no_score_factors(api: Api) -> None:
    """Rule 4: the guarantee path is never scored, so the agent finds no score factor."""
    events = _events(api, golden_return(api.serving, ELENA), "What is this case about?")
    calls = [c["tool"] for c in _of(events, "tool_call")]
    assert calls == ["get_case", "get_score_factors"]
    factors = next(r for r in _of(events, "tool_result") if r["tool"] == "get_score_factors")
    assert factors["ok"] is False
    (answer,) = _of(events, "answer")
    assert "guarantee path" in answer["text"] and "Score factor" not in answer["text"]
    assert any("not scored" in m for m in answer["evidence_missing"])


def test_a_case_without_linked_accounts_says_so(api: Api) -> None:
    events = _events(api, golden_return(api.serving, GIULIA), LINK)
    (answer,) = _of(events, "answer")
    assert "No linked accounts" in answer["text"]
    assert any("linked accounts" in m for m in answer["evidence_missing"])


def test_a_write_request_is_refused_before_any_tool(api: Api) -> None:
    return_id, _ = marco_return(api)
    events = _events(api, return_id, WRITE)
    assert [e["type"] for e in events] == ["refusal", "done"]
    assert events[0]["reason"] == "write_request" and events[0]["text"] == REFUSAL
    assert events[1]["steps_used"] == 0


def test_every_answer_is_kept_with_its_question(api: Api) -> None:
    return_id, _ = marco_return(api)
    _events(api, return_id, LINK)
    _events(api, return_id, WRITE)
    rows = api.run(
        lambda conn: conn.execute(
            select(llm_generations)
            .where(llm_generations.c.kind == "agent_answer")
            .order_by(llm_generations.c.generation_id)
        )
    )
    stored = [dict(r) for r in rows.mappings().all()] if hasattr(rows, "mappings") else []
    assert [r["generation_id"] for r in stored] == ["GEN-AGENT-000001", "GEN-AGENT-000002"]
    assert stored[0]["llm_case"]["question"] == LINK and stored[1]["output"]["source"] == "refusal"


# ---- Errors -------------------------------------------------------------------------------------


def test_roles_unknown_returns_and_invalid_questions(api: Api) -> None:
    return_id, _ = marco_return(api)
    _error(_ask(api, return_id, LINK, who="ahmed"), 403, "forbidden_role")
    _error(_ask(api, "RET-NOPE", LINK), 404, "not_found")
    _error(_ask(api, return_id, ""), 422, "invalid_request")


def test_the_rate_limit_answers_429(api: Api) -> None:
    api.app.state.agent_rate_limiter = RateLimiter(limit=2, window_s=60)
    return_id, _ = marco_return(api)
    assert _ask(api, return_id, LINK).status_code == 200
    assert _ask(api, return_id, LINK, who="luca").status_code == 200  # counted per person
    assert _ask(api, return_id, TERMS).status_code == 200
    body = _error(_ask(api, return_id, TERMS), 429, "rate_limited")
    assert "try again" in body["message"]


def test_a_sliding_window() -> None:
    now = [0.0]
    limiter = RateLimiter(limit=1, window_s=10, clock=lambda: now[0])
    assert limiter.retry_after("sara") is None
    assert limiter.retry_after("sara") == 10
    now[0] = 10.5
    assert limiter.retry_after("sara") is None


def test_cached_mode_is_503(api: Api) -> None:
    settings = settings_for(api.settings.serving_dir, api.database, demo_mode="cached")
    for cached in open_api(api.serving, settings, api.database, clock=api.clock):
        return_id, _ = marco_return(cached)
        body = _error(_ask(cached, return_id, LINK), 503, "llm_unavailable")
        assert body["request_id"]


def test_tokens_rebuild_the_answer() -> None:
    text = "word " * 300 + "x" * 900 + "\n- line two"
    chunks = tokens_of(text)
    assert "".join(chunks) == text and all(0 < len(c) <= 400 for c in chunks)


# ---- vLLM mode (Gemma tool calling through the same client protocol) ----------------------------


class FakeChat:
    """A ChatClient that plays scripted assistant messages (or raises)."""

    def __init__(self, script: Sequence[Mapping[str, Any] | BaseException]) -> None:
        self.script = list(script)
        self.calls = 0

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return dict(item)

    def complete(self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()) -> Any:
        raise LlmUnavailable("drafts are not part of these tests")


def _tool(name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    call = {"id": f"call-{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments or {})}}  # fmt: skip
    return {"content": "", "tool_calls": [call]}


def _vllm_events(api: Api, client: FakeChat, return_id: str, question: str) -> Any:
    settings = settings_for(
        api.settings.serving_dir, api.database, llm_mode="vllm",
        vllm_base_url="http://localhost:8001", vllm_api_key="test-key",
    )  # fmt: skip
    for live in open_api(api.serving, settings, api.database, clock=api.clock, llm_client=client):  # type: ignore[arg-type]
        return _ask(live, return_id, question)
    raise AssertionError("open_api yields once")


def test_vllm_answer_checked_and_streamed(api: Api) -> None:
    return_id, _ = marco_return(api)
    record = api.serving.store_return(return_id)
    assert record is not None
    client = FakeChat(
        [_tool("get_case"), {"content": f"Order {record.order_id} is the order of this case."}]
    )
    response = _vllm_events(api, client, return_id, "Which order is this case about?")
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    validate_body("agent_stream", events)
    (answer,) = _of(events, "answer")
    assert answer["text"] == f"Order {record.order_id} is the order of this case."
    generation = events[-1]["generation"]
    assert generation["llm_mode"] == "vllm" and generation["generation_path"] == "model"
    assert generation["model_calls"] == 1 and events[-1]["steps_used"] == 1


def test_vllm_unverifiable_answers_fall_back_after_one_retry(api: Api) -> None:
    return_id, _ = marco_return(api)
    ungrounded = {"content": "This case has 4242 orders on file."}
    client = FakeChat([ungrounded, ungrounded])
    response = _vllm_events(api, client, return_id, "How many orders are on file?")
    events = parse_sse(response.text)
    validate_body("agent_stream", events)
    (answer,) = _of(events, "answer")
    assert answer["text"].startswith("No verified answer")
    generation = events[-1]["generation"]
    assert (generation["generation_path"], generation["fallback_reason"]) == (
        "template",
        "invalid_twice",
    )
    assert generation["model_calls"] == 2 and generation["validator_codes"] == ["UNGROUNDED_NUMBER"]


def test_vllm_decision_wording_goes_to_the_template_at_once(api: Api) -> None:
    return_id, _ = marco_return(api)
    client = FakeChat([{"content": "You should reject this return and block the coupons."}])
    response = _vllm_events(api, client, return_id, "What do you see in this case?")
    events = parse_sse(response.text)
    generation = events[-1]["generation"]
    assert (generation["generation_path"], generation["fallback_reason"]) == (
        "template",
        "decision_echo",
    )
    assert generation["model_calls"] == 1 and "DECISION_ECHO" in generation["validator_codes"]
    assert client.calls == 1


def test_vllm_down_is_503(api: Api) -> None:
    return_id, _ = marco_return(api)
    client = FakeChat([LlmUnavailable("vLLM unreachable: URLError")])
    _error(
        _vllm_events(api, client, return_id, "What do you see in this case?"),
        503,
        "llm_unavailable",
    )


def test_vllm_write_request_never_calls_the_model(api: Api) -> None:
    return_id, _ = marco_return(api)
    client = FakeChat([])
    events = parse_sse(_vllm_events(api, client, return_id, WRITE).text)
    assert [e["type"] for e in events] == ["refusal", "done"] and client.calls == 0
