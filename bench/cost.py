"""Cost per decision with the LLM on the MI300X, idle time included (NFR-13, spec 12 "Costo
completo"). Versioned assumptions; every input says whether it was measured or assumed.

The droplet is billed for the time it exists, busy or idle (spec 11: 1.99 USD per hour). A cost
per decision is therefore the droplet time of an operating scenario divided by the decisions of
that scenario, never the GPU seconds of one generation alone. Scenarios of cost-v1:

- nightly-batch (the declared operating scenario of NFR-13, ADR-02 and ADR-03): every night the
  droplet is created from the snapshot, starts vLLM, runs the nightly pipeline (B4, measured)
  and generates the day's drafts at the measured batch-32 rate (B3), then is destroyed. Boot and
  idle margin are assumptions until measured.
- business-hours: the droplet serves live generation 12 hours a day, idle or not.
- always-on: 24 hours a day.

Decisions per day: the return requests per day of the store the benchmark ran on (the synthetic
Navigli Fashion store, 28 days before the scoring instant; nature synthetic). The target is
<= 0.01 EUR per decision; the break-even volume says how many decisions per day meet it.
Development experiments (spikes, benchmarks, failed runs) are not in any scenario.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

TARGET_EUR_PER_DECISION = Decimal("0.01")  # NFR-13
VERSION = "cost-v1"
DECLARED = "nightly-batch"
SECONDS_PER_HOUR = Decimal(3600)
VOLUME_WINDOW_DAYS = 28


@dataclass(frozen=True)
class Assumptions:
    """cost-v1. Change a value only with a new version (a cost is comparable within one)."""

    version: str = VERSION
    usd_per_hour: Decimal = Decimal("1.99")  # spec 10 and 11, AMD Developer Cloud MI300X droplet
    usd_per_hour_source: str = "AMD Developer Cloud MI300X droplet price in spec 10 and 11"
    eur_per_usd: Decimal = Decimal("0.86")
    eur_per_usd_source: str = (
        "assumption, a round figure near the ECB EUR/USD reference rates of 2025-2026; replace "
        "with the ECB reference rate of the day before quoting a cost"
    )
    billing: str = "per second while the droplet exists, powered on or off (assumption)"
    boot_seconds: Decimal = Decimal(600)  # droplet from snapshot to compose up: not measured
    idle_margin_seconds: Decimal = Decimal(600)  # waits between and after the jobs: assumption
    vllm_start_seconds: Decimal = Decimal(600)  # used only when B3 did not measure the start

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "usd_per_hour": str(self.usd_per_hour),
            "usd_per_hour_source": self.usd_per_hour_source,
            "eur_per_usd": str(self.eur_per_usd),
            "eur_per_usd_source": self.eur_per_usd_source,
            "billing": self.billing,
            "boot_seconds": str(self.boot_seconds),
            "idle_margin_seconds": str(self.idle_margin_seconds),
            "vllm_start_seconds_if_unmeasured": str(self.vllm_start_seconds),
            "target_eur_per_decision": str(TARGET_EUR_PER_DECISION),
        }


ASSUMPTIONS = Assumptions()
SCENARIOS: dict[str, str] = {
    "nightly-batch": "Droplet created each night: boot, vLLM start, nightly pipeline, the day's "
    "drafts at the batch-32 rate, idle margin, then destroyed (declared scenario, NFR-13)",
    "business-hours": "Droplet on 12 hours a day for live generation, idle or busy",
    "always-on": "Droplet on 24 hours a day",
}
ALWAYS_ON_HOURS = {"business-hours": Decimal(12), "always-on": Decimal(24)}


@dataclass(frozen=True)
class Inputs:
    decisions_per_day: Decimal
    seconds_per_decision: Decimal | None = None  # measured: batch-32 wall time / 32
    pipeline_seconds: Decimal | None = None  # measured: B4, without its generation step
    vllm_start_seconds: Decimal | None = None  # measured: start to healthy


@dataclass(frozen=True)
class Cost:
    scenario: str
    eur_per_decision: Decimal
    eur_per_1000_decisions: Decimal
    usd_per_day: Decimal
    eur_per_day: Decimal
    droplet_seconds_per_day: Decimal
    breakdown: dict[str, Any]
    meets_target: bool
    break_even_decisions_per_day: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "description": SCENARIOS[self.scenario],
            "eur_per_decision": str(self.eur_per_decision),
            "eur_per_1000_decisions": str(self.eur_per_1000_decisions),
            "usd_per_day": str(self.usd_per_day),
            "eur_per_day": str(self.eur_per_day),
            "droplet_seconds_per_day": str(self.droplet_seconds_per_day),
            "breakdown": self.breakdown,
            "meets_target": self.meets_target,
            "target_eur_per_decision": str(TARGET_EUR_PER_DECISION),
            "break_even_decisions_per_day": self.break_even_decisions_per_day,
        }


class CostUnavailable(ValueError):
    """A scenario that needs a measurement the run does not have."""


def _q(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def droplet_seconds(scenario: str, inputs: Inputs, a: Assumptions = ASSUMPTIONS) -> dict[str, Any]:
    """The droplet seconds per day of a scenario and where each part comes from."""
    if scenario in ALWAYS_ON_HOURS:
        total = ALWAYS_ON_HOURS[scenario] * SECONDS_PER_HOUR
        parts: dict[str, Any] = {"on_hours": str(ALWAYS_ON_HOURS[scenario])}
        if inputs.seconds_per_decision is not None:
            busy = inputs.decisions_per_day * inputs.seconds_per_decision
            parts["busy_seconds_measured_rate"] = str(_q(busy, "0.1"))
            parts["capacity_ok"] = busy <= total
        return {"total": total, "parts": parts}
    if scenario != "nightly-batch":
        raise ValueError(f"unknown scenario {scenario!r}")
    if inputs.seconds_per_decision is None or inputs.pipeline_seconds is None:
        raise CostUnavailable(
            "nightly-batch needs the measured batch-32 generation rate (B3 live) and the "
            "measured nightly pipeline (B4)"
        )
    vllm, vllm_origin = (
        (inputs.vllm_start_seconds, "measured")
        if inputs.vllm_start_seconds is not None
        else (a.vllm_start_seconds, "assumed")
    )
    generation = inputs.decisions_per_day * inputs.seconds_per_decision
    total = a.boot_seconds + vllm + inputs.pipeline_seconds + generation + a.idle_margin_seconds
    return {
        "total": total,
        "parts": {
            "boot_seconds": {"value": str(a.boot_seconds), "origin": "assumed"},
            "vllm_start_seconds": {"value": str(_q(vllm, "0.1")), "origin": vllm_origin},
            "pipeline_seconds": {
                "value": str(_q(inputs.pipeline_seconds, "0.1")),
                "origin": "measured (B4)",
            },
            "generation_seconds": {
                "value": str(_q(generation, "0.1")),
                "origin": "measured rate (B3 batch 32) x decisions per day",
            },
            "idle_margin_seconds": {"value": str(a.idle_margin_seconds), "origin": "assumed"},
        },
    }


def cost(scenario: str, inputs: Inputs, a: Assumptions = ASSUMPTIONS) -> Cost:
    if inputs.decisions_per_day <= 0:
        raise ValueError("decisions_per_day must be positive")
    seconds = droplet_seconds(scenario, inputs, a)
    total: Decimal = seconds["total"]
    usd = total / SECONDS_PER_HOUR * a.usd_per_hour
    eur = usd * a.eur_per_usd
    per_decision = eur / inputs.decisions_per_day
    # Volume that meets the target, the droplet time growing with the generation it adds.
    if scenario == "nightly-batch" and inputs.seconds_per_decision is not None:
        fixed = total - inputs.decisions_per_day * inputs.seconds_per_decision
        per_second = a.usd_per_hour * a.eur_per_usd / SECONDS_PER_HOUR
        margin = TARGET_EUR_PER_DECISION - inputs.seconds_per_decision * per_second
        break_even = (
            int((fixed * per_second / margin).to_integral_value(ROUND_CEILING))
            if margin > 0
            else -1
        )
    else:
        break_even = int((eur / TARGET_EUR_PER_DECISION).to_integral_value(ROUND_CEILING))
    return Cost(
        scenario=scenario,
        eur_per_decision=_q(per_decision, "0.000001"),
        eur_per_1000_decisions=_q(per_decision * 1000, "0.01"),
        usd_per_day=_q(usd, "0.01"),
        eur_per_day=_q(eur, "0.01"),
        droplet_seconds_per_day=_q(total, "0.1"),
        breakdown=seconds["parts"],
        meets_target=per_decision <= TARGET_EUR_PER_DECISION,
        break_even_decisions_per_day=break_even,
    )


def decisions_per_day(data_root: Path, as_of: datetime, days: int = VOLUME_WINDOW_DAYS) -> Decimal:
    """Return requests per day in the `days` before as_of, read as known at as_of (rule 7)."""
    import polars as pl

    from contracts.io import read_table

    table, _ = read_table("store/returns", data_root, as_of=as_of)
    frame = pl.from_arrow(table)
    assert isinstance(frame, pl.DataFrame)
    count = frame.filter(pl.col("event_at") > as_of - timedelta(days=days)).height
    return _q(Decimal(count) / Decimal(days), "0.01")


def all_costs(inputs: Inputs, a: Assumptions = ASSUMPTIONS) -> dict[str, Mapping[str, Any]]:
    """Every scenario: its cost, or why it cannot be computed with these inputs."""
    out: dict[str, Mapping[str, Any]] = {}
    for scenario in SCENARIOS:
        try:
            out[scenario] = cost(scenario, inputs, a).as_dict()
        except CostUnavailable as exc:
            out[scenario] = {"scenario": scenario, "unavailable": str(exc)}
    return out
