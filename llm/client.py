"""Gemma clients: deterministic mock, allow-listed local vLLM, or AMD Token Factory.

Local vLLM uses strict guided JSON. Token Factory uses JSON-object output with the same
case schema in the prompt, local validation, pacing and bounded 429 backoff (ADR-18).
Both transports disable proxies and redirects. No runtime dependency beyond the stdlib.
"""

from __future__ import annotations

import copy
import email.utils
import http.client
import ipaddress
import json
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from llm.phrasebook import MIN_SENTENCES, allowed_sentences, context_of_case, max_extras
from llm.prompts import case_messages
from llm.templates import output_from_case

OUTPUT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "contracts/json/llm_output.schema.json"
LOCAL_HOSTNAMES = frozenset({"vllm", "localhost"})
# Loopback and private networks only (adversarial review of 13 Oct, finding 6): link-local,
# reserved, documentation, benchmark, broadcast, 6to4 and NAT64 addresses are all refused. The
# compose network is IPv4 only (infra/docker-compose.yml enables no IPv6), so IPv6 unique local
# addresses (fc00::/7) are refused too, the EC2 metadata endpoint fd00:ec2::254 among them
# (round 2, finding R8).
LOCAL_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(network)
    for network in ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
DEFAULT_MODEL = "google/gemma-4-31B-it"
TEMPERATURE = 0.2
MAX_RESPONSE_BYTES = 1_000_000


class LlmUnavailable(RuntimeError):
    """The model could not answer: connection, timeout, HTTP error or malformed response."""


MAX_RETRY_AFTER_S = 3600.0
# The longest a Token Factory call waits for the shared quota while holding the client's lock;
# past it the call fails at once (the caller's reviewed fallback answers) instead of making every
# other call queue behind it for minutes.
MAX_QUOTA_WAIT_S = 30.0


def retry_after_seconds(value: str | None) -> float:
    """Retry-After as seconds: a number or an HTTP-date (RFC 9110); 60 when missing or
    unreadable, at most an hour. The provider's window is kept whole: a retry inside it is
    refused anyway and spends the shared quota of the team."""
    if not value:
        return 60.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return 60.0
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = max(0.0, (when - datetime.now(UTC)).total_seconds())
    if not math.isfinite(seconds) or seconds < 0:
        return 60.0
    return min(seconds, MAX_RETRY_AFTER_S)


class RateLimited(LlmUnavailable):
    """A provider rejected the request; never work around its shared quota."""

    def __init__(self, retry_after: str | None) -> None:
        super().__init__("model endpoint answered HTTP 429")
        self.delay_s = retry_after_seconds(retry_after)


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


def case_output_schema(case: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    """The output schema for one case: the sentence id enum narrowed to the phrasebook ids
    allowed for its decision, and the number of ids to its extras (two in Q3 and Q4);
    SCORE labels excluded for unscored cases; decision_echo stays open for validation."""
    narrowed: dict[str, Any] = copy.deepcopy(dict(schema))
    ids = narrowed["properties"]["customer_message"]["properties"]["sentence_ids"]
    ctx = context_of_case(case)
    ids["items"]["enum"] = [s.id for s in allowed_sentences(ctx)]
    ids["maxItems"] = MIN_SENTENCES + max_extras(ctx)
    if case.get("path") != "withdrawal" or case.get("evidence_quality") != "sufficient":
        labels = narrowed["properties"]["operator_explanation"]["items"]["properties"]["label"]
        labels["enum"] = ["OBSERVED", "HYPOTHESIS"]
    return narrowed


def check_local_url(base_url: str) -> str:
    """The chat completions URL of a local vLLM base URL; ValueError for any other host. The
    error names the part refused, never the URL: it reaches the public /readyz and 503 bodies,
    and a URL can hold credentials or a token."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    refused = [
        name
        for name, bad in (
            ("a scheme other than http or https", parts.scheme not in ("http", "https")),
            ("a user", parts.username is not None),
            ("a query", bool(parts.query)),
            ("a fragment", bool(parts.fragment)),
        )
        if bad
    ]
    if refused:
        raise ValueError(f"VLLM_BASE_URL has {', '.join(refused)}: refused (a plain http(s) URL)")
    if host not in LOCAL_HOSTNAMES:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("Rule 2: the host of VLLM_BASE_URL is not the local vLLM") from None
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if not any(address in network for network in LOCAL_NETWORKS):
            raise ValueError("Rule 2: the host of VLLM_BASE_URL is not a local address")
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
            if exc.code == 429:
                raise RateLimited(exc.headers.get("Retry-After")) from None
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
                "json_schema": {
                    "name": "llm_output",
                    "schema": case_output_schema(case, self._schema),
                    "strict": True,
                },
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


TOKENFACTORY_BASE_URL = "https://developer.amd.com.cn/radeon/api/v1"
TOKENFACTORY_MODEL = "Gemma-4-31B-IT"


class TokenFactoryClient(VllmClient):
    """AMD shared Gemma inference, with JSON-object output and local validation (ADR-18).

    The destination is fixed: the local endpoint allow-list remains unchanged. Calls on this
    client are serialised and paced below the observed team RPM cap. Only 429 is retried,
    respecting Retry-After with jitter. All consumers share this one client in the API process.
    """

    def __init__(self, api_key: str, *, timeout_s: float = 90.0) -> None:
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise ValueError("RADEON_API_KEY is required (a single-line token)")
        self.config = VllmConfig(TOKENFACTORY_BASE_URL, api_key, TOKENFACTORY_MODEL, timeout_s)
        self.endpoint = TOKENFACTORY_BASE_URL + "/chat/completions"
        self._schema = output_schema()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self._lock = threading.Lock()
        self._next_request = 0.0

    def _post(self, body: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        adapted = copy.deepcopy(dict(body))
        messages = adapted["messages"]
        # Shared Gemma is text-only. Never send a merchant photo to an unsupported model.
        if any(not isinstance(m.get("content"), (str, type(None))) for m in messages):
            raise LlmUnavailable("Token Factory Gemma does not support inspection photos")
        response_format = adapted.get("response_format", {})
        if response_format.get("type") == "json_schema":
            schema = response_format["json_schema"]["schema"]
            messages[0]["content"] += "\nRequired JSON schema:\n" + json.dumps(schema)
            messages[0]["content"] += (
                "\nUse at most three operator_explanation statements. "
                "Write no numbers or fact identifiers in statement text: the application "
                "shows exact values beside the cited facts. Put identifiers only in fact_ids."
            )
            adapted["response_format"] = {"type": "json_object"}
        adapted["reasoning_effort"] = "low"
        started = time.perf_counter()
        with self._lock:
            for attempt in range(3):
                wait = self._next_request - time.monotonic()
                if wait > MAX_QUOTA_WAIT_S:
                    # Inside the provider's window: refuse now, send nothing, free the lock.
                    raise RateLimited(str(math.ceil(wait)))
                time.sleep(max(0.0, wait))
                self._next_request = time.monotonic() + 3.1
                try:
                    data, _ = super()._post(adapted)
                    return data, time.perf_counter() - started
                except RateLimited as exc:
                    # Every later call, not only this retry, starts after the provider's window.
                    self._next_request = max(
                        self._next_request,
                        time.monotonic() + exc.delay_s + random.uniform(0.1, 0.4),
                    )
                    if attempt == 2:
                        raise
        raise AssertionError("unreachable")  # pragma: no cover

    def probe(self) -> str | None:
        """Check authentication and catalog membership without generating text."""
        request = urllib.request.Request(
            TOKENFACTORY_BASE_URL + "/models",
            headers={"Authorization": f"Bearer {self.config.api_key}"},
        )
        try:
            with self._opener.open(request, timeout=10) as response:
                listed = json.loads(response.read(MAX_RESPONSE_BYTES))
            models = listed.get("data", []) if isinstance(listed, dict) else []
            if not isinstance(models, list) or not any(
                isinstance(m, dict) and m.get("id") == self.config.model for m in models
            ):
                return "Token Factory Gemma is not in the current catalog"
        except (OSError, ValueError, http.client.HTTPException) as exc:
            return f"Token Factory unavailable: {type(exc).__name__}"
        return None


def client_from_env(env: Mapping[str, str] | None = None) -> MockClient | VllmClient:
    """The client for LLM_MODE (default mock); vllm needs VLLM_BASE_URL and VLLM_API_KEY."""
    values = os.environ if env is None else env
    mode = values.get("LLM_MODE", "mock")
    if mode == "mock":
        return MockClient()
    if mode == "tokenfactory":
        return TokenFactoryClient(values.get("RADEON_API_KEY", ""))
    if mode != "vllm":
        raise ValueError(f"LLM_MODE must be mock, vllm or tokenfactory, got {mode!r}")
    return VllmClient(
        VllmConfig(
            base_url=values.get("VLLM_BASE_URL", ""),
            api_key=values.get("VLLM_API_KEY", ""),
            model=values.get("GEMMA_MODEL", DEFAULT_MODEL),
        )
    )
