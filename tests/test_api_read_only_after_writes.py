"""A store written on a running demo clock, then served read-only (ADR-15 section 21, contract
1.8.1), on the tiny store of the harness (Navigli Fashion, scale 0.05, seed 7; every number
synthetic).

The read-only demo freezes the demo clock (section 20). It froze it at the snapshot instant, so
a store someone had acted on with writes on (an approval and a lift of the Marco ring case, an
hour after the reset) was served with a 'now' earlier than its acts, and the views of those acts
answered 500 (DecisionView: approved_at after as_of; CouponRuleApplication: reversed after
as_of). A frozen clock now stands at the later of the snapshot instant and the latest act
recorded in the store, computed when the clock is (re-)anchored; a fresh reset still freezes it
exactly at the snapshot instant.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update

from apps.api import schemas as s
from apps.api.app import create_app
from apps.api.clock import DemoClock
from apps.api.db import make_engine
from apps.api.runtime import NOT_ACTS, act_columns, latest_act
from apps.api.serving import Serving
from contracts.sql import TABLES, UtcDateTime, demo_reset
from tests.api_harness import Api, settings_for, sqlite_url, validate_body
from tests.test_api_operator_support import decide, ok, ring_return

REASON = "The customer explained the returns by phone; the restriction is not justified"


def _started(database: Path, at: datetime) -> None:
    """The demo reset of `database` as if it had ended at the wall time `at`."""

    async def go() -> None:
        engine = make_engine(sqlite_url(database))
        try:
            async with engine.begin() as conn:
                await conn.execute(update(demo_reset).values(started_at=at))
        finally:
            await engine.dispose()

    asyncio.run(go())


def _latest(database: Path) -> datetime | None:
    async def go() -> datetime | None:
        engine = make_engine(sqlite_url(database))
        try:
            async with engine.connect() as conn:
                return await latest_act(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _act_on_marco(serving: Serving, serving_dir: Path, database: Path) -> tuple[str, datetime]:
    """Approve and lift the Marco ring case with writes on, on the running demo clock of a reset
    that ended an hour ago: the return id and the instant of the lift (the latest act)."""
    _started(database, datetime.now(UTC) - timedelta(hours=1))
    settings = settings_for(serving_dir, database)
    assert not settings.frozen_clock
    app = create_app(settings, serving=serving)
    with TestClient(app) as client:
        clock = app.state.runtime.clock
        assert isinstance(clock, DemoClock) and not clock.frozen
        api = Api(app, client, clock, serving, settings, database)  # type: ignore[arg-type]
        marco = ring_return(api)
        ok(decide(api, marco, "approve"), "decision_result")
        lifted = ok(
            decide(
                api, marco, "lift", approver="luca", override_reason=REASON,
                override_category="customer_context",
            ),
            "decision_result",
        )  # fmt: skip
    return marco, datetime.fromisoformat(lifted["decision"]["approved_at"])


def test_a_store_acted_on_with_writes_on_is_served_read_only(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    """The repro of the review: act with PUBLIC_READ_ONLY off, restart with it on, on the same
    database. The case card and the coupon rules answered 500; the frozen clock now stands at
    the lift, the latest act, and every view of the store is valid at that instant."""
    database = tmp_path / "acted.sqlite"
    shutil.copyfile(seeded_db[0], database)
    marco, lifted_at = _act_on_marco(serving, serving_dir, database)
    assert lifted_at > serving.as_of + timedelta(minutes=50)
    assert _latest(database) == lifted_at

    settings = settings_for(serving_dir, database, public_read_only=True)
    assert settings.frozen_clock
    app = create_app(settings, serving=serving)
    with TestClient(app) as client:
        card = client.get(f"/api/returns/{marco}")
        assert card.status_code == 200, card.text
        validate_body("case_card", card.json())
        assert datetime.fromisoformat(card.json()["decision"]["as_of"]) == lifted_at
        rules = client.get("/api/coupon-rules")
        assert rules.status_code == 200, rules.text
        validate_body("coupon_rules", rules.json())
        assert datetime.fromisoformat(rules.json()["as_of"]) == lifted_at
        for path, model in (
            ("/api/returns", "queue_response"),
            ("/api/audit", "audit_page"),
            ("/api/outbox", "outbox"),
            ("/api/demo/cases", "demo_cases"),
        ):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            validate_body(model, response.json())
        cases = s.DemoCasesResponse.model_validate(client.get("/api/demo/cases").json())
        assert cases.snapshot_as_of == serving.as_of and cases.as_of == lifted_at
        body = client.get("/readyz").json()
        validate_body("readyz_response", body)
        clock = s.ClockView.model_validate(body["clock"])
        assert clock.kind == "demo" and clock.frozen
        assert clock.now == lifted_at and clock.snapshot_as_of == serving.as_of
        assert clock.note == s.FROZEN_AFTER_ACTS_NOTE
        # a client never advances a frozen clock: it stands at the lift however long it runs
        runtime_clock = app.state.runtime.clock
        assert isinstance(runtime_clock, DemoClock)
        assert runtime_clock.now() == lifted_at
        assert runtime_clock.anchored is not None
        assert runtime_clock.anchored[0] == serving.as_of


def test_a_fresh_reset_freezes_exactly_at_the_snapshot_instant(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    """Every row of the reset is dated at the snapshot instant (a fixed clock), so its latest
    act is the snapshot instant itself, and the read-only demo of a fresh reset stands there with
    the note of section 19."""
    database = tmp_path / "fresh.sqlite"
    shutil.copyfile(seeded_db[0], database)
    _started(database, datetime.now(UTC) - timedelta(days=30))
    assert _latest(database) == serving.as_of
    settings = settings_for(serving_dir, database, public_read_only=True)
    with TestClient(create_app(settings, serving=serving)) as client:
        body = client.get("/readyz").json()
    validate_body("readyz_response", body)
    clock = s.ClockView.model_validate(body["clock"])
    assert clock.frozen and clock.now == clock.snapshot_as_of == serving.as_of
    assert clock.note == s.FROZEN_CLOCK_NOTE


def test_a_running_clock_ignores_the_latest_act() -> None:
    """Only a frozen clock stands at the latest act; a running one runs from the reset."""
    wall = [datetime(2026, 10, 18, 8, 0, tzinfo=UTC)]
    snapshot = datetime(2026, 10, 1, tzinfo=UTC)
    later = snapshot + timedelta(days=2)
    running = DemoClock(wall=lambda: wall[0])
    running.anchor(snapshot, wall[0], latest_act=later)
    assert running.now() == snapshot
    wall[0] += timedelta(hours=1)
    assert running.now() == snapshot + timedelta(hours=1)
    frozen = DemoClock(wall=lambda: wall[0], frozen=True)
    frozen.anchor(snapshot, wall[0], latest_act=later)
    assert frozen.now() == later and frozen.anchored == (snapshot, wall[0])
    wall[0] += timedelta(days=40)
    assert frozen.now() == later
    frozen.anchor(snapshot, wall[0], latest_act=snapshot - timedelta(days=1))  # never before
    assert frozen.now() == snapshot
    frozen.anchor(snapshot, wall[0])
    assert frozen.now() == snapshot


def test_a_frozen_clock_past_its_snapshot_says_why() -> None:
    """ClockView: a frozen clock stands at its snapshot instant, or past it at the latest act
    recorded in the store, and then its note says so (rule 11); never before it."""
    snapshot = datetime(2026, 10, 1, tzinfo=UTC)
    view = {
        "kind": "demo",
        "now": snapshot + timedelta(hours=1),
        "snapshot_as_of": snapshot,
        "started_at": snapshot + timedelta(days=3),
        "frozen": True,
        "note": s.FROZEN_AFTER_ACTS_NOTE,
    }
    s.ClockView.model_validate(view)
    s.ClockView.model_validate({**view, "now": snapshot, "note": s.FROZEN_CLOCK_NOTE})
    for broken in (
        {"note": s.FROZEN_CLOCK_NOTE},  # past the snapshot without saying why
        {"now": snapshot - timedelta(seconds=1)},  # before the snapshot
    ):
        try:
            s.ClockView.model_validate({**view, **broken})
        except ValueError:
            continue
        raise AssertionError(f"accepted {broken}")


def test_every_timestamp_column_is_classified() -> None:
    """Every timestamp of contracts/sql.py either dates an act on the API's clock (the latest of
    them floors a frozen clock) or is named as not one: a new column must be classified."""
    acts = {(c.table.name, c.name) for c in act_columns()}
    every = {(t.name, c.name) for t in TABLES for c in t.columns if isinstance(c.type, UtcDateTime)}
    assert acts | NOT_ACTS == every and not acts & NOT_ACTS
    assert ("audit_log", "at") in acts and ("decisions", "approved_at") in acts
    assert ("coupon_rule_applications", "reversed_at") in acts
    assert ("telemetry_samples", "sampled_at") in NOT_ACTS  # the wall clock
    assert ("demo_reset", "started_at") in NOT_ACTS  # the wall clock
