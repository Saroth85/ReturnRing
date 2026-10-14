"""GET /api/audit (US-604: filters, pages, CSV export with AUDIT_CSV_COLUMNS) and GET /api/outbox
(spec 4: the simulated outbox, visible). Both read the append-only log and the outbox as
written; nothing here writes."""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.roles import ACTOR_HEADER
from apps.api.services import decisions as store
from apps.api.services.audit import entries, record
from apps.api.services.generation import load_draft
from apps.api.services.outbox import compose, queue
from pipeline.generator.golden import ELENA, GIULIA
from tests.api_harness import CLOCK_AT, Api, golden_return, validate_body


def _page(api: Api, **params: Any) -> dict[str, Any]:
    response = api.client.get("/api/audit", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body("audit_page", body)
    return body


def _all(api: Api) -> list[s.AuditEntry]:
    async def run(conn: AsyncConnection) -> list[s.AuditEntry]:
        return await entries(conn)

    return api.run(run)


def test_the_log_after_the_reset(api: Api) -> None:
    """The reset logs the coupon rule, then the proposal and the draft of every open return;
    the page shows the newest first."""
    log = _all(api)
    returns = len(api.serving.open_return_ids())
    body = _page(api, page_size=500)
    assert body["total"] == len(log) == 1 + 2 * returns
    ids = [i["entry_id"] for i in body["items"]]
    assert ids == sorted(ids, reverse=True)
    stamps = [i["at"] for i in body["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert body["items"][-1]["action"] == "coupon_rule_approved"


def test_pages(api: Api) -> None:
    whole = _page(api, page_size=500)["items"]
    first, second = _page(api, page=1, page_size=10), _page(api, page=2, page_size=10)
    assert first["total"] == second["total"] == len(whole)
    assert first["items"] + second["items"] == whole[:20]
    assert (first["page"], first["page_size"]) == (1, 10)
    beyond = _page(api, page=99, page_size=10)
    assert beyond["items"] == [] and beyond["total"] == len(whole)


def test_filters(api: Api) -> None:
    giulia = golden_return(api.serving, GIULIA)
    by_return = _page(api, return_id=giulia)["items"]
    assert {i["return_id"] for i in by_return} == {giulia}
    assert {i["action"] for i in by_return} == {"decision_proposed", "draft_generated"}
    decision = next(i["decision_id"] for i in by_return if i["action"] == "decision_proposed")
    by_decision = _page(api, decision_id=decision)["items"]
    assert {i["decision_id"] for i in by_decision} == {decision} and len(by_decision) == 2
    rules = _page(api, actor="luca")["items"]
    assert [i["action"] for i in rules] == ["coupon_rule_approved"]
    both = _page(api, action=["coupon_rule_approved", "draft_generated"])
    assert {i["action"] for i in both["items"]} == {"coupon_rule_approved", "draft_generated"}
    drafts = _page(api, entity_type="llm_generation", page_size=500)
    assert drafts["total"] == len(api.serving.open_return_ids())
    at = api.serving.as_of
    assert _page(api, since=at.isoformat())["total"] == _page(api)["total"]
    assert _page(api, until=at.isoformat())["total"] == 0  # until is exclusive
    later = (at + timedelta(seconds=1)).isoformat()
    assert _page(api, since=later)["total"] == 0


def test_invalid_queries_are_refused(api: Api) -> None:
    for params in (
        {"page": 0}, {"page_size": 501}, {"action": "deleted"}, {"since": "yesterday"},
        {"entity_type": "customer"}, {"format": "xml"},
    ):  # fmt: skip
        response = api.client.get("/api/audit", params=params)
        assert response.status_code == 422, params
        assert response.json()["code"] == "invalid_request"


def _csv(api: Api, **params: Any) -> list[dict[str, str]]:
    response = api.client.get("/api/audit", params={**params, "format": "csv"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    reader = csv.DictReader(io.StringIO(response.text))
    assert tuple(reader.fieldnames or ()) == s.AUDIT_CSV_COLUMNS
    return list(reader)


def test_the_csv_export_is_the_filtered_log(api: Api) -> None:
    """US-604: every entry the filters select (not one page), in the order of the pages, with
    JSON cells for before, after and model_versions."""
    giulia = golden_return(api.serving, GIULIA)
    rows = _csv(api, return_id=giulia, page_size=1)
    items = _page(api, return_id=giulia, page_size=500)["items"]
    assert len(rows) == len(items) == 2
    for row, item in zip(rows, items, strict=True):
        assert int(row["entry_id"]) == item["entry_id"] and row["at"] == item["at"]
        assert row["action"] == item["action"] and row["actor"] == item["actor"]
        for key in ("before", "after", "model_versions"):
            assert (json.loads(row[key]) if row[key] else None) == item[key]
        assert row["reason"] == (item["reason"] or "")
    assert len(_csv(api)) == _page(api)["total"]


def test_a_formula_in_a_reason_is_neutralised_in_the_csv(api: Api) -> None:
    """A free-text reason that a spreadsheet would run as a formula starts with a quote in the
    CSV; the JSON keeps the reason as written."""
    elena = golden_return(api.serving, ELENA)

    async def run(conn: AsyncConnection) -> None:
        await record(
            conn, at=CLOCK_AT, actor="sara", action="evidence_added",
            entity_type="evidence_addition", entity_id="EV-000001", return_id=elena,
            reason="=HYPERLINK(\"http://x\")", after={"kind": "other"},
        )  # fmt: skip

    api.run(run)
    rows = _csv(api, action="evidence_added")
    assert rows[0]["reason"] == '\'=HYPERLINK("http://x")'
    item = _page(api, action="evidence_added")["items"][0]
    assert item["reason"] == '=HYPERLINK("http://x")'


def test_the_log_is_open_to_every_demo_user(api: Api) -> None:
    for user in ("sara", "luca", "ahmed"):
        assert api.client.get("/api/audit", headers={ACTOR_HEADER: user}).status_code == 200
    denied = api.client.get("/api/audit", params={"format": "csv"}, headers={ACTOR_HEADER: "x1"})
    assert denied.status_code == 403


# ---- Outbox --------------------------------------------------------------------------------------


def _outbox(api: Api, **params: Any) -> dict[str, Any]:
    response = api.client.get("/api/outbox", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body("outbox", body)
    return body


def test_the_outbox_is_empty_until_a_person_acts(api: Api) -> None:
    assert _outbox(api) == {"items": [], "total": 0}


def _send(api: Api, return_id: str, lang: str, minutes: int) -> s.OutboxMessage:
    async def run(conn: AsyncConnection) -> s.OutboxMessage:
        found = await store.latest_view(conn, return_id, CLOCK_AT, line_price=lambda _: Decimal(1))
        assert found is not None
        view, _ = found
        draft = await load_draft(conn, view)
        message = await compose(
            conn, view, lang=lang, created_by="sara",  # type: ignore[arg-type]
            created_at=CLOCK_AT + timedelta(minutes=minutes),
            preferred=draft.message.sentence_ids if draft else None,
        )  # fmt: skip
        return await queue(conn, message, view, request_id="req-outbox")

    return api.run(run)


def test_the_outbox_lists_the_messages_newest_first(api: Api) -> None:
    giulia, elena = golden_return(api.serving, GIULIA), golden_return(api.serving, ELENA)
    sent = [_send(api, giulia, "it", 1), _send(api, elena, "en", 2), _send(api, giulia, "en", 3)]
    body = _outbox(api)
    assert body["total"] == 3
    assert [m["message_id"] for m in body["items"]] == [m.message_id for m in reversed(sent)]
    assert body["items"][0] == sent[-1].model_dump(mode="json")
    only = _outbox(api, return_id=giulia)
    assert only["total"] == 2 and {m["return_id"] for m in only["items"]} == {giulia}
    paged = _outbox(api, page=2, page_size=2)
    assert paged["total"] == 3 and [m["message_id"] for m in paged["items"]] == [sent[0].message_id]
    clause = {m["return_id"]: m["legal_clause_kind"] for m in body["items"]}
    assert clause[elena] == "guarantee"  # the fixed clause of the guarantee path (rule 10)


def test_invalid_outbox_queries_are_refused(api: Api) -> None:
    for params in ({"page_size": 101}, {"page": 0}, {"return_id": "bad id"}):
        response = api.client.get("/api/outbox", params=params)
        assert response.status_code == 422, params
