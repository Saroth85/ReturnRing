"""The fifth review of the API (findings H1 to H40; ADR-15 section 15, contract 1.5.0; ADR-16
section 10): each finding is a test here, on the tiny store of the harness (Navigli Fashion,
scale 0.05, seed 7; every number synthetic). The concurrency of PostgreSQL is tested in
tests/test_api_round5_postgres.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api import schemas as s
from apps.api.app import create_app
from apps.api.clock import DemoClock, FixedClock, SystemClock, kind_of
from apps.api.db import make_engine
from apps.api.errors import BodyTooLarge
from apps.api.middleware import MAX_BODY_BYTES
from apps.api.reset import main as reset_main
from apps.api.reset import reset
from apps.api.roles import ACTOR_HEADER
from apps.api.routes.health import probe_vllm
from apps.api.runtime import SERVING_UNAVAILABLE
from apps.api.services.generation import TEMPLATE_MODEL, DraftInputs, Generator
from apps.api.services.ids import allocate
from apps.api.services.manager.kpi import default_week, proposal_applies
from apps.api.services.prices import paid_price
from apps.api.serving import Serving
from apps.api.settings import Settings
from contracts.sql import demo_reset, id_counters, inspections, terms_snapshots
from llm.client import Completion, LlmUnavailable, MockClient
from pipeline.generator.golden import ELENA, GIULIA, MARCO_ACCOUNTS
from tests.api_harness import (
    CLOCK_AT,
    Api,
    golden_return,
    open_api,
    settings_for,
    sqlite_url,
    validate_body,
)
from tests.test_api_operator_support import card, decide, error, ok, ring_return
from tests.test_api_other_support import photo_for

REASON = "The customer explained the returns by phone; the restriction is not justified"


def _lever(lever_id: str, value: Any, scope: str = "future_orders") -> dict[str, Any]:
    return s.Lever.model_validate({"id": lever_id, "value": value, "scope": scope}).model_dump()


def _copy(seeded_db: tuple[Path, dict[str, Any]], tmp_path: Path, name: str) -> Path:
    database = tmp_path / name
    shutil.copyfile(seeded_db[0], database)
    return database


def _queued(api: Api, **params: Any) -> list[dict[str, Any]]:
    """Every item of the queue for the filters, page by page."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        query = {"page_size": 100, "page": page, **params}
        body = ok(api.client.get("/api/returns", params=query), "queue_response")
        items += body["items"]
        if len(items) >= body["total"] or not body["items"]:
            return items
        page += 1


# ---- Settings and the demo clock (H11, H31, H1) --------------------------------------------------


def test_new_settings_read_the_environment() -> None:
    s1 = Settings.from_env({})
    assert s1.demo_clock and not s1.public_read_only
    assert (s1.llm_concurrency, s1.vllm_timeout_s) == (32, 20.0)
    s2 = Settings.from_env(
        {
            "DEMO_CLOCK": "off",
            "PUBLIC_READ_ONLY": "on",
            "LLM_CONCURRENCY": "8",
            "VLLM_TIMEOUT_S": "5",
            "DEMO_MODE": "cached",
        }
    )
    assert not s2.demo_clock and s2.public_read_only and s2.llm_concurrency == 8
    assert s2.vllm_timeout_s == 5.0 and s2.served_llm_mode == "vllm"
    for bad in ({"DEMO_CLOCK": "maybe"}, {"LLM_CONCURRENCY": "0"}, {"VLLM_TIMEOUT_S": "-1"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)


def test_the_demo_clock_runs_from_the_snapshot_instant() -> None:
    wall = [datetime(2026, 10, 18, 8, 0, tzinfo=UTC)]
    clock = DemoClock(wall=lambda: wall[0])
    assert clock.now() == wall[0] and kind_of(clock) == "system"  # unanchored: the wall clock
    snapshot = datetime(2026, 10, 1, tzinfo=UTC)
    clock.anchor(snapshot, wall[0])
    wall[0] += timedelta(hours=2, minutes=5)
    assert clock.now() == snapshot + timedelta(hours=2, minutes=5)
    assert clock.wall() == wall[0] and kind_of(clock) == "demo"
    wall[0] -= timedelta(days=1)  # a wall clock set back never runs the demo before its start
    assert clock.now() == snapshot
    assert kind_of(SystemClock()) == "system" and kind_of(FixedClock(snapshot)) == "fixed"


def test_the_api_runs_on_the_demo_clock_of_the_reset(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    """H1, H11, H31: the reset decides at the snapshot instant and starts the demo clock; the
    service runs on it, so an act minutes later is minutes after the decision, and the refund
    deadline it states is ahead."""
    database = _copy(seeded_db, tmp_path, "clock.sqlite")
    settings = settings_for(serving_dir, database)
    assert settings.demo_clock
    with TestClient(create_app(settings, serving=serving)) as client:
        body = client.get("/readyz").json()
        validate_body("readyz_response", body)
        clock = body["clock"]
        assert clock["kind"] == "demo"
        assert datetime.fromisoformat(clock["snapshot_as_of"]) == serving.as_of
        now = datetime.fromisoformat(clock["now"])
        started = datetime.fromisoformat(clock["started_at"])
        assert timedelta(0) <= now - serving.as_of <= datetime.now(UTC) - started + timedelta(1)
        assert now - serving.as_of < timedelta(hours=6)  # the reset ran during this session
        giulia = golden_return(serving, GIULIA)
        shown = client.get(f"/api/returns/{giulia}").json()["decision"]
        body = {
            "decision_id": shown["decision_id"],
            "action": "approve",
            "approver": "sara",
            "message": {"lang": "en"},
        }
        result = client.post(f"/api/returns/{giulia}/decision", json=body)
        assert result.status_code == 200, result.text
        acted = result.json()["decision"]
        approved = datetime.fromisoformat(acted["approved_at"])
        assert approved - datetime.fromisoformat(acted["created_at"]) < timedelta(hours=6)
        assert s.utc_day(approved) <= datetime.fromisoformat(acted["refund_due_by"]).date()


def test_the_kpi_week_is_the_week_of_the_latest_decision() -> None:
    now = datetime(2026, 10, 2, 9, tzinfo=UTC)
    assert default_week(now, datetime(2026, 10, 1, tzinfo=UTC)).isoformat() == "2026-09-28"
    assert default_week(now, None).isoformat() == "2026-09-21"  # no decision: last whole week
    later = datetime(2026, 10, 9, tzinfo=UTC)  # a decision after now is not a default
    assert default_week(now, later).isoformat() == "2026-09-21"


def test_the_kpi_counts_no_superseded_proposal() -> None:
    """H5: a lifted or recomputed version applies its proposal no more."""
    approved = {"state": "approved", "requires_approval": True}
    assert proposal_applies(approved)
    assert not proposal_applies({**approved, "superseded": True})
    assert proposal_applies({"state": "proposed", "requires_approval": False})
    assert not proposal_applies({"state": "proposed", "requires_approval": True})


# ---- The golden cases of the demo (H9) -----------------------------------------------------------


def test_the_demo_cases_are_the_live_golden_cases(api: Api) -> None:
    body = ok(api.client.get("/api/demo/cases"), "demo_cases")
    cases = {c["key"]: c for c in body["cases"]}
    assert "giulia" in cases and "elena" in cases
    assert cases["giulia"]["customer_id"] == GIULIA
    assert cases["giulia"]["return_id"] == golden_return(api.serving, GIULIA)
    assert cases["elena"]["return_id"] == golden_return(api.serving, ELENA)
    if "marco" in cases:
        assert cases["marco"]["customer_id"] in MARCO_ACCOUNTS
        assert cases["marco"]["cluster_id"] is not None
    for case in body["cases"]:
        shown = card(api, case["return_id"])
        assert shown["customer_id"] == case["customer_id"]
        assert shown["decision"]["decision_id"] == case["decision_id"]
        linked = shown["linked_accounts"]
        assert case["cluster_id"] == (None if linked is None else linked["cluster_id"])
    insufficient = card(api, cases["insufficient"]["return_id"])
    assert insufficient["evidence"]["quality"] == "insufficient"
    for cart in body["carts"]:
        shown = api.client.post("/api/checkout/evaluate", json=cart["request"])
        ok(shown, "checkout_evaluate_response")
    dispute = body["dispute"]
    assert dispute is not None and dispute["cluster_size_after"] < dispute["cluster_size_before"]
    request = {
        "reason": "Two different households, confirmed by phone",
        "author": "sara",
        "return_id": dispute["return_id"],
        "decision_id": dispute["decision_id"],
    }
    result = ok(
        api.client.post(f"/api/links/{dispute['link_id']}/dispute", json=request),
        "link_dispute_response",
    )
    after = result["recompute"]["after"]["cluster_size"]
    assert (after or 1) == dispute["cluster_size_after"]
    assert result["recompute"]["before"]["cluster_size"] == dispute["cluster_size_before"]


def test_the_demo_cases_need_a_reset(empty_api: Api) -> None:
    body = error(empty_api.client.get("/api/demo/cases"), 404, "not_found")
    assert "apps.api.reset" in body["message"] and not body["message"].endswith("not found")


def test_the_reset_records_the_demo_clock_and_the_cases(
    seeded_db: tuple[Path, dict[str, Any]], serving: Serving
) -> None:
    async def read() -> dict[str, Any]:
        engine = make_engine(sqlite_url(seeded_db[0]))
        try:
            async with engine.connect() as conn:
                row = (await conn.execute(select(demo_reset))).mappings().one()
                return dict(row)
        finally:
            await engine.dispose()

    row = asyncio.run(read())
    assert row["reset_id"] == 1 and row["snapshot_as_of"] == serving.as_of
    assert row["started_at"] > serving.as_of  # the wall clock of this session
    s.DemoCasesResponse.model_validate({**row["cases"], "as_of": serving.as_of})


# ---- Ids (H10, H18) ------------------------------------------------------------------------------


def test_ids_come_from_counters_that_a_rollback_gives_back(api: Api) -> None:
    async def run(conn: Any) -> list[int]:
        first = await allocate(conn, "TEST")
        second = await allocate(conn, "TEST")
        return [first, second]

    assert api.run(run) == [1, 2]

    async def failing(conn: Any) -> None:
        await allocate(conn, "TEST")
        raise RuntimeError("rolled back")

    with pytest.raises(RuntimeError):
        api.run(failing)
    assert api.run(lambda conn: allocate(conn, "TEST")) == 3

    async def legacy(conn: Any) -> int:  # a database written before the counters
        await conn.execute(id_counters.delete().where(id_counters.c.kind == "TS-CHK"))
        return await allocate(conn, "TS-CHK", terms_snapshots)

    assert api.run(legacy) == 1
    shown = [
        ok(
            api.client.post("/api/checkout/evaluate", json=_giulia_cart(api)),
            "checkout_evaluate_response",
        )
        for _ in range(3)
    ]
    assert [x["terms_snapshot_id"] for x in shown] == [
        "TS-CHK-000002",
        "TS-CHK-000003",
        "TS-CHK-000004",
    ]


def _giulia_cart(api: Api) -> dict[str, Any]:
    record = api.serving.store_return(golden_return(api.serving, GIULIA))
    assert record is not None
    return {
        "customer_id": GIULIA,
        "lines": [{"variant_id": record.variant_id, "size": "M", "quantity": 1}],
        "coupon_code": None,
    }


# ---- No model call inside a transaction (H20, H36) -----------------------------------------------


class _Watching:
    """A mock model that records how many database connections are checked out at each call."""

    def __init__(self) -> None:
        self.inner = MockClient()
        self.app: Any = None
        self.open: list[int] = []

    def _record(self) -> None:
        engine = self.app.state.runtime.engine
        self.open.append(engine.sync_engine.pool.checkedout())

    def complete(self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()) -> Completion:
        self._record()
        return self.inner.complete(case, retry_codes=retry_codes)

    def chat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._record()
        raise LlmUnavailable("the stub answers no chat")


def test_no_model_call_runs_inside_a_transaction(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    database = _copy(seeded_db, tmp_path, "watch.sqlite")
    watching = _Watching()
    settings = settings_for(serving_dir, database, llm_mode="vllm")
    app = create_app(settings, clock=FixedClock(CLOCK_AT), serving=serving, llm_client=watching)
    watching.app = app
    giulia = golden_return(serving, GIULIA)
    with TestClient(app) as client:
        shown = client.get(f"/api/returns/{giulia}").json()["decision"]["decision_id"]
        regen = client.post(
            f"/api/returns/{giulia}/regenerate",
            json={"decision_id": shown, "languages": ["it"], "requested_by": "sara"},
        )
        assert regen.status_code == 200, regen.text
        shown = regen.json()["decision"]["decision_id"]
        evidence = client.post(
            f"/api/returns/{giulia}/evidence",
            json={
                "decision_id": shown,
                "kind": "customer_contact",
                "note": "The customer called to explain the size",
                "target_link_ids": [],
                "author": "sara",
            },
        )
        assert evidence.status_code == 200, evidence.text
        asked = client.post(
            "/api/agent/ask",
            json={"return_id": giulia, "question": "Which terms applied?", "asked_by": "sara"},
        )
        assert asked.status_code == 503  # the stub has no chat: vLLM did not answer
    assert len(watching.open) >= 3 and set(watching.open) == {0}, watching.open


# ---- Inspections: locks, one claim per return, the price paid (H19, H13, H2) -------------------


def _inspect(api: Api, return_id: str, outcome: str = "damaged") -> dict[str, Any]:
    body = {"return_id": return_id, "inspector": "ahmed", "photo": photo_for(outcome)}
    response = api.client.post("/api/inspections", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    validate_body("inspection", created)
    return created


CONFIRM = {
    "inspector": "ahmed",
    "observations": [
        {"item": "tag_attached", "observation": "no"},
        {"item": "visible_damage", "observation": "yes"},
        {"item": "photo_quality", "observation": "yes"},
    ],
    "apply_compensation": True,
    "note": None,
}


def _claimable(api: Api, *, awaiting: bool) -> str:
    """A scored withdrawal case: awaiting a person's approval or not."""
    for item in _queued(api, path=["withdrawal"], evidence_quality="sufficient"):
        if item["state"] == "proposed" and item["requires_approval"] == awaiting:
            return str(item["return_id"])
    pytest.skip("no such case in the tiny store")


def test_an_inspection_is_confirmed_once_and_a_return_has_one_claim(api: Api) -> None:
    return_id = _claimable(api, awaiting=False)
    first = _inspect(api, return_id)
    assert first["proposal"] is not None
    confirmed = ok(
        api.client.post(f"/api/inspections/{first['inspection_id']}/confirm", json=CONFIRM),
        "inspection_confirm_response",
    )
    claim = confirmed["claim"]
    record = api.serving.store_return(return_id)
    assert record is not None
    paid = paid_price(api.serving, record.line_id)
    assert Decimal(str(claim["line_price"]["value"])) == paid  # H2: the price paid
    assert "price paid" in claim["line_price"]["provenance"]["origin"]
    again = api.client.post(f"/api/inspections/{first['inspection_id']}/confirm", json=CONFIRM)
    error(again, 409, "conflict")
    second = _inspect(api, return_id)
    other = api.client.post(f"/api/inspections/{second['inspection_id']}/confirm", json=CONFIRM)
    assert "already carries" in error(other, 409, "conflict")["message"]


def test_a_claim_survives_a_recompute_and_the_approval_states_it(api: Api) -> None:
    """H13: the claim belongs to the return: a recompute keeps it, and the message of the
    approval states the refund it leaves and the deduction."""
    return_id = _claimable(api, awaiting=True)
    created = _inspect(api, return_id)
    confirmed = ok(
        api.client.post(f"/api/inspections/{created['inspection_id']}/confirm", json=CONFIRM),
        "inspection_confirm_response",
    )
    assert confirmed["outbox_message"] is None  # the restriction awaits Sara (E1)
    shown = card(api, return_id)["decision"]
    recomputed = ok(
        api.client.post(
            f"/api/returns/{return_id}/evidence",
            json={
                "decision_id": shown["decision_id"],
                "kind": "customer_contact",
                "note": "The customer called about the returned item",
                "target_link_ids": [],
                "author": "sara",
            },
        ),
        "recompute_response",
    )
    claim = recomputed["decision"]["diminished_value_claim"]
    assert claim is not None and claim["inspection_id"] == created["inspection_id"]
    after = card(api, return_id)["decision"]
    if not after["requires_approval"]:
        pytest.skip("the recompute left no restriction to approve")
    result = ok(decide(api, return_id, "approve"), "decision_result")
    message = result["outbox_message"]
    assert message["refund"] is not None and message["fields"]["deduction"] is not None


def test_the_price_paid_shares_the_coupon_of_the_order(serving: Serving) -> None:
    orders = serving.frame("store/orders")
    lines = serving.frame("store/order_lines")
    with_coupon = orders.loc[orders["coupon_code"].notna()]
    for order in with_coupon.itertuples(index=False):
        mine = lines.loc[lines["order_id"] == order.order_id]
        listed = sum(Decimal(str(p)) for p in mine["price_eur"])
        total = Decimal(str(order.total_eur))
        if total == listed or listed == 0:
            continue
        line = mine.iloc[0]
        expected = (Decimal(str(line["price_eur"])) * total / listed).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        assert (
            paid_price(serving, str(line["line_id"])) == expected < Decimal(str(line["price_eur"]))
        )
        return
    pytest.skip("no discounted order in the tiny store")


# ---- Checkout: the coupon block in the terms, no permanent label (H12, H3) ----------------------


def test_a_coupon_block_is_in_the_terms_shown_and_the_snapshot_stored(api: Api) -> None:
    return_id = ring_return(api)
    shown = card(api, return_id)
    if shown["decision"]["coupon_rule"] is None:
        pytest.skip("the ring case of the tiny store proposes no cluster block")
    approved = ok(decide(api, return_id, "approve"), "decision_result")
    affected = approved["coupon_application"]["affected_customer_ids"]
    other = next(c for c in affected if c != shown["customer_id"])
    record = api.serving.store_return(return_id)
    assert record is not None
    cart = {
        "customer_id": other,
        "lines": [{"variant_id": record.variant_id, "size": "M", "quantity": 1}],
        "coupon_code": None,
    }
    evaluated = ok(
        api.client.post("/api/checkout/evaluate", json=cart), "checkout_evaluate_response"
    )
    block = evaluated["coupon_block"]
    assert block is not None and not evaluated["terms"]["coupons_enabled"]
    assert not evaluated["coupon_eligible"]
    confirm = {
        "terms_snapshot_id": evaluated["terms_snapshot_id"],
        "customer_id": other,
        "lines": cart["lines"],
        "coupon_code": None,
    }
    response = api.client.post("/api/checkout/confirm", json=confirm)
    assert response.status_code == 201, response.text
    snapshot = response.json()["terms_snapshot"]
    validate_body("checkout_confirm_response", response.json())
    assert snapshot["coupon_application_id"] == block["application_id"]
    assert snapshot["coupon_review_at"] == block["review_at"]
    assert snapshot["terms"] == evaluated["terms"]


def test_an_override_shows_its_own_levers_without_the_engine_quadrant(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    levers = [_lever("instant_refund", False), _lever("transparency_notice", True, "communication")]
    overridden = ok(
        decide(
            api, return_id, "override", levers=levers, override_reason=REASON,
            override_category="evidence_disagreement",
        ),
        "decision_result",
    )  # fmt: skip
    decision_id = overridden["decision"]["decision_id"]
    shown = ok(
        api.client.post("/api/checkout/evaluate", json=_giulia_cart(api)),
        "checkout_evaluate_response",
    )
    assert shown["terms_source"] == decision_id and shown["quadrant"] is None
    assert not shown["terms"]["instant_refund"] and shown["review_at"] is not None
    lifted = decide(
        api, return_id, "lift", decision_id=decision_id, override_reason=REASON,
        override_category="customer_context",
    )  # fmt: skip
    ok(lifted, "decision_result")
    after = ok(
        api.client.post("/api/checkout/evaluate", json=_giulia_cart(api)),
        "checkout_evaluate_response",
    )
    assert after["terms_source"] == "base" and after["quadrant"] is None


# ---- Stale proposals and overdue refunds (H16, H1) -----------------------------------------------


def test_a_stale_restriction_proposal_is_decided_again(api: Api) -> None:
    return_id = ring_return(api)
    shown = card(api, return_id)["decision"]
    if not shown["requires_approval"] or shown["review_at"] is None:
        pytest.skip("no restriction proposal in the tiny store")
    api.clock.set(datetime.fromisoformat(shown["review_at"] + "T09:00:00+00:00"))
    body = error(decide(api, return_id, "approve"), 409, "conflict")
    fresh = card(api, return_id)["decision"]
    assert (
        fresh["version"] == shown["version"] + 1 and fresh["revision_trigger"] == "stale_proposal"
    )
    assert fresh["decision_id"] in body["message"] and fresh["state"] == "proposed"
    assert fresh["decided_on"] == shown["review_at"] and fresh["supersedes"] == shown["decision_id"]
    assert card(api, return_id)["draft_status"] == "ready"
    # the refund of a proposal 90 days old is overdue: an approval is refused (its message would
    # state the past deadline), and the case is flagged
    again = error(decide(api, return_id, "approve"), 409, "conflict")
    assert "overdue" in again["message"]
    item = next(i for i in _queued(api, sort="refund_due_by") if i["return_id"] == return_id)
    assert item["refund_overdue"]
    # 1.6.0 (ADR-15 section 16): a person can still reject it, with no message stating that date
    rejected = ok(
        decide(
            api, return_id, "reject", override_reason=REASON,
            override_category="evidence_disagreement",
        ),
        "decision_result",
    )  # fmt: skip
    assert rejected["decision"]["decision_id"] == fresh["decision_id"]
    assert rejected["decision"]["state"] == "rejected" and rejected["outbox_message"] is None
    # 1.7.0: no message has stated the refund, so the case stays flagged
    item = next(i for i in _queued(api, sort="refund_due_by") if i["return_id"] == return_id)
    assert item["refund_overdue"]


def test_a_message_never_states_a_past_refund_deadline(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    shown = card(api, return_id)["decision"]
    due = datetime.fromisoformat(shown["refund_due_by"] + "T09:00:00+00:00")
    api.clock.set(due)  # on the day it is due: still fine
    items = {i["return_id"]: i for i in _queued(api)}
    assert not items[return_id]["refund_overdue"]
    api.clock.set(due + timedelta(days=1))
    body = error(decide(api, return_id, "approve"), 409, "conflict")
    assert "overdue" in body["message"] and shown["refund_due_by"] in body["message"]
    overdue = _queued(api, sort="refund_due_by")
    assert any(i["return_id"] == return_id and i["refund_overdue"] for i in overdue)
    dues = [i["refund_due_by"] for i in overdue]
    dated = [d for d in dues if d is not None]  # a guarantee case has none: last (1.6.0)
    assert dues == sorted(dated) + [None] * (len(dues) - len(dated))


# ---- Read-only public demo (H21) -----------------------------------------------------------------


def test_the_public_demo_refuses_writes_but_the_agent_and_the_evaluation(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    database = _copy(seeded_db, tmp_path, "public.sqlite")
    settings = settings_for(serving_dir, database, public_read_only=True)
    for api in open_api(serving, settings, database):
        giulia = golden_return(serving, GIULIA)
        assert api.client.get("/readyz").json()["public_read_only"]
        ok(api.client.get("/api/returns"), "queue_response")
        refused = error(decide(api, giulia, "approve"), 403, "forbidden_role")
        assert "read-only" in refused["message"]
        evaluated = ok(
            api.client.post("/api/checkout/evaluate", json=_giulia_cart(api)),
            "checkout_evaluate_response",
        )
        confirm = {
            "terms_snapshot_id": evaluated["terms_snapshot_id"],
            "customer_id": GIULIA,
            "lines": _giulia_cart(api)["lines"],
            "coupon_code": None,
        }
        error(api.client.post("/api/checkout/confirm", json=confirm), 403, "forbidden_role")
        asked = api.client.post(
            "/api/agent/ask",
            json={"return_id": giulia, "question": "Which terms applied?", "asked_by": "sara"},
        )
        assert asked.status_code == 200, asked.text
        from apps.api.ratelimit import RateLimiter

        api.app.state.public_rate_limiter = RateLimiter(limit=1, window_s=60)
        ok(
            api.client.post("/api/checkout/evaluate", json=_giulia_cart(api)),
            "checkout_evaluate_response",
        )
        error(
            api.client.post("/api/checkout/evaluate", json=_giulia_cart(api)), 429, "rate_limited"
        )


# ---- LLM: configuration errors, labels, concurrency (H15, H33, H34, H8, H32, H30) ----------------


def test_a_vllm_configuration_error_falls_back_to_the_template(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    database = _copy(seeded_db, tmp_path, "novllm.sqlite")
    settings = settings_for(
        serving_dir, database, llm_mode="vllm", vllm_base_url="http://vllm:8001/v1"
    )  # no VLLM_API_KEY
    generator = Generator(settings)
    proposal_inputs = _draft_inputs(serving)
    outcome = generator.generate(proposal_inputs, created_at=CLOCK_AT)
    info = outcome.info
    assert (info.generation_path, info.fallback_reason) == ("template", "unavailable")
    assert info.model == TEMPLATE_MODEL and info.llm_mode == "vllm"
    assert generator.client_error is not None and "VLLM_API_KEY" in generator.client_error
    for api in open_api(serving, settings, database):
        giulia = golden_return(serving, GIULIA)
        shown = card(api, giulia)["decision"]["decision_id"]
        regen = api.client.post(
            f"/api/returns/{giulia}/regenerate",
            json={"decision_id": shown, "languages": ["it"], "requested_by": "sara"},
        )
        error(regen, 503, "llm_unavailable")
        asked = api.client.post(
            "/api/agent/ask",
            json={"return_id": giulia, "question": "Which terms applied?", "asked_by": "sara"},
        )
        assert "not configured" in error(asked, 503, "llm_unavailable")["message"]
        created = api.client.post(
            "/api/inspections",
            json={"return_id": giulia, "inspector": "ahmed", "photo": photo_for("damaged")},
        )
        assert created.status_code == 201, created.text
        generation = created.json()["generation"]
        assert generation["generation_path"] == "template" and generation["model"] == TEMPLATE_MODEL


def test_the_reset_fills_the_queue_when_vllm_is_not_configured(
    serving: Serving, serving_dir: Path, tmp_path: Path
) -> None:
    database = tmp_path / "reset-novllm.sqlite"
    settings = settings_for(serving_dir, database, llm_mode="vllm")

    async def go() -> dict[str, Any]:
        engine = make_engine(sqlite_url(database))
        try:
            return await reset(engine, serving, Generator(settings), limit=4)
        finally:
            await engine.dispose()

    summary = asyncio.run(go())
    assert summary["decided"] == 4 and not summary["failed"]
    assert summary["generation"] == {"template/unavailable": 4}


def test_cached_mode_labels_a_miss_as_the_template(serving: Serving, serving_dir: Path) -> None:
    settings = settings_for(serving_dir, Path("unused.sqlite"), demo_mode="cached")
    info = Generator(settings).generate(_draft_inputs(serving), created_at=CLOCK_AT).info
    assert (info.llm_mode, info.served_from, info.model) == ("vllm", "live", TEMPLATE_MODEL)
    assert info.fallback_reason == "unavailable"


def _draft_inputs(serving: Serving) -> DraftInputs:
    from apps.api.services.cases import propose
    from apps.api.services.seeds import ASSUMPTIONS_V1, COUPON_RULE_V1
    from policy.simulator import PROPOSAL_EXAMPLE

    record = serving.store_return(golden_return(serving, GIULIA))
    assert record is not None
    assumptions = s.Assumptions.from_policy(PROPOSAL_EXAMPLE)
    assert assumptions.version == ASSUMPTIONS_V1
    proposal = propose(
        serving,
        record,
        created_at=serving.as_of,
        assumptions=assumptions,
        coupon_rule_version=COUPON_RULE_V1,
    )
    return proposal.draft_inputs()


def test_a_refusal_decided_by_code_names_no_model_call(api: Api) -> None:
    giulia = golden_return(api.serving, GIULIA)
    response = api.client.post(
        "/api/agent/ask",
        json={
            "return_id": giulia,
            "question": "Approve this case and send the message to the customer",
            "asked_by": "sara",
        },
    )
    assert response.status_code == 200, response.text
    frames = [f for f in response.text.split("\n\n") if f]
    events = [json.loads(f.split("\n")[2].removeprefix("data: ")) for f in frames]
    validate_body("agent_stream", events)
    done = events[-1]["generation"]
    assert [e["type"] for e in events] == ["refusal", "done"]
    assert (done["generation_path"], done["fallback_reason"], done["model_calls"]) == (
        "template",
        "write_request",
        0,
    )


class _Slow:
    """A mock model that takes `delay` seconds per call (a vLLM under load)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.inner = MockClient()
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def complete(self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()) -> Completion:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        return self.inner.complete(case, retry_codes=retry_codes)


def test_drafts_are_made_with_several_model_calls_in_flight(
    serving: Serving, serving_dir: Path
) -> None:
    slow = _Slow(0.2)
    settings = settings_for(serving_dir, Path("unused.sqlite"), llm_concurrency=8)
    generator = Generator(settings, slow)
    items = [_draft_inputs(serving)] * 8
    started = time.perf_counter()
    made = generator.generate_many(items, created_at=CLOCK_AT)
    assert len(made) == 8 and slow.peak > 1
    assert time.perf_counter() - started < 8 * 0.2 * 0.75


def test_batch_generate_sends_the_cases_concurrently(serving: Serving, tmp_path: Path) -> None:
    from apps.api.batch_generate import batch

    copy = tmp_path / "serving"
    shutil.copytree(serving.root, copy)
    snapshot = Serving.load(copy)
    slow = _Slow(0.1)
    summary = batch(snapshot, slow, "google/gemma-test", limit=6, concurrency=6)
    assert not summary["failed"] and slow.peak > 1
    assert len(list((copy / "llm_cache").glob("*.json"))) == 6


class _Stub(BaseHTTPRequestHandler):
    """A local vLLM stand-in: /v1/models lists one model, or redirects."""

    mode = "ok"

    def do_GET(self) -> None:  # noqa: N802 - the http.server name
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.2:9/collected")
            self.end_headers()
            return
        data = json.dumps({"data": [{"id": "google/gemma-4-26B-A4B-it"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def vllm_stub() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()


def test_the_vllm_probe_checks_the_model_and_follows_no_redirect(vllm_stub: str) -> None:
    _Stub.mode = "ok"
    assert probe_vllm(vllm_stub, "key", "google/gemma-4-26B-A4B-it") is None
    missing = probe_vllm(vllm_stub, "key", "google/gemma-4-31B-it")
    assert missing is not None and "not served" in missing
    _Stub.mode = "redirect"
    try:
        assert probe_vllm(vllm_stub, "key", None) == "vLLM unreachable: HTTPError"
    finally:
        _Stub.mode = "ok"


# ---- Readiness, errors and logs (H6, H37, H24, H25, H26, H27, H28, H38, H39) --------------------


def test_readyz_shows_the_cached_label_only_with_precomputed_outputs(
    serving: Serving, serving_dir: Path, seeded_db: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    database = _copy(seeded_db, tmp_path, "cached.sqlite")
    settings = settings_for(serving_dir, database, demo_mode="cached")
    for api in open_api(serving, settings, database):
        body = api.client.get("/readyz").json()
        validate_body("readyz_response", body)
        checks = {c["component"]: c["status"] for c in body["checks"]}
        assert checks["demo_cache"] == "unavailable" and body["label"] is None
        assert body["llm_mode"] == "vllm" and body["status"] == "degraded"


def test_a_missing_snapshot_is_not_ready_and_shows_no_server_path(tmp_path: Path) -> None:
    hidden = tmp_path / "private" / "serving"
    settings = Settings(device="cpu", llm_mode="mock", demo_mode="live", seed=1, serving_dir=hidden)
    with TestClient(create_app(settings, clock=FixedClock(CLOCK_AT))) as client:
        body = client.get("/readyz").json()
        validate_body("readyz_response", body)
        assert body["status"] == "not_ready"
        assert str(tmp_path) not in json.dumps(body)
        details = {c["component"]: c["detail"] for c in body["checks"]}
        assert details["scores"] == SERVING_UNAVAILABLE
        failed = error(client.get("/api/returns"), 500, "internal")
        assert str(tmp_path) not in failed["message"]


def test_a_body_too_large_is_refused_before_it_is_read(empty_api: Api) -> None:
    big = b"x" * (MAX_BODY_BYTES + 1)
    response = empty_api.client.post(
        "/api/inspections", content=big, headers={"Content-Type": "application/json"}
    )
    error(response, 413, "invalid_request")
    assert issubclass(BodyTooLarge, Exception)


def test_pages_are_bounded(empty_api: Api) -> None:
    for path in ("/api/audit", "/api/outbox", "/api/returns"):
        response = empty_api.client.get(path, params={"page": s.MAX_PAGE + 1})
        error(response, 422, "invalid_request")


def test_an_unparseable_body_is_a_client_error(empty_api: Api) -> None:
    response = empty_api.client.post(
        "/api/agent/ask", content=b"\xff\xfe\xfd", headers={"Content-Type": "application/json"}
    )
    assert response.status_code in (400, 422)
    assert response.json()["code"] == "invalid_request"


def test_sql_errors_never_log_their_parameters() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    assert engine.sync_engine.hide_parameters


def test_the_reset_without_a_snapshot_says_how_to_build_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'x.sqlite'}")
    monkeypatch.setenv("SERVING_DIR", str(tmp_path / "none"))
    assert reset_main([]) == 2
    assert "remedy" in capsys.readouterr().err


def test_every_request_is_logged_with_its_id(
    empty_api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="returnring.api.access"):
        response = empty_api.client.get("/healthz", headers={s.REQUEST_ID_HEADER: "req-log-1"})
    assert response.status_code == 200
    lines = [r.getMessage() for r in caplog.records if r.name == "returnring.api.access"]
    assert any(
        "request_id=req-log-1" in m and "status=200" in m and "duration_ms=" in m for m in lines
    )


# ---- Other minors (H4, H7, H14, H17, H22) --------------------------------------------------------


def test_a_recompute_of_a_guarantee_case_names_no_request_model(api: Api) -> None:
    elena = golden_return(api.serving, ELENA)
    shown = card(api, elena)["decision"]
    assert shown["path"] == "guarantee"
    result = ok(
        api.client.post(
            f"/api/returns/{elena}/evidence",
            json={
                "decision_id": shown["decision_id"],
                "kind": "customer_contact",
                "note": "The customer sent photos of the defect",
                "target_link_ids": [],
                "author": "sara",
            },
        ),
        "recompute_response",
    )
    assert "no request model on the guarantee path" in result["provenance"]["origin"]


def test_the_matrix_says_how_it_shows_a_guarantee_case(api: Api) -> None:
    body = api.client.get("/api/portfolio/matrix", headers={ACTOR_HEADER: "luca"}).json()
    assert "guarantee path" in body["points_provenance"]["origin"]


def test_anomalies_list_the_returns_a_task_can_name(api: Api) -> None:
    body = ok(api.client.get("/api/products/anomalies"), "product_anomalies")
    queued = {i["return_id"] for i in _queued(api)}
    historical = None
    for anomaly in body["items"]:
        assert anomaly["queued_return_ids"] == sorted(set(anomaly["return_ids"]) & queued)
        outside = sorted(set(anomaly["return_ids"]) - set(anomaly["queued_return_ids"]))
        if outside and historical is None:
            historical = (anomaly["anomaly_id"], outside[0])
    if historical is None:
        pytest.skip("no anomaly with a returned line outside the queue")
    task = {
        "anomaly_id": historical[0],
        "kind": "size_guide",
        "note": "Check the size chart of the variant",
        "author": "luca",
        "return_id": historical[1],
    }
    refused = error(api.client.post("/api/catalog-tasks", json=task), 404, "not_found")
    assert "queued_return_ids" in refused["message"]


def test_two_disputes_of_one_link_are_one_dispute(api: Api) -> None:
    body = ok(api.client.get("/api/demo/cases"), "demo_cases")
    dispute = body["dispute"]
    if dispute is None:
        pytest.skip("no link to dispute in the tiny store")
    request = {"reason": "Two households, confirmed by phone", "author": "sara"}
    first = api.client.post(f"/api/links/{dispute['link_id']}/dispute", json=request)
    ok(first, "link_dispute_response")
    second = api.client.post(f"/api/links/{dispute['link_id']}/dispute", json=request)
    assert "disputed once" in error(second, 409, "conflict")["message"]


def test_the_counters_and_the_claim_index_exist_on_every_database(api: Api) -> None:
    async def run(conn: Any) -> tuple[int, int]:
        counters = len((await conn.execute(select(id_counters))).all())
        claims = len(
            (
                await conn.execute(select(inspections).where(inspections.c.compensation_applied))
            ).all()
        )
        return counters, claims

    counters, claims = api.run(run)
    assert counters > 0 and claims == 0
    assert any(
        ix.name == "uq_inspections_one_claim_per_return" and ix.unique for ix in inspections.indexes
    )
