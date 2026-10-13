from __future__ import annotations

import pytest

from apps.api.settings import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DEVICE", "LLM_MODE", "DEMO_MODE", "SEED"):
        monkeypatch.delenv(key, raising=False)
    s = Settings.from_env()
    assert (s.device, s.llm_mode, s.demo_mode, s.seed) == ("cpu", "mock", "live", 42)


def test_rejects_unknown_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODE", "anthropic")
    with pytest.raises(ValueError):
        Settings.from_env()
