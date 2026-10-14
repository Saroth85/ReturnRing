"""GET and PUT /api/simulator/assumptions: versioned, append-only assumption sets with their net
benefit and the four sensitivity scenarios of policy.simulator (US-602, ECO-01; rules 9, 11)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.roles import ACTOR_HEADER
from apps.api.services.audit import entries
from apps.api.services.seeds import current_assumptions
from contracts.sql import audit_log, simulator_assumptions
from tests.api_harness import CLOCK_AT, Api, validate_body

EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples" / "api"
URL = "/api/simulator/assumptions"


def _example(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text("utf-8"))


def _get(api: Api) -> dict[str, Any]:
    response = api.client.get(URL)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body("simulator_assumptions", body)
    return body


def _figures(version: dict[str, Any]) -> dict[str, Any]:
    """What the example fixes: the assumption values, the components and the scenario totals
    (not the provenance, the creation time or who created it)."""
    nb = version["net_benefit"]
    return {
        "assumptions": nb["assumptions"],
        "components": [(c["id"], c["amount"]["value"]) for c in nb["components"]],
        "total": nb["total"]["value"],
        "sensitivity": [
            (x["id"], x["net_benefit"]["assumptions"], x["net_benefit"]["total"]["value"])
            for x in version["sensitivity"]
        ],
        "baseline": version["baseline"],
        "note": version["note"],
        "supersedes": version["supersedes"],
        "version": version["version"],
    }


def test_the_seeded_version_reproduces_eco_01(api: Api) -> None:
    """ECO-01: the illustrative example of spec 8.10 gives 368, 48, 128 and -192 EUR."""
    body = _get(api)
    current = body["current"]
    assert [h["version"] for h in body["history"]] == ["assumptions-v1"]
    assert current["net_benefit"]["total"]["value"] == 368.0
    totals = {x["id"]: x["net_benefit"]["total"]["value"] for x in current["sensitivity"]}
    assert totals == {"base": 368.0, "logistics_halved": 48.0, "abandonment_doubled": 128.0,
                      "both": -192.0}  # fmt: skip
    assert current["created_by"] == "system"
    assert current["net_benefit"]["total"]["provenance"]["nature"] == "simulated"
    assert _figures(current) == _figures(_example("simulator_assumptions.v1.json")["current"])


def test_no_component_is_a_refund(api: Api) -> None:
    """Rule 9: the refund owed to the consumer is never a saving."""
    current = _get(api)["current"]
    ids = [c["id"] for c in current["net_benefit"]["components"]]
    assert list(ids) == list(s.NET_BENEFIT_COMPONENTS)
    assert not any("refund" in key for key in current["net_benefit"]["assumptions"])


def test_a_new_version_from_the_current_one(api: Api) -> None:
    """The example PUT: Luca renegotiates the carrier contract; v2 supersedes v1, which stays
    in the history unchanged (append-only), and the act is in the audit log."""
    request = _example("simulator_assumptions_update.json")
    response = api.client.put(URL, json=request)
    assert response.status_code == 200, response.text
    body = response.json()
    validate_body("simulator_assumptions", body)
    assert [h["version"] for h in body["history"]] == ["assumptions-v2", "assumptions-v1"]
    expected = _example("simulator_assumptions.v2.json")
    assert _figures(body["current"]) == _figures(expected["current"])
    assert body["current"]["created_by"] == "luca"
    assert body["current"]["created_at"] == "2026-10-02T09:00:00Z"
    assert _figures(body["history"][1]) == _figures(expected["history"][1])
    assert _get(api) == body

    async def run(conn: AsyncConnection) -> tuple[list[s.AuditEntry], str]:
        log = [e for e in await entries(conn) if e.action == "assumptions_updated"]
        return log, (await current_assumptions(conn)).version

    log, current = api.run(run)
    assert current == "assumptions-v2"
    assert len(log) == 1
    entry = log[0]
    assert (entry.actor, entry.actor_role, entry.entity_type, entry.entity_id) == (
        "luca", "manager", "simulator_assumptions", "assumptions-v2",
    )  # fmt: skip
    assert entry.before == {"version": "assumptions-v1"}
    assert entry.after == {"version": "assumptions-v2"}
    assert entry.at == CLOCK_AT and entry.request_id is not None


def test_a_stale_base_version_is_a_conflict(api: Api) -> None:
    request = _example("simulator_assumptions_update.json")
    assert api.client.put(URL, json=request).status_code == 200
    again = api.client.put(URL, json=request)
    assert again.status_code == 409
    validate_body("error", again.json())
    assert again.json()["code"] == "conflict"
    unknown = api.client.put(URL, json={**request, "base_version": "assumptions-v9"})
    assert unknown.status_code == 409
    assert [h["version"] for h in _get(api)["history"]] == ["assumptions-v2", "assumptions-v1"]


def test_only_the_manager_creates_a_version(api: Api) -> None:
    request = _example("simulator_assumptions_update.json")
    for author in ("sara", "ahmed"):
        response = api.client.put(URL, json={**request, "author": author})
        assert response.status_code == 403, author
        assert response.json()["code"] == "forbidden_role"
    system = api.client.put(URL, json={**request, "author": "system"})
    assert system.status_code == 422  # a person, never the system (rule 5)
    header = api.client.put(URL, json=request, headers={ACTOR_HEADER: "sara"})
    assert header.status_code == 403
    assert [h["version"] for h in _get(api)["history"]] == ["assumptions-v1"]


def test_a_refund_is_not_an_assumption(api: Api) -> None:
    """Rule 9: there is no refund input; an unknown field is refused."""
    request = _example("simulator_assumptions_update.json")
    request["assumptions"]["refunds_avoided_eur"] = 1000.0
    response = api.client.put(URL, json=request)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_invalid_values_are_refused(api: Api) -> None:
    request = _example("simulator_assumptions_update.json")
    for field, value in (
        ("logistics_cost_per_return_eur", -1.0),
        ("logistics_returns_avoided", 1.5),
        ("incentives_eur", 1.234),
    ):
        bad = {**request, "assumptions": {**request["assumptions"], field: value}}
        response = api.client.put(URL, json=bad)
        assert response.status_code == 422, field


def test_a_cost_whose_half_is_not_in_cents_is_refused(api: Api) -> None:
    """The logistics_halved scenario halves the cost per return: 6.55 EUR would need 3.275 EUR,
    which no euro amount of the contract holds (2 decimals). The API refuses it (422) rather
    than round a scenario of policy.simulator."""
    request = _example("simulator_assumptions_update.json")
    request["assumptions"]["logistics_cost_per_return_eur"] = 6.55
    response = api.client.put(URL, json=request)
    assert response.status_code == 422
    assert "logistics_halved" in response.json()["message"]


def test_the_versions_are_append_only(api: Api) -> None:
    assert (
        api.client.put(URL, json=_example("simulator_assumptions_update.json")).status_code == 200
    )

    async def tamper(conn: AsyncConnection) -> None:
        await conn.execute(update(simulator_assumptions).values(note="changed"))

    try:
        api.run(tamper)
    except Exception as exc:  # noqa: BLE001 - the trigger's error, whatever the driver
        assert "append-only" in str(exc)
    else:
        raise AssertionError("simulator_assumptions accepted an UPDATE")

    async def chain(conn: AsyncConnection) -> list[tuple[str, int, str | None]]:
        rows = await conn.execute(
            select(
                simulator_assumptions.c.version,
                simulator_assumptions.c.seq,
                simulator_assumptions.c.supersedes_version,
            ).order_by(simulator_assumptions.c.seq)
        )
        return [(r[0], r[1], r[2]) for r in rows.all()]

    assert api.run(chain) == [("assumptions-v1", 1, None), ("assumptions-v2", 2, "assumptions-v1")]


def test_new_decisions_use_the_new_version(api: Api) -> None:
    assert (
        api.client.put(URL, json=_example("simulator_assumptions_update.json")).status_code == 200
    )
    ready = api.client.get("/readyz").json()
    assert ready["assumptions_version"] == "assumptions-v2"

    async def count(conn: AsyncConnection) -> int:
        rows = await conn.execute(
            select(audit_log).where(audit_log.c.action == "assumptions_updated")
        )
        return len(rows.all())

    assert api.run(count) == 1
