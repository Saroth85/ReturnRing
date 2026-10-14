"""Shared helpers of the operator tests (E3: US-301..US-310, US-604, US-607; ADR-15 section 4).

Every response is checked against its JSON Schema and its model (tests/api_harness.validate_body);
every error against ErrorResponse with its contract code. The data is the tiny synthetic store of
the harness (Navigli Fashion, scale 0.05, seed 7): every number is synthetic, none is a result.
This module holds no test.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.audit import entries
from contracts.sql import metadata
from pipeline.generator.golden import MARCO_ACCOUNTS
from tests.api_harness import Api, golden_return, validate_body

MESSAGE_ACTIONS = ("approve", "override", "reject")


def ok(response: Response, model: str) -> dict[str, Any]:
    """A 200 whose body is a valid `model` of the contract."""
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body(model, body)
    return body


def error(response: Response, status: int, code: str) -> dict[str, Any]:
    """An ErrorResponse with this status and contract code, and the request id header."""
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    validate_body("error", body)
    assert body["code"] == code, body
    assert body["request_id"] == response.headers[s.REQUEST_ID_HEADER]
    return body


def card(api: Api, return_id: str) -> dict[str, Any]:
    return ok(api.client.get(f"/api/returns/{return_id}"), "case_card")


def latest(api: Api, return_id: str) -> str:
    decision_id: str = card(api, return_id)["decision"]["decision_id"]
    return decision_id


def ring_return(api: Api) -> str:
    """The open return of the Marco ring account of the tiny store (Q4, cluster of 7)."""
    scores = api.serving.frame("scores/request_scores")
    customer = next(c for c in MARCO_ACCOUNTS if (scores["customer_id"] == c).any())
    return golden_return(api.serving, customer)


def decision_body(
    api: Api,
    return_id: str,
    action: str,
    *,
    decision_id: str | None = None,
    approver: str = "sara",
    lang: str = "it",
    **fields: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "decision_id": decision_id or latest(api, return_id),
        "action": action,
        "approver": approver,
    }
    if action in MESSAGE_ACTIONS:
        body["message"] = {"lang": lang}
    body.update(fields)
    validate_body("decision_request", body)
    return body


def decide(api: Api, return_id: str, action: str, **kwargs: Any) -> Response:
    body = decision_body(api, return_id, action, **kwargs)
    return api.client.post(f"/api/returns/{return_id}/decision", json=body)


def rows(api: Api, name: str, **where: Any) -> list[dict[str, Any]]:
    """The rows of a table of contracts/sql.py, optionally filtered by column values."""
    table = metadata.tables[name]

    async def run(conn: AsyncConnection) -> list[dict[str, Any]]:
        query = select(table)
        for column, value in where.items():
            query = query.where(table.c[column] == value)
        return [dict(r) for r in (await conn.execute(query)).mappings().all()]

    return api.run(run)


def audit(api: Api, return_id: str) -> list[s.AuditEntry]:
    async def run(conn: AsyncConnection) -> list[s.AuditEntry]:
        return await entries(conn, return_id=return_id)

    return api.run(run)


def by_id(items: list[Mapping[str, Any]], key: str = "return_id") -> dict[str, Mapping[str, Any]]:
    return {str(item[key]): item for item in items}
