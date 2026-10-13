"""The intake of a return (flow F1; ADR-15 section 4 "Return intake") and the case card
(US-302..US-309), the reference implementation of the route builders.

Intake, in this order:

1. the path, from the reason, before any score (rule 4, ADR-09): a defect or non-conformity
   follows the legal guarantee and is never scored;
2. on the withdrawal path, the request's risk as known when it arrived (Serving.request_score:
   the batch, or the request model run on request_features at the request instant);
3. the customer snapshot strictly before the request (MOD-08): evidence quality, value and the
   cluster; the thresholds of spec 8.6;
4. policy.engine.decide on the day of the intake, policy.legal_guard (in decide and again in the
   DecisionView model), the alternatives priced by policy.simulator;
5. the version stored with its inputs, their provenance, score factors and versions, the
   proposed cluster coupon application, and the decision_proposed audit entry (system);
6. the draft through llm/ (services.generation), stored and logged; until then the card says
   draft_status pending.

The API never decides beyond policy.engine and never predicts beyond the snapshot's models
(rule 1); the customer value is read for the quadrant only, never as a risk feature (rule 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Literal

import pandas as pd
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services import decisions as store
from apps.api.services.alternatives import CaseFacts, alternatives
from apps.api.services.audit import record as audit
from apps.api.services.generation import DraftInputs, Generator, load_draft, next_generation_id
from apps.api.services.generation import store as store_generation
from apps.api.services.ids import application_id, decision_id
from apps.api.services.provenance import from_file, provenance
from apps.api.services.seeds import current_assumptions, current_coupon_rule
from apps.api.serving import (
    BASE_TERMS,
    CustomerContext,
    RequestScore,
    ReturnRecord,
    Serving,
    ServingError,
)
from contracts.parquet import IDENTIFIER_KINDS
from contracts.sql import catalog_tasks, evidence_additions, link_disputes, return_requests
from llm.case import CLUSTER_FEATURES, ClusterSummary
from pipeline.anomalies.catalogue import weekly_series
from pipeline.features.evidence import EVIDENCE_THRESHOLDS
from pipeline.features.value import VALUE_ASSUMPTIONS
from policy.engine import decide
from policy.types import (
    CaseScores,
    Decision,
    EvidenceQuality,
    Path,
    ReturnReason,
    ReturnRequest,
)

CENT: Final = Decimal("0.01")
YEAR: Final = timedelta(days=365)
Source = Literal["store", "adapter"]


class IntakeError(RuntimeError):
    """A return the intake cannot decide: a bug upstream, never a decision (rule 1)."""


@dataclass(frozen=True)
class Proposal:
    """What the intake computes before it stores anything (batch_generate reuses it)."""

    record: ReturnRecord
    request: ReturnRequest
    score: RequestScore | None  # None on the guarantee path: never scored (rule 4)
    context: CustomerContext
    decision: Decision
    view: s.DecisionView
    alternatives: list[s.Alternative]
    raw_factors: list[dict[str, Any]] | None
    factors: list[s.ScoreContribution]  # shown on the card when the case is scored
    cluster: ClusterSummary | None  # the cluster the engine was given

    def draft_inputs(self) -> DraftInputs:
        return DraftInputs(
            view=self.view,
            request=self.request,
            factors=self.factors,
            cluster=self.cluster,
            customer_text=self.record.customer_text,
        )


def policy_request(record: ReturnRecord) -> ReturnRequest:
    return ReturnRequest(
        return_id=record.return_id,
        order_id=record.order_id,
        customer_id=record.customer_id,
        reason=record.reason,
        delivered_on=record.delivered_on,
        notice_on=record.notice_on,
        order_terms=record.order_terms,
    )


def _scored(path: Path, evidence: EvidenceQuality) -> bool:
    return path is Path.WITHDRAWAL and evidence is EvidenceQuality.SUFFICIENT


def cluster_summary(serving: Serving, context: CustomerContext) -> ClusterSummary | None:
    """The cluster the engine was given, with its size and the identifier kinds shared in it."""
    cluster_id = context.cluster_id
    if cluster_id is None:
        return None
    members = serving.cluster_members(cluster_id, context.as_of)
    links = serving.cluster_links(members, context.as_of)
    kinds = {k for row in links["kinds"] for k in row}
    return ClusterSummary(
        cluster_id, len(members), tuple(k for k in IDENTIFIER_KINDS if k in kinds)
    )


def inputs_provenance(
    serving: Serving, score: RequestScore | None, context: CustomerContext, decided_on: date
) -> s.Provenance:
    """The provenance of the engine inputs: the scores, the customer snapshot and the
    thresholds as known at the request, synthetic for Navigli Fashion (rule 11)."""
    base = serving.provenance["scores/request_scores"]
    risk = (
        f"risk {score.model_version} ({score.source}) at the request"
        if score is not None and score.risk is not None
        else "no risk (guarantee path, rule 4)"
    )
    snapshot = "computed at the request" if context.live else f"of {context.as_of:%Y-%m-%d}"
    origin = (
        f"engine inputs: {risk}; customer snapshot {snapshot} (value, evidence, cluster); risk "
        "threshold at precision >= 0.8 on validation, value threshold at the 70th percentile; "
        "Navigli Fashion synthetic store"
    )
    return provenance(origin, base.period_start, min(decided_on, base.period_end), base.nature)


def _facts(serving: Serving, record: ReturnRecord, context: CustomerContext) -> CaseFacts:
    line = serving.by_key("store/order_lines", record.line_id)
    price = Decimal(str(line["price_eur"])) if line else Decimal(0)
    cluster = context.cluster
    return CaseFacts(
        customer_id=record.customer_id,
        returns_12m=int(context.value["n_returns"]),
        kept_eur_12m=Decimal(str(context.value["kept_eur"])),
        cluster_id=context.cluster_id,
        cluster_size=int(cluster["cluster_size"]) if cluster else 1,
        item_price_eur=price,
        reason=record.reason,
        variant_anomaly=variant_anomaly(serving, record.variant_id, record.known_at) is not None,
    )


def propose(
    serving: Serving,
    record: ReturnRecord,
    *,
    created_at: datetime,
    assumptions: s.Assumptions,
    coupon_rule_version: str,
    version: int = 1,
) -> Proposal:
    """The engine's proposal for a return request (steps 1 to 4 of the module docstring)."""
    path = record.path  # rule 4: the path before any score
    score = serving.request_score(record) if path is Path.WITHDRAWAL else None
    if score is not None and score.path is not path:
        raise IntakeError(f"{record.return_id}: the score says {score.path}, the reason {path}")
    if score is not None and score.risk is None:
        raise IntakeError(f"{record.return_id}: a withdrawal request without a risk")
    context = serving.customer_context(record.customer_id, record.known_at)
    thresholds = serving.thresholds(context)
    decided_on = s.utc_day(created_at)
    request = policy_request(record)
    scores = CaseScores(
        risk=score.risk if score is not None and score.risk is not None else 0.0,
        value_eur=context.value_eur,
        evidence_quality=context.evidence_quality,
        cluster_id=context.cluster_id,
    )
    decision = decide(request, scores, BASE_TERMS, thresholds, decided_on, coupon_rule_version)
    if (
        score is not None
        and score.source == "batch"
        and not context.live
        and decision.quadrant != score.quadrant
    ):
        raise IntakeError(
            f"{record.return_id}: quadrant {decision.quadrant} differs from the batch scoring "
            f"{score.quadrant}: the thresholds or the snapshot are not the scoring's"
        )
    did = decision_id(record.return_id, version)
    proposed = s.levers_from_policy(decision.levers, BASE_TERMS)
    withdrawal = path is Path.WITHDRAWAL
    inputs = s.DecisionInputs(
        order_id=record.order_id,
        customer_id=record.customer_id,
        reason=record.reason,
        delivered_on=record.delivered_on,
        notice_on=record.notice_on,
        order_terms=s.Terms.from_policy(record.order_terms),
        base_terms=s.Terms.from_policy(BASE_TERMS),
        risk=scores.risk if withdrawal else None,
        value_eur=float(scores.value_eur) if withdrawal else None,
        evidence_quality=scores.evidence_quality,
        cluster_id=scores.cluster_id,
        thresholds=s.DecisionThresholds(
            risk=thresholds.risk, value_eur=float(thresholds.value_eur)
        ),
        provenance=inputs_provenance(serving, score, context, decided_on),
    )
    coupon_rule = None
    if decision.coupon_rule is not None:
        if context.cluster_id is None or decision.coupon_rule.rule_version is None:
            raise IntakeError(f"{record.return_id}: a coupon block without a cluster or a rule")
        coupon_rule = s.CouponRuleRef(
            rule_version=decision.coupon_rule.rule_version,
            application_id=application_id(record.return_id, version),
            cluster_id=context.cluster_id,
            affected_customer_ids=serving.cluster_members(context.cluster_id, context.as_of),
            confirmed_by=None,
            state="proposed",
            in_force=False,
        )
    view = s.DecisionView(
        decision_id=did,
        return_id=record.return_id,
        version=version,
        revision_trigger="initial",
        supersedes=None,
        superseded_by=None,
        created_at=created_at,
        as_of=created_at,
        decided_on=decided_on,
        state="proposed",
        path=decision.path,
        evidence_quality=decision.evidence_quality,
        quadrant=decision.quadrant,
        missing_evidence=context.missing_evidence,
        proposed_levers=proposed,
        levers=proposed,
        lever_ids=[x.lever_id() for x in proposed],
        requires_approval=decision.requires_approval,
        in_force=not decision.requires_approval,
        current_return_terms=s.Terms.from_policy(decision.current_return_terms),
        future_terms=s.Terms.from_policy(decision.levers.future_terms),
        refund_due_by=decision.refund_due_by,
        review_at=decision.review_on,
        approver=None,
        approved_at=None,
        override_reason=None,
        override_category=None,
        coupon_rule=coupon_rule,
        diminished_value_claim=None,
        inputs=inputs,
        rules_version=decision.rules_version,
        model_versions=serving.model_versions(),
        assumptions_version=assumptions.version,
        generation_id=None,
    )
    options = alternatives(did, decision, proposed, _facts(serving, record, context), assumptions)
    raw = [dict(f) for f in score.factors] if score is not None else None
    shown = _scored(path, decision.evidence_quality) and score is not None
    factors = [store.score_contribution(f) for f in score.factors] if shown and score else []
    return Proposal(
        record=record,
        request=policy_request(record),
        score=score,
        context=context,
        decision=decision,
        view=view,
        alternatives=options,
        raw_factors=raw,
        factors=factors,
        cluster=cluster_summary(serving, context),
    )


async def insert_return(
    conn: AsyncConnection, record: ReturnRecord, *, source: Source, received_at: datetime
) -> None:
    """The return_requests row (step 1 of F1): the request as the shop sent it."""
    await conn.execute(
        insert(return_requests).values(
            return_id=record.return_id,
            order_id=record.order_id,
            line_id=record.line_id,
            customer_id=record.customer_id,
            variant_id=record.variant_id,
            reason=record.reason.value,
            customer_text=record.customer_text,
            delivered_on=record.delivered_on,
            notice_on=record.notice_on,
            requested_at=record.requested_at,
            order_terms_snapshot_id=record.terms_snapshot_id,
            source=source,
            received_at=received_at,
        )
    )


async def intake(
    conn: AsyncConnection,
    serving: Serving,
    generator: Generator | None,
    record: ReturnRecord,
    *,
    at: datetime,
    source: Source,
    request_id: str | None = None,
) -> s.DecisionView:
    """Flow F1 for one return request: store it, decide (version 1), log, and draft unless
    `generator` is None (the draft then stays pending, for the generation queue)."""
    exists = await conn.scalar(
        select(return_requests.c.return_id).where(return_requests.c.return_id == record.return_id)
    )
    if exists is not None:
        raise IntakeError(f"{record.return_id} is already in the queue")
    await insert_return(conn, record, source=source, received_at=at)
    proposal = propose(
        serving,
        record,
        created_at=at,
        assumptions=await current_assumptions(conn),
        coupon_rule_version=await current_coupon_rule(conn),
    )
    view = proposal.view
    await store.insert_version(conn, view, proposal.alternatives, proposal.raw_factors)
    application = view.coupon_rule.application_id if view.coupon_rule else None
    await audit(
        conn,
        at=at,
        actor="system",
        action="decision_proposed",
        entity_type="decision",
        entity_id=view.decision_id,
        return_id=view.return_id,
        decision_id=view.decision_id,
        after=s.proposal_audit_state(view, application),
        rules_version=view.rules_version,
        model_versions=view.model_versions,
        assumptions_version=view.assumptions_version,
        request_id=request_id,
    )
    if generator is None:
        return view
    outcome = generator.generate(
        proposal.draft_inputs(),
        generation_id=await next_generation_id(conn, record.return_id),
        created_at=at,
    )
    await store_generation(conn, outcome, view, request_id=request_id)
    return view.model_copy(update={"generation_id": outcome.info.generation_id})


# ---- The case card (US-302) --------------------------------------------------------------------


def _metric(value: float | int, unit: s.Unit, prov: s.Provenance) -> s.Metric:
    return s.Metric(value=value, unit=unit, provenance=prov)


def _eur(x: Any) -> float:
    return float(Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP))


def evidence_view(serving: Serving, context: CustomerContext) -> s.EvidenceView:
    """The three components of evidence quality against their thresholds (US-209, US-307)."""
    ev, th = context.evidence, EVIDENCE_THRESHOLDS
    if str(ev["thresholds_version"]) != th.version:
        raise ServingError(f"evidence thresholds {ev['thresholds_version']}, code {th.version}")
    day = context.as_of.date()
    when = "computed at the request" if context.live else f"of {day}"
    base = serving.provenance["features/evidence_quality"]
    prov = from_file(base, f"evidence quality {th.version}, customer snapshot {when}", day)
    linked = ev["n_observations_linked"]
    observations = int(linked) if linked is not None else int(ev["n_observations"])
    coherence = ev["reason_coherence"]
    judged = coherence is not None and int(ev["n_reasons"]) >= th.min_reasons
    consistency_prov = (
        prov
        if judged
        else from_file(
            base,
            f"evidence quality {th.version}: fewer than {th.min_reasons} withdrawal returns, no "
            "reason coherence required (threshold 0); value 0 without any return",
            day,
        )
    )
    values: list[tuple[s.EvidenceComponentId, float | int, float | int, s.Unit, s.Provenance]] = [
        ("observations", observations, th.min_observations, "count", prov),
        (
            "identifier_completeness",
            float(ev["identifier_completeness"]),
            th.min_identifier_completeness,
            "ratio",
            prov,
        ),
        (
            "reason_consistency",
            float(coherence) if coherence is not None else 0.0,
            th.min_reason_coherence if judged else 0.0,
            "ratio",
            consistency_prov,
        ),
    ]
    components = [
        s.EvidenceComponent(
            id=cid,
            value=_metric(value, unit, p),
            threshold=_metric(limit, unit, p),
            passed=value >= limit,
        )
        for cid, value, limit, unit, p in values
    ]
    return s.EvidenceView(
        quality=context.evidence_quality,
        components=components,
        missing_evidence=context.missing_evidence,
    )


def value_view(serving: Serving, context: CustomerContext, threshold: float) -> s.ValueView:
    """V_c of spec 8.5 with its three parts, as pipeline.features.value computes it."""
    v, a = context.value, VALUE_ASSUMPTIONS
    if str(v["assumptions_version"]) != a.version:
        raise ServingError(f"value assumptions {v['assumptions_version']}, code {a.version}")
    kept = Decimal(str(v["kept_eur"]))
    margin = (a.gross_margin * kept).quantize(CENT, rounding=ROUND_HALF_UP)
    logistics = (a.logistics_cost_per_return_eur * int(v["n_returns"])).quantize(CENT)
    coupons = Decimal(str(v["discount_eur"]))
    value = Decimal(str(v["value_eur"]))
    if margin - logistics - coupons != value:
        raise ServingError(f"{context.customer_id}: value does not reconcile with its parts")
    day = context.as_of.date()
    base = serving.provenance["features/customer_value"]
    prov = from_file(base, f"customer value {a.version}, 12-month net margin to {day}", day)
    limit = from_file(base, f"value threshold: 70th percentile of customer value at {day}", day)
    return s.ValueView(
        value_eur=_metric(float(value), "eur", prov),
        threshold=_metric(threshold, "eur", limit),
        band="high" if float(value) >= threshold else "low",
        kept_margin_eur=_metric(float(margin), "eur", prov),
        return_logistics_eur=_metric(float(logistics), "eur", prov),
        coupon_discounts_eur=_metric(float(coupons), "eur", prov),
    )


def _point(label: s.StatementLabel, text: str, kind: s.EntityKind, entity: str) -> s.EvidencePoint:
    return s.EvidencePoint(label=label, statement=text, refs=[s.EntityRef(kind=kind, id=entity)])


def risk_view(
    view: s.DecisionView,
    factors: Sequence[s.ScoreContribution],
    context: CustomerContext,
    history: s.OrderHistory,
) -> s.RiskView:
    """The risk given to the engine, its top 3 contributions (not causes) and the favourable
    and unfavourable elements side by side (US-302, US-204)."""
    inputs = view.inputs
    if inputs.risk is None:
        raise ValueError("risk is shown only for a scored case")
    prov = inputs.provenance
    customer = inputs.customer_id
    cluster = inputs.cluster_id
    favourable: list[s.EvidencePoint] = []
    unfavourable: list[s.EvidencePoint] = []
    for f in factors:
        target: tuple[s.EntityKind, str] = (
            ("cluster", cluster)
            if cluster is not None and f.feature in CLUSTER_FEATURES
            else ("customer", customer)
        )
        point = _point("SCORE", f.phrase, *target)
        (unfavourable if f.contribution > 0 else favourable).append(point)
    lines, returned = int(history.lines.value), int(history.returned_lines.value)
    if lines:
        day = f"{view.as_of:%Y-%m-%d}"
        text = f"Kept {lines - returned} of {lines} order lines in the 12 months to {day}"
        (favourable if returned * 2 <= lines else unfavourable).append(
            _point("OBSERVED", text, "customer", customer)
        )
    residual = context.features["residual_z"] if context.features else None
    threshold = inputs.thresholds.risk
    return s.RiskView(
        score=_metric(inputs.risk, "probability", prov),
        threshold=_metric(threshold, "probability", prov),
        band="high" if inputs.risk >= threshold else "low",
        top_factors=list(factors),
        residual_z=None if residual is None else _metric(float(residual), "z_score", prov),
        favourable=favourable,
        unfavourable=unfavourable,
        model_version=view.model_versions.get("risk", "unknown"),
        provenance=prov,
    )


def _ts(value: Any) -> datetime:
    moment: datetime = pd.Timestamp(value).to_pydatetime()
    return moment


async def linked_accounts(
    conn: AsyncConnection, serving: Serving, cluster_id: str, customer_id: str, as_of: datetime
) -> s.LinkedAccountsView:
    """The cluster the engine was given: every account and link, kinds, dates, frequency and
    disputes (US-203, US-302, US-310). A link is a shared identifier, never a finding about who
    uses the accounts (rule 11: linked accounts, never the same person)."""
    members = serving.cluster_members(cluster_id, as_of)
    links = serving.cluster_links(members, as_of)
    cluster = serving.at("features/clusters", as_of).loc[customer_id]
    base = serving.provenance["features/links"]
    prov = from_file(base, f"account graph at {as_of:%Y-%m-%d}", as_of.date())
    disputes = {
        r["link_id"]: r for r in (await conn.execute(select(link_disputes))).mappings().all()
    }
    explained: dict[str, str] = {}
    for row in (await conn.execute(select(evidence_additions))).mappings().all():
        for link in row["target_link_ids"]:
            explained.setdefault(link, row["evidence_id"])
    accounts = [account_node(serving, c, customer_id, as_of) for c in members]
    views = []
    for r in links.itertuples(index=False):
        lid = s.link_id(r.customer_a, r.customer_b)
        dispute = disputes.get(lid)
        views.append(
            s.AccountLink(
                link_id=lid,
                customer_a=r.customer_a,
                customer_b=r.customer_b,
                kinds=list(r.kinds),
                weight=float(r.weight),
                weight_decayed=float(r.weight_decayed),
                first_shared_at=_ts(r.first_shared_at),
                last_shared_at=_ts(r.last_shared_at),
                n_shared_events=int(r.n_shared_events),
                max_sharers=int(r.max_sharers),
                disputed=bool(r.disputed) or dispute is not None,
                dispute=(
                    None
                    if dispute is None
                    else s.LinkDisputeInfo(
                        dispute_id=dispute["dispute_id"],
                        reason=dispute["reason"],
                        author=dispute["author"],
                        created_at=dispute["created_at"],
                    )
                ),
                explained_by_evidence_id=explained.get(lid),
            )
        )
    rate = cluster["cluster_return_rate"]
    return s.LinkedAccountsView(
        cluster_id=cluster_id,
        cluster_size=len(members),
        accounts=accounts,
        links=views,
        features=s.ClusterFeatures(
            n_distinct_cards=_metric(int(cluster["n_distinct_cards"]), "count", prov),
            creation_spread_days=_metric(float(cluster["creation_spread_days"]), "days", prov),
            welcome_coupons_per_account=_metric(
                float(cluster["welcome_coupons_per_account"]), "ratio", prov
            ),
            cluster_return_rate=None if pd.isna(rate) else _metric(float(rate), "ratio", prov),
            strong_id_share=_metric(float(cluster["strong_id_share"]), "ratio", prov),
        ),
        as_of=as_of,
        provenance=prov,
    )


def account_node(
    serving: Serving, customer_id: str, case_customer: str, as_of: datetime
) -> s.AccountNode:
    customers = serving.frame("store/customers")
    created = customers.loc[customers["customer_id"] == customer_id, "event_at"].iloc[0]
    coupons = serving.frame("store/coupons")
    welcome = coupons.loc[
        (coupons["customer_id"] == customer_id)
        & (coupons["kind"] == "welcome")
        & coupons["order_id"].notna()
        & (coupons["available_at"] <= pd.Timestamp(as_of))
    ]
    lines = serving.customer_lines(customer_id)
    mine = lines.loc[
        (lines["ordered_at"] > pd.Timestamp(as_of - YEAR))
        & (lines["ordered_at"] <= pd.Timestamp(as_of))
    ]
    returned = mine["return_known_at"] <= pd.Timestamp(as_of)
    return s.AccountNode(
        customer_id=customer_id,
        display_name=serving.display_names.get(customer_id),
        created_at=_ts(created),
        is_case_customer=customer_id == case_customer,
        welcome_coupon_redeemed_at=_ts(welcome["event_at"].min()) if len(welcome) else None,
        orders_12m=int(mine["order_id"].nunique()),
        returns_12m=int(returned.fillna(False).sum()),
    )


async def order_history(
    conn: AsyncConnection, serving: Serving, customer_id: str, as_of: datetime
) -> s.OrderHistory:
    """The customer's orders placed in the 12 months to as_of, every line, with the returns
    known at as_of (store and shop adapter)."""
    stamp = pd.Timestamp(as_of)
    lines = serving.customer_lines(customer_id)
    mine = lines.loc[
        (lines["ordered_at"] > pd.Timestamp(as_of - YEAR)) & (lines["ordered_at"] <= stamp)
    ]
    adapter = {
        r["line_id"]: r
        for r in (
            await conn.execute(
                select(return_requests).where(
                    (return_requests.c.customer_id == customer_id)
                    & (return_requests.c.source == "adapter")
                )
            )
        )
        .mappings()
        .all()
    }
    orders = serving.frame("store/orders").set_index("order_id")
    coupons = serving.frame("store/coupons")
    kinds = dict(zip(coupons["coupon_code"], coupons["kind"], strict=True))
    views = []
    for order_id, group in mine.groupby("order_id", sort=True):
        order = orders.loc[order_id]
        line_views = []
        for r in group.sort_values("line_id").itertuples(index=False):
            known = not pd.isna(r.return_known_at) and r.return_known_at <= stamp
            extra = adapter.get(r.line_id)
            return_id = r.return_id if known else (extra["return_id"] if extra else None)
            reason = r.reason if known else (extra["reason"] if extra else None)
            line_views.append(
                s.OrderLineView(
                    line_id=r.line_id,
                    variant_id=r.variant_id,
                    category=r.category,
                    size=None if pd.isna(r.size) else str(r.size),
                    price_eur=r.price_cents / 100,
                    returned=return_id is not None,
                    return_id=return_id,
                    return_reason=reason,
                )
            )
        delivered = group["delivered_at"].iloc[0]
        delivery_known = group["delivery_known_at"].iloc[0]
        coupon = order["coupon_code"]
        has_coupon = coupon is not None and not pd.isna(coupon)
        views.append(
            s.OrderView(
                order_id=str(order_id),
                placed_at=_ts(group["ordered_at"].iloc[0]),
                delivered_on=(
                    _ts(delivered).date()
                    if not pd.isna(delivered) and delivery_known <= stamp
                    else None
                ),
                total_eur=_eur(order["total_eur"]),
                coupon_code=str(coupon) if has_coupon else None,
                coupon_kind=kinds.get(coupon) if has_coupon else None,
                terms_snapshot_id=str(order["terms_snapshot_id"]),
                lines=line_views,
            )
        )
    all_lines = [x for o in views for x in o.lines]
    returned = sum(x.returned for x in all_lines)
    prov = from_file(
        serving.provenance["store/order_lines"],
        f"orders of {customer_id} in the 12 months to {as_of:%Y-%m-%d}",
        as_of.date(),
    )
    rate = round(returned / len(all_lines), 4) if all_lines else 0.0
    return s.OrderHistory(
        orders=views,
        lines=_metric(len(all_lines), "count", prov),
        returned_lines=_metric(returned, "count", prov),
        return_rate=_metric(rate, "ratio", prov),
        provenance=prov,
    )


def terms_snapshot(serving: Serving, record: ReturnRecord) -> s.TermsSnapshotView:
    """The terms shown at checkout and stored with the order of the return (ADR-05)."""
    row = serving.by_key("store/terms_snapshots", record.terms_snapshot_id)
    order = serving.by_key("store/orders", record.order_id)
    if row is None or order is None:
        raise ServingError(f"{record.return_id}: the terms snapshot of the order is unknown")
    shown = _ts(row["event_at"])
    placed = _ts(order["event_at"])
    source = str(row["source"])
    return s.TermsSnapshotView(
        terms_snapshot_id=str(row["terms_snapshot_id"]),
        order_id=record.order_id,
        customer_id=record.customer_id,
        source=source,
        quadrant_at_purchase=row["quadrant_at_purchase"],
        terms=s.Terms.from_policy(record.order_terms),
        base_terms=s.Terms.from_policy(BASE_TERMS),
        review_at=None,
        shown_at=shown,
        confirmed_at=max(shown, placed),
    )


def variant_anomaly(serving: Serving, variant_id: str, at: datetime) -> Mapping[str, Any] | None:
    """The flagged week of the variant at the last anomaly snapshot computed by `at` (the
    highest z), if any (US-208, US-606)."""
    frame = serving.frame("features/product_anomalies")
    known = frame.loc[frame["as_of"] <= pd.Timestamp(at)]
    if known.empty:
        return None
    latest = known.loc[known["as_of"] == known["as_of"].max()]
    rows = latest.loc[latest["variant_id"] == variant_id]
    if rows.empty:
        return None
    row = rows.sort_values(["z", "week"], ascending=[False, False]).iloc[0]
    return {str(k): v for k, v in row.to_dict().items()}


async def product_anomaly(
    conn: AsyncConnection, serving: Serving, variant_id: str, at: datetime
) -> s.ProductAnomaly | None:
    """The anomaly of the returned variant (spec 8.9) with its catalogue task, if one was
    opened, and the variant's weekly returns for the reason (the trend)."""
    row = variant_anomaly(serving, variant_id, at)
    if row is None:
        return None
    week = pd.Timestamp(row["week"]).date()
    anomaly = s.anomaly_id(variant_id, ReturnReason(row["reason"]), week)
    task = (
        (
            await conn.execute(
                select(catalog_tasks)
                .where(catalog_tasks.c.anomaly_id == anomaly)
                .order_by(catalog_tasks.c.created_at)
            )
        )
        .mappings()
        .first()
    )
    status: s.AnomalyStatus = "open"
    if task is not None:
        status = "resolved" if task["status"] == "done" else "task_opened"
    snap_at = s.utc_day(_ts(row["as_of"]))
    variant = serving.by_key("store/variants", variant_id) or {}
    prov = from_file(serving.provenance["features/product_anomalies"], None, snap_at)
    sales = serving.sales
    series = weekly_series(sales, variant_id, str(row["reason"]), _ts(row["as_of"]))
    start = pd.Timestamp(week, tz="UTC")
    in_week = sales.loc[
        (sales["variant_id"] == variant_id)
        & (sales["reason"] == row["reason"])
        & (sales["week"] == start)
        & (sales["return_known_at"] <= row["as_of"])
    ]
    returns = serving.frame("store/returns")
    ids = sorted(returns.loc[returns["line_id"].isin(set(in_week["line_id"])), "return_id"])
    trend = [
        s.WeeklyReturnRate(
            week=pd.Timestamp(r.week).date(),
            sales=int(r.n),
            returns=int(r.observed),
            return_rate=round(int(r.observed) / int(r.n), 4) if int(r.n) else 0.0,
        )
        for r in series.itertuples(index=False)
    ]
    return s.ProductAnomaly(
        anomaly_id=anomaly,
        variant_id=variant_id,
        product_id=str(variant.get("product_id", "")),
        category=str(variant.get("category", "")),
        brand=variant.get("brand"),
        reason=row["reason"],
        week=week,
        observed=_metric(int(row["observed"]), "count", prov),
        expected=_metric(float(row["expected"]), "expected_count", prov),
        z=_metric(float(row["z"]), "z_score", prov),
        sales=_metric(int(row["n"]), "count", prov),
        status=status,
        catalog_task_id=None if task is None else task["task_id"],
        return_ids=[str(i) for i in ids],
        trend=trend,
    )


def record_of(serving: Serving, ret: Mapping[str, Any]) -> ReturnRecord:
    """The ReturnRecord of a return_requests row (the request as stored)."""
    found = serving.store_return(ret["return_id"])
    if found is not None:
        return found
    return serving.adapter_return(
        ret["return_id"], ret["line_id"], ReturnReason(ret["reason"]), ret["requested_at"],
        ret["customer_text"],
    )  # fmt: skip


async def case_card(
    conn: AsyncConnection, serving: Serving, return_id: str, as_of: datetime
) -> s.CaseCard | None:
    """GET /api/returns/{return_id}: the latest version of the case and everything Sara needs
    to decide it (US-302), or None when the return is not in the queue."""
    ret = await store.return_row(conn, return_id)
    if ret is None:
        return None
    record = record_of(serving, ret)
    line = serving.by_key("store/order_lines", record.line_id) or {}
    price = Decimal(str(line.get("price_eur", 0)))
    found = await store.latest_view(conn, return_id, as_of, line_price=lambda _: price)
    if found is None:
        return None
    view, row = found
    context = serving.customer_context(record.customer_id, record.known_at)
    history = await order_history(conn, serving, record.customer_id, as_of)
    factors = store.factors_of(row)
    scored = _scored(view.path, view.evidence_quality)
    linked = (
        await linked_accounts(
            conn, serving, view.inputs.cluster_id, record.customer_id, context.as_of
        )
        if view.inputs.cluster_id is not None and not context.live
        else None
    )
    variant = serving.by_key("store/variants", record.variant_id) or {}
    threshold = (
        view.inputs.thresholds.value_eur
        if view.inputs.value_eur is not None
        else float(context.value_threshold)
    )
    draft = await load_draft(conn, view)
    rows = await store.decision_rows(conn, return_id)
    store_prov = from_file(
        serving.provenance["store/returns"], "Navigli Fashion synthetic store", as_of.date()
    )
    return s.CaseCard(
        return_id=return_id,
        order_id=record.order_id,
        customer_id=record.customer_id,
        display_name=serving.display_names.get(record.customer_id),
        as_of=as_of,
        item=s.ReturnedItem(
            line_id=record.line_id,
            variant_id=record.variant_id,
            product_id=str(variant.get("product_id", "")),
            category=str(variant.get("category", "")),
            brand=variant.get("brand"),
            size=line.get("size"),
            price_eur=_eur(line.get("price_eur", 0)),
        ),
        request=s.ReturnRequestView(
            reason=record.reason,
            customer_text=ret["customer_text"],
            requested_at=record.requested_at,
            delivered_on=record.delivered_on,
            notice_on=record.notice_on,
            days_since_delivery=(record.notice_on - record.delivered_on).days,
        ),
        path=view.path,
        evidence=evidence_view(serving, context),
        risk=risk_view(view, factors, context, history) if scored else None,
        value=value_view(serving, context, threshold),
        linked_accounts=linked,
        orders=history,
        order_terms=terms_snapshot(serving, record),
        decision=view,
        alternatives=store.alternatives_of(row),
        draft_status="ready" if draft is not None else "pending",
        draft=draft,
        product_anomaly=await product_anomaly(conn, serving, record.variant_id, as_of),
        revisions=[store.revision(r) for r in rows],
        provenance=store_prov,
    )
