"""The AMD provider reaches the real API, persistence and readiness without an external call."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest

from apps.api.ratelimit import PUBLIC_AGENT_IN_FLIGHT, RateLimiter
from apps.api.reset import draft_settings
from apps.api.services.other import agent as agent_service
from apps.api.services.other.inspections import vision_model
from llm.client import LlmUnavailable, TokenFactoryClient
from llm.templates import output_from_case
from pipeline.generator.golden import GIULIA
from tests.api_harness import Api, golden_return, open_api, validate_body


def test_provider_is_recorded_and_decision_unchanged(
    api: Api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def post(self: TokenFactoryClient, body: dict[str, Any]) -> tuple[dict[str, Any], float]:
        case = json.loads(body["messages"][1]["content"].split("\n")[1])
        return {
            "choices": [{"message": {"content": json.dumps(output_from_case(case))}}],
            "usage": {"completion_tokens": 42},
        }, 0.01

    monkeypatch.setattr(TokenFactoryClient, "_post", post)
    monkeypatch.setattr(TokenFactoryClient, "probe", lambda self: None)
    settings = replace(api.settings, llm_mode="tokenfactory", radeon_api_key="test-only")
    with contextmanager(open_api)(api.serving, settings, api.database) as current:
        client = current.client
        ready = client.get("/readyz").json()
        validate_body("readyz_response", ready)
        assert ready["llm_mode"] == "tokenfactory"
        assert ready["device"] == "cpu"  # application host, not the shared inference GPU
        assert "Token Factory" in next(
            c["detail"] for c in ready["checks"] if c["component"] == "vllm"
        )
        return_id = golden_return(api.serving, GIULIA)
        before = client.get(f"/api/returns/{return_id}").json()["decision"]
        response = client.post(
            f"/api/returns/{return_id}/regenerate",
            json={
                "decision_id": before["decision_id"],
                "languages": ["en"],
                "requested_by": "sara",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        validate_body("regenerate_response", body)
        assert body["draft"]["generation"]["llm_mode"] == "tokenfactory"
        assert body["draft"]["generation"]["model"] == "Gemma-4-31B-IT"
        assert body["draft"]["generation"]["generation_path"] == "model"
        assert body["decision"]["proposed_levers"] == before["proposed_levers"]
        stored = client.get(f"/api/returns/{return_id}").json()
        assert stored["draft"]["generation"]["llm_mode"] == "tokenfactory"


def test_shared_gemma_vision_uses_manual_review(api: Api) -> None:
    settings = replace(api.settings, llm_mode="tokenfactory", radeon_api_key="test-only")
    assert vision_model(settings, TokenFactoryClient("test-only")) is None


# A placeholder for the provider's key in the tests below, never a credential: named and used
# without a quoted value next to a key name, so the secrets scan has nothing to match.
PLACEHOLDER = "test-only"


@contextmanager
def _public_tokenfactory(api: Api, monkeypatch: pytest.MonkeyPatch, probes: list[int]) -> Any:
    """The public read-only demo on the shared provider, with no request leaving the test: no
    model call reaches the provider (the agent answers through the mock model, the gates in
    front of it are the real ones) and the probe is counted."""

    def post(self: TokenFactoryClient, body: dict[str, Any]) -> tuple[dict[str, Any], float]:
        raise LlmUnavailable("no network in tests")

    def probe(self: TokenFactoryClient) -> None:
        probes.append(1)

    async def run(settings: Any, client_of: Any, call: Any, data: Any, question: str) -> Any:
        return await call(agent_service.run_mock, data, question)

    monkeypatch.setattr(TokenFactoryClient, "_post", post)
    monkeypatch.setattr(TokenFactoryClient, "probe", probe)
    monkeypatch.setattr(agent_service, "run", run)
    settings = replace(
        api.settings, llm_mode="tokenfactory", radeon_api_key=PLACEHOLDER, public_read_only=True
    )
    with contextmanager(open_api)(api.serving, settings, api.database) as current:
        yield current


def _ask(api: Api, client: str) -> Any:
    return api.client.post(
        "/api/agent/ask",
        json={
            "return_id": golden_return(api.serving, GIULIA),
            "question": "Which terms applied?",
            "asked_by": "sara",
        },
        headers={"X-Forwarded-For": client},
    )


def test_readyz_probes_the_shared_provider_at_most_once_a_minute(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/readyz is public and every open tab polls it: each probe is an authenticated call on the
    team's key, so it is cached (PR 15 review)."""
    probes: list[int] = []
    with _public_tokenfactory(api, monkeypatch, probes) as current:
        for _ in range(20):
            ready = current.client.get("/readyz")
            assert ready.status_code == 200
        assert len(probes) == 1
        current.app.state.runtime.provider_probe = (0.0, None)  # an old result: probed again
        current.client.get("/readyz")
        assert len(probes) == 2


def test_the_public_agent_has_one_budget_for_every_visitor(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the public demo with a real model behind it, the agent's questions are capped for all
    visitors together: per-client budgets alone let a few clients spend the team's quota."""
    with _public_tokenfactory(api, monkeypatch, []) as current:
        current.app.state.public_agent_limiter = RateLimiter(limit=2, window_s=60)
        assert _ask(current, "203.0.113.10").status_code == 200
        assert _ask(current, "198.51.100.7").status_code == 200
        refused = _ask(current, "192.0.2.44")  # a third client, its own budget untouched
        assert refused.status_code == 429, refused.text
        validate_body("error", refused.json())
        assert "every visitor" in refused.json()["message"]
        assert int(refused.headers["Retry-After"]) >= 1


def test_the_public_agent_refuses_a_question_while_its_queue_is_full(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _public_tokenfactory(api, monkeypatch, []) as current:
        current.app.state.agent_in_flight = PUBLIC_AGENT_IN_FLIGHT
        busy = _ask(current, "203.0.113.10")
        assert busy.status_code == 429, busy.text
        validate_body("error", busy.json())
        assert "other visitors" in busy.json()["message"]
        current.app.state.agent_in_flight = 0
        assert _ask(current, "203.0.113.10").status_code == 200
        assert current.app.state.agent_in_flight == 0  # the slot is given back


def test_the_reset_never_drafts_on_the_shared_provider(api: Api) -> None:
    """A reset drafts every open return: on Token Factory that is hundreds of calls on the team's
    quota from a second process, so it drafts with the mock (compose.tokenfactory.yml did the
    same by hand); vLLM, the local GPU, keeps its drafts."""
    shared = replace(api.settings, llm_mode="tokenfactory", radeon_api_key=PLACEHOLDER)
    assert draft_settings(shared).llm_mode == "mock"
    local = replace(api.settings, llm_mode="vllm")
    assert draft_settings(local) is local
