"""The operator routes on PostgreSQL 16 (asyncpg): every CHECK, trigger and lock of
contracts/sql.py in play (ADR-15 section 6). Runs with RETURNRING_TEST_POSTGRES_URL (a
postgresql+asyncpg URL of a database the test may drop and recreate); skipped otherwise.

The flow is the demo's: the queue, Marco's approval (the coupon block confirmed) and its lift
(the block reversed), evidence on the Rossi shared card, a dispute in Marco's ring, a new draft
for Giulia; every response passes its JSON Schema and its model.
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.api.db import make_engine
from apps.api.reset import reset
from apps.api.services.generation import Generator
from apps.api.serving import Serving
from pipeline.generator.golden import GIULIA, ROSSI_CARD_ACCOUNTS
from tests.api_harness import golden_return, open_api, settings_for
from tests.test_api_operator_support import card, decide, latest, ok, ring_return

POSTGRES_URL = os.environ.get("RETURNRING_TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="RETURNRING_TEST_POSTGRES_URL is not set")
def test_the_operator_flows_on_postgresql(
    serving: Serving, serving_dir: Path, seeded_db: tuple[Path, Any], tmp_path: Path
) -> None:
    assert POSTGRES_URL
    settings = settings_for(serving_dir, tmp_path / "unused.sqlite", database_url=POSTGRES_URL)

    async def run() -> dict[str, Any]:
        engine = make_engine(POSTGRES_URL or "")
        try:
            return await reset(engine, serving, Generator(settings))
        finally:
            await engine.dispose()

    summary = asyncio.run(run())
    assert not summary["failed"] and summary["decided"] == seeded_db[1]["decided"]
    for api in open_api(serving, settings, tmp_path / "unused.sqlite"):
        queue = ok(api.client.get("/api/returns", params={"page_size": 100}), "queue_response")
        assert queue["total"] == summary["decided"]
        marco = ring_return(api)
        approved = ok(decide(api, marco, "approve"), "decision_result")
        assert approved["coupon_application"]["state"] == "confirmed"
        api.clock.advance(timedelta(hours=1))
        lifted = ok(
            decide(
                api, marco, "lift", override_reason="Rectification asked by the customer",
                override_category="customer_context",
            ),
            "decision_result",
        )  # fmt: skip
        assert lifted["coupon_application"]["state"] == "reversed"
        scores = api.serving.frame("scores/request_scores")
        rossi = str(
            sorted(scores.loc[scores["customer_id"].isin(ROSSI_CARD_ACCOUNTS), "return_id"])[0]
        )
        evidence = {
            "decision_id": latest(api, rossi),
            "kind": "shared_payment_explained",
            "note": "The card belongs to the parents: one household",
            "target_link_ids": ["C-ROSSI-CARD-1~C-ROSSI-CARD-2"],
            "author": "sara",
        }
        ok(api.client.post(f"/api/returns/{rossi}/evidence", json=evidence), "recompute_response")
        api.clock.advance(timedelta(hours=1))
        disputed = ok(
            api.client.post(
                "/api/links/C-MARCO-3~C-MARCO-7/dispute",
                json={
                    "reason": "Card used once for a gift",
                    "author": "luca",
                    "return_id": marco,
                    "decision_id": latest(api, marco),
                },
            ),
            "link_dispute_response",
        )
        assert disputed["affected_return_ids"] == [marco]
        giulia = golden_return(api.serving, GIULIA)
        regenerate = {"decision_id": latest(api, giulia), "requested_by": "sara"}
        ok(
            api.client.post(f"/api/returns/{giulia}/regenerate", json=regenerate),
            "regenerate_response",
        )
        for return_id in (marco, rossi, giulia):
            card(api, return_id)
        record = ok(
            api.client.get(f"/api/decisions/{approved['decision']['decision_id']}"),
            "decision_record",
        )
        assert record["decision"]["superseded_by"] == lifted["decision"]["decision_id"]
