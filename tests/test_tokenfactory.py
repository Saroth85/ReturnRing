"""AMD adapter: real validator and HTTP transport, with no external network or secrets."""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import pytest

from apps.api.settings import Settings
from llm.client import (
    MAX_QUOTA_WAIT_S,
    TOKENFACTORY_BASE_URL,
    TOKENFACTORY_MODEL,
    LlmUnavailable,
    RateLimited,
    TokenFactoryClient,
    VllmClient,
    client_from_env,
)
from llm.generate import generate
from llm.templates import output_from_case
from tests.llm_factories import make
from tests.test_llm_client import FakeServer, chat_response

pytest_plugins = ["tests.test_llm_client"]


@pytest.mark.parametrize("key", ["", "bad\nkey", "bad\rkey"])
def test_missing_or_multiline_key_is_refused(key: str) -> None:
    with pytest.raises(ValueError, match="RADEON_API_KEY"):
        TokenFactoryClient(key)


def test_environment_and_settings_are_explicit() -> None:
    env = {"LLM_MODE": "tokenfactory", "RADEON_API_KEY": "test-only"}
    client = client_from_env(env)
    assert isinstance(client, TokenFactoryClient)
    assert client.endpoint == TOKENFACTORY_BASE_URL + "/chat/completions"
    settings = Settings.from_env(env)
    assert settings.gemma_model == TOKENFACTORY_MODEL
    assert settings.llm_concurrency == 1
    assert settings.vllm_timeout_s == 90
    assert "test-only" not in repr(settings)
    assert "test-only" not in repr(client.config)
    with pytest.raises(ValueError, match="DEMO_MODE=live"):
        Settings.from_env({**env, "DEMO_MODE": "cached"})


def test_json_object_still_passes_the_real_validator(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = make("Q4_cluster")
    server.reply(chat_response(json.dumps(output_from_case(scenario.case))))
    client = TokenFactoryClient("test-only")
    client.endpoint = server.url + "/v1/chat/completions"
    monkeypatch.setattr("llm.client.time.sleep", lambda _: None)
    result = generate(scenario.case, scenario.expected, client)
    assert result.path == "model"
    body = server.requests[0]["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert "Required JSON schema:" in body["messages"][0]["content"]
    assert body["model"] == TOKENFACTORY_MODEL
    assert body["reasoning_effort"] == "low"


def test_changed_decision_uses_template(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = make("Q4_cluster")
    changed = output_from_case(scenario.case)
    changed["decision_echo"]["quadrant"] = "Q1"
    server.reply(chat_response(json.dumps(changed)))
    client = TokenFactoryClient("test-only")
    client.endpoint = server.url + "/v1/chat/completions"
    monkeypatch.setattr("llm.client.time.sleep", lambda _: None)
    result = generate(scenario.case, scenario.expected, client)
    assert result.fallback_reason == "decision_echo"
    assert result.output.decision_echo.quadrant == "Q4"


def test_tools_are_preserved_and_photos_refused(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TokenFactoryClient("test-only")
    client.endpoint = server.url + "/v1/chat/completions"
    monkeypatch.setattr("llm.client.time.sleep", lambda _: None)
    tools = [{"type": "function", "function": {"name": "get_case", "parameters": {}}}]
    client.chat([{"role": "system", "content": "Read only"}], tools=tools)
    assert server.requests[0]["body"]["tools"] == tools
    with pytest.raises(LlmUnavailable, match="photos"):
        client.chat([{"role": "user", "content": [{"type": "image_url"}]}])
    assert len(server.requests) == 1


def test_rate_limit_waits_then_succeeds(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server.reply({}, status=429, headers={"Retry-After": "7"})
    client = TokenFactoryClient("test-only")
    client.endpoint = server.url + "/v1/chat/completions"
    waits: list[float] = []
    monkeypatch.setattr("llm.client.time.sleep", waits.append)
    client.complete(make("Q1").case)
    assert len(server.requests) == 2
    assert any(7.1 <= wait <= 7.4 for wait in waits)


def test_rate_limit_exhaustion_is_bounded(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for _ in range(3):
        server.reply({}, status=429, headers={"Retry-After": "0"})
    client = TokenFactoryClient("test-only")
    client.endpoint = server.url + "/v1/chat/completions"
    monkeypatch.setattr("llm.client.time.sleep", lambda _: None)
    with pytest.raises(RateLimited):
        client.complete(make("Q1").case)
    assert len(server.requests) == 3


@pytest.mark.parametrize(
    "header, delay",
    [
        (None, 60),
        ("bad", 60),
        ("-1", 60),
        ("nan", 60),
        ("inf", 60),
        ("200", 200),  # the provider's window, never cut short (a retry inside it is refused)
        ("7200", 3600),  # at most an hour
        ("0", 0),
    ],
)
def test_bad_retry_header_has_a_bounded_default(header: str | None, delay: int) -> None:
    assert RateLimited(header).delay_s == delay


def test_an_http_date_retry_header_is_the_seconds_until_then() -> None:
    when = datetime.now(UTC) + timedelta(seconds=90)
    delay = RateLimited(format_datetime(when, usegmt=True)).delay_s
    assert 85 <= delay <= 91
    past = datetime.now(UTC) - timedelta(seconds=90)
    assert RateLimited(format_datetime(past, usegmt=True)).delay_s == 0


def test_a_long_provider_window_fails_fast_and_is_kept(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 whose window is longer than MAX_QUOTA_WAIT_S is not waited out while holding the
    client's lock (every other call would queue behind it for minutes); the next call inside the
    window is refused without a request, so the shared quota is never hit again too early."""
    server.reply({}, status=429, headers={"Retry-After": "300"})
    client = TokenFactoryClient("test-only")
    client.endpoint = server.url + "/v1/chat/completions"
    waits: list[float] = []
    monkeypatch.setattr("llm.client.time.sleep", waits.append)
    with pytest.raises(RateLimited):
        client.complete(make("Q1").case)
    assert len(server.requests) == 1
    with pytest.raises(RateLimited) as refused:
        client.complete(make("Q1").case)
    assert len(server.requests) == 1  # nothing sent inside the provider's window
    assert refused.value.delay_s > MAX_QUOTA_WAIT_S
    assert all(wait <= MAX_QUOTA_WAIT_S for wait in waits)


def test_the_next_call_respects_the_last_backoff(
    server: FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a 429 every later call starts no earlier than its Retry-After, not 3.1 s later."""
    for _ in range(3):
        server.reply({}, status=429, headers={"Retry-After": "20"})
    client = TokenFactoryClient("test-only")
    client.endpoint = server.url + "/v1/chat/completions"
    now = [1000.0]
    monkeypatch.setattr("llm.client.time.monotonic", lambda: now[0])
    monkeypatch.setattr("llm.client.time.sleep", lambda s: now.__setitem__(0, now[0] + s))
    with pytest.raises(RateLimited):
        client.complete(make("Q1").case)
    sent_at = [now[0]]
    server.reply(chat_response(json.dumps(output_from_case(make("Q1").case))))
    client.complete(make("Q1").case)
    assert len(server.requests) == 4
    assert now[0] - sent_at[0] >= 20  # the new call waited for the provider's window


@pytest.mark.parametrize(
    "body, ok",
    [
        ({"data": [{"id": TOKENFACTORY_MODEL}]}, True),
        ({"data": [{"id": "other"}]}, False),
        ({"data": "bad"}, False),
        ([], False),
    ],
)
def test_catalog_probe(body: Any, ok: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TokenFactoryClient("test-only")
    monkeypatch.setattr(
        client._opener, "open", lambda *a, **kw: io.BytesIO(json.dumps(body).encode())
    )
    assert (client.probe() is None) is ok


def test_catalog_probe_error_does_not_expose_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TokenFactoryClient("test-only")

    def failed(*args: Any, **kwargs: Any) -> None:
        raise urllib.error.URLError("test-only")

    monkeypatch.setattr(client._opener, "open", failed)
    assert client.probe() == "Token Factory unavailable: URLError"


def test_shared_provider_keeps_local_allowlist() -> None:
    from llm.client import VllmConfig

    with pytest.raises(ValueError, match="local"):
        VllmClient(VllmConfig(TOKENFACTORY_BASE_URL, "test-only"))
