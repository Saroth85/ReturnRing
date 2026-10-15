"""Decision versions in the database and their API views (ADR-15 sections 4 and 6; US-303,
US-604, US-607; rules 3, 4, 5 and 11).

A version is one decisions row: the engine inputs with their provenance and score factors, the
engine output, the alternatives, the human outcome and the version chain. Its view (DecisionView)
is computed when served, at `as_of`: in_force of the decision and of its cluster coupon
application, superseded_by, and the diminished-value claim of a confirmed inspection. Every view
passes policy.legal_guard in its model validator.

A claim belongs to the return (1.5.0, H13): confirmed on a version, it stays on every later
version of the return (a recompute keeps it, so the message of the act states the deduction),
and a return has at most one (uq_inspections_one_claim_per_return).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.prices import paid_provenance
from apps.api.services.provenance import provenance
from contracts.sql import coupon_rule_applications, decisions, inspections, return_requests

LinePrice = Callable[[str], Decimal | None]  # return_id -> price paid for the returned line

CLAIM_ORIGIN: Final = "diminished-value claim: rule {rule} on inspection {inspection}"


def in_force(
    state: str, requires_approval: bool, review_at: Any, as_of: datetime, superseded: bool
) -> bool:
    """DecisionView.in_force: approved, overridden, or proposed without approval; not
    superseded; before the review date, in UTC days (US-607: a restriction lapses)."""
    lapsed = review_at is not None and s.utc_day(as_of) >= review_at
    standing = state in ("approved", "overridden") or (
        state == "proposed" and not requires_approval
    )
    return standing and not lapsed and not superseded


def _levers(items: Sequence[Mapping[str, Any]]) -> list[s.Lever]:
    return [s.Lever.model_validate(dict(x)) for x in items]


def _money(value: Any) -> float | None:
    return None if value is None else float(Decimal(str(value)))


def decision_row(
    view: s.DecisionView,
    alternatives: Sequence[s.Alternative],
    score_factors: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """The decisions row of a version (generation_id is set when its draft is stored)."""
    inputs = view.inputs
    recommended = next(a for a in alternatives if a.recommended)
    rule = view.coupon_rule
    return {
        "decision_id": view.decision_id,
        "return_id": view.return_id,
        "version": view.version,
        "supersedes_decision_id": view.supersedes,
        "revision_trigger": view.revision_trigger,
        "customer_id": inputs.customer_id,
        "order_id": inputs.order_id,
        "risk": inputs.risk,
        "value_eur": None if inputs.value_eur is None else Decimal(str(inputs.value_eur)),
        "cluster_id": inputs.cluster_id,
        "risk_threshold": inputs.thresholds.risk,
        "value_threshold_eur": Decimal(str(inputs.thresholds.value_eur)),
        "inputs_provenance": inputs.provenance.model_dump(mode="json"),
        "snapshot_on": inputs.snapshot_on,
        "scored_on": inputs.scored_on,
        "score_factors": None if score_factors is None else [dict(f) for f in score_factors],
        "base_terms": inputs.base_terms.model_dump(),
        "order_terms": inputs.order_terms.model_dump(),
        "path": view.path.value,
        "evidence_quality": view.evidence_quality.value,
        "missing_evidence": list(view.missing_evidence),
        "quadrant": view.quadrant.value if view.quadrant else None,
        "proposed_levers": [x.model_dump() for x in view.proposed_levers],
        "levers": [x.model_dump() for x in view.levers],
        "requires_approval": view.requires_approval,
        "current_terms": view.current_return_terms.model_dump(),
        "future_terms": view.future_terms.model_dump(),
        "refund_due_by": view.refund_due_by,
        "decided_on": view.decided_on,
        "review_at": view.review_at,
        "alternatives": [a.model_dump(mode="json") for a in alternatives],
        "expected_net_benefit_eur": Decimal(str(recommended.net_benefit.total.value)),
        "coupon_rule_version": rule.rule_version if rule else None,
        "coupon_application_id": rule.application_id if rule else None,
        "state": view.state,
        "approver": view.approver,
        "approved_at": view.approved_at,
        "override_reason": view.override_reason,
        "override_category": view.override_category,
        "rules_version": view.rules_version,
        "model_versions": dict(view.model_versions),
        "assumptions_version": view.assumptions_version,
        "generation_id": None,
        "generation_path": None,
        "created_at": view.created_at,
    }


async def insert_version(
    conn: AsyncConnection,
    view: s.DecisionView,
    alternatives: Sequence[s.Alternative],
    score_factors: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Insert a version and, when its levers block the cluster's coupons, the proposed
    application of the general rule to the cluster (US-402, LEG-09)."""
    s.DecisionRecord(decision=view, alternatives=list(alternatives))  # the contract first
    await conn.execute(insert(decisions).values(**decision_row(view, alternatives, score_factors)))
    rule = view.coupon_rule
    if rule is not None:
        if view.review_at is None:
            raise ValueError("a coupon block applies until the review date of its decision")
        await conn.execute(
            insert(coupon_rule_applications).values(
                application_id=rule.application_id,
                rule_version=rule.rule_version,
                cluster_id=rule.cluster_id,
                decision_id=view.decision_id,
                affected_customer_ids=list(rule.affected_customer_ids),
                state=rule.state,
                proposed_at=view.created_at,
                confirmed_by=rule.confirmed_by,
                confirmed_at=view.approved_at if rule.confirmed_by else None,
                review_at=view.review_at,
                reversible=True,
            )
        )


async def _one(conn: AsyncConnection, query: Any) -> dict[str, Any] | None:
    row = (await conn.execute(query)).mappings().first()
    return None if row is None else dict(row)


async def decision_rows(conn: AsyncConnection, return_id: str) -> list[dict[str, Any]]:
    """Every version of a return's decision, in version order."""
    query = (
        select(decisions).where(decisions.c.return_id == return_id).order_by(decisions.c.version)
    )
    return [dict(r) for r in (await conn.execute(query)).mappings().all()]


async def decision_row_by_id(conn: AsyncConnection, decision_id: str) -> Mapping[str, Any] | None:
    return await _one(conn, select(decisions).where(decisions.c.decision_id == decision_id))


async def return_row(conn: AsyncConnection, return_id: str) -> Mapping[str, Any] | None:
    return await _one(conn, select(return_requests).where(return_requests.c.return_id == return_id))


def claim_provenance(rule_id: str, inspection_id: str, confirmed_at: datetime) -> s.Provenance:
    day = s.utc_day(confirmed_at)
    origin = CLAIM_ORIGIN.format(rule=rule_id, inspection=inspection_id)
    return provenance(origin, day, day, "synthetic")


async def _claim(
    conn: AsyncConnection, version: int, return_id: str, line_price: LinePrice | None
) -> s.ClaimView | None:
    """The claim of the return confirmed on this version or an earlier one (H13)."""
    row = await _one(
        conn,
        select(inspections)
        .join(decisions, decisions.c.decision_id == inspections.c.decision_id)
        .where(
            (inspections.c.return_id == return_id)
            & inspections.c.compensation_applied
            & (decisions.c.version <= version)
        )
        .order_by(inspections.c.confirmed_at, inspections.c.inspection_id),
    )
    if row is None:
        return None
    price = line_price(return_id) if line_price is not None else None
    if price is None:
        raise ValueError(f"{return_id}: the price of the returned line is unknown")
    prov = claim_provenance(row["compensation_rule_id"], row["inspection_id"], row["confirmed_at"])
    paid = paid_provenance(s.utc_day(row["confirmed_at"]))
    return s.ClaimView(
        inspection_id=row["inspection_id"],
        amount=s.Metric(value=_money(row["compensation_eur"]) or 0.0, unit="eur", provenance=prov),
        line_price=s.Metric(value=float(price), unit="eur", provenance=paid),
        photo_evidence_ids=[row["photo_id"]],
        confirmed_by=row["confirmed_by"],
        rule_id=row["compensation_rule_id"],
    )


async def view_of(
    conn: AsyncConnection,
    row: Mapping[str, Any],
    as_of: datetime,
    *,
    line_price: LinePrice | None = None,
) -> s.DecisionView:
    """The DecisionView of a decisions row, as served at `as_of`."""
    ret = await return_row(conn, row["return_id"])
    if ret is None:
        raise LookupError(f"{row['return_id']}: return request missing")
    successor = await conn.scalar(
        select(decisions.c.decision_id).where(
            decisions.c.supersedes_decision_id == row["decision_id"]
        )
    )
    standing = in_force(
        row["state"], row["requires_approval"], row["review_at"], as_of, successor is not None
    )
    coupon_rule = None
    if row["coupon_application_id"] is not None:
        app = await _one(
            conn,
            select(coupon_rule_applications).where(
                coupon_rule_applications.c.application_id == row["coupon_application_id"]
            ),
        )
        if app is None:
            raise LookupError(f"{row['coupon_application_id']}: coupon application missing")
        coupon_rule = s.CouponRuleRef(
            rule_version=app["rule_version"],
            application_id=app["application_id"],
            cluster_id=app["cluster_id"],
            affected_customer_ids=list(app["affected_customer_ids"]),
            confirmed_by=app["confirmed_by"],
            state=app["state"],
            in_force=(
                app["state"] == "confirmed"
                and standing
                and row["review_at"] is not None
                and s.utc_day(as_of) < row["review_at"]
            ),
        )
    levers = _levers(row["levers"])
    inputs = s.DecisionInputs(
        order_id=row["order_id"],
        customer_id=row["customer_id"],
        reason=ret["reason"],
        delivered_on=ret["delivered_on"],
        notice_on=ret["notice_on"],
        order_terms=s.Terms.model_validate(row["order_terms"]),
        base_terms=s.Terms.model_validate(row["base_terms"]),
        risk=row["risk"],
        value_eur=_money(row["value_eur"]),
        evidence_quality=row["evidence_quality"],
        cluster_id=row["cluster_id"],
        thresholds=s.DecisionThresholds(
            risk=row["risk_threshold"], value_eur=_money(row["value_threshold_eur"]) or 0.0
        ),
        provenance=s.Provenance.model_validate(row["inputs_provenance"]),
        snapshot_on=row["snapshot_on"],
        scored_on=row["scored_on"],
    )
    return s.DecisionView(
        decision_id=row["decision_id"],
        return_id=row["return_id"],
        version=row["version"],
        revision_trigger=row["revision_trigger"],
        supersedes=row["supersedes_decision_id"],
        superseded_by=successor,
        created_at=row["created_at"],
        as_of=as_of,
        decided_on=row["decided_on"],
        state=row["state"],
        path=row["path"],
        evidence_quality=row["evidence_quality"],
        quadrant=row["quadrant"],
        missing_evidence=list(row["missing_evidence"]),
        proposed_levers=_levers(row["proposed_levers"]),
        levers=levers,
        lever_ids=[x.lever_id() for x in levers],
        requires_approval=row["requires_approval"],
        in_force=standing,
        current_return_terms=s.Terms.model_validate(row["current_terms"]),
        future_terms=s.Terms.model_validate(row["future_terms"]),
        refund_due_by=row["refund_due_by"],
        review_at=row["review_at"],
        approver=row["approver"],
        approved_at=row["approved_at"],
        override_reason=row["override_reason"],
        override_category=row["override_category"],
        coupon_rule=coupon_rule,
        diminished_value_claim=await _claim(conn, row["version"], row["return_id"], line_price),
        inputs=inputs,
        rules_version=row["rules_version"],
        model_versions=dict(row["model_versions"]),
        assumptions_version=row["assumptions_version"],
        generation_id=row["generation_id"],
    )


def alternatives_of(row: Mapping[str, Any]) -> list[s.Alternative]:
    return [s.Alternative.model_validate(a) for a in row["alternatives"]]


def factors_of(row: Mapping[str, Any]) -> list[s.ScoreContribution]:
    """The score factors given with a version, as the card shows them (ScoreContribution)."""
    return [score_contribution(f) for f in row["score_factors"] or ()]


def score_contribution(factor: Mapping[str, Any]) -> s.ScoreContribution:
    """A shap_top3 entry of ml.risk.explain as a ScoreContribution: its value, its signed
    contribution and its fixed English phrase (a contribution to the score, not a cause)."""
    contribution = float(factor["contribution"])
    return s.ScoreContribution(
        feature=str(factor["feature"]),
        feature_value=factor["value"],
        contribution=contribution,
        direction="raises" if contribution > 0 else "lowers",
        phrase=str(factor["phrase_en"]),
    )


async def latest_view(
    conn: AsyncConnection, return_id: str, as_of: datetime, line_price: LinePrice | None = None
) -> tuple[s.DecisionView, Mapping[str, Any]] | None:
    rows = await decision_rows(conn, return_id)
    if not rows:
        return None
    return await view_of(conn, rows[-1], as_of, line_price=line_price), rows[-1]


def revision(view_row: Mapping[str, Any]) -> s.RevisionSummary:
    """The summary of a version for the case card (F7: every version stays in the log)."""
    levers = _levers(view_row["levers"])
    risk = view_row["risk"]
    return s.RevisionSummary(
        decision_id=view_row["decision_id"],
        version=view_row["version"],
        trigger=view_row["revision_trigger"],
        created_at=view_row["created_at"],
        quadrant=view_row["quadrant"],
        risk=(
            None
            if risk is None
            else s.Metric(
                value=risk,
                unit="probability",
                provenance=s.Provenance.model_validate(view_row["inputs_provenance"]),
            )
        ),
        lever_ids=[x.lever_id() for x in levers],
        state=view_row["state"],
    )
