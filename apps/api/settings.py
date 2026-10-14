"""Runtime switches read from the environment (CLAUDE.md, Environment flags; ADR-16).

DEVICE=cpu|gpu, LLM_MODE=mock|vllm, DEMO_MODE=live|cached, SEED; DATABASE_URL (PostgreSQL, or
SQLite in tests), SERVING_DIR (the serving snapshot, read only), VLLM_BASE_URL, VLLM_API_KEY and
GEMMA_MODEL (LLM_MODE=vllm only; rule 2: the local vLLM is the only model endpoint).

ADR-15 section 15 (contract 1.5.0) adds:

- DEMO_CLOCK=on|off (default on): 'now' is the instant of the serving snapshot plus the time
  elapsed since the demo reset (apps.api.clock.DemoClock), so refund deadlines, review dates and
  the KPI week stay coherent with the decisions the reset made at the snapshot instant; off runs
  the API on the wall clock;
- PUBLIC_READ_ONLY=on|off (default off): the public demo answers every write route with 403
  forbidden_role, except the agent and the checkout evaluation, which are rate limited per client
  (spec 11, "Sicurezza"); the proxy protects the telemetry data with basic auth;
- LLM_CONCURRENCY (default 32): model calls in flight at once (the reset, batch_generate and the
  API's own executor for model calls);
- VLLM_TIMEOUT_S (default 20): seconds a vLLM call may take before it counts as unavailable.
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
_SWITCHES = {"on": True, "true": True, "1": True, "off": False, "false": False, "0": False}
DEFAULT_LLM_CONCURRENCY = 32
DEFAULT_VLLM_TIMEOUT_S = 20.0
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
    demo_clock: bool = True
    public_read_only: bool = False
    llm_concurrency: int = DEFAULT_LLM_CONCURRENCY
    vllm_timeout_s: float = DEFAULT_VLLM_TIMEOUT_S

    def __post_init__(self) -> None:
        if not 1 <= self.llm_concurrency <= 256:
            raise ValueError(f"LLM_CONCURRENCY must be 1 to 256, got {self.llm_concurrency}")
        if not 0 < self.vllm_timeout_s <= 600:
            raise ValueError(f"VLLM_TIMEOUT_S must be in (0, 600], got {self.vllm_timeout_s}")

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
            demo_clock=_switch(source, "DEMO_CLOCK", True),
            public_read_only=_switch(source, "PUBLIC_READ_ONLY", False),
            llm_concurrency=int(source.get("LLM_CONCURRENCY") or DEFAULT_LLM_CONCURRENCY),
            vllm_timeout_s=float(source.get("VLLM_TIMEOUT_S") or DEFAULT_VLLM_TIMEOUT_S),
        )

    @property
    def cached(self) -> bool:
        return self.demo_mode == "cached"

    @property
    def served_llm_mode(self) -> LlmMode:
        """The mode of the outputs the API serves: vllm in cached mode, whose outputs vLLM made
        on the MI300X (a cache miss is the template, labelled), LLM_MODE otherwise (US-901)."""
        return "vllm" if self.cached else self.llm_mode


def _switch(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = (source.get(key) or "").strip().lower()
    if not raw:
        return default
    if raw not in _SWITCHES:
        raise ValueError(f"{key} must be on or off, got {raw!r}")
    return _SWITCHES[raw]
