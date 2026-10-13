"""Runtime switches read from the environment (CLAUDE.md, Environment flags; ADR-16).

DEVICE=cpu|gpu, LLM_MODE=mock|vllm, DEMO_MODE=live|cached, SEED; DATABASE_URL (PostgreSQL, or
SQLite in tests), SERVING_DIR (the serving snapshot, read only), VLLM_BASE_URL, VLLM_API_KEY and
GEMMA_MODEL (LLM_MODE=vllm only; rule 2: the local vLLM is the only model endpoint).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from contracts.vocab import LlmMode, ServedFrom
from llm.client import DEFAULT_MODEL

Device = Literal["cpu", "gpu"]

_ALLOWED = {
    "DEVICE": ("cpu", "gpu"),
    "LLM_MODE": ("mock", "vllm"),
    "DEMO_MODE": ("live", "cached"),
}
_DRIVERS = {
    "postgresql": "postgresql+asyncpg",
    "postgres": "postgresql+asyncpg",
    "sqlite": "sqlite+aiosqlite",
}


def async_database_url(url: str) -> str:
    """The SQLAlchemy async URL of DATABASE_URL: asyncpg for PostgreSQL (psycopg is LGPL, spec
    10), aiosqlite for SQLite. A URL that already names a driver is kept."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        raise ValueError(f"DATABASE_URL is not a URL: {url!r}")
    if "+" in scheme:
        return url
    if scheme not in _DRIVERS:
        raise ValueError(f"DATABASE_URL must be PostgreSQL or SQLite, got {scheme!r}")
    return f"{_DRIVERS[scheme]}://{rest}"


@dataclass(frozen=True)
class Settings:
    device: Device
    llm_mode: LlmMode
    demo_mode: ServedFrom
    seed: int
    database_url: str | None = None
    serving_dir: Path = Path("serving")
    vllm_base_url: str | None = None
    vllm_api_key: str | None = field(default=None, repr=False)
    gemma_model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        values = {k: source.get(k, v[0]) for k, v in _ALLOWED.items()}
        for key, value in values.items():
            if value not in _ALLOWED[key]:
                raise ValueError(f"{key} must be one of {_ALLOWED[key]}, got {value!r}")
        url = source.get("DATABASE_URL") or None
        return cls(
            device=cast(Device, values["DEVICE"]),
            llm_mode=cast(LlmMode, values["LLM_MODE"]),
            demo_mode=cast(ServedFrom, values["DEMO_MODE"]),
            seed=int(source.get("SEED", "42")),
            database_url=async_database_url(url) if url else None,
            serving_dir=Path(source.get("SERVING_DIR", "serving")),
            vllm_base_url=source.get("VLLM_BASE_URL") or None,
            vllm_api_key=source.get("VLLM_API_KEY") or None,
            gemma_model=source.get("GEMMA_MODEL") or DEFAULT_MODEL,
        )

    @property
    def cached(self) -> bool:
        return self.demo_mode == "cached"
