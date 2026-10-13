"""The two headers of every response (ADR-15 section 2) and the last-resort 500.

X-Request-Id: the caller's id when it is a valid id, a new one otherwise; it is also the
request_id of an ErrorResponse and of the audit entries written by the request (NFR-12).
X-ReturnRing-Mode: live or cached (US-901), on every response, errors and streams included.

A plain ASGI middleware, so the server-sent events of the agent stream through untouched.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.errors import LOG, ApiError
from apps.api.schemas import ID_PATTERN, MODE_HEADER, REQUEST_ID_HEADER

_ID: Final = re.compile(ID_PATTERN)
MAX_ID: Final = 96


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
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == REQUEST_ID_HEADER.lower():
                incoming = value.decode("latin-1")
        request_id = accepted_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id
        headers_out = [
            (REQUEST_ID_HEADER.lower().encode("latin-1"), request_id.encode("latin-1")),
            (MODE_HEADER.lower().encode("latin-1"), self.mode),
        ]
        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                names = {h[0] for h in headers_out}
                kept = [h for h in message.get("headers", []) if h[0].lower() not in names]
                message = {**message, "headers": [*kept, *headers_out]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:  # last resort: the contract body, never a bare traceback
            LOG.exception("unhandled error, request %s", request_id)
            if started:
                return
            body = ApiError(500, "internal", "internal error").body(request_id)
            payload: Any = body.model_dump(mode="json")
            data = json.dumps(payload).encode("utf-8")
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(data)).encode("latin-1")),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": data})
