"""Serve the API and built SPA as one CPU service, including on hosts with a dynamic PORT.

The public demo defaults to read-only. Telemetry always requires the admin password; enabling
writes also protects every API write, as the Compose deployment's Caddy proxy does. This module
does not create a database, download a snapshot, reset demo state or receive build-time secrets.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.datastructures import Headers
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.app import configure_logging, create_app
from apps.api.settings import Settings

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
)


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


class PublicAccess:
    """The public proxy's admin boundary, without buffering the agent's response stream."""

    def __init__(self, app: ASGIApp, *, password: str, read_only: bool) -> None:
        self.app = app
        self._password = password.encode("utf-8")
        self.read_only = read_only

    def _authorized(self, scope: Scope) -> bool:
        scheme, _, encoded = Headers(scope=scope).get("authorization", "").partition(" ")
        if scheme.lower() != "basic" or not self._password:
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return False
        user, separator, password = decoded.partition(b":")
        return bool(
            separator
            and secrets.compare_digest(user, b"admin")
            and secrets.compare_digest(password, self._password)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        protected = _under(path, "/api/telemetry") or (
            not self.read_only and _under(path, "/api") and scope["method"] not in _SAFE_METHODS
        )
        if protected and not self._authorized(scope):
            response = Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="ReturnRing admin"'},
            )
            await response(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"same-origin"),
                        (b"content-security-policy", _CSP.encode("ascii")),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_headers)


def add_public_ui(
    app: FastAPI, *, ui_dir: Path, admin_password: str, public_read_only: bool
) -> FastAPI:
    """Add the SPA after API routes; missing API endpoints must retain their HTTP errors."""
    root = ui_dir.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError("built public UI missing: build apps/ui and set PUBLIC_DEMO_UI_DIR")
    app.add_middleware(PublicAccess, password=admin_password, read_only=public_read_only)
    # Hashed bundles have a separate mount: an absent JS file must be 404, not index.html.
    app.mount("/assets", StaticFiles(directory=root / "assets", check_dir=False))

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def ui(path: str) -> FileResponse:
        absolute_path = "/" + path
        if any(_under(absolute_path, p) for p in ("/api", "/healthz", "/readyz")):
            raise HTTPException(status_code=404, detail="Not Found")
        requested = (root / path).resolve()
        if not requested.is_relative_to(root):
            raise HTTPException(status_code=404, detail="Not Found")
        if requested.is_file():
            return FileResponse(requested, headers={"Cache-Control": "no-cache"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def create_public_app() -> FastAPI:
    environment = dict(os.environ)
    if not environment.get("PUBLIC_READ_ONLY", "").strip():
        environment["PUBLIC_READ_ONLY"] = "true"
    settings = Settings.from_env(environment)
    return add_public_ui(
        create_app(settings),
        ui_dir=Path(environment.get("PUBLIC_DEMO_UI_DIR", "apps/ui/dist")),
        admin_password=environment.get("DEMO_ADMIN_PASSWORD", ""),
        public_read_only=settings.public_read_only,
    )


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    configure_logging()
    uvicorn.run(create_public_app(), host="0.0.0.0", port=port, workers=1)


if __name__ == "__main__":
    main()
