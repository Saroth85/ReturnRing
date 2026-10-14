"""The two headers of every response (ADR-15 section 2) and the last-resort 500.

X-Request-Id: the caller's id when it is a valid id, a new one otherwise; it is also the
request_id of an ErrorResponse and of the audit entries written by the request (NFR-12).
X-ReturnRing-Mode: live or cached (US-901), on every response, errors and streams included.

A plain ASGI middleware, so the server-sent events of the agent stream through untouched.

Contract 1.5.0 (ADR-15 section 15):

- a body larger than MAX_BODY_BYTES (8 MiB: a photo of the inspection is at most about 5 MB)
  answers 413 invalid_request before it is read whole, by its Content-Length or while it
  streams (H24); the proxy limits it too;
- every request is logged on one line: request id, method, path, status and duration (H39,
  NFR-12).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.errors import LOG, ApiError, BodyTooLarge
from apps.api.schemas import ID_PATTERN, MODE_HEADER, REQUEST_ID_HEADER

_ID: Final = re.compile(ID_PATTERN)
MAX_ID: Final = 96
MAX_BODY_BYTES: Final = 8 * 1024 * 1024
ACCESS: Final = logging.getLogger("returnring.api.access")


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:16]}"


def accepted_request_id(value: str | None) -> str:
    if value and len(value) <= MAX_ID and _ID.fullmatch(value):
        return value
    return new_request_id()


class RequestContext:
    def __init__(self, app: ASGIApp, mode: str) -> None:
        self.app = app
        self.mode = mode.encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        length = None
        for key, value in scope.get("headers", []):
            name = key.decode("latin-1").lower()
            if name == REQUEST_ID_HEADER.lower():
                incoming = value.decode("latin-1")
            elif name == "content-length":
                length = value.decode("latin-1")
        request_id = accepted_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id
        headers_out = [
            (REQUEST_ID_HEADER.lower().encode("latin-1"), request_id.encode("latin-1")),
            (MODE_HEADER.lower().encode("latin-1"), self.mode),
        ]
        started = False
        status = 0
        began = time.perf_counter()

        async def send_with_headers(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = int(message["status"])
                names = {h[0] for h in headers_out}
                kept = [h for h in message.get("headers", []) if h[0].lower() not in names]
                message = {**message, "headers": [*kept, *headers_out]}
            await send(message)

        received = 0

        async def receive_bounded() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY_BYTES:
                    raise BodyTooLarge
            return message

        async def answer(error: ApiError) -> None:
            payload: Any = error.body(request_id).model_dump(mode="json")
            data = json.dumps(payload).encode("utf-8")
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": error.status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(data)).encode("latin-1")),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": data})

        too_large = ApiError(
            413, "invalid_request", f"the request body is larger than {MAX_BODY_BYTES} bytes"
        )
        try:
            if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
                await answer(too_large)
            else:
                await self.app(scope, receive_bounded, send_with_headers)
        except BodyTooLarge:
            if not started:
                await answer(too_large)
        except Exception:  # last resort: the contract body, never a bare traceback
            LOG.exception("unhandled error, request %s", request_id)
            if not started:
                await answer(ApiError(500, "internal", "internal error"))
        finally:
            ACCESS.info(
                "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                request_id,
                scope.get("method", "-"),
                scope.get("path", "-"),
                status or "-",
                (time.perf_counter() - began) * 1000,
            )
