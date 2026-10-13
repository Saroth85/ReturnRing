"""Every error path answers ErrorResponse {code, message, violations, request_id} (ADR-15
section 2): raise ApiError (or one of its constructors) in a route or a service; pydantic errors
of a body, path or query become 422 invalid_request with their violations; an unknown path is
404 not_found; anything unexpected is 500 internal, logged with its request id (NFR-12)."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.schemas import ErrorCode, ErrorResponse, ViolationView
from policy.legal_guard import Violation

LOG: Final = logging.getLogger("returnring.api")
MAX_TEXT: Final = 300  # ShortText of the contract


def _short(text: str) -> str:
    text = " ".join(text.split()) or "-"
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


class ApiError(Exception):
    """An error the API answers on purpose, with its status and contract code."""

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        violations: Sequence[tuple[str, str]] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code: ErrorCode = code
        self.message = message
        self.violations = list(violations)

    def body(self, request_id: str | None) -> ErrorResponse:
        return ErrorResponse(
            code=self.code,
            message=_short(self.message),
            violations=[
                ViolationView(code=_short(c), message=_short(m)) for c, m in self.violations
            ],
            request_id=request_id,
        )


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def conflict(message: str) -> ApiError:
    return ApiError(409, "conflict", message)


def stale_decision(sent: str, latest: str) -> ApiError:
    return ApiError(
        409, "stale_decision", f"{sent} is not the latest version of the case: {latest} is"
    )


def forbidden_role(message: str) -> ApiError:
    return ApiError(403, "forbidden_role", message)


def legal_guard(violations: Iterable[Violation]) -> ApiError:
    found = [(v.code, v.message) for v in violations]
    return ApiError(422, "legal_guard", "policy.legal_guard refuses the decision", found)


def invalid_request(message: str, violations: Sequence[tuple[str, str]] = ()) -> ApiError:
    return ApiError(422, "invalid_request", message, violations)


def llm_unavailable(message: str) -> ApiError:
    return ApiError(503, "llm_unavailable", message)


def rate_limited(message: str) -> ApiError:
    return ApiError(429, "rate_limited", message)


def not_implemented(method: str, path: str) -> ApiError:
    """The 501 of an endpoint of ADR-15 that a route builder has not implemented yet."""
    return ApiError(501, "internal", f"{method} {path} is not implemented yet")


def request_id_of(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def error_json(error: ApiError, request_id: str | None) -> JSONResponse:
    return JSONResponse(error.body(request_id).model_dump(mode="json"), status_code=error.status)


def _location(loc: Sequence[Any]) -> str:
    return ".".join(str(part) for part in loc) or "request"


def validation_violations(errors: Iterable[Any]) -> list[tuple[str, str]]:
    out = []
    for error in errors:
        if isinstance(error, dict):
            out.append(
                (str(error.get("type", "invalid")), f"{_location(error.get('loc', ()))}: "
                 f"{error.get('msg', 'invalid value')}")
            )  # fmt: skip
    return out


async def _api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_json(exc, request_id_of(request))


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    error = invalid_request("the request is invalid", validation_violations(exc.errors()))
    return error_json(error, request_id_of(request))


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 404:
        error = not_found(f"{request.method} {request.url.path}")
    elif exc.status_code == 405:
        error = ApiError(405, "invalid_request", f"{request.method} is not allowed here")
    else:
        error = ApiError(exc.status_code, "internal", str(exc.detail))
    return error_json(error, request_id_of(request))


def install(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
