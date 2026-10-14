"""GET /api/products/anomalies and the catalogue tasks (US-208, US-606, spec 8.9, E2E-04).

An anomaly is a product matter: it names no customer and changes no customer's risk, terms or
messages. Elena's defect return opens a catalogue task; her own case stays as it was. After the
change, the variant's return rate for the reason is followed week by week, with the caveat that
a decrease alone does not prove the effect of the change."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.audit import entries
from contracts.sql import catalog_tasks, decisions, outbox
from pipeline.anomalies.catalogue import weekly_series
from pipeline.anomalies.detect import ANOMALY_THRESHOLDS
from pipeline.generator.golden import DEFECTIVE_JACKET, ELENA, GIULIA
from tests.api_harness import Api, golden_return, validate_body

URL = "/api/products/anomalies"


def _anomalies(api: Api, **params: Any) -> dict[str, Any]:
    response = api.client.get(URL, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body("product_anomalies", body)
    return body


def _flagged(api: Api) -> pd.DataFrame:
    frame = api.serving.frame("features/product_anomalies")
    return frame.loc[frame["as_of"] == frame["as_of"].max()]


def _elena_anomaly(api: Api) -> dict[str, Any]:
    card = api.client.get(f"/api/returns/{golden_return(api.serving, ELENA)}").json()
    anomaly: dict[str, Any] = card["product_anomaly"]
    assert anomaly is not None and anomaly["variant_id"] == DEFECTIVE_JACKET
    return anomaly


def _create(api: Api, anomaly_id: str, **changes: Any) -> Any:
    body = {
        "anomaly_id": anomaly_id,
        "kind": "batch_check",
        "note": "Check the sleeve length of the September batch against the size guide",
        "author": "luca",
        **changes,
    }
    return api.client.post("/api/catalog-tasks", json=body)


def test_the_anomalies_of_the_latest_snapshot(api: Api) -> None:
    body = _anomalies(api)
    flagged = _flagged(api)
    assert len(body["items"]) == len(flagged)
    assert body["thresholds"] == {
        "z": ANOMALY_THRESHOLDS.z,
        "min_sales": ANOMALY_THRESHOLDS.min_sales,
    }
    assert body["as_of"] == "2026-10-02T09:00:00Z"
    keys = {(r.variant_id, r.reason, r.week.isoformat()) for r in flagged.itertuples()}
    assert {(i["variant_id"], i["reason"], i["week"]) for i in body["items"]} == keys
    zs = [i["z"]["value"] for i in body["items"]]
    assert zs == sorted(zs, reverse=True)
    assert all(i["status"] == "open" and i["catalog_task_id"] is None for i in body["items"])
    assert body["provenance"]["nature"] == "synthetic"


def test_an_anomaly_names_no_customer(api: Api) -> None:
    """Spec 8.9, E2E-04: a product matter; the returns are listed by id, no customer is."""
    text = json.dumps(_anomalies(api))
    customers = set(api.serving.frame("store/customers")["customer_id"])
    assert not any(c in text for c in customers)
    names = {n for n in api.serving.display_names.values() if n}
    assert not any(n in text for n in names)


def test_the_anomaly_of_elena_is_the_one_of_her_card(api: Api) -> None:
    """The anomaly list and the case card show the same anomaly of the jacket (golden case)."""
    on_card = _elena_anomaly(api)
    listed = {i["anomaly_id"]: i for i in _anomalies(api)["items"]}
    assert listed[on_card["anomaly_id"]] == on_card


def test_filters(api: Api) -> None:
    jacket = _anomalies(api, variant_id=DEFECTIVE_JACKET)["items"]
    assert jacket and {i["variant_id"] for i in jacket} == {DEFECTIVE_JACKET}
    assert _anomalies(api, status=["resolved"])["items"] == []
    assert len(_anomalies(api, status=["open", "task_opened"])["items"]) == len(_flagged(api))
    bad = api.client.get(URL, params={"status": "closed"})
    assert bad.status_code == 422


def test_elena_opens_a_catalogue_task_and_nothing_else(api: Api) -> None:
    """E2E-04: the catalogue task is created and logged; Elena's decision, levers and messages
    do not change, and no customer is restricted."""
    elena = golden_return(api.serving, ELENA)
    anomaly = _elena_anomaly(api)

    async def state(conn: AsyncConnection) -> tuple[list[dict[str, Any]], int]:
        rows = (await conn.execute(select(decisions))).mappings().all()
        messages = len((await conn.execute(select(outbox))).all())
        return [dict(r) for r in rows], messages

    before, _ = api.run(state)
    response = _create(api, anomaly["anomaly_id"], return_id=elena)
    assert response.status_code == 201, response.text
    task = response.json()
    validate_body("catalog_task", task)
    assert task["task_id"] == "CT-000001" and task["status"] == "open"
    assert (task["variant_id"], task["reason"]) == (anomaly["variant_id"], anomaly["reason"])
    assert task["created_by"] == "luca" and task["created_at"] == "2026-10-02T09:00:00Z"
    assert task["return_id"] == elena and task["follow_up"] is None and task["done_at"] is None
    after, messages = api.run(state)
    assert after == before and messages == 0
    card = api.client.get(f"/api/returns/{elena}").json()
    validate_body("case_card", card)
    assert card["product_anomaly"]["status"] == "task_opened"
    assert card["product_anomaly"]["catalog_task_id"] == "CT-000001"
    assert card["decision"]["levers"] == [] and not card["decision"]["requires_approval"]
    listed = {i["anomaly_id"]: i for i in _anomalies(api)["items"]}
    assert listed[anomaly["anomaly_id"]] == card["product_anomaly"]
    log = api.run(lambda conn: entries(conn, return_id=elena))
    opened = [e for e in log if e.action == "catalog_task_opened"]
    assert len(opened) == 1
    assert (opened[0].actor, opened[0].entity_type, opened[0].entity_id) == (
        "luca", "catalog_task", "CT-000001",
    )  # fmt: skip
    assert opened[0].after == {"kind": "batch_check", "anomaly_id": anomaly["anomaly_id"]}
    assert "C-" not in json.dumps(task)  # the task names no customer


def test_a_task_needs_a_flagged_anomaly_and_an_operator_or_manager(api: Api) -> None:
    anomaly_id = _anomalies(api)["items"][0]["anomaly_id"]
    unknown = _create(api, "V-NOPE~defective~2026-09-21")
    assert unknown.status_code == 404 and unknown.json()["code"] == "not_found"
    malformed = _create(api, "V-NOPE")
    assert malformed.status_code == 404
    assert _create(api, anomaly_id, author="ahmed").status_code == 403
    assert _create(api, anomaly_id, author="system").status_code == 422
    assert _create(api, anomaly_id, note="ok").status_code == 422  # a note of 3+ characters
    assert _create(api, anomaly_id, return_id="RET-NOPE").status_code == 404
    giulia = golden_return(api.serving, GIULIA)
    other_variant = _create(api, anomaly_id, return_id=giulia)
    assert other_variant.status_code == 422 and "variant" in other_variant.json()["message"]
    assert _create(api, anomaly_id, author="sara").status_code == 201
    again = _create(api, anomaly_id)
    assert again.status_code == 409 and again.json()["code"] == "conflict"


def _patch(
    api: Api, task_id: str, status: str, note: str | None = None, author: str = "luca"
) -> Any:
    body: dict[str, Any] = {"status": status, "author": author}
    if note is not None:
        body["note"] = note
    return api.client.patch(f"/api/catalog-tasks/{task_id}", json=body)


def test_a_task_moves_to_done_and_its_follow_up_starts(api: Api) -> None:
    anomaly = _elena_anomaly(api)
    task_id = _create(api, anomaly["anomaly_id"]).json()["task_id"]
    moved = _patch(api, task_id, "in_progress", "Batch pulled from stock for checks")
    assert moved.status_code == 200, moved.text
    validate_body("catalog_task", moved.json())
    assert moved.json()["note"] == "Batch pulled from stock for checks"
    api.clock.advance(timedelta(hours=2))
    done = _patch(api, task_id, "done")
    assert done.status_code == 200, done.text
    body = done.json()
    validate_body("catalog_task", body)
    assert body["status"] == "done" and body["done_at"] == "2026-10-02T11:00:00Z"
    assert body["note"] == "Batch pulled from stock for checks"  # kept without a new note
    follow = body["follow_up"]
    assert follow is not None and follow["caveat"] == s.FOLLOW_UP_CAVEAT
    assert follow["weeks_after"] == 0 and follow["after_rate"] is None  # no week after yet
    assert 0 < follow["baseline_rate"]["value"] <= 1
    listed = {i["anomaly_id"]: i for i in _anomalies(api)["items"]}
    assert listed[anomaly["anomaly_id"]]["status"] == "resolved"
    log = [e for e in api.run(lambda conn: entries(conn)) if e.entity_id == task_id]
    assert [(e.action, e.before, e.after) for e in log] == [
        ("catalog_task_opened", None, {"kind": "batch_check", "anomaly_id": anomaly["anomaly_id"]}),
        ("catalog_task_updated", {"status": "open"}, {"status": "in_progress"}),
        ("catalog_task_updated", {"status": "in_progress"}, {"status": "done"}),
    ]  # fmt: skip
    assert _patch(api, task_id, "cancelled").status_code == 409  # done is final
    assert _patch(api, task_id, "done").status_code == 409


def test_task_updates_are_checked(api: Api) -> None:
    anomaly = _anomalies(api)["items"][0]
    task_id = _create(api, anomaly["anomaly_id"]).json()["task_id"]
    assert _patch(api, "CT-999999", "done").status_code == 404
    assert _patch(api, task_id, "done", author="ahmed").status_code == 403
    assert _patch(api, task_id, "open").status_code == 422  # not a target state
    assert _patch(api, task_id, "in_progress").status_code == 200
    assert _patch(api, task_id, "in_progress").status_code == 409  # no change


def test_a_cancelled_task_reopens_the_anomaly(api: Api) -> None:
    anomaly = _anomalies(api)["items"][0]
    first = _create(api, anomaly["anomaly_id"]).json()["task_id"]
    assert _patch(api, first, "cancelled", "Opened by mistake").status_code == 200
    listed = {i["anomaly_id"]: i for i in _anomalies(api)["items"]}
    assert listed[anomaly["anomaly_id"]]["status"] == "open"
    second = _create(api, anomaly["anomaly_id"])
    assert second.status_code == 201
    listed = {i["anomaly_id"]: i for i in _anomalies(api)["items"]}
    assert listed[anomaly["anomaly_id"]]["catalog_task_id"] == second.json()["task_id"]


def test_the_follow_up_of_a_change_made_weeks_ago(api: Api) -> None:
    """US-606: the variant's weekly return rate for the reason before and after the change, from
    the sales known in the snapshot; the full weeks after the change only."""
    item = next(i for i in _anomalies(api)["items"] if i["variant_id"] == DEFECTIVE_JACKET)
    change = pd.Timestamp("2026-09-02T10:00:00Z")  # a Wednesday: its week is left out

    async def run(conn: AsyncConnection) -> None:
        await conn.execute(
            insert(catalog_tasks).values(
                task_id="CT-000001", anomaly_id=item["anomaly_id"], variant_id=item["variant_id"],
                reason=item["reason"], week=pd.Timestamp(item["week"]).date(), kind="size_guide",
                status="done", note="Size chart corrected", created_by="luca",
                created_at=change.to_pydatetime() - timedelta(days=2),
                updated_at=change.to_pydatetime(), done_at=change.to_pydatetime(),
            )
        )  # fmt: skip

    api.run(run)
    tasks = api.client.get("/api/catalog-tasks")
    assert tasks.status_code == 200
    validate_body("catalog_tasks", tasks.json())
    task = tasks.json()["items"][0]
    follow = task["follow_up"]
    series = weekly_series(api.serving.sales, item["variant_id"], item["reason"], api.serving.as_of)
    week = series["week"]
    before = series.loc[
        (week < pd.Timestamp("2026-08-31T00:00Z")) & (week >= pd.Timestamp("2026-07-06T00:00Z"))
    ]
    after = series.loc[
        (week >= pd.Timestamp("2026-09-07T00:00Z"))
        & (week + pd.Timedelta(days=7) <= pd.Timestamp(api.serving.as_of))
    ]  # fmt: skip
    assert follow["baseline_rate"]["value"] == pytest.approx(
        before["observed"].sum() / before["n"].sum(), abs=1e-4
    )
    assert follow["weeks_after"] == int((after["n"] > 0).sum()) > 0
    assert follow["after_rate"]["value"] == pytest.approx(
        after["observed"].sum() / after["n"].sum(), abs=1e-4
    )
    assert follow["caveat"] == s.FOLLOW_UP_CAVEAT
