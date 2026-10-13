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
    check_local_url,
    client_from_env,
    output_schema,
)
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
        "json_schema": {"name": "llm_output", "schema": output_schema(), "strict": True},
    }
    assert "$schema" not in body["response_format"]["json_schema"]["schema"]
    system, user = body["messages"]
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    assert user["role"] == "user"
    assert json.loads(user["content"].split("\n", 1)[1]) == CASE


def test_retry_codes_are_sent_as_feedback(server: FakeServer) -> None:
    client(server.url).complete(CASE, retry_codes=("BANNED_TERM", "TOO_LONG"))
    feedback = server.requests[0]["body"]["messages"][-1]
    assert feedback["role"] == "user"
    assert "BANNED_TERM, TOO_LONG" in feedback["content"]


def test_usage_is_optional(server: FakeServer) -> None:
    server.reply({"choices": [{"message": {"content": "x"}}]})
    server.reply({"choices": [{"message": {"content": "x"}}], "usage": {"completion_tokens": "a"}})
    assert client(server.url).complete(CASE).completion_tokens is None
    assert client(server.url).complete(CASE).completion_tokens is None


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
