"""The manager routes on PostgreSQL 16 (asyncpg): the same answers as on SQLite after the same
reset, and every write (assumption version, catalogue task, coupon rule, reversal) through the
CHECKs and append-only triggers of contracts/sql.py. Runs with RETURNRING_TEST_POSTGRES_URL."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update

from apps.api.db import make_engine
from apps.api.reset import reset
from apps.api.services.generation import Generator
from apps.api.serving import Serving
from contracts.sql import coupon_rule_applications, decisions
from pipeline.generator.golden import MARCO_ACCOUNTS
from tests.api_harness import CLOCK_AT, Api, golden_return, open_api, settings_for, validate_body
from tests.test_api_manager_evaluation import (  # noqa: F401 - pytest fixtures
    evaluated_api_fixture,
    evaluated_dir_fixture,
    evaluated_serving_fixture,
    metrics_fixture,
)

POSTGRES_URL = os.environ.get("RETURNRING_TEST_POSTGRES_URL")
READS = (
    "/api/portfolio/matrix",
    "/api/simulator/assumptions",
    "/api/kpi?week_start=2026-09-28",
    "/api/fairness",
    "/api/audit?page_size=500",
    "/api/outbox",
    "/api/products/anomalies",
    "/api/catalog-tasks",
    "/api/coupon-rules",
)


def _reads(api: Api) -> dict[str, Any]:
    out = {}
    for path in READS:
        response = api.client.get(path)
        assert response.status_code == 200, (path, response.text)
        out[path] = response.json()
    return out


def _approve_marco(url: str, serving: Serving) -> None:
    req = serving.frame("scores/request_scores")
    customer = next(c for c in MARCO_ACCOUNTS if (req["customer_id"] == c).any())
    return_id = golden_return(serving, customer)

    async def run() -> None:
        engine = make_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    update(decisions)
                    .where(decisions.c.return_id == return_id)
                    .values(state="approved", approver="sara", approved_at=CLOCK_AT)
                )
                await conn.execute(
                    update(coupon_rule_applications).values(
                        state="confirmed", confirmed_by="sara", confirmed_at=CLOCK_AT
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.skipif(not POSTGRES_URL, reason="RETURNRING_TEST_POSTGRES_URL is not set")
def test_the_manager_routes_on_postgresql(
    evaluated_serving: Serving,
    evaluated_dir: Path,
    evaluated_api: Api,
    tmp_path: Path,
) -> None:
    assert POSTGRES_URL
    settings = settings_for(evaluated_dir, tmp_path / "unused.sqlite", database_url=POSTGRES_URL)

    async def run() -> dict[str, Any]:
        engine = make_engine(POSTGRES_URL or "")
        try:
            return await reset(engine, evaluated_serving, Generator(settings))
        finally:
            await engine.dispose()

    summary = asyncio.run(run())
    assert not summary["failed"]
    for api in open_api(evaluated_serving, settings, tmp_path / "unused.sqlite"):
        on_pg = _reads(api)
        on_sqlite = _reads(evaluated_api)
        for path in READS:
            if path.startswith("/api/audit"):  # request ids differ between the two runs
                strip = [{**i, "request_id": None} for i in on_pg[path]["items"]]
                assert strip == [{**i, "request_id": None} for i in on_sqlite[path]["items"]]
            else:
                assert on_pg[path] == on_sqlite[path], path
        update_body = {
            "base_version": "assumptions-v1",
            "assumptions": {
                k: v
                for k, v in on_pg["/api/simulator/assumptions"]["current"]["net_benefit"][
                    "assumptions"
                ].items()
                if k not in ("version", "basis", "based_on")
            }
            | {"logistics_cost_per_return_eur": 6.5},
            "note": "Carrier contract renegotiated: 6.50 EUR per return (hypothesis)",
            "author": "luca",
        }
        put = api.client.put("/api/simulator/assumptions", json=update_body)
        assert put.status_code == 200, put.text
        assert put.json()["current"]["net_benefit"]["total"]["value"] == 248.0
        assert api.client.put("/api/simulator/assumptions", json=update_body).status_code == 409
        anomaly = on_pg["/api/products/anomalies"]["items"][0]["anomaly_id"]
        created = api.client.post(
            "/api/catalog-tasks",
            json={
                "anomaly_id": anomaly,
                "kind": "batch_check",
                "note": "Check the batch",
                "author": "luca",
            },  # fmt: skip
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]
        done = api.client.patch(
            f"/api/catalog-tasks/{task_id}", json={"status": "done", "author": "sara"}
        )
        assert done.status_code == 200, done.text
        validate_body("catalog_task", done.json())
        rule = api.client.post("/api/coupon-rules", json={"text": "Coupons paused (v2)",
                                                         "approved_by": "luca"})  # fmt: skip
        assert rule.status_code == 201, rule.text
        _approve_marco(POSTGRES_URL, evaluated_serving)
        application = rule.json()["applications"][0]["application_id"]
        reversed_ = api.client.post(
            f"/api/coupon-rules/applications/{application}/reverse",
            json={"author": "luca", "reason": "Rectification on request"},
        )
        assert reversed_.status_code == 200, reversed_.text
        validate_body("coupon_rule_application", reversed_.json())
        after = _reads(api)
        for path, body in after.items():
            name = {
                "/api/portfolio/matrix": "portfolio_matrix",
                "/api/simulator/assumptions": "simulator_assumptions",
                "/api/kpi?week_start=2026-09-28": "kpi",
                "/api/fairness": "fairness",
                "/api/audit?page_size=500": "audit_page",
                "/api/outbox": "outbox",
                "/api/products/anomalies": "product_anomalies",
                "/api/catalog-tasks": "catalog_tasks",
                "/api/coupon-rules": "coupon_rules",
            }[path]
            validate_body(name, body)
        actions = {i["action"] for i in after["/api/audit?page_size=500"]["items"]}
        assert {
            "assumptions_updated", "catalog_task_opened", "catalog_task_updated",
            "coupon_rule_approved", "coupon_rule_application_reversed",
        } <= actions  # fmt: skip
        csv = api.client.get("/api/audit", params={"format": "csv"})
        assert (
            csv.status_code == 200
            and csv.text.count("\r\n") == len(after["/api/audit?page_size=500"]["items"]) + 1
        )
