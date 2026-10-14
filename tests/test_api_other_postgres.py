"""The routes of the other areas on PostgreSQL 16 (asyncpg): every CHECK of terms_snapshots,
inspections, llm_generations and outbox sees their rows, and the telemetry table is created and
read there (ADR-15 section 6). Runs when RETURNRING_TEST_POSTGRES_URL is set."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api.db import make_engine
from apps.api.reset import reset
from apps.api.services.generation import Generator
from apps.api.services.other import telemetry as tm
from apps.api.serving import Serving
from pipeline.generator.golden import GIULIA
from tests.api_harness import golden_return, open_api, settings_for, validate_body
from tests.test_api_other_support import (
    approve_in_db,
    decision_of,
    marco_return,
    parse_sse,
    photo_for,
)

POSTGRES_URL = os.environ.get("RETURNRING_TEST_POSTGRES_URL")


def _run[T](fn: Callable[[AsyncConnection], Awaitable[T]]) -> T:
    async def go() -> T:
        engine = make_engine(POSTGRES_URL or "")
        try:
            async with engine.begin() as conn:
                return await fn(conn)
        finally:
            await engine.dispose()

    return asyncio.run(go())


async def _approve(conn: AsyncConnection, *, return_id: str, at: datetime) -> None:
    row = await decision_of(conn, return_id)
    await approve_in_db(conn, row["decision_id"], "sara", at)


@pytest.mark.skipif(not POSTGRES_URL, reason="RETURNRING_TEST_POSTGRES_URL is not set")
def test_the_other_routes_on_postgresql(
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
    assert asyncio.run(tm.sample_loop(POSTGRES_URL, source_file=tmp_path / "none", count=1)) == 1
    for api in open_api(serving, settings, tmp_path / "unused.sqlite"):
        giulia = golden_return(serving, GIULIA)
        record = serving.store_return(giulia)
        assert record is not None
        cart = {
            "customer_id": GIULIA,
            "lines": [{"variant_id": record.variant_id, "size": "M", "quantity": 1}],
            "coupon_code": None,
        }
        shown = api.client.post("/api/checkout/evaluate", json=cart)
        assert shown.status_code == 200, shown.text
        validate_body("checkout_evaluate_response", shown.json())
        confirm = {**cart, "terms_snapshot_id": shown.json()["terms_snapshot_id"]}
        bound = api.client.post("/api/checkout/confirm", json=confirm)
        assert bound.status_code == 201, bound.text
        validate_body("checkout_confirm_response", bound.json())

        marco, customer = marco_return(api)

        _run(partial(_approve, return_id=marco, at=api.clock.now()))
        marco_record = serving.store_return(marco)
        assert marco_record is not None
        restricted = api.client.post(
            "/api/checkout/evaluate",
            json={**cart, "customer_id": customer,
                  "lines": [{"variant_id": marco_record.variant_id, "size": "M", "quantity": 1}]},
        )  # fmt: skip
        assert restricted.status_code == 200, restricted.text
        assert restricted.json()["coupon_block"] is not None

        for return_id in (giulia, marco):
            created = api.client.post(
                "/api/inspections",
                json={"return_id": return_id, "inspector": "ahmed", "photo": photo_for("damaged")},
            )
            assert created.status_code == 201, created.text
            observations = [
                {"item": "tag_attached", "observation": "no"},
                {"item": "visible_damage", "observation": "yes"},
                {"item": "photo_quality", "observation": "yes"},
            ]
            done = api.client.post(
                f"/api/inspections/{created.json()['inspection_id']}/confirm",
                json={"inspector": "ahmed", "observations": observations,
                      "apply_compensation": True},
            )  # fmt: skip
            assert done.status_code == 200, done.text
            validate_body("inspection_confirm_response", done.json())
            assert done.json()["outbox_message"] is not None

        asked = api.client.post(
            "/api/agent/ask",
            json={"return_id": marco, "question": "Which evidence supports the link?",
                  "asked_by": "sara"},
        )  # fmt: skip
        assert asked.status_code == 200, asked.text
        validate_body("agent_stream", parse_sse(asked.text))
        for path, name in (("/api/telemetry", "telemetry"), ("/api/benchmarks", "benchmarks")):
            response = api.client.get(path)
            assert response.status_code == 200, response.text
            validate_body(name, response.json())
