"""FastAPI entry point. Business endpoints arrive story by story (docs/spec.md section 9)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import FastAPI

from apps.api.settings import Settings
from policy.simulator import PROPOSAL_EXAMPLE, NetBenefit, net_benefit, sensitivity

settings = Settings.from_env()
app = FastAPI(title="ReturnRing API", version="0.1.0")


def _money(x: Decimal) -> str:
    return f"{x:.2f}"


def _benefit(nb: NetBenefit) -> dict[str, Any]:
    return {
        "total_eur": _money(nb.total),
        "components_eur": {k: _money(v) for k, v in nb.components.items()},
        "period": nb.period,
        "nature": nb.nature,
        "assumptions_version": nb.version,
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    return {
        "status": "ready",
        "mode": settings.demo_mode,
        "llm_mode": settings.llm_mode,
        "device": settings.device,
    }


@app.get("/api/simulator/assumptions")
def simulator_assumptions() -> dict[str, Any]:
    """Illustrative example only: every figure is labelled as simulated (US-906)."""
    return {
        "source": "Illustrative example from the proposals document (12 Oct 2026); "
        "not a ReturnRing result",
        "base": _benefit(net_benefit(PROPOSAL_EXAMPLE)),
        "sensitivity": {k: _benefit(v) for k, v in sensitivity(PROPOSAL_EXAMPLE).items()},
    }
