"""Runtime switches read from the environment (see CLAUDE.md, Environment flags)."""

from __future__ import annotations

import os
from dataclasses import dataclass

_ALLOWED = {
    "DEVICE": ("cpu", "gpu"),
    "LLM_MODE": ("mock", "vllm"),
    "DEMO_MODE": ("live", "cached"),
}


@dataclass(frozen=True)
class Settings:
    device: str
    llm_mode: str
    demo_mode: str
    seed: int

    @classmethod
    def from_env(cls) -> Settings:
        values = {k: os.environ.get(k, v[0]) for k, v in _ALLOWED.items()}
        for key, value in values.items():
            if value not in _ALLOWED[key]:
                raise ValueError(f"{key} must be one of {_ALLOWED[key]}, got {value!r}")
        return cls(
            device=values["DEVICE"],
            llm_mode=values["LLM_MODE"],
            demo_mode=values["DEMO_MODE"],
            seed=int(os.environ.get("SEED", "42")),
        )
