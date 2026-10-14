"""ASGI entry point: uvicorn apps.api.main:app (apps/api/Dockerfile). The app reads its settings
from the environment (CLAUDE.md, Environment flags; ADR-16) and logs one line per request with
its time and level (NFR-12)."""

from __future__ import annotations

from apps.api.app import configure_logging, create_app

configure_logging()
app = create_app()
