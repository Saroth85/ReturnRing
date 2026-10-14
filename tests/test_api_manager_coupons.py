"""The general coupon rule and its cluster applications (US-402, US-607, LEG-09).

The rule is versioned and approved by the manager; each application to a cluster is a proposal a
person confirms with the decision, stays reversible, lapses at its review date, and a person can
reverse it on request with a reason (rectification)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api.roles import ACTOR_HEADER
from apps.api.services.audit import entries
from apps.api.services.seeds import COUPON_RULE_V1, current_coupon_rule
from contracts.sql import coupon_rule_applications, coupon_rules, decisions
from pipeline.generator.golden import MARCO_ACCOUNTS
from tests.api_harness import CLOCK_AT, Api, golden_return, validate_body

EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples" / "api"
URL = "/api/coupon-rules"
REASON = "The linked accounts share a pickup point only: application reversed"


def _example(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text("utf-8"))


def _rules(api: Api) -> dict[str, Any]:
    response = api.client.get(URL)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body("coupon_rules", body)
    return body


def _marco(api: Api) -> str:
    req = api.serving.frame("scores/request_scores")
    customer = next(c for c in MARCO_ACCOUNTS if (req["customer_id"] == c).any())
    return golden_return(api.serving, customer)


def _application(api: Api, return_id: str) -> str:
    card = api.client.get(f"/api/returns/{return_id}").json()
    application: str = card["decision"]["coupon_rule"]["application_id"]
    return application


def _approve(api: Api, return_id: str) -> None:
    """Sara approves the Q4 proposal and confirms its coupon application, as POST .../decision
    writes it (the operator routes)."""

    async def run(conn: AsyncConnection) -> None:
        await conn.execute(
            update(decisions)
            .where(decisions.c.return_id == return_id)
            .values(state="approved", approver="sara", approved_at=CLOCK_AT)
        )
        await conn.execute(
            update(coupon_rule_applications)
            .where(
                coupon_rule_applications.c.decision_id.in_(
                    select(decisions.c.decision_id).where(decisions.c.return_id == return_id)
                )
            )
            .values(state="confirmed", confirmed_by="sara", confirmed_at=CLOCK_AT)
        )

    api.run(run)


def test_the_rule_and_its_applications_after_the_reset(api: Api) -> None:
    body = _rules(api)
    current = body["current"]
    assert current["rule_version"] == COUPON_RULE_V1 and current["active"]
    assert current["approved_by"] == "luca" and current["supersedes"] is None
    assert body["history"] == [current]
    assert body["as_of"] == "2026-10-02T09:00:00Z"
    marco = _marco(api)
    application = _application(api, marco)
    apps = {a["application_id"]: a for a in body["applications"]}
    mine = apps[application]
    assert mine["state"] == "proposed" and not mine["in_force"]  # waits for a person (LEG-09)
    assert mine["confirmed_by"] is None and mine["reversible"] is True
    assert sorted(mine["affected_customer_ids"]) == sorted(MARCO_ACCOUNTS)
    assert all(a["as_of"] == body["as_of"] for a in body["applications"])
    assert all(a["review_at"] > "2026-10-02" for a in body["applications"])


def test_a_new_rule_version_by_the_manager(api: Api) -> None:
    """LEG-09: the general rule is versioned and approved by the manager; the history keeps
    every version, one active; the next decisions apply the new version."""
    request = _example("coupon_rule_create.json")
    response = api.client.post(URL, json=request)
    assert response.status_code == 201, response.text
    body = response.json()
    validate_body("coupon_rules", body)
    current = body["current"]
    assert current["rule_version"] == "coupon-rule-v2"
    assert current["supersedes"] == COUPON_RULE_V1 and current["text"] == request["text"]
    assert current["approved_by"] == "luca" and current["approved_at"] == "2026-10-02T09:00:00Z"
    assert [r["rule_version"] for r in body["history"]] == ["coupon-rule-v2", COUPON_RULE_V1]
    assert [r["active"] for r in body["history"]] == [True, False]
    assert _rules(api) == body
    expected = _example("coupon_rules.after_create.json")
    assert [r["supersedes"] for r in body["history"]] == [
        r["supersedes"] for r in expected["history"]
    ]

    async def run(conn: AsyncConnection) -> tuple[str, list[Any]]:
        log = [e for e in await entries(conn) if e.entity_type == "coupon_rule"]
        return await current_coupon_rule(conn), log

    version, log = api.run(run)
    assert version == "coupon-rule-v2"
    seeded = {"rule_version": COUPON_RULE_V1, "note": "loaded by the demo reset (US-903)"}
    assert [(e.actor, e.before, e.after) for e in log] == [
        ("luca", None, seeded),
        ("luca", {"rule_version": COUPON_RULE_V1}, {"rule_version": "coupon-rule-v2"}),
    ]


def test_only_the_manager_approves_a_rule(api: Api) -> None:
    request = _example("coupon_rule_create.json")
    for person in ("sara", "ahmed"):
        response = api.client.post(URL, json={**request, "approved_by": person})
        assert response.status_code == 403, person
    assert api.client.post(URL, json={**request, "approved_by": "system"}).status_code == 422
    assert api.client.post(URL, json={**request, "text": ""}).status_code == 422
    assert [r["rule_version"] for r in _rules(api)["history"]] == [COUPON_RULE_V1]

    async def tamper(conn: AsyncConnection) -> None:
        await conn.execute(update(coupon_rules).values(text="changed"))

    try:
        api.run(tamper)
    except Exception as exc:  # noqa: BLE001 - the trigger's error, whatever the driver
        assert "append-only" in str(exc)
    else:
        raise AssertionError("coupon_rules accepted an UPDATE")


def _reverse(api: Api, application: str, **changes: Any) -> Any:
    body = {"author": "luca", "reason": REASON, **changes}
    return api.client.post(f"{URL}/applications/{application}/reverse", json=body)


def test_a_proposed_application_is_not_reversed(api: Api) -> None:
    """A proposal is confirmed or not by the decision; there is no block to reverse yet."""
    application = _application(api, _marco(api))
    response = _reverse(api, application)
    assert response.status_code == 409 and response.json()["code"] == "conflict"


def test_a_person_reverses_a_confirmed_application(api: Api) -> None:
    """US-402, US-607, LEG-09: the linked accounts get their coupons back on request; the
    decision stays as approved, its block is reversed and no longer in force."""
    marco = _marco(api)
    application = _application(api, marco)
    _approve(api, marco)
    confirmed = {a["application_id"]: a for a in _rules(api)["applications"]}[application]
    assert confirmed["state"] == "confirmed" and confirmed["in_force"]
    api.clock.advance(timedelta(hours=1))
    response = _reverse(api, application)
    assert response.status_code == 200, response.text
    body = response.json()
    validate_body("coupon_rule_application", body)
    assert body["state"] == "reversed" and not body["in_force"] and body["reversible"]
    assert (body["reversed_by"], body["reversal_reason"]) == ("luca", REASON)
    assert body["reversed_at"] == body["as_of"] == "2026-10-02T10:00:00Z"
    assert body["confirmed_by"] == "sara"
    listed = {a["application_id"]: a for a in _rules(api)["applications"]}[application]
    assert listed == {**body, "as_of": listed["as_of"]}
    card = api.client.get(f"/api/returns/{marco}").json()
    validate_body("case_card", card)
    rule = card["decision"]["coupon_rule"]
    assert rule["state"] == "reversed" and not rule["in_force"]
    assert card["decision"]["state"] == "approved" and card["decision"]["in_force"]
    log = [e for e in api.run(lambda conn: entries(conn)) if e.entity_id == application]
    assert len(log) == 1
    entry = log[0]
    assert entry.action == "coupon_rule_application_reversed" and entry.reason == REASON
    assert (entry.actor, entry.actor_role) == ("luca", "manager")
    assert entry.return_id == marco and entry.decision_id == card["decision"]["decision_id"]
    assert entry.before == {"state": "confirmed"} and entry.after == {"state": "reversed"}
    assert entry.rules_version == card["decision"]["rules_version"]
    assert entry.model_versions == card["decision"]["model_versions"]
    again = _reverse(api, application)
    assert again.status_code == 409


def test_a_reversal_needs_a_person_and_a_reason(api: Api) -> None:
    marco = _marco(api)
    application = _application(api, marco)
    _approve(api, marco)
    assert _reverse(api, "CRA-NOPE").status_code == 404
    assert _reverse(api, application, author="ahmed").status_code == 403
    assert _reverse(api, application, author="system").status_code == 422
    assert _reverse(api, application, reason="no").status_code == 422
    assert _reverse(api, application, reason="   ").status_code == 422
    assert _reverse(api, application, author="sara").status_code == 200  # an operator may


def test_an_application_lapses_at_its_review_date(api: Api) -> None:
    """US-607: no permanent label; served after its review date the block is not in force."""
    marco = _marco(api)
    application = _application(api, marco)
    _approve(api, marco)
    review = {a["application_id"]: a for a in _rules(api)["applications"]}[application]["review_at"]
    api.clock.advance(timedelta(days=120))
    lapsed = {a["application_id"]: a for a in _rules(api)["applications"]}[application]
    assert lapsed["review_at"] == review and lapsed["state"] == "confirmed"
    assert not lapsed["in_force"]
    response = api.client.get(URL, headers={ACTOR_HEADER: "ahmed"})
    assert response.status_code == 200
