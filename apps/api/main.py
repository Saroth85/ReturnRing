"""ASGI entry point: uvicorn apps.api.main:app (apps/api/Dockerfile). The app reads its settings
from the environment (CLAUDE.md, Environment flags; ADR-16)."""

from __future__ import annotations

from apps.api.app import create_app

app = create_app()
