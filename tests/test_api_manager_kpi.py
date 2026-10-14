"""GET /api/kpi: the KPIs of a week (US-603) from the decisions and audit tables, the simulated
net benefit of the week's decisions (policy.simulator, rule 9), the realized one kept apart, and
the false positives of the offline evaluation report (rule 12)."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.ids import decision_id
from apps.api.services.manager.kpi import default_week
from contracts.sql import decisions
from pipeline.generator.golden import GIULIA, MARCO_ACCOUNTS
from policy.simulator import Assumptions as SimAssumptions
from policy.simulator import net_benefit
from tests.api_harness import CLOCK_AT, Api, golden_return, validate_body
from tests.test_api_manager_evaluation import (  # noqa: F401 - pytest fixtures
    evaluated_api_fixture,
    evaluated_dir_fixture,
    evaluated_serving_fixture,
    metrics_fixture,
    unevaluated_api_fixture,
    unevaluated_dir_fixture,
    unevaluated_serving_fixture,
)

RESET_WEEK = "2026-09-28"  # the week of the snapshot instant (2026-10-01), when the reset decided
QUANTITIES = (
    "logistics_returns_avoided",
    "undue_discounts_avoided",
    "minutes_saved",
    "incentives_eur",
    "system_cost_eur",
    "error_cost_eur",
    "abandonment_margin_lost_eur",
    "incremental_margin_eur",
)


def _kpi(api: Api, week: str | None = None) -> dict[str, Any]:
    params = {"week_start": week} if week else None
    response = api.client.get("/api/kpi", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body("kpi", body)
    return body


def _rows(api: Api) -> list[dict[str, Any]]:
    async def run(conn: AsyncConnection) -> list[dict[str, Any]]:
        return [dict(r) for r in (await conn.execute(select(decisions))).mappings().all()]

    return api.run(run)


def _marco_return(api: Api) -> str:
    req = api.serving.frame("scores/request_scores")
    customer = next(c for c in MARCO_ACCOUNTS if (req["customer_id"] == c).any())
    return golden_return(api.serving, customer)


def _act(api: Api, return_id: str, state: str, category: str | None = None) -> None:
    """A person's act on the proposal of a return, written as the operator routes write it."""

    async def run(conn: AsyncConnection) -> None:
        values: dict[str, Any] = {"state": state, "approver": "sara", "approved_at": CLOCK_AT}
        if state != "approved":
            values |= {"override_reason": "Customer called the shop", "override_category": category}
        if state == "rejected":
            values["levers"] = []
        await conn.execute(
            update(decisions).where(decisions.c.return_id == return_id).values(**values)
        )

    api.run(run)


def test_the_default_week_is_the_last_complete_week() -> None:
    assert default_week(datetime.fromisoformat("2026-10-02T09:00:00+00:00")) == date(2026, 9, 21)
    assert default_week(datetime.fromisoformat("2026-10-05T00:00:00+00:00")) == date(2026, 9, 28)
    assert default_week(datetime.fromisoformat("2026-10-04T23:59:59+00:00")) == date(2026, 9, 21)


def test_a_week_without_decisions(evaluated_api: Api) -> None:
    body = _kpi(evaluated_api, "2026-09-21")
    assert (body["period_start"], body["period_end"]) == ("2026-09-21", "2026-09-27")
    assert body["decisions"]["total"]["value"] == 0
    assert body["abstention_rate"]["value"] == 0
    assert body["net_benefit_simulated"]["total"]["value"] == 0
    assert body["net_benefit_simulated"]["assumptions"]["version"] == "kpi/2026-09-21"
    assert body["net_benefit_realized"] is None


def test_the_week_of_the_reset(evaluated_api: Api) -> None:
    """Every return of the reset is proposed in the week of the snapshot instant; none is acted
    on yet: all pending, abstentions are the withdrawal returns with insufficient evidence."""
    rows = _rows(evaluated_api)
    body = _kpi(evaluated_api, RESET_WEEK)
    counts = body["decisions"]
    assert counts["total"]["value"] == len(rows) == len(evaluated_api.serving.open_return_ids())
    assert counts["pending"]["value"] == len(rows)
    assert counts["approved"]["value"] == counts["overridden"]["value"] == 0
    abstentions = sum(
        r["path"] == "withdrawal" and r["evidence_quality"] == "insufficient" for r in rows
    )
    assert body["abstentions_insufficient_evidence"]["value"] == abstentions
    assert body["abstention_rate"]["value"] == pytest.approx(abstentions / len(rows), abs=1e-4)
    assert body["decision_time_p50"]["value"] == body["decision_time_p95"]["value"] == 0
    assert body["override_reasons"] == []
    assert body["open_catalog_tasks"]["value"] == 0


def _expected_week_benefit(rows: list[dict[str, Any]], stored: s.Assumptions) -> Decimal:
    """policy.simulator on the summed quantities of the proposals that apply: approved, or
    proposed without a need for approval (the levers in force by themselves)."""
    sums = dict.fromkeys(QUANTITIES, Decimal(0))
    for row in rows:
        applies = row["state"] == "approved" or (
            row["state"] == "proposed" and not row["requires_approval"]
        )
        if not applies:
            continue
        proposal = next(a for a in row["alternatives"] if a["recommended"])
        for name in QUANTITIES:
            sums[name] += Decimal(str(proposal["net_benefit"]["assumptions"][name]))
    policy = SimAssumptions(
        period="test",
        logistics_returns_avoided=int(sums["logistics_returns_avoided"]),
        logistics_cost_per_return_eur=Decimal(str(stored.logistics_cost_per_return_eur)),
        undue_discounts_avoided=int(sums["undue_discounts_avoided"]),
        avg_undue_discount_eur=Decimal(str(stored.avg_undue_discount_eur)),
        minutes_saved=sums["minutes_saved"],
        hourly_cost_eur=Decimal(str(stored.hourly_cost_eur)),
        incentives_eur=sums["incentives_eur"],
        system_cost_eur=sums["system_cost_eur"],
        error_cost_eur=sums["error_cost_eur"],
        abandonment_margin_lost_eur=sums["abandonment_margin_lost_eur"],
        incremental_margin_eur=sums["incremental_margin_eur"],
    )
    return net_benefit(policy).total


def test_people_acts_change_the_week(evaluated_api: Api) -> None:
    """Approve the Q4 of the ring, override Giulia's upgrade, reject another proposal: counts,
    decision times, override reasons and the simulated net benefit follow (US-603)."""
    api = evaluated_api
    marco, giulia = _marco_return(api), golden_return(api.serving, GIULIA)
    other = next(r for r in api.serving.open_return_ids() if r not in (marco, giulia))
    _act(api, marco, "approved")
    _act(api, giulia, "overridden", "customer_context")
    _act(api, other, "rejected", "evidence_disagreement")
    rows = _rows(api)
    body = _kpi(api, RESET_WEEK)
    counts = {k: v["value"] for k, v in body["decisions"].items()}
    assert counts == {
        "total": len(rows), "approved": 1, "overridden": 1, "rejected": 1,
        "pending": len(rows) - 3,
    }  # fmt: skip
    waited = (CLOCK_AT - rows[0]["created_at"]).total_seconds()
    assert body["decision_time_p50"]["value"] == body["decision_time_p95"]["value"] == waited
    reasons = {r["category"]: r["count"]["value"] for r in body["override_reasons"]}
    assert reasons == {"customer_context": 1, "evidence_disagreement": 1}
    nb = body["net_benefit_simulated"]
    assumptions = nb["assumptions"]
    assert assumptions["version"] == f"kpi/{RESET_WEEK}"
    assert assumptions["based_on"] == "assumptions-v1" and assumptions["basis"] == "simulated"
    assert nb["total"]["provenance"]["nature"] == "simulated"
    stored = s.SimulatorAssumptionsResponse.model_validate(
        api.client.get("/api/simulator/assumptions").json()
    ).current.net_benefit.assumptions
    assert Decimal(str(nb["total"]["value"])) == _expected_week_benefit(rows, stored)
    assert not any(c["id"] == "refund" for c in nb["components"])  # rule 9
    assert body["net_benefit_realized"] is None  # no outcome observed after the snapshot


def test_the_week_is_priced_with_the_stored_version_in_force(evaluated_api: Api) -> None:
    """US-602: a new assumption version reprices the simulated net benefit of the week."""
    api = evaluated_api
    before = _kpi(api, RESET_WEEK)["net_benefit_simulated"]
    current = api.client.get("/api/simulator/assumptions").json()["current"]
    inputs = {
        k: v
        for k, v in current["net_benefit"]["assumptions"].items()
        if k not in ("version", "basis", "based_on")
    }
    inputs["hourly_cost_eur"] = 30.0
    put = api.client.put(
        "/api/simulator/assumptions",
        json={
            "base_version": current["version"],
            "assumptions": inputs,
            "note": "Operator hour priced at 30 EUR",
            "author": "luca",
        },  # fmt: skip
    )
    assert put.status_code == 200, put.text
    after = _kpi(api, RESET_WEEK)["net_benefit_simulated"]
    assert after["assumptions"]["based_on"] == "assumptions-v2"
    assert after["assumptions"]["hourly_cost_eur"] == 30.0
    quantities = ("minutes_saved", "logistics_returns_avoided", "system_cost_eur")
    assert all(after["assumptions"][q] == before["assumptions"][q] for q in quantities)


def test_false_positives_come_from_the_offline_report(
    evaluated_api: Api, metrics: dict[str, Any]
) -> None:
    body = _kpi(evaluated_api, RESET_WEEK)
    f02 = metrics["fair02"]
    assert body["evaluation_report_id"] == f"eval-{metrics['eval_end'][:10]}"
    cohorts = {c["cohort"]: c for c in body["false_positives"]}
    assert cohorts["all"]["false_positive_rate"]["value"] == pytest.approx(f02["fpr"], abs=1e-6)
    assert cohorts["all"]["customers"]["value"] == f02["non_abusers"]
    rows = {(r["dimension"], r["segment"]): r for r in f02["rows"]}
    new = rows[("New customers", "opened under 90 days ago")]
    assert cohorts["new_customers"]["customers"]["value"] == new["non_abusers"]
    family = rows[("Families (ground truth)", "family member")]
    assert cohorts["households"]["customers"]["value"] == family["non_abusers"]
    low = next(
        r for (d, seg), r in rows.items() if d == "Low-data groups" and seg[:12] == "fewer than 4"
    )
    assert cohorts["low_data"]["customers"]["value"] == low["non_abusers"]
    assert "high_value" not in cohorts  # not measured by the offline FAIR-02: not invented
    assert all(c["false_positive_rate"]["provenance"]["nature"] == "synthetic"
               for c in body["false_positives"])  # fmt: skip


def test_open_catalogue_tasks_of_the_week(evaluated_api: Api) -> None:
    api = evaluated_api
    anomaly = api.client.get("/api/products/anomalies").json()["items"][0]
    created = api.client.post(
        "/api/catalog-tasks",
        json={
            "anomaly_id": anomaly["anomaly_id"],
            "kind": "size_guide",
            "note": "Check the size chart",
            "author": "luca",
        },  # fmt: skip
    )
    assert created.status_code == 201, created.text
    assert _kpi(api, RESET_WEEK)["open_catalog_tasks"]["value"] == 1
    assert _kpi(api)["open_catalog_tasks"]["value"] == 1  # the default: the reset week (H31)
    assert _kpi(api, "2026-09-21")["open_catalog_tasks"]["value"] == 0  # created after it


def test_invalid_weeks_are_refused(evaluated_api: Api) -> None:
    for week in ("2026-09-29", "2026-10-05", "not-a-date"):  # not a Monday; in the future
        response = evaluated_api.client.get("/api/kpi", params={"week_start": week})
        assert response.status_code == 422, (week, response.text)
        validate_body("error", response.json())
        assert response.json()["code"] == "invalid_request"


def test_kpi_without_the_report_is_not_found(unevaluated_api: Api) -> None:
    response = unevaluated_api.client.get("/api/kpi")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    # declared in the contract, and the reason without a stray "not found" (1.5.0, H17)
    assert not response.json()["message"].endswith("not found")
    for path in ("/api/kpi", "/api/fairness"):
        assert 404 in next(ep for ep in s.ENDPOINTS if ep.path == path).errors


def test_a_recomputed_proposal_counts_once(evaluated_api: Api) -> None:
    """A proposal superseded before anyone acted on it is replaced by its successor: the week
    counts the case once (F7: both versions stay in the log)."""
    api = evaluated_api
    giulia = golden_return(api.serving, GIULIA)

    async def supersede(conn: AsyncConnection) -> None:
        row = dict(
            (await conn.execute(select(decisions).where(decisions.c.return_id == giulia)))
            .mappings()
            .one()
        )
        row |= {
            "decision_id": decision_id(giulia, 2),
            "version": 2,
            "supersedes_decision_id": row["decision_id"],
            "revision_trigger": "evidence_added",
            "created_at": CLOCK_AT,
            "decided_on": CLOCK_AT.date(),
            "generation_id": None,
            "generation_path": None,
        }
        await conn.execute(decisions.insert().values(**row))

    api.run(supersede)
    counts = Counter({k: v["value"] for k, v in _kpi(api, RESET_WEEK)["decisions"].items()})
    assert counts["total"] == len(api.serving.open_return_ids())
