"""Writes python-parity.json: policy.simulator outputs the browser port must reproduce (ECO-01-UI).

The API turns JSON numbers into Decimal with Decimal(str(x)) (apps/api/schemas.py, _dec), so the
inputs here are floats converted the same way. Run from the repository root after a change to
policy/simulator.py, then run the UI tests:

    python apps/ui/src/sim/parity/gen_parity.py
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from policy.simulator import (  # noqa: E402
    PROPOSAL_EXAMPLE,
    Assumptions,
    NetBenefit,
    net_benefit,
    sensitivity,
)

OUT = Path(__file__).with_name("python-parity.json")
EUR_FIELDS = (
    "logistics_cost_per_return_eur",
    "avg_undue_discount_eur",
    "hourly_cost_eur",
    "incentives_eur",
    "system_cost_eur",
    "error_cost_eur",
    "abandonment_margin_lost_eur",
    "incremental_margin_eur",
)


def from_json(inputs: dict[str, Any], version: str) -> Assumptions:
    """The conversion of AssumptionInputs.to_policy: Decimal(str(float))."""

    def dec(name: str) -> Decimal:
        return Decimal(str(inputs[name]))

    return Assumptions(
        period=str(inputs["period"]),
        logistics_returns_avoided=int(inputs["logistics_returns_avoided"]),
        logistics_cost_per_return_eur=dec("logistics_cost_per_return_eur"),
        undue_discounts_avoided=int(inputs["undue_discounts_avoided"]),
        avg_undue_discount_eur=dec("avg_undue_discount_eur"),
        minutes_saved=dec("minutes_saved"),
        hourly_cost_eur=dec("hourly_cost_eur"),
        incentives_eur=dec("incentives_eur"),
        system_cost_eur=dec("system_cost_eur"),
        error_cost_eur=dec("error_cost_eur"),
        abandonment_margin_lost_eur=dec("abandonment_margin_lost_eur"),
        incremental_margin_eur=dec("incremental_margin_eur"),
        version=version,
    )


def dump(nb: NetBenefit) -> dict[str, Any]:
    return {
        "components": {key: str(value) for key, value in nb.components.items()},
        "total": str(nb.total),
    }


def case(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    a = from_json(inputs, "assumptions-parity")
    return {
        "name": name,
        "inputs": inputs,
        "net_benefit": dump(net_benefit(a)),
        "sensitivity": {key: dump(value) for key, value in sensitivity(a).items()},
    }


def inputs_of(a: Assumptions) -> dict[str, Any]:
    out: dict[str, Any] = {
        "period": a.period,
        "logistics_returns_avoided": a.logistics_returns_avoided,
        "undue_discounts_avoided": a.undue_discounts_avoided,
        "minutes_saved": float(a.minutes_saved),
    }
    out.update({name: float(getattr(a, name)) for name in EUR_FIELDS})
    return out


def random_inputs(rng: random.Random) -> dict[str, Any]:
    def eur(top: int) -> float:
        return round(rng.randint(0, top * 100) / 100, 2)

    return {
        "period": "random",
        "logistics_returns_avoided": rng.randint(0, 5000),
        "logistics_cost_per_return_eur": eur(40),
        "undue_discounts_avoided": rng.randint(0, 2000),
        "avg_undue_discount_eur": eur(60),
        "minutes_saved": round(rng.randint(0, 600_000) / 10, 1),
        "hourly_cost_eur": eur(90),
        "incentives_eur": eur(20_000),
        "system_cost_eur": eur(20_000),
        "error_cost_eur": eur(20_000),
        "abandonment_margin_lost_eur": eur(20_000),
        "incremental_margin_eur": eur(20_000),
    }


def main() -> None:
    base = inputs_of(PROPOSAL_EXAMPLE)
    edges: list[tuple[str, dict[str, Any]]] = [
        ("spec 8.10 example (ECO-01)", base),
        ("assumptions-v2: carrier at 6.50", {**base, "logistics_cost_per_return_eur": 6.5}),
        ("thirds of an hour", {**base, "minutes_saved": 100.0}),
        (
            "a half cent from halving 8.05",
            {**base, "logistics_returns_avoided": 3, "logistics_cost_per_return_eur": 8.05},
        ),
        ("two minutes at 0.15 per hour", {**base, "minutes_saved": 2.0, "hourly_cost_eur": 0.15}),
        ("one minute at 0.30 per hour", {**base, "minutes_saved": 1.0, "hourly_cost_eur": 0.3}),
        ("seven minutes at 0.15 per hour", {**base, "minutes_saved": 7.0, "hourly_cost_eur": 0.15}),
        (
            "fifty minutes at 22.50 per hour",
            {**base, "minutes_saved": 50.0, "hourly_cost_eur": 22.5},
        ),
        ("tenth of a minute", {**base, "minutes_saved": 0.1, "hourly_cost_eur": 0.03}),
        ("everything zero", {name: 0 if name != "period" else "zero" for name in base}),
        (
            "large volumes",
            {
                **base,
                "logistics_returns_avoided": 1_000_000,
                "logistics_cost_per_return_eur": 99999.99,
                "minutes_saved": 9_999_999.9,
                "hourly_cost_eur": 333.33,
            },
        ),
        (
            "odd unit cost halved",
            {**base, "logistics_returns_avoided": 7, "logistics_cost_per_return_eur": 0.01},
        ),
    ]
    rng = random.Random(42)
    randoms = [(f"random {i}", random_inputs(rng)) for i in range(120)]
    # The scenario arithmetic of sensitivity() on a set with the base's period, for the record.
    halved = replace(PROPOSAL_EXAMPLE, logistics_cost_per_return_eur=Decimal("4"))
    assert net_benefit(halved).total == Decimal("48.00")
    cases = [case(name, inputs) for name, inputs in (*edges, *randoms)]
    # One case per line: small, and a change to policy/simulator.py shows as a readable diff.
    lines = ",\n".join(json.dumps(c, separators=(",", ":")) for c in cases)
    header = '{"generated_by":"apps/ui/src/sim/parity/gen_parity.py","source":"policy/simulator.py"'
    OUT.write_text(f'{header},"cases":[\n{lines}\n]}}\n')
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
