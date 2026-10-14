"""The rules and legal behaviour items of the review backlog of 14 Oct 2026 (ADR-15 section
16, contract 1.6.0), on the tiny store of the harness (Navigli Fashion, scale 0.05, seed 7;
every number synthetic):

1. the public or cached demo runs on a frozen demo clock, so no open return drifts into an
   overdue refund after the reset, and /readyz says so;
2. a guarantee case states no refund deadline: no 14-day date, never overdue, never blocked
   (rule 4, NFR-17);
3. after the refund deadline, a pending proposal can still be rejected or overridden to the
   standard terms, with no message stating a past date;
4. the score and value facts of a draft and of the agent are dated with the customer snapshot
   the engine read (rule 11);
5. the refund stated in a message has its own provenance, not the claim's (rule 11);
6. the coupon block shown to a linked account names no cause (rule 11).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.app import create_app
from apps.api.clock import DemoClock, kind_of
from apps.api.db import make_engine
from apps.api.services.other.agent import case_data
from apps.api.serving import Serving
from apps.api.settings import Settings
from contracts.sql import demo_reset
from llm.phrasebook import (
    COUPON_BLOCK_TEXT,
    cluster_coupon_block_text,
    phrase_problems,
)
from llm.text import LANGS, Lang
from pipeline.generator.golden import GIULIA
from tests.api_harness import Api, golden_return, settings_for, sqlite_url, validate_body
from tests.test_api_operator_support import card, decide, error, ok, ring_return
from tests.test_api_other_support import photo_for

REASON = "The customer explained the returns by phone; the restriction is not justified"
EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples" / "api"


def _queued(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    """Every item of the queue for the filters, page by page."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        response = client.get("/api/returns", params={"page_size": 100, "page": page, **params})
        assert response.status_code == 200, response.text
        body = response.json()
        validate_body("queue_response", body)
        items += body["items"]
        if len(items) >= body["total"] or not body["items"]:
            return items
        page += 1


def _at(day: str | date, hour: int = 9) -> datetime:
    found = date.fromisoformat(day) if isinstance(day, str) else day
    return datetime.combine(found, time(hour), tzinfo=UTC)


def _load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


# ---- 1. The public or cached demo runs on a frozen clock ----------------------------------------


def test_a_frozen_demo_clock_stands_at_the_snapshot_instant() -> None:
    wall = [datetime(2026, 10, 18, 8, 0, tzinfo=UTC)]
    clock = DemoClock(wall=lambda: wall[0], frozen=True)
    assert clock.frozen and clock.now() == wall[0]  # unanchored: the wall clock
    snapshot = datetime(2026, 10, 1, tzinfo=UTC)
    clock.anchor(snapshot, wall[0])
    wall[0] += timedelta(days=40)
    assert clock.now() == snapshot and clock.wall() == wall[0] and kind_of(clock) == "demo"
    assert not DemoClock().frozen


def test_only_the_read_only_demo_freezes_the_clock() -> None:
    """1.7.0 (review of section 16): a frozen clock dates every act at the same instant, so it
    stands only where writes are refused. The cached demo is read-only by default
    (infra/compose.demo.yml); with writes on, its clock runs."""
    assert not Settings.from_env({}).frozen_clock
    assert Settings.from_env({"PUBLIC_READ_ONLY": "on"}).frozen_clock
    assert not Settings.from_env({"DEMO_MODE": "cached"}).frozen_clock
    assert Settings.from_env({"DEMO_MODE": "cached", "PUBLIC_READ_ONLY": "on"}).frozen_clock
    for env in (
        {},
        {"PUBLIC_READ_ONLY": "on"},
        {"DEMO_MODE": "cached"},
        {"DEMO_MODE": "cached", "PUBLIC_READ_ONLY": "on"},
    ):
        settings = Settings.from_env(env)
        clock = create_app(settings).state.runtime.clock
        assert isinstance(clock, DemoClock) and clock.frozen == settings.frozen_clock


def test_a_frozen_clock_view_stands_at_its_snapshot() -> None:
    snapshot = datetime(2026, 10, 1, tzinfo=UTC)
    view = {
        "kind": "demo",
        "now": snapshot,
        "snapshot_as_of": snapshot,
        "started_at": snapshot + timedelta(days=3),
        "frozen": True,
        "note": "Demo clock, frozen",
    }
    s.ClockView.model_validate(view)
    with pytest.raises(ValidationError, match="frozen"):
        s.ClockView.model_validate({**view, "now": snapshot + timedelta(minutes=1)})
    with pytest.raises(ValidationError, match="frozen"):
        s.ClockView.model_validate(
            {**view, "kind": "system", "snapshot_as_of": None, "started_at": None}
        )


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


@pytest.mark.parametrize(
    "switch",
    [
        {},
        {"public_read_only": True},
        {"demo_mode": "cached", "public_read_only": True},
        {"demo_mode": "cached"},
    ],
    ids=["live", "public", "cached", "cached-writes"],
)
def test_no_open_return_drifts_into_an_overdue_refund_on_the_public_demo(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
    switch: Mapping[str, Any],
) -> None:
    """Backlog item 1: a month after the reset, the live demo clock has moved past the refund
    deadlines of the reset's proposals; the read-only demo (public, or cached as compose.demo.yml
    starts it) keeps the clock at the snapshot instant, and /readyz tells the truth (kind, now,
    snapshot, start, frozen). A cached demo with writes on keeps a running clock (1.7.0)."""
    database = tmp_path / "clock.sqlite"
    shutil.copyfile(seeded_db[0], database)
    month_ago = datetime.now(UTC) - timedelta(days=30)
    _started(database, month_ago)
    settings = settings_for(serving_dir, database, **switch)
    frozen = bool(switch.get("public_read_only"))
    with TestClient(create_app(settings, serving=serving)) as client:
        body = client.get("/readyz").json()
        validate_body("readyz_response", body)
        clock = body["clock"]
        now = datetime.fromisoformat(clock["now"])
        assert clock["kind"] == "demo" and clock["frozen"] is frozen
        assert datetime.fromisoformat(clock["snapshot_as_of"]) == serving.as_of
        assert abs(datetime.fromisoformat(clock["started_at"]) - month_ago) < timedelta(seconds=1)
        assert ("frozen" in clock["note"]) is frozen
        snapshot_day = s.utc_day(serving.as_of)
        at_reset = {
            i["return_id"]
            for i in _queued(client)
            if i["state"] == "proposed"
            and i["refund_due_by"] is not None
            and date.fromisoformat(i["refund_due_by"]) < snapshot_day
        }
        overdue = {i["return_id"] for i in _queued(client) if i["refund_overdue"]}
        if frozen:
            assert now == serving.as_of
            assert overdue == at_reset  # none drifted past its deadline since the reset
        else:
            assert now - serving.as_of >= timedelta(days=29)
            assert overdue > at_reset  # the live demo clock runs: deadlines pass


def test_a_cached_demo_with_writes_on_can_lift_a_restriction_after_a_reversal(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    """Review of section 16: on a frozen clock every act has the same instant, so a lift after
    someone reversed the cluster coupon application looked like a reversal at the lift itself,
    and the lift answered 500. A cached demo with writes on keeps a running clock (1.7.0)."""
    database = tmp_path / "cached.sqlite"
    shutil.copyfile(seeded_db[0], database)
    settings = settings_for(serving_dir, database, demo_mode="cached")
    app = create_app(settings, serving=serving)
    with TestClient(app) as client:
        clock = app.state.runtime.clock
        assert isinstance(clock, DemoClock) and not clock.frozen
        api = Api(app, client, clock, serving, settings, database)  # type: ignore[arg-type]
        return_id = ring_return(api)
        if card(api, return_id)["decision"]["coupon_rule"] is None:
            pytest.skip("the ring case of the tiny store proposes no cluster block")
        approved = ok(decide(api, return_id, "approve"), "decision_result")
        application = approved["coupon_application"]["application_id"]
        reversed_ = client.post(
            f"/api/coupon-rules/applications/{application}/reverse",
            json={"author": "luca", "reason": "The linked accounts share a pickup point only"},
        )
        assert reversed_.status_code == 200, reversed_.text
        lifted = ok(
            decide(
                api, return_id, "lift", approver="luca", override_reason=REASON,
                override_category="customer_context",
            ),
            "decision_result",
        )  # fmt: skip
        reversal = lifted["coupon_application"]
        assert reversal["reversed_by"] == "luca"
        assert datetime.fromisoformat(reversal["reversed_at"]) < datetime.fromisoformat(
            lifted["decision"]["approved_at"]
        )


# ---- 2. A guarantee case states no refund deadline (rule 4, NFR-17) -----------------------------


def _guarantee(api: Api) -> dict[str, Any]:
    for item in _queued(api.client, path=["guarantee"]):
        if item["state"] == "proposed":
            return item
    pytest.skip("no guarantee case in the tiny store")


def test_a_guarantee_case_has_no_refund_deadline_and_is_never_overdue(api: Api) -> None:
    item = _guarantee(api)
    return_id = item["return_id"]
    assert item["refund_due_by"] is None and not item["refund_overdue"]
    shown = card(api, return_id)["decision"]
    assert shown["path"] == "guarantee" and shown["refund_due_by"] is None
    late = date.fromisoformat(shown["inputs"]["notice_on"]) + timedelta(days=60)
    api.clock.set(_at(late))
    again = next(i for i in _queued(api.client) if i["return_id"] == return_id)
    assert again["refund_due_by"] is None and not again["refund_overdue"]
    result = ok(decide(api, return_id, "approve"), "decision_result")  # no 409 (rule 4)
    message = result["outbox_message"]
    assert message is not None and message["fields"]["deadline"] is None
    assert result["decision"]["refund_due_by"] is None


def test_the_refund_deadline_is_stated_exactly_on_the_withdrawal_path() -> None:
    giulia = _load("case_card.giulia.json")
    s.CaseCard.model_validate(giulia)
    decision = giulia["decision"]
    assert decision["path"] == "withdrawal" and decision["refund_due_by"] is not None
    with pytest.raises(ValidationError, match="refund"):
        s.DecisionView.model_validate({**decision, "refund_due_by": None})
    guarantee = next(
        c
        for c in (_load(p.name) for p in sorted(EXAMPLES.glob("case_card.*.json")))
        if c["decision"]["path"] == "guarantee"
    )
    assert guarantee["decision"]["refund_due_by"] is None
    with pytest.raises(ValidationError, match="refund"):
        s.DecisionView.model_validate(
            {**guarantee["decision"], "refund_due_by": decision["refund_due_by"]}
        )
    queue = _load("queue_response.json")
    items = {i["path"]: i for i in queue["items"]}
    if "guarantee" in items:
        with pytest.raises(ValidationError, match="refund"):
            s.QueueItem.model_validate({**items["guarantee"], "refund_due_by": "2026-10-12"})
    with pytest.raises(ValidationError, match="refund"):
        s.QueueItem.model_validate({**items["withdrawal"], "refund_due_by": None})


# ---- 3. After the deadline: reject or the standard terms, without a message ---------------------


def _past_the_deadline(api: Api, return_id: str) -> dict[str, Any]:
    shown: dict[str, Any] = card(api, return_id)["decision"]
    due = date.fromisoformat(shown["refund_due_by"])
    api.clock.set(_at(due + timedelta(days=1)))
    return shown


def test_after_the_refund_deadline_a_restriction_can_still_be_rejected(api: Api) -> None:
    return_id = ring_return(api)
    shown = _past_the_deadline(api, return_id)
    if not shown["requires_approval"]:
        pytest.skip("no restriction proposal in the tiny store")
    body = error(decide(api, return_id, "approve"), 409, "conflict")
    assert "overdue" in body["message"]
    restrictive = [
        {"id": "instant_refund", "value": False, "scope": "future_orders"},
        {"id": "transparency_notice", "value": True, "scope": "communication"},
    ]
    refused = decide(
        api, return_id, "override", levers=restrictive, override_reason=REASON,
        override_category="evidence_disagreement",
    )  # fmt: skip
    assert "overdue" in error(refused, 409, "conflict")["message"]
    rejected = ok(
        decide(
            api, return_id, "reject", override_reason=REASON,
            override_category="evidence_disagreement",
        ),
        "decision_result",
    )  # fmt: skip
    assert rejected["decision"]["state"] == "rejected" and rejected["outbox_message"] is None
    # 1.7.0: no message has stated the refund, so the queue still flags it as overdue
    item = next(i for i in _queued(api.client) if i["return_id"] == return_id)
    assert item["state"] == "rejected" and item["refund_overdue"]
    overdue = _queued(api.client, sort="refund_due_by")
    assert any(i["return_id"] == return_id and i["refund_overdue"] for i in overdue)


def test_after_the_refund_deadline_the_standard_terms_send_no_message(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    _past_the_deadline(api, return_id)
    result = ok(
        decide(
            api, return_id, "override", levers=[], override_reason=REASON,
            override_category="customer_context",
        ),
        "decision_result",
    )  # fmt: skip
    assert result["decision"]["state"] == "overridden" and result["decision"]["levers"] == []
    assert result["outbox_message"] is None
    item = next(i for i in _queued(api.client) if i["return_id"] == return_id)
    assert item["state"] == "overridden" and item["refund_overdue"]  # still unstated (1.7.0)


def test_a_case_decided_in_time_is_never_flagged_overdue(api: Api) -> None:
    """The refund of a case decided before its deadline was stated by the act's message: the
    case is not flagged once the deadline passes (1.5.0, H1; kept by 1.7.0)."""
    return_id = golden_return(api.serving, GIULIA)
    result = ok(
        decide(
            api, return_id, "override", levers=[], override_reason=REASON,
            override_category="customer_context",
        ),
        "decision_result",
    )  # fmt: skip
    assert result["outbox_message"] is not None
    _past_the_deadline(api, return_id)
    item = next(i for i in _queued(api.client) if i["return_id"] == return_id)
    assert item["state"] == "overridden" and not item["refund_overdue"]


def _queue_with(item: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    queue = _load("queue_response.json")
    return {**queue, "items": [{**item, **changes}], "total": 1}


def test_the_queue_flags_an_overdue_refund_no_message_stated() -> None:
    """refund_overdue: past refund_due_by and no message has stated the refund; a proposal, or a
    rejection or the standard terms after the deadline (1.7.0). An approval, or levers other
    than the standard ones, always sent a message."""
    queue = _load("queue_response.json")
    item = next(
        i
        for i in queue["items"]
        if i["refund_due_by"] is not None and not i["lever_ids"] and not i["requires_approval"]
    )  # a withdrawal on the standard terms
    past = (date.fromisoformat(queue["as_of"][:10]) - timedelta(days=1)).isoformat()
    proposed = {"state": "proposed", "refund_due_by": past}
    s.QueueResponse.model_validate(_queue_with(item, **proposed, refund_overdue=True))
    with pytest.raises(ValidationError, match="refund_overdue"):
        s.QueueResponse.model_validate(_queue_with(item, **proposed, refund_overdue=False))
    for state in ("rejected", "overridden"):  # in time or late: the queue cannot tell
        for flag in (True, False):
            late = {"state": state, "refund_due_by": past, "refund_overdue": flag}
            s.QueueResponse.model_validate(_queue_with(item, **late))
    with pytest.raises(ValidationError, match="refund_overdue"):
        s.QueueResponse.model_validate(
            _queue_with(item, state="approved", refund_due_by=past, refund_overdue=True)
        )
    benefit = next(i for i in queue["items"] if i["lever_ids"] and not i["requires_approval"])
    with pytest.raises(ValidationError, match="refund_overdue"):
        s.QueueResponse.model_validate(
            _queue_with(benefit, state="overridden", refund_due_by=past, refund_overdue=True)
        )
    future = (date.fromisoformat(queue["as_of"][:10]) + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError, match="refund_overdue"):
        s.QueueResponse.model_validate(
            _queue_with(item, state="rejected", refund_due_by=future, refund_overdue=True)
        )


def _late_without_message(result: Mapping[str, Any]) -> dict[str, Any]:
    """`result` as if the person had acted the day after the refund deadline, with no message."""
    decision = result["decision"]
    due = date.fromisoformat(decision["refund_due_by"])
    at = _at(due + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    return {
        **result,
        "decision": {**decision, "approved_at": at, "as_of": at},
        "audit_entry": {**result["audit_entry"], "at": at},
        "outbox_message": None,
    }


def test_a_result_without_a_message_is_only_a_late_act_on_the_standard_terms() -> None:
    rejected = _load("decision_result.reject.json")
    s.DecisionResult.model_validate(rejected)
    s.DecisionResult.model_validate(_late_without_message(rejected))
    with pytest.raises(ValidationError, match="message"):
        s.DecisionResult.model_validate({**rejected, "outbox_message": None})  # in time
    approved = _load("decision_result.marco_approved.json")
    s.DecisionResult.model_validate(approved)
    with pytest.raises(ValidationError, match="message"):
        s.DecisionResult.model_validate(_late_without_message(approved))  # terms to announce


# ---- 4. Draft and agent facts are dated with the customer snapshot (rule 11) --------------------


def test_draft_and_agent_facts_are_dated_with_the_customer_snapshot(api: Api) -> None:
    """Value, score factors and cluster are facts of the customer snapshot the engine read; the
    risk score is computed at the request (request features and that snapshot), so its fact is
    dated with the day it was scored (review of section 16, 1.7.0). The agent's case summary
    states no risk figure: it keeps the snapshot day of its factors."""
    return_id = ring_return(api)
    shown = card(api, return_id)
    decision = shown["decision"]
    snapshot = decision["inputs"]["snapshot_on"]
    assert snapshot < decision["decided_on"]  # the engine read a snapshot before the request
    assert shown["value"]["value_eur"]["provenance"]["period_end"] == snapshot  # as the card
    record = api.serving.store_return(return_id)
    assert record is not None
    scored = s.utc_day(api.serving.request_score(record).decided_at).isoformat()
    assert decision["inputs"]["scored_on"] == scored
    assert snapshot < scored <= decision["decided_on"]  # the request came after the snapshot
    draft = shown["draft"]
    assert draft is not None
    periods = {f["metric"]: f["period"] for f in draft["case_facts"]}
    assert periods.pop("risk_score") == f"as of {scored}"
    assert periods.pop("value_eur_12m") == f"12 months to {snapshot}"
    periods.pop("days_since_delivery")
    assert periods and set(periods.values()) == {f"as of {snapshot}"}

    async def read(conn: AsyncConnection) -> date:
        found = await case_data(conn, api.serving, return_id, api.clock.now())
        assert found is not None
        return found.case.as_of

    assert api.run(read).isoformat() == snapshot


def test_a_draft_dated_with_the_decision_day_is_refused() -> None:
    marco = _load("case_card.marco.json")
    s.CaseCard.model_validate(marco)
    decision = marco["decision"]
    assert decision["inputs"]["snapshot_on"] < decision["decided_on"]
    facts = [
        {**f, "period": f"as of {decision['decided_on']}"} if f["period"].startswith("as of") else f
        for f in marco["draft"]["case_facts"]
    ]
    with pytest.raises(ValidationError):
        s.CaseCard.model_validate({**marco, "draft": {**marco["draft"], "case_facts": facts}})
    late = {**decision["inputs"], "snapshot_on": "2026-12-01"}
    with pytest.raises(ValidationError, match="snapshot"):
        s.DecisionView.model_validate({**decision, "inputs": late})


def test_a_risk_score_dated_with_the_snapshot_is_refused() -> None:
    """The risk score is dated with the day it was scored, at the request: after the customer
    snapshot, by the decision day, and only when there is a risk (1.7.0)."""
    marco = _load("case_card.marco.json")
    decision, inputs = marco["decision"], marco["decision"]["inputs"]
    assert inputs["snapshot_on"] < inputs["scored_on"] <= decision["decided_on"]

    def dated(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        period = f"as of {inputs['snapshot_on']}"
        return [{**f, "period": period} if f["metric"] == "risk_score" else f for f in facts]

    draft = marco["draft"]
    statements = [{**x, "facts": dated(x["facts"])} for x in draft["explanation"]]
    wrong_draft = {**draft, "case_facts": dated(draft["case_facts"]), "explanation": statements}
    with pytest.raises(ValidationError, match="risk_score"):
        s.CaseCard.model_validate({**marco, "draft": wrong_draft})
    for scored_on in ("2026-08-31", "2026-12-01", None):
        wrong = {**inputs, "scored_on": scored_on}
        with pytest.raises(ValidationError, match="scored"):
            s.DecisionView.model_validate({**decision, "inputs": wrong})
    elena = _load("case_card.elena.json")["decision"]  # the guarantee path: never scored
    assert elena["inputs"]["risk"] is None and elena["inputs"]["scored_on"] is None
    with pytest.raises(ValidationError, match="scored"):
        s.DecisionView.model_validate(
            {**elena, "inputs": {**elena["inputs"], "scored_on": elena["decided_on"]}}
        )


# ---- 5. The refund of a message has its own provenance (rule 11) --------------------------------

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


def _claimed(api: Api, *, awaiting: bool) -> tuple[str, dict[str, Any]]:
    for item in _queued(api.client, path=["withdrawal"], evidence_quality="sufficient"):
        if item["state"] == "proposed" and item["requires_approval"] == awaiting:
            return_id = str(item["return_id"])
            body = {"return_id": return_id, "inspector": "ahmed", "photo": photo_for("damaged")}
            created = api.client.post("/api/inspections", json=body)
            assert created.status_code == 201, created.text
            inspection_id = created.json()["inspection_id"]
            confirmed = ok(
                api.client.post(f"/api/inspections/{inspection_id}/confirm", json=CONFIRM),
                "inspection_confirm_response",
            )
            return return_id, confirmed
    pytest.skip("no such case in the tiny store")


def _check_refund(message: Mapping[str, Any], claim: Mapping[str, Any]) -> None:
    """The refund stated is the price paid for the line less the claim, with the provenance of
    the line price (its origin, period and nature) and the claim named: never the claim's."""
    refund = message["refund"]["provenance"]
    paid = claim["line_price"]["provenance"]
    assert refund != claim["amount"]["provenance"]
    origin = f"{paid['origin']}, less the diminished-value claim of {claim['inspection_id']}"
    assert refund == {**paid, "origin": origin}


def test_after_the_refund_deadline_a_claim_case_can_still_be_rejected(api: Api) -> None:
    """Review of section 16: a claim confirmed while a restriction awaits approval sends no
    message; once the refund deadline passed, every act answered 409 and the case stayed
    proposed for good. A rejection goes on without a message (1.7.0), the claim stays on the
    decision, and the queue flags the refund: no message has stated it, nor the deduction."""
    return_id, held = _claimed(api, awaiting=True)
    assert held["outbox_message"] is None and held["claim"] is not None
    _past_the_deadline(api, return_id)
    refused = error(decide(api, return_id, "approve"), 409, "conflict")
    assert "overdue" in refused["message"]  # its message would state the past deadline
    rejected = ok(
        decide(
            api, return_id, "reject", override_reason=REASON,
            override_category="evidence_disagreement",
        ),
        "decision_result",
    )  # fmt: skip
    decision = rejected["decision"]
    assert decision["state"] == "rejected" and rejected["outbox_message"] is None
    assert decision["diminished_value_claim"]["inspection_id"] == held["claim"]["inspection_id"]
    item = next(i for i in _queued(api.client) if i["return_id"] == return_id)
    assert item["state"] == "rejected" and item["refund_overdue"]


def test_the_refund_stated_by_a_message_has_its_own_provenance(api: Api) -> None:
    _, confirmed = _claimed(api, awaiting=False)
    assert confirmed["outbox_message"] is not None
    _check_refund(confirmed["outbox_message"], confirmed["claim"])
    assert "price paid" in confirmed["outbox_message"]["refund"]["provenance"]["origin"]
    return_id, held = _claimed(api, awaiting=True)
    assert held["outbox_message"] is None  # the restriction awaits Sara
    approved = ok(decide(api, return_id, "approve"), "decision_result")
    _check_refund(approved["outbox_message"], approved["decision"]["diminished_value_claim"])


def test_a_refund_with_the_claim_provenance_is_refused() -> None:
    confirmed = _load("inspection_confirm_response.json")
    s.InspectionConfirmResponse.model_validate(confirmed)
    message = confirmed["outbox_message"]
    claim = confirmed["claim"]
    assert message is not None and message["refund"] is not None
    _check_refund(message, claim)
    wrong = {
        **message,
        "refund": {**message["refund"], "provenance": claim["amount"]["provenance"]},
    }
    with pytest.raises(ValidationError, match="refund"):
        s.InspectionConfirmResponse.model_validate({**confirmed, "outbox_message": wrong})


# ---- 6. The coupon block of a linked account names no cause (rule 11) ---------------------------


@pytest.mark.parametrize("lang", LANGS)
def test_the_cluster_coupon_block_is_a_neutral_reviewed_text(lang: Lang) -> None:
    for day, it_date in ((date(2026, 12, 30), "del 30"), (date(2026, 12, 11), "dell'11")):
        text = cluster_coupon_block_text(day, lang)
        expected = it_date if lang == "it" else f"{day.day} December 2026"
        assert expected in text and "{" not in text
        stripped = text.replace(text[text.index(expected) :].rstrip("."), "").strip()
        # every check of a customer text but terms wording: like COUPON_BLOCK_TEXT, it names
        # discount codes (ADR-14 decision 12)
        problems = [p for p in phrase_problems(stripped, lang) if p[0] != "TERMS_WORDING"]
        assert problems == [], stripped
    text = cluster_coupon_block_text(date(2026, 12, 30), lang)
    for cause in ("previous returns", "resi precedenti", "linked", "collegat", "cluster"):
        assert cause not in text
    assert text != COUPON_BLOCK_TEXT[lang]


def test_a_linked_account_sees_a_coupon_block_that_names_no_cause(api: Api) -> None:
    return_id = ring_return(api)
    shown = card(api, return_id)
    if shown["decision"]["coupon_rule"] is None:
        pytest.skip("the ring case of the tiny store proposes no cluster block")
    approved = ok(decide(api, return_id, "approve"), "decision_result")
    application = approved["coupon_application"]
    review = date.fromisoformat(application["review_at"])
    record = api.serving.store_return(return_id)
    assert record is not None

    def block(customer_id: str) -> dict[str, Any]:
        cart = {
            "customer_id": customer_id,
            "lines": [{"variant_id": record.variant_id, "size": "M", "quantity": 1}],
            "coupon_code": None,
        }
        evaluated = ok(
            api.client.post("/api/checkout/evaluate", json=cart), "checkout_evaluate_response"
        )
        found: dict[str, Any] = evaluated["coupon_block"]
        assert found is not None
        return found

    linked = next(c for c in application["affected_customer_ids"] if c != shown["customer_id"])
    neutral = block(linked)["text"]
    assert neutral == {lang: cluster_coupon_block_text(review, lang) for lang in LANGS}
    assert block(shown["customer_id"])["text"] == COUPON_BLOCK_TEXT  # the case's own customer


def test_the_coupon_block_is_one_of_the_two_reviewed_texts() -> None:
    marco = _load("checkout_evaluate_response.marco.json")
    s.CheckoutEvaluateResponse.model_validate(marco)
    block = marco["coupon_block"]
    review = date.fromisoformat(block["review_at"])
    neutral = {lang: cluster_coupon_block_text(review, lang) for lang in LANGS}
    for text in (COUPON_BLOCK_TEXT, neutral):
        s.CouponBlock.model_validate({**block, "text": text})
    other_day = {
        lang: cluster_coupon_block_text(review + timedelta(days=1), lang) for lang in LANGS
    }
    with pytest.raises(ValidationError, match="coupon block"):
        s.CouponBlock.model_validate({**block, "text": other_day})
