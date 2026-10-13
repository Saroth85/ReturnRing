"""Engine decisions and LLM cases shared by the LLM tests: one scenario per kind of decision."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from llm.case import ClusterSummary, ScoreFactor, build_case
from llm.decision import ExpectedDecision, serialize_decision
from policy.engine import decide
from policy.types import (
    CaseScores,
    Decision,
    EvidenceQuality,
    ReturnReason,
    ReturnRequest,
    ReturnTerms,
)
from tests.factories import BASE, THRESHOLDS, TODAY, request, scores

ROOT = Path(__file__).resolve().parents[1]
POLICY_TEXT = "Navigli Fashion return policy (demo): returns within 30 days, free shipping."
MARCO = ClusterSummary("R-MARCO", 7, ("device", "address", "card"))
COUPONS = ScoreFactor(
    "cluster_welcome_coupons", 7, "7 linked accounts each used the welcome coupon", "cluster"
)
RETURNS_90D = ScoreFactor("returns_90d", 5, "5 returns in the last 90 days")
DAYS = ScoreFactor("days_since_delivery", 6, "returned 6 days after delivery", "return")
MISSING = ("fewer than 3 orders observed", "no identifier on file")


@dataclass(frozen=True)
class Scenario:
    request: ReturnRequest
    scores: CaseScores
    factors: tuple[ScoreFactor, ...] = ()
    cluster: ClusterSummary | None = None
    missing: tuple[str, ...] = ()


INSUFFICIENT = EvidenceQuality.INSUFFICIENT
SCENARIOS: dict[str, Scenario] = {
    "Q1": Scenario(request(), scores(0.1, "500"), (RETURNS_90D,)),
    "Q2": Scenario(request(), scores(0.2, "50"), (RETURNS_90D, DAYS)),
    "Q3": Scenario(request(), scores(0.9, "500"), (RETURNS_90D,)),
    "Q3_cluster": Scenario(
        request(), scores(0.9, "500", cluster="R-MARCO"), (COUPONS, RETURNS_90D), MARCO
    ),
    "Q4": Scenario(request(), scores(0.87, "42.10"), (RETURNS_90D, DAYS)),
    "Q4_cluster": Scenario(request(), scores(0.87, "42.10", cluster="R-MARCO"), (COUPONS,), MARCO),
    "Q4_negative_margin": Scenario(request(), scores(0.87, "-42.10"), (RETURNS_90D,)),
    "insufficient": Scenario(request(), scores(0.6, "50", INSUFFICIENT), missing=MISSING),
    "guarantee": Scenario(
        request(ReturnReason.DEFECTIVE), scores(0.9, "50", cluster="R-MARCO"), (COUPONS,), MARCO
    ),
    "guarantee_insufficient": Scenario(
        request(ReturnReason.NOT_AS_DESCRIBED), scores(0.9, "50", INSUFFICIENT), missing=MISSING
    ),
}


@dataclass(frozen=True)
class Built:
    decision: Decision
    case: dict[str, Any]
    expected: ExpectedDecision
    base: ReturnTerms = BASE


def make(
    name: str,
    *,
    customer_text: str | None = "Non mi sta bene.",
    refund_eur: Decimal | None = None,
    return_id: str = "RET-1",
    base: ReturnTerms = BASE,
) -> Built:
    """The scenario decided on a merchant base (the Navigli base by default); the order was
    bought under that base."""
    s = SCENARIOS[name]
    req = replace(s.request, return_id=return_id, order_terms=base)
    decision = decide(req, s.scores, base, THRESHOLDS, TODAY)
    case = build_case(
        decision,
        req,
        base,
        policy_text=POLICY_TEXT,
        scores=s.scores,
        factors=s.factors,
        cluster=s.cluster,
        missing_evidence=s.missing,
        customer_text=customer_text,
        refund_eur=refund_eur,
    )
    _, expected = serialize_decision(decision, base, refund_eur)
    return Built(decision, case, expected, base)


def example(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / f"contracts/examples/{name}.json").read_text())
    return data


def good_output() -> dict[str, Any]:
    """The llm_output example: valid for the golden Q4 case with a cluster."""
    return example("llm_output")
