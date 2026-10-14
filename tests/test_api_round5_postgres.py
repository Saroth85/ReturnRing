"""Concurrent writers on PostgreSQL 16 (asyncpg, READ COMMITTED): the findings H10, H18, H19 and
H22 of the fifth review of the API (ADR-15 section 15). Requests run at the same time through
one app (a thread each): ids never collide, an inspection is confirmed once, a link is disputed
once, and no request answers 500. Runs when RETURNRING_TEST_POSTGRES_URL is set."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from apps.api.db import make_engine
from apps.api.reset import reset
from apps.api.services.generation import Generator
from apps.api.serving import Serving
from pipeline.generator.golden import GIULIA
from tests.api_harness import golden_return, open_api, settings_for, validate_body
from tests.test_api_other_support import photo_for

POSTGRES_URL = os.environ.get("RETURNRING_TEST_POSTGRES_URL")
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


def _together(calls: list[Callable[[], Response]]) -> list[Response]:
    """The calls at the same time, one thread each."""
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(lambda call: call(), calls))


def _queue(client: TestClient) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        body = client.get("/api/returns", params={"page": page, "page_size": 100}).json()
        items += body["items"]
        if len(items) >= body["total"] or not body["items"]:
            return items
        page += 1


@pytest.mark.skipif(not POSTGRES_URL, reason="RETURNRING_TEST_POSTGRES_URL is not set")
def test_concurrent_writers_never_collide_on_postgresql(
    serving: Serving, serving_dir: Path, tmp_path: Path
) -> None:
    assert POSTGRES_URL
    settings = settings_for(serving_dir, tmp_path / "unused.sqlite", database_url=POSTGRES_URL)

    async def fresh() -> dict[str, Any]:
        engine = make_engine(POSTGRES_URL or "")
        try:
            return await reset(engine, serving, Generator(settings))
        finally:
            await engine.dispose()

    assert not asyncio.run(fresh())["failed"]
    for api in open_api(serving, settings, tmp_path / "unused.sqlite"):
        _concurrent(api.client, serving)


def _concurrent(client: TestClient, serving: Serving) -> None:
    giulia = golden_return(serving, GIULIA)
    record = serving.store_return(giulia)
    assert record is not None
    cart = {
        "customer_id": GIULIA,
        "lines": [{"variant_id": record.variant_id, "size": "M", "quantity": 1}],
        "coupon_code": None,
    }
    # H10, H18: eight checkouts at once, eight snapshots
    shown = _together([lambda: client.post("/api/checkout/evaluate", json=cart)] * 8)
    assert [r.status_code for r in shown] == [200] * 8, [r.text for r in shown]
    ids = {r.json()["terms_snapshot_id"] for r in shown}
    assert len(ids) == 8

    # approvals of different cases at once: one message each, distinct ids
    items = [
        i
        for i in _queue(client)
        if i["state"] == "proposed" and not i["requires_approval"] and i["path"] == "withdrawal"
    ][:4]
    assert len(items) >= 2

    def approve(item: dict[str, Any]) -> Callable[[], Response]:
        body = {
            "decision_id": item["decision_id"],
            "action": "approve",
            "approver": "sara",
            "message": {"lang": "it"},
        }
        return lambda: client.post(f"/api/returns/{item['return_id']}/decision", json=body)

    approved = _together([approve(i) for i in items])
    assert [r.status_code for r in approved] == [200] * len(items), [r.text for r in approved]
    messages = {r.json()["outbox_message"]["message_id"] for r in approved}
    assert len(messages) == len(items)

    # H19: one inspection confirmed twice at once: once
    target = next(
        i
        for i in _queue(client)
        if i["state"] == "approved" and i["evidence_quality"] == "sufficient"
    )
    created = client.post(
        "/api/inspections",
        json={
            "return_id": target["return_id"],
            "inspector": "ahmed",
            "photo": photo_for("damaged"),
        },
    )
    assert created.status_code == 201, created.text
    path = f"/api/inspections/{created.json()['inspection_id']}/confirm"
    confirmed = _together([lambda: client.post(path, json=CONFIRM)] * 2)
    assert sorted(r.status_code for r in confirmed) == [200, 409], [r.text for r in confirmed]
    messages_after = client.get(
        "/api/outbox",
        params={"return_id": target["return_id"]},
        headers={"X-ReturnRing-Actor": "luca"},
    ).json()["items"]
    deductions = [m for m in messages_after if m["fields"]["deduction"] is not None]
    assert len(deductions) == 1  # one deduction message, never two

    # H10, H22: one link disputed twice at once: once, never a 500
    demo = client.get("/api/demo/cases").json()
    validate_body("demo_cases", demo)
    dispute = demo["dispute"]
    assert dispute is not None
    request = {"reason": "Two households, confirmed by phone", "author": "sara"}
    link = f"/api/links/{dispute['link_id']}/dispute"
    disputed = _together([lambda: client.post(link, json=request)] * 2)
    assert sorted(r.status_code for r in disputed) == [200, 409], [r.text for r in disputed]

    # three agent questions at once: three answers with their own ids
    question = {"return_id": giulia, "question": "Which terms applied?", "asked_by": "luca"}
    asked = _together([lambda: client.post("/api/agent/ask", json=question)] * 3)
    assert [r.status_code for r in asked] == [200] * 3, [r.text for r in asked]
