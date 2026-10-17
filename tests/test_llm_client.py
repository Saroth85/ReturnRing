"""llm/client.py against a fake OpenAI-compatible server on 127.0.0.1 (no real network, rule 2)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from llm.client import (
    MAX_RESPONSE_BYTES,
    TEMPERATURE,
    Completion,
    LlmUnavailable,
    MockClient,
    VllmClient,
    VllmConfig,
    case_output_schema,
    check_local_url,
    client_from_env,
    output_schema,
)
from llm.phrasebook import SENTENCE_IDS, allowed_sentences, context_of_case
from llm.prompts import SYSTEM_PROMPT
from tests.llm_factories import make

CASE = make("Q4_cluster").case
OK_CONTENT = '{"ok": true}'


def chat_response(content: Any = OK_CONTENT, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"completion_tokens": 42},
    }
    body.update(extra)
    return body


@dataclass
class FakeServer:
    """Answers each POST with the next scripted reply: (status, body bytes, headers, delay)."""

    url: str = ""
    replies: list[tuple[int, bytes, dict[str, str], float]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def reply(
        self, body: Any, status: int = 200, headers: dict[str, str] | None = None, delay: float = 0
    ) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.replies.append((status, raw, headers or {}, delay))


@pytest.fixture
def server() -> Iterator[FakeServer]:
    fake = FakeServer()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length", "0"))
            fake.requests.append(
                {
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(self.rfile.read(length) or b"{}"),
                }
            )
            status, raw, headers, delay = (
                fake.replies.pop(0)
                if fake.replies
                else (200, json.dumps(chat_response()).encode(), {}, 0)
            )
            time.sleep(delay)
            try:
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except OSError:
                pass  # the client gave up (timeout test)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    fake.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    yield fake
    httpd.shutdown()
    httpd.server_close()


def client(url: str, **overrides: Any) -> VllmClient:
    config: dict[str, Any] = {"base_url": f"{url}/v1", "api_key": "test-key", "timeout_s": 5.0}
    config.update(overrides)
    return VllmClient(VllmConfig(**config))


def test_complete_sends_a_guided_json_request(server: FakeServer) -> None:
    completion = client(server.url, model="gemma-test", max_tokens=321).complete(CASE)
    assert completion.content == OK_CONTENT
    assert completion.completion_tokens == 42
    assert completion.elapsed_s >= 0
    [request] = server.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    body = request["body"]
    assert body["model"] == "gemma-test"
    assert body["temperature"] == TEMPERATURE == 0.2
    assert body["max_tokens"] == 321
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "llm_output",
            "schema": case_output_schema(CASE, output_schema()),
            "strict": True,
        },
    }
    assert "$schema" not in body["response_format"]["json_schema"]["schema"]
    system, user = body["messages"]
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    assert user["role"] == "user"
    assert json.loads(user["content"].split("\n")[1]) == CASE
    assert (
        "- INFO_TERMS: Questions about these terms? Just reply to this message." in user["content"]
    )
    assert "OPEN_SORRY_ITEM" not in user["content"]  # a guarantee sentence, not for this case
    assert user["content"].endswith(  # round 3, finding T4: the coherence rules of the selection
        "Rules:\n- At most one of HELP_SIZE, CONTACT_REPLY, INFO_TERMS.\n"
        "- CLOSE_DAY only after OPEN_THANKS or OPEN_MESSAGE or OPEN_CHOICE."
    )


@pytest.mark.parametrize("name", ["Q4_cluster", "Q2", "guarantee", "insufficient"])
def test_the_guided_schema_allows_only_the_sentences_of_the_case(name: str) -> None:
    """Round 2, finding R1: the enum is narrowed per case; the decision echo stays open, so a
    misread decision still reaches the validator as DECISION_ECHO."""
    case = make(name).case
    full = output_schema()
    narrowed = case_output_schema(case, full)
    enum = narrowed["properties"]["customer_message"]["properties"]["sentence_ids"]["items"]
    assert enum["enum"] == [s.id for s in allowed_sentences(context_of_case(case))]
    assert set(enum["enum"]) < set(SENTENCE_IDS)
    assert narrowed["properties"]["decision_echo"] == full["properties"]["decision_echo"]
    if name in ("guarantee", "insufficient"):
        labels = narrowed["properties"]["operator_explanation"]["items"]["properties"]["label"]
        assert labels["enum"] == ["OBSERVED", "HYPOTHESIS"]
    else:
        assert (
            narrowed["properties"]["operator_explanation"]
            == full["properties"]["operator_explanation"]
        )
    full_enum = full["properties"]["customer_message"]["properties"]["sentence_ids"]["items"]
    assert full_enum["enum"] == list(SENTENCE_IDS)  # the shared schema is not changed


def test_retry_codes_are_sent_as_feedback(server: FakeServer) -> None:
    client(server.url).complete(CASE, retry_codes=("BANNED_TERM", "TOO_LONG"))
    feedback = server.requests[0]["body"]["messages"][-1]
    assert feedback["role"] == "user"
    assert "BANNED_TERM; TOO_LONG" in feedback["content"]


def test_usage_is_optional(server: FakeServer) -> None:
    server.reply({"choices": [{"message": {"content": "x"}}]})
    server.reply({"choices": [{"message": {"content": "x"}}], "usage": {"completion_tokens": "a"}})
    assert client(server.url).complete(CASE).completion_tokens is None
    assert client(server.url).complete(CASE).completion_tokens is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, None), (False, None), (-1, None), (1.5, None), (0, 0), (12, 12)],
)
def test_usage_keeps_only_non_negative_integer_token_counts(
    server: FakeServer, raw: Any, expected: int | None
) -> None:
    server.reply(chat_response(usage={"completion_tokens": raw}))
    assert client(server.url).complete(CASE).completion_tokens == expected


@pytest.mark.parametrize(
    "reply",
    [
        (500, b'{"error": "boom"}'),
        (401, b'{"error": "no key"}'),
        (200, b"not json"),
        (200, b"[1, 2]"),
        (200, b'{"choices": []}'),
        (200, b'{"choices": [{"message": "text"}]}'),
        (200, json.dumps(chat_response(content=None)).encode()),
        (200, json.dumps(chat_response(content=["a"])).encode()),
        (200, b"x" * (MAX_RESPONSE_BYTES + 10)),
    ],
)
def test_bad_replies_make_the_model_unavailable(
    server: FakeServer, reply: tuple[int, bytes]
) -> None:
    status, raw = reply
    server.reply(raw, status=status)
    with pytest.raises(LlmUnavailable):
        client(server.url).complete(CASE)


def test_timeout_makes_the_model_unavailable(server: FakeServer) -> None:
    server.reply(chat_response(), delay=1.0)
    with pytest.raises(LlmUnavailable):
        client(server.url, timeout_s=0.2).complete(CASE)


def test_connection_refused_makes_the_model_unavailable() -> None:
    with pytest.raises(LlmUnavailable):
        client("http://127.0.0.1:9", timeout_s=1.0).complete(CASE)


def test_redirects_are_never_followed(server: FakeServer) -> None:
    server.reply(b"", status=302, headers={"Location": "http://example.com/v1/chat/completions"})
    with pytest.raises(LlmUnavailable):
        client(server.url).complete(CASE)
    assert len(server.requests) == 1


def test_environment_proxies_are_ignored(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert client(server.url).complete(CASE).content == OK_CONTENT


def test_chat_with_tools(server: FakeServer) -> None:
    server.reply({"choices": [{"message": {"tool_calls": []}}]})
    tools = [{"type": "function", "function": {"name": "get_case", "parameters": {}}}]
    message = client(server.url).chat([{"role": "user", "content": "hi"}], tools=tools)
    assert message == {"tool_calls": []}
    body = server.requests[0]["body"]
    assert body["tools"] == tools and body["tool_choice"] == "auto"
    assert "response_format" not in body
    client(server.url).chat([{"role": "user", "content": "hi"}])
    assert "tools" not in server.requests[1]["body"]


# ---- Rule 2: only the local vLLM ----------------------------------------------------------------


@pytest.mark.parametrize(
    "url, endpoint",
    [
        ("http://vllm:8001/v1", "http://vllm:8001/v1/chat/completions"),
        ("http://vllm:8001/v1/", "http://vllm:8001/v1/chat/completions"),
        ("http://localhost:8001", "http://localhost:8001/v1/chat/completions"),
        ("http://127.0.0.1:8001/v1", "http://127.0.0.1:8001/v1/chat/completions"),
        ("https://10.0.0.5:8001/v1", "https://10.0.0.5:8001/v1/chat/completions"),
        ("http://[::1]:8001/v1", "http://[::1]:8001/v1/chat/completions"),
        ("http://172.20.0.4:8001", "http://172.20.0.4:8001/v1/chat/completions"),  # compose
        ("http://192.168.1.10/v1", "http://192.168.1.10/v1/chat/completions"),
        ("http://[::ffff:127.0.0.1]/v1", "http://[::ffff:127.0.0.1]/v1/chat/completions"),
    ],
)
def test_local_urls_are_accepted(url: str, endpoint: str) -> None:
    assert check_local_url(url) == endpoint


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://vllm.example.com/v1",
        "http://8.8.8.8/v1",
        "http://169.254.169.254/v1",  # cloud metadata, link-local
        # Adversarial review of 13 Oct, finding 6: mapped, reserved and documentation addresses
        "http://[::ffff:a9fe:a9fe]/v1",  # IPv4-mapped cloud metadata
        "http://[::ffff:8.8.8.8]/v1",
        "http://192.0.2.1/v1",
        "http://198.18.0.1/v1",
        "http://240.0.0.1/v1",
        "http://255.255.255.255/v1",
        "http://0.0.0.0/v1",
        "http://100.64.0.1/v1",
        "http://[2001:db8::1]/v1",
        "http://[2001::1]/v1",
        "http://[2002:808:808::1]/v1",
        "http://[64:ff9b::a9fe:a9fe]/v1",
        "http://[fe80::1]/v1",
        "http://[::]/v1",
        # Round 2, finding R8: the compose network is IPv4 only, so no IPv6 unique local address
        "http://[fd00:ec2::254]/v1",  # EC2 IPv6 instance metadata
        "http://[fd00::5]:8001/v1",
        "http://127.1/v1",
        "ftp://vllm/v1",
        "http://user:secret@vllm/v1",
        "http://vllm/v1?redirect=x",
        "http://vllm/v1#x",
        "",
    ],
)
def test_other_hosts_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        check_local_url(url)


@pytest.mark.parametrize(
    ("url", "part"),
    [
        ("http://admin:hunter2@vllm:8001/v1", "a user"),
        ("http://:hunter2@vllm:8001/v1", "a user"),  # a password with an empty user name
        ("http://vllm:8001/v1?token=hunter2", "a query"),
        ("http://vllm:8001/v1#hunter2", "a fragment"),
        ("hunter2://vllm/v1", "a scheme other than http or https"),
        ("ftp://admin:hunter2@vllm/v1?x=1", "a scheme other than http or https, a user, a query"),
        ("http://hunter2.example.com/v1", "not the local vLLM"),
        ("http://203.0.113.99/v1", "not a local address"),
    ],
)
def test_a_refused_url_is_named_by_its_part_never_echoed(url: str, part: str) -> None:
    """The error reaches the public /readyz and the 503 bodies (review of 14 Oct 2026): it says
    which part is refused and never repeats the URL, its credentials, query or host."""
    with pytest.raises(ValueError, match="VLLM_BASE_URL") as caught:
        check_local_url(url)
    message = str(caught.value)
    assert part in message
    assert "hunter2" not in message and "203.0.113" not in message and "admin" not in message


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:hunter2/v1",
        "http://localhost:0/v1",
        "http://localhost:65536/v1",
        "http://localhost:-1/v1",
        "http://[hunter2]/v1",
        "http://hunter2／localhost/v1",
    ],
)
def test_invalid_authority_is_refused_without_echoing_configuration(url: str) -> None:
    with pytest.raises(ValueError, match="VLLM_BASE_URL") as caught:
        check_local_url(url)
    assert "hunter2" not in str(caught.value)


def test_api_key_is_required_and_never_shown() -> None:
    with pytest.raises(ValueError):
        VllmClient(VllmConfig(base_url="http://vllm:8001/v1", api_key=""))
    assert "s3cret" not in repr(VllmConfig(base_url="http://vllm:8001/v1", api_key="s3cret"))


def test_client_from_env() -> None:
    assert isinstance(client_from_env({}), MockClient)
    assert isinstance(client_from_env({"LLM_MODE": "mock"}), MockClient)
    vllm = client_from_env(
        {"LLM_MODE": "vllm", "VLLM_BASE_URL": "http://vllm:8001/v1", "VLLM_API_KEY": "k"}
    )
    assert isinstance(vllm, VllmClient)
    assert vllm.endpoint == "http://vllm:8001/v1/chat/completions"
    assert vllm.config.model == "google/gemma-4-31B-it"
    with pytest.raises(ValueError):
        client_from_env({"LLM_MODE": "openai"})
    with pytest.raises(ValueError):
        client_from_env({"LLM_MODE": "vllm", "VLLM_API_KEY": "k"})
    assert isinstance(client_from_env(None), MockClient | VllmClient)


# ---- Mock ----------------------------------------------------------------------------------------


def test_mock_client_is_deterministic_and_scriptable() -> None:
    mock = MockClient(["raw text", {"a": 1}, LlmUnavailable("down")])
    assert mock.complete(CASE) == Completion("raw text")
    assert mock.complete(CASE, retry_codes=["SCHEMA"]) == Completion('{"a": 1}')
    with pytest.raises(LlmUnavailable):
        mock.complete(CASE)
    first, second = mock.complete(CASE), MockClient().complete(CASE)
    assert first == second
    assert json.loads(first.content)["decision_echo"]["quadrant"] == "Q4"
    assert mock.calls == [(), ("SCHEMA",), (), ()]
