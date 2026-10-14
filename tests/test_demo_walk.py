"""The golden demo walk (scripts/demo_walk.py) in CI, on the tiny snapshot of the harness.

The walk the presenter runs over HTTP against the full store (`python -m scripts.demo_walk`)
runs here through the ASGI app, CPU and LLM_MODE=mock: the queue, Giulia, a Marco ring account
approved with its coupon block, the checkout of another ring account and its confirmation, the
Rossi family, Elena and her catalogue task, a case with insufficient evidence, a link dispute,
the three agent questions, the matrix, the simulator, KPI, fairness and the audit CSV. Every
response is checked against its JSON Schema and model, with the latency targets of US-301 and
US-403. Every number is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.demo_walk import CHECKOUT_LIMIT_S, QUEUE_LIMIT_S, main, walk
from tests.api_harness import Api

STEPS = [
    "readiness",
    "demo_cases",
    "queue",
    "giulia",
    "marco",
    "checkout",
    "rossi",
    "elena",
    "insufficient",
    "dispute",
    "agent",
    "manager",
]


@pytest.mark.e2e
def test_the_golden_demo_walk(api: Api) -> None:
    report = walk(api.client)
    assert report.failed == {}, json.dumps(report.failed, indent=2)
    assert report.passed == STEPS
    targets = report.facts["targets"]
    assert targets["queue_first_page_s"] < QUEUE_LIMIT_S
    assert targets["checkout_evaluate_s"] < CHECKOUT_LIMIT_S
    assert report.facts["manager"]["simulator"]["base"] == 368.0  # ECO-01
    assert report.facts["marco"]["review_at"] is not None  # rule 5
    assert all(c.status in (200, 201, 409) for c in report.calls)
    # a second walk needs a new reset: no restriction awaits approval any more
    again = walk(api.client)
    assert again.passed == ["readiness", "demo_cases"] and "reset" in again.failed["queue"]


@pytest.mark.e2e
def test_the_command_reports_an_api_it_cannot_reach(tmp_path: Path) -> None:
    out = tmp_path / "walk.json"
    assert main(["--base-url", "http://127.0.0.1:9", "--report", str(out), "--timeout", "2"]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert list(report["failed"]) == ["readiness"] and report["passed"] == []
