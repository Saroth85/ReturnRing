"""The AMD provider reaches the real API, persistence and readiness without an external call."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest

from apps.api.services.other.inspections import vision_model
from llm.client import TokenFactoryClient
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
