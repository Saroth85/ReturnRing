"""Clients for Gemma: a deterministic mock (LLM_MODE=mock) and the local vLLM (LLM_MODE=vllm).

Rule 2: the only model endpoint is the local vLLM. VllmClient accepts only the compose service
name "vllm", "localhost" or a loopback or private IP address; it never uses proxies and never
follows redirects. Standard library only (urllib): no new runtime dependency. No retries inside
the client: llm/generate.py decides what happens after a failure.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from llm.prompts import case_messages
from llm.templates import output_from_case

OUTPUT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts/json/llm_output.schema.json"
LOCAL_HOSTNAMES = frozenset({"vllm", "localhost"})
DEFAULT_MODEL = "google/gemma-4-31B-it"
TEMPERATURE = 0.2
MAX_RESPONSE_BYTES = 1_000_000


class LlmUnavailable(RuntimeError):
    """The model could not answer: connection, timeout, HTTP error or malformed response."""


@dataclass(frozen=True)
class Completion:
    content: str
    elapsed_s: float = 0.0
    completion_tokens: int | None = None


class LlmClient(Protocol):
    def complete(
        self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()
    ) -> Completion: ...


class ChatClient(Protocol):
    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]: ...


ScriptItem = str | Mapping[str, Any] | BaseException


class MockClient:
    """Deterministic model: a schema-valid output derived from the case alone. Tests inject bad
    outputs or failures through `script`, one item per call, before the default output."""

    def __init__(self, script: Iterable[ScriptItem] = ()) -> None:
        self._script = list(script)
        self.calls: list[tuple[str, ...]] = []

    def complete(self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()) -> Completion:
        self.calls.append(tuple(retry_codes))
        if self._script:
            item = self._script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return Completion(item if isinstance(item, str) else json.dumps(item))
        return Completion(json.dumps(output_from_case(case), ensure_ascii=False))


def output_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema.pop("$schema", None)
    return schema


def check_local_url(base_url: str) -> str:
    """The chat completions URL of a local vLLM base URL; ValueError for any other host."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or parts.username or parts.query or parts.fragment:
        raise ValueError(f"VLLM_BASE_URL must be a plain http(s) URL, got {base_url!r}")
    if host not in LOCAL_HOSTNAMES:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError(f"Rule 2: {host!r} is not the local vLLM") from None
        if address.is_link_local or not (address.is_loopback or address.is_private):
            raise ValueError(f"Rule 2: {host!r} is not a local address")
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path += "/v1"
    return f"{parts.scheme}://{parts.netloc}{path}/chat/completions"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None  # urllib then raises HTTPError for the 3xx response


@dataclass(frozen=True)
class VllmConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    timeout_s: float = 60.0
    max_tokens: int = 1200


class VllmClient:
    """OpenAI-compatible /v1/chat/completions on the local vLLM, with guided JSON output."""

    def __init__(self, config: VllmConfig) -> None:
        if not config.api_key:
            raise ValueError("VLLM_API_KEY is required: vLLM refuses requests without it")
        self.config = config
        self.endpoint = check_local_url(config.base_url)
        self._schema = output_schema()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _post(self, body: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        start = time.perf_counter()
        try:
            with self._opener.open(request, timeout=self.config.timeout_s) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise LlmUnavailable(f"vLLM answered HTTP {exc.code}") from None
        except (OSError, http.client.HTTPException) as exc:  # URLError, timeouts, resets
            raise LlmUnavailable(f"vLLM unreachable: {type(exc).__name__}") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LlmUnavailable("vLLM response too large")
        try:
            data = json.loads(raw)
        except ValueError:
            raise LlmUnavailable("vLLM response is not JSON") from None
        if not isinstance(data, dict):
            raise LlmUnavailable("vLLM response is not a JSON object")
        return data, time.perf_counter() - start

    @staticmethod
    def _message(data: Mapping[str, Any]) -> dict[str, Any]:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LlmUnavailable("vLLM response has no message") from None
        if not isinstance(message, dict):
            raise LlmUnavailable("vLLM message is not an object")
        return message

    def complete(self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()) -> Completion:
        body = {
            "model": self.config.model,
            "messages": case_messages(case, retry_codes),
            "temperature": TEMPERATURE,
            "max_tokens": self.config.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "llm_output", "schema": self._schema, "strict": True},
            },
        }
        data, elapsed = self._post(body)
        content = self._message(data).get("content")
        if not isinstance(content, str):
            raise LlmUnavailable("vLLM message has no text content")
        usage = data.get("usage")
        tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        return Completion(content, elapsed, tokens if isinstance(tokens, int) else None)

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        """One chat turn with optional tool calling (the investigator agent); returns the
        assistant message."""
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": TEMPERATURE,
            "max_tokens": self.config.max_tokens,
        }
        if tools is not None:
            body["tools"] = list(tools)
            body["tool_choice"] = tool_choice or "auto"
        data, _ = self._post(body)
        return self._message(data)


def client_from_env(env: Mapping[str, str] | None = None) -> MockClient | VllmClient:
    """The client for LLM_MODE (default mock); vllm needs VLLM_BASE_URL and VLLM_API_KEY."""
    values = os.environ if env is None else env
    mode = values.get("LLM_MODE", "mock")
    if mode == "mock":
        return MockClient()
    if mode != "vllm":
        raise ValueError(f"LLM_MODE must be mock or vllm, got {mode!r}")
    return VllmClient(
        VllmConfig(
            base_url=values.get("VLLM_BASE_URL", ""),
            api_key=values.get("VLLM_API_KEY", ""),
            model=values.get("GEMMA_MODEL", DEFAULT_MODEL),
        )
    )
