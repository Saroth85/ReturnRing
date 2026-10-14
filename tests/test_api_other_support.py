"""Helpers of the tests of the other areas (tests/test_api_other_*.py): no test here.

- approve_in_db: what POST .../decision (approve) leaves in the tables, for a decision of the
  reset (the operator's route is built and tested in its own module);
- supersede_in_db: a later version of a decision (a recompute), so the earlier one leaves force;
- parse_sse: the frames of a text/event-stream body, each checked against sse_frame;
- photo_for: a JPEG whose bytes give a chosen outcome of the deterministic mock vision.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.other.inspections import JPEG_MAGIC, mock_outcome
from contracts.sql import coupon_rule_applications, decisions
from pipeline.generator.golden import MARCO_ACCOUNTS
from tests.api_harness import Api, golden_return

_EVENT: TypeAdapter[s.AgentEventModel] = TypeAdapter(s.AgentEvent)


def marco_return(api: Api) -> tuple[str, str]:
    """The open return of the first ring account that has one, and that account."""
    scores = api.serving.frame("scores/request_scores")
    customer = next(c for c in MARCO_ACCOUNTS if (scores["customer_id"] == c).any())
    return golden_return(api.serving, customer), customer


async def decision_of(conn: AsyncConnection, return_id: str) -> dict[str, Any]:
    """The latest decisions row of a return."""
    rows = (
        await conn.execute(
            select(decisions)
            .where(decisions.c.return_id == return_id)
            .order_by(decisions.c.version.desc())
        )
    ).mappings()
    return dict(rows.first() or {})


async def approve_in_db(
    conn: AsyncConnection, decision_id: str, approver: str, at: datetime
) -> None:
    """A person approves the proposal and confirms its cluster coupon application (US-303,
    US-402): the rows as the approve action leaves them."""
    await conn.execute(
        update(decisions)
        .where(decisions.c.decision_id == decision_id)
        .values(state="approved", approver=approver, approved_at=at)
    )
    await conn.execute(
        update(coupon_rule_applications)
        .where(coupon_rule_applications.c.decision_id == decision_id)
        .values(state="confirmed", confirmed_by=approver, confirmed_at=at)
    )


async def reverse_in_db(conn: AsyncConnection, application_id: str, at: datetime) -> None:
    await conn.execute(
        update(coupon_rule_applications)
        .where(coupon_rule_applications.c.application_id == application_id)
        .values(
            state="reversed",
            reversed_by="luca",
            reversed_at=at,
            reversal_reason="Rectification asked by the customer",
        )
    )


async def supersede_in_db(conn: AsyncConnection, decision_id: str, at: datetime) -> str:
    """Version n + 1 of a decision without a coupon block (a recompute on the day `at`): the
    version shown before is no longer in force."""
    rows = await conn.execute(select(decisions).where(decisions.c.decision_id == decision_id))
    row = dict(rows.mappings().one())
    if row["coupon_application_id"] is not None:
        raise ValueError("supersede_in_db is for a decision without a coupon block")
    version = int(row["version"]) + 1
    new_id = f"{decision_id.rsplit('-', 1)[0]}-{version}"
    row.update(
        decision_id=new_id,
        version=version,
        supersedes_decision_id=decision_id,
        revision_trigger="evidence_added",
        created_at=at,
        decided_on=s.utc_day(at),
        review_at=None if row["review_at"] is None else s.utc_day(at) + timedelta(days=90),
        state="proposed",
        approver=None,
        approved_at=None,
        override_reason=None,
        override_category=None,
        generation_id=None,
        generation_path=None,
    )
    await conn.execute(decisions.insert().values(**row))
    return new_id


def parse_sse(text: str) -> list[dict[str, Any]]:
    """The events of a server-sent event body; each frame is exactly sse_frame of its event."""
    events: list[dict[str, Any]] = []
    assert text.endswith("\n\n"), "every frame ends with a blank line"
    for frame in text.split("\n\n")[:-1]:
        lines = frame.split("\n")
        assert [line.split(": ", 1)[0] for line in lines] == ["event", "id", "data"], frame
        event = json.loads(lines[2].removeprefix("data: "))
        model = _EVENT.validate_python(event)
        assert frame + "\n\n" == s.sse_frame(model)
        events.append(event)
    return events


def photo_for(outcome: str) -> dict[str, str]:
    """A JPEG upload (magic number first) that the mock vision reads as `outcome`; for
    "unreadable", bytes that are no JPEG at all."""
    if outcome == "unreadable":
        return {"media_type": "image/jpeg", "data_base64": base64.b64encode(b"GIF89a").decode()}
    for i in range(4096):
        data = JPEG_MAGIC + b"\xe0\x00\x10JFIF" + i.to_bytes(2, "big")
        if mock_outcome(data) == outcome:
            return {"media_type": "image/jpeg", "data_base64": base64.b64encode(data).decode()}
    raise AssertionError(f"no photo gives {outcome}")
