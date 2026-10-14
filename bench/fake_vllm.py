"""A local fake of the OpenAI-compatible vLLM server, for the B3 dry run and the tests.

It listens on 127.0.0.1 only (rule 2: llm.client.VllmClient accepts loopback), checks the bearer
key like vLLM started with --api-key, and answers /v1/chat/completions with the llm_output that
llm.templates.output_from_case derives from the case in the user message, the same answer as the
MockClient, so the whole path (prompt, guided-JSON request, validator) runs. Its latency is a
declared toy model (time to first token plus tokens at a fixed rate, every request independent):
the numbers of a dry run check the harness, they say nothing about Gemma or the MI300X.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from llm.templates import output_from_case

FAKE_VERSION = "fake-openai-0"


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256  # a batch of 32 connects at once; the default backlog is 5


@dataclass(frozen=True)
class FakeLatency:
    ttft_s: float = 0.02
    tokens_per_s: float = 2000.0

    def seconds(self, completion_tokens: int) -> float:
        return self.ttft_s + completion_tokens / self.tokens_per_s


def _case_of(messages: Any) -> Mapping[str, Any] | None:
    """The case JSON of llm.prompts.user_message: the line after the first one."""
    if not isinstance(messages, list):
        return None
    for message in messages:
        if isinstance(message, Mapping) and message.get("role") == "user":
            lines = str(message.get("content", "")).split("\n")
            if len(lines) > 1:
                try:
                    case = json.loads(lines[1])
                except ValueError:
                    return None
                return case if isinstance(case, Mapping) else None
    return None


class FakeVllm:
    """Start with `with FakeVllm() as fake:`; fake.base_url is http://127.0.0.1:<port>/v1."""

    def __init__(
        self,
        api_key: str = "dry-run",
        model: str = "fake/gemma-dry-run",
        latency: FakeLatency | None = None,
        fail_status: int | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.latency = latency or FakeLatency()
        self.fail_status = fail_status  # every chat request answers this HTTP status
        self.requests = 0
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()
        self._server = _Server(("127.0.0.1", 0), self._handler())
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}/v1"

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return None

            def _send(self, status: int, body: Mapping[str, Any]) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/version":
                    self._send(200, {"version": FAKE_VERSION})
                elif self.path == "/health":
                    self._send(200, {})
                elif self.path == "/v1/models":
                    self._send(200, {"data": [{"id": fake.model, "max_model_len": 16384}]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.headers.get("Authorization") != f"Bearer {fake.api_key}":
                    self._send(401, {"error": "Unauthorized"})
                    return
                if self.path != "/v1/chat/completions":
                    self._send(404, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length))
                except ValueError:
                    self._send(400, {"error": "invalid JSON"})
                    return
                with fake._lock:
                    fake.requests += 1
                    fake._in_flight += 1
                    fake.max_in_flight = max(fake.max_in_flight, fake._in_flight)
                try:
                    if fake.fail_status is not None:
                        self._send(fake.fail_status, {"error": "fake failure"})
                        return
                    messages = body.get("messages") if isinstance(body, Mapping) else None
                    case = _case_of(messages)
                    content = (
                        json.dumps(output_from_case(case), ensure_ascii=False) if case else "{}"
                    )
                    completion = max(1, len(content) // 4)
                    prompt = sum(len(str(m.get("content", ""))) for m in messages or []) // 4
                    time.sleep(fake.latency.seconds(completion))
                    self._send(
                        200,
                        {
                            "id": f"fake-{fake.requests}",
                            "object": "chat.completion",
                            "model": fake.model,
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": content},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": prompt,
                                "completion_tokens": completion,
                                "total_tokens": prompt + completion,
                            },
                        },
                    )
                finally:
                    with fake._lock:
                        fake._in_flight -= 1

        return Handler

    def start(self) -> FakeVllm:
        if self._thread is None:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> FakeVllm:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()
