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
import shutil
from pathlib import Path
from typing import Any

import pytest

from apps.api.serving import Serving
from scripts.demo_walk import CHECKOUT_LIMIT_S, QUEUE_LIMIT_S, main, walk
from tests.api_harness import Api, open_api, settings_for

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
def test_the_read_only_walk_of_the_public_demo(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    """PUBLIC_READ_ONLY (spec 11, the default of the cached demo): the same steps read the same
    cases; every write of the pitch is a 403 forbidden_role that changes nothing, the checkout
    evaluation and the agent still answer, and the walk can run again without a reset (it
    changes no decision)."""
    database = tmp_path / "public.sqlite"
    shutil.copyfile(seeded_db[0], database)
    settings = settings_for(serving_dir, database, public_read_only=True)
    for api in open_api(serving, settings, database):
        report = walk(api.client)
        assert report.failed == {}, json.dumps(report.failed, indent=2)
        assert report.passed == STEPS
        assert report.facts["mode"]["public_read_only"] is True
        facts = report.facts
        decision = "POST /api/returns/{}/decision".format
        assert facts["refused_writes"] == [
            decision(facts["giulia"]["return_id"]),
            decision(facts["marco"]["return_id"]),
            "POST /api/checkout/confirm",
            decision(facts["elena"]["return_id"]),
            "POST /api/catalog-tasks",
            decision(facts["insufficient"]["return_id"]),
            f"POST /api/links/{facts['dispute']['link_id']}/dispute",
        ]
        assert {c.status for c in report.calls} <= {200, 403}
        assert len(facts["agent"]) == 3  # the agent still answers (rate limited)
        again = walk(api.client)
        assert again.failed == {} and again.passed == STEPS


@pytest.mark.e2e
def test_the_command_reports_an_api_it_cannot_reach(tmp_path: Path) -> None:
    out = tmp_path / "walk.json"
    assert main(["--base-url", "http://127.0.0.1:9", "--report", str(out), "--timeout", "2"]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert list(report["failed"]) == ["readiness"] and report["passed"] == []
