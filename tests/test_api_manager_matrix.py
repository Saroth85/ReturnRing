"""GET /api/portfolio/matrix: every scored customer of the serving snapshot as a risk-value
point, the thresholds of spec 8.6, counts and euros per quadrant and the legend that value is
never a signal of abuse (US-601, ADR-04; rules 6, 11)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.roles import ACTOR_HEADER
from apps.api.services.cases import insert_return
from contracts.sql import return_requests
from pipeline.generator.golden import (
    ELENA,
    GIULIA,
    MARCO_ACCOUNTS,
    ROSSI_ACCOUNTS,
    ROSSI_CARD_ACCOUNTS,
)
from policy.engine import Thresholds, quadrant_for
from policy.types import CaseScores, EvidenceQuality, Quadrant, ReturnReason
from tests.api_harness import CLOCK_AT, Api, golden_return, validate_body


def _matrix(api: Api) -> dict[str, Any]:
    response = api.client.get("/api/portfolio/matrix")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    validate_body("portfolio_matrix", body)
    return body


def _points(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["customer_id"]: p for p in body["points"]}


def test_every_scored_customer_is_one_point(api: Api) -> None:
    body = _matrix(api)
    scores = api.serving.frame("scores/scores")
    ids = [p["customer_id"] for p in body["points"]]
    assert ids == sorted(scores["customer_id"]) and len(set(ids)) == len(ids)
    assert body["legend"] == s.VALUE_LEGEND
    assert body["as_of"] == "2026-10-02T09:00:00Z"
    inside = sum(q["customers"]["value"] for q in body["quadrants"])
    assert inside + body["insufficient_evidence"]["value"] == len(ids)
    for q in body["quadrants"]:
        assert q["value_eur"]["unit"] == "eur" and q["customers"]["unit"] == "count"


def test_the_thresholds_are_those_of_the_batch(api: Api) -> None:
    """Spec 8.6: the customer model's risk threshold (precision >= 0.8 on validation) and the
    70th percentile of customer value at the snapshot; the batch quadrants follow them."""
    body = _matrix(api)
    thresholds = body["thresholds"]
    model = api.serving.models.customer
    assert thresholds["risk"]["value"] == (model.threshold if model.threshold is not None else 1.0)
    assert Decimal(str(thresholds["value_eur"]["value"])) == api.serving.value_threshold(
        api.serving.as_of
    )
    assert "70th percentile" in thresholds["value_basis"]
    assert "precision >= 0.8" in thresholds["risk_basis"]


def test_customers_without_a_case_are_their_batch_scores(api: Api) -> None:
    body = _matrix(api)
    points = _points(body)
    carded = set(api.serving.frame("scores/request_scores")["customer_id"])
    checked = 0
    for row in api.serving.frame("scores/scores").itertuples(index=False):
        if row.customer_id in carded:
            continue
        p = points[row.customer_id]
        assert p["evidence_quality"] == row.evidence_quality
        assert Decimal(str(p["value_eur"])) == row.value_eur
        if row.evidence_quality == "insufficient":
            assert p["risk"] is None and p["quadrant"] is None
        else:
            assert p["risk"] == pytest.approx(row.risk) and p["quadrant"] == row.quadrant
        checked += 1
    assert checked > 100


def _card(api: Api, return_id: str) -> dict[str, Any]:
    response = api.client.get(f"/api/returns/{return_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_customer_with_a_case_is_its_card(api: Api) -> None:
    """ADR-15: the matrix and the case card show one risk, one value, one cluster, the same
    returns and welcome coupons (the latest return of a customer with several)."""
    body = _matrix(api)
    points = _points(body)
    thresholds = Thresholds(
        risk=float(body["thresholds"]["risk"]["value"]),
        value_eur=Decimal(str(body["thresholds"]["value_eur"]["value"])),
    )

    async def requests(conn: AsyncConnection) -> pd.DataFrame:
        rows = (await conn.execute(select(return_requests))).mappings().all()
        return pd.DataFrame([dict(r) for r in rows])

    req = api.run(requests)
    latest = req.sort_values(["customer_id", "requested_at", "return_id"]).groupby("customer_id")
    scores = api.serving.frame("scores/scores").set_index("customer_id")
    for row in latest.tail(1).itertuples(index=False):
        card = _card(api, row.return_id)
        p = points[row.customer_id]
        d = card["decision"]
        assert p["evidence_quality"] == d["evidence_quality"], row.return_id
        assert p["value_eur"] == card["value"]["value_eur"]["value"], row.return_id
        assert p["cluster_id"] == d["inputs"]["cluster_id"], row.return_id
        returned = sum(line["returned"] for o in card["orders"]["orders"] for line in o["lines"])
        welcome = sum(o["coupon_kind"] == "welcome" for o in card["orders"]["orders"])
        assert (p["returns_12m"], p["welcome_coupons"]) == (returned, welcome), row.return_id
        if card["risk"] is not None:
            assert p["risk"] == card["risk"]["score"]["value"], row.return_id
        elif d["path"] == "withdrawal":  # insufficient evidence: no score anywhere
            assert p["risk"] is None and d["evidence_quality"] == "insufficient"
        else:  # the customer is scored; the defect return never is (rule 4)
            assert d["inputs"]["risk"] is None
            assert p["risk"] == pytest.approx(scores.loc[row.customer_id, "risk"])
        if p["risk"] is not None:
            expected = quadrant_for(
                CaseScores(
                    p["risk"], Decimal(str(p["value_eur"])),
                    EvidenceQuality(p["evidence_quality"]), p["cluster_id"],
                ),
                thresholds,
            )  # fmt: skip
            assert p["quadrant"] == expected.value


def test_golden_cases_in_the_matrix(api: Api) -> None:
    """The demo story: the ring account of the open case in Q4 with its cluster, Giulia in Q1,
    the Rossi families never in Q3 or Q4, Elena scored as a customer although her defect
    return is not."""
    points = _points(_matrix(api))
    req = api.serving.frame("scores/request_scores")
    marco = next(c for c in MARCO_ACCOUNTS if (req["customer_id"] == c).any())
    assert points[marco]["quadrant"] == "Q4" and points[marco]["cluster_id"] is not None
    assert points[GIULIA]["quadrant"] == "Q1"
    for customer in (*ROSSI_ACCOUNTS, *ROSSI_CARD_ACCOUNTS):
        assert points[customer]["quadrant"] not in (Quadrant.Q3.value, Quadrant.Q4.value)
    golden_return(api.serving, ELENA)
    assert points[ELENA]["risk"] is not None
    assert points[GIULIA]["display_name"] == "Giulia Bianchi"


def test_quadrant_totals_are_computed_on_the_points(api: Api) -> None:
    body = _matrix(api)
    for q in body["quadrants"]:
        inside = [p for p in body["points"] if p["quadrant"] == q["quadrant"]]
        assert q["customers"]["value"] == len(inside)
        total = sum((Decimal(str(p["value_eur"])) for p in inside), Decimal(0))
        assert Decimal(str(q["value_eur"]["value"])) == total
        assert q["returns_12m"]["value"] == sum(p["returns_12m"] for p in inside)


def test_every_figure_carries_its_provenance(api: Api) -> None:
    body = _matrix(api)
    for metric in (
        body["thresholds"]["risk"], body["thresholds"]["value_eur"],
        body["insufficient_evidence"], *[q["customers"] for q in body["quadrants"]],
    ):  # fmt: skip
        assert metric["provenance"]["nature"] == "synthetic"
        assert metric["provenance"]["origin"]
    assert "case card" in body["points_provenance"]["origin"]


def test_the_matrix_is_gzipped_and_open_to_every_demo_user(api: Api) -> None:
    response = api.client.get(
        "/api/portfolio/matrix", headers={"Accept-Encoding": "gzip", ACTOR_HEADER: "ahmed"}
    )
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    validate_body("portfolio_matrix", response.json())
    plain = api.client.get("/api/portfolio/matrix", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers and plain.json() == response.json()
    denied = api.client.get("/api/portfolio/matrix", headers={ACTOR_HEADER: "mallory"})
    assert denied.status_code == 403


def test_returns_of_the_shop_adapter_count_on_the_point(api: Api) -> None:
    """A return the shop adapter sent after the snapshot counts among the customer's returns,
    as on the case card."""
    before = _points(_matrix(api))
    lines = api.serving.lines
    known = lines.loc[
        lines["return_known_at"].isna() & lines["delivered"]
        & (lines["ordered_at"] > CLOCK_AT - timedelta(days=300))
    ]  # fmt: skip
    line = known.iloc[0]
    record = api.serving.adapter_return(
        "RET-ADAPTER-1", str(line["line_id"]), ReturnReason.SIZE, CLOCK_AT, None
    )

    async def run(conn: AsyncConnection) -> None:
        await insert_return(conn, record, source="adapter", received_at=CLOCK_AT)

    api.run(run)
    after = _points(_matrix(api))
    customer = str(line["customer_id"])
    assert after[customer]["returns_12m"] == before[customer]["returns_12m"] + 1
