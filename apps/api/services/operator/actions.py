"""A person's act on a case: POST /api/returns/{return_id}/decision (US-303, US-306, US-607;
ADR-15 section 4 "Decisions"; rules 1, 3, 4, 5, 10 and 11).

approve, override and reject act on the proposed latest version, which keeps its number:

- approve confirms the engine's proposal (levers, if sent, must equal it: 409) with
  policy.engine.approve; the approver also confirms the proposed cluster coupon application;
- override sets other levers with a reason and a category. policy.legal_guard judges the
  levers chosen (to_policy_decision: an approved decision without the engine's quadrant) and
  refuses a violation with its codes (422 legal_guard). A restrictive choice needs approval and
  has the review date of the version (decided_on + REVIEW_DAYS). A coupon block applies to a
  cluster only on the engine's proposal (LEG-09): an override keeps it or drops it, never adds
  one (422);
- reject leaves no lever in force; the order keeps its terms and the return is never refused.

A restriction is approved, and its coupon block confirmed, before its review date: a proposal
that reached it is stale (409). Each of the three writes, in one transaction with the version,
one audit entry (before: the engine's proposal) and one outbox message composed by
llm.phrasebook for the decision as it stands: the sentences the person chose, or the draft's
when they are allowed for it, or the fallback of llm.templates (rule 10).

lift and renew review an approved or overridden restriction, the latest version. Each creates
the next version (trigger review) from policy.engine.decide on the latest scores at the day of
the review (services.cases.propose on the account graph as people left it):

- lift: overridden to the standard levers, before or after the review date; the coupon block
  of the restriction, if confirmed, is reversed by the person, with the lift's reason;
- renew: at or after the review date (409 before: no early renewal, no permanent label), the
  person approves the fresh proposal, which must be a restriction (409 otherwise: lift it); a
  coupon block of it gets a new application confirmed by the person.

Neither sends a message (ADR-15 section 12, E27): the customer sees the terms at checkout.

Contract 1.5.0 (ADR-15 section 15):

- a proposal whose review window has passed (a restriction on or after its review date, or a
  restrictive override of a version decided REVIEW_DAYS ago) is stale: the act answers 409
  StaleProposal and the route decides the case again (trigger stale_proposal, recompute_stale),
  so the next attempt acts on a fresh proposal (H16);
- approve, override and reject send a message: on a case whose refund deadline has passed the
  act answers 409 instead of promising a past date (H1); the queue flags such a case
  (refund_overdue). A stale proposal is decided again first: its refund is always overdue too
  (14 days from the notice come before a review date 90 days after the decision).

Contract 1.6.0 (ADR-15 section 16): after the refund deadline a person can still reject the
proposal or override it to the standard levers, with no message (outbox_message null,
s.late_without_message): neither sets terms to announce, and the refund is owed all the same.
Approve and an override to other levers still answer 409. A guarantee case states no refund
deadline, so its acts are never refused for one (rule 4, NFR-17). 1.7.0: a case with a
diminished-value claim too (its refund and deduction are then stated outside ReturnRing), and
the queue keeps every such case refund_overdue, since no message has stated the refund.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import ApiError, conflict, invalid_request, legal_guard
from apps.api.services import decisions as store
from apps.api.services.audit import record
from apps.api.services.generation import load_draft
from apps.api.services.operator import coupons
from apps.api.services.operator.graph import GraphView, exclusions
from apps.api.services.operator.recompute import Recomputed, recompute_version
from apps.api.services.operator.shown import Shown, shown
from apps.api.services.operator.versions import next_version
from apps.api.services.outbox import check_refund_due, compose, queue
from apps.api.services.seeds import current_assumptions, current_coupon_rule
from apps.api.serving import Serving
from contracts.sql import decisions
from llm.decision import serialize_decision
from llm.phrasebook import context_of, selection_problems
from policy.engine import REVIEW_DAYS, approve
from policy.legal_guard import check, is_restrictive
from policy.types import CouponRuleApplication as PolicyCouponRule
from policy.types import DecisionState

PROPOSAL_ACTIONS: Final = ("approve", "override", "reject")
# The decisions columns a person's act on a proposed version changes.
ACTED_COLUMNS: Final = (
    "state",
    "levers",
    "requires_approval",
    "future_terms",
    "review_at",
    "approver",
    "approved_at",
    "override_reason",
    "override_category",
    "coupon_rule_version",
    "coupon_application_id",
)


async def act(
    conn: AsyncConnection,
    serving: Serving,
    return_id: str,
    body: s.DecisionRequest,
    now: datetime,
    request_id: str | None = None,
) -> s.DecisionResult:
    """The act of `body` on the case `return_id`, at `now`, in the caller's transaction."""
    case = await shown(conn, serving, return_id, body.decision_id, now, lock=True)
    if body.action in PROPOSAL_ACTIONS:
        return await _on_proposal(conn, case, body, now, request_id)
    return await _review(conn, serving, case, body, now, request_id)


def revalidate(view: s.DecisionView, **updates: Any) -> s.DecisionView:
    """The view with `updates`, validated again (the engine invariants and the legal guard)."""
    return s.DecisionView.model_validate({**view.model_dump(), **updates})


class StaleProposal(ApiError):
    """409: a restriction proposal at or past its review date (US-607); the route decides the
    case again (recompute_stale) before it answers."""

    def __init__(self, view: s.DecisionView, review_at: Any) -> None:
        self.decision_id = view.decision_id
        super().__init__(
            409,
            "conflict",
            f"{view.decision_id} reached its review date, {review_at.isoformat()}: a restriction "
            "is approved before it; the proposal is stale and the case is decided again (US-607)",
        )

    def recomputed(self, decision_id: str) -> None:
        self.message = (
            f"{self.decision_id} was stale (its review date passed before anyone acted on it): "
            f"the case is decided again as {decision_id}; act on that version (US-607)"
        )


def _not_stale(view: s.DecisionView, review_at: Any, now: datetime) -> None:
    if review_at is not None and s.utc_day(now) >= review_at:
        raise StaleProposal(view, review_at)


def is_stale(view: s.DecisionView, now: datetime) -> bool:
    """A proposal decided REVIEW_DAYS ago or more: a restriction can no longer be approved or
    chosen on it before its review date."""
    return view.state == "proposed" and s.utc_day(now) >= view.decided_on + timedelta(
        days=REVIEW_DAYS
    )


async def recompute_stale(
    conn: AsyncConnection,
    serving: Serving,
    return_id: str,
    decision_id: str,
    now: datetime,
    request_id: str | None,
) -> Recomputed | None:
    """The next version of a stale proposal (trigger stale_proposal, by the system), or None
    when the case moved on meanwhile; services.operator.recompute.store_draft adds its draft."""
    case = await shown(conn, serving, return_id, None, now, lock=True)
    if case.view.decision_id != decision_id or not is_stale(case.view, now):
        return None
    made = [e for e in await exclusions(conn) if e.created_at <= now]
    return await recompute_version(
        conn, serving, case, trigger="stale_proposal", made_before=made, made_after=made,
        author="system", reason="the proposal reached its review date before anyone acted",
        act_entry=None, now=now, request_id=request_id,
    )  # fmt: skip


def _levers_chosen(levers: Sequence[s.Lever], base: Any) -> list[s.Lever]:
    """The levers of an override as the contract lists them (differences from the standard, in
    catalogue order); a lever given twice or equal to its standard value is refused (422)."""
    try:
        return s.levers_from_policy(s.to_policy_levers(levers, base), base)
    except ValueError as exc:
        raise invalid_request(f"the levers chosen are invalid: {exc}") from None


async def _on_proposal(
    conn: AsyncConnection,
    case: Shown,
    body: s.DecisionRequest,
    now: datetime,
    request_id: str | None,
) -> s.DecisionResult:
    view = case.view
    if view.state != "proposed":
        raise conflict(
            f"{view.decision_id} is already {view.state}: only a proposed version can be "
            "approved, overridden or rejected"
        )
    base = view.inputs.base_terms.to_policy()
    decision, request = s.to_policy_decision(view)
    approver = body.approver
    rule = view.coupon_rule
    if body.action == "approve":
        if body.levers is not None and list(body.levers) != list(view.proposed_levers):
            raise conflict(
                f"approve confirms the proposal of {view.decision_id}: the levers sent differ from "
                "it (override changes them)"
            )
        _not_stale(view, view.review_at, now)
        approve(decision, request, base, approver)  # policy.engine: the guard on the approval
        levers, needs, review = list(view.proposed_levers), view.requires_approval, view.review_at
        kept = rule
    else:
        levers = _levers_chosen(body.levers or [], base) if body.action == "override" else []
        policy_levers = s.to_policy_levers(levers, base)
        needs = is_restrictive(policy_levers, base)
        review = view.decided_on + timedelta(days=REVIEW_DAYS) if needs else None
        _not_stale(view, review, now)
        blocks = policy_levers.cluster_coupons_disabled
        version = rule.rule_version if rule else await current_coupon_rule(conn)
        candidate = replace(
            decision,
            state=DecisionState.APPROVED if body.action == "override" else DecisionState.REJECTED,
            quadrant=None,
            levers=policy_levers,
            requires_approval=needs,
            review_on=review,
            approver=approver,
            coupon_rule=(
                PolicyCouponRule(
                    rule_version=version, confirmed_by=approver, cluster_id=view.inputs.cluster_id
                )
                if blocks
                else None
            ),
        )
        violations = check(candidate, request, base)
        if violations:
            raise legal_guard(violations)
        if blocks and rule is None:
            raise invalid_request(
                "an override cannot add a cluster coupon block the engine did not propose: the "
                "general coupon rule applies to the cluster of a Q4 case on its proposal (LEG-09)"
            )
        kept = rule if blocks else None
    state = {"approve": "approved", "override": "overridden", "reject": "rejected"}[body.action]
    standing = store.in_force(state, needs, review, now, False)
    acted = revalidate(
        view,
        state=state,
        levers=[x.model_dump() for x in levers],
        lever_ids=[x.lever_id() for x in levers],
        requires_approval=needs,
        review_at=review,
        future_terms=s.Terms.from_policy(s.to_policy_levers(levers, base).future_terms),
        approver=approver,
        approved_at=now,
        as_of=now,
        override_reason=body.override_reason,
        override_category=body.override_category,
        in_force=standing,
        coupon_rule=(
            None
            if kept is None
            else {
                **kept.model_dump(),
                "confirmed_by": approver,
                "state": "confirmed",
                "in_force": standing and review is not None and s.utc_day(now) < review,
            }
        ),
    )
    silent = s.late_without_message(acted)  # a late reject or standard terms (1.6.0)
    if not silent:
        check_refund_due(view, now)  # its message states the refund deadline (H1)
    full = store.decision_row(acted, store.alternatives_of(case.row), case.row["score_factors"])
    result = await conn.execute(
        update(decisions)
        .where((decisions.c.decision_id == view.decision_id) & (decisions.c.state == "proposed"))
        .values(**{k: full[k] for k in ACTED_COLUMNS})
    )
    if result.rowcount != 1:
        raise conflict(f"{view.decision_id} was decided meanwhile")
    if acted.coupon_rule is not None:
        await coupons.confirm(conn, acted.coupon_rule.application_id, approver, now)
    row = await store.decision_row_by_id(conn, view.decision_id)
    assert row is not None
    served = await store.view_of(conn, row, now, line_price=case.line_price)
    entry = await record(
        conn,
        at=now,
        actor=approver,
        action=s.result_action(served),
        entity_type="decision",
        entity_id=served.decision_id,
        return_id=served.return_id,
        decision_id=served.decision_id,
        reason=served.override_reason,
        before=s.proposal_audit_state(view, rule.application_id if rule else None),
        after=s.DecisionAuditState.from_view(served),
        rules_version=served.rules_version,
        model_versions=served.model_versions,
        assumptions_version=served.assumptions_version,
        request_id=request_id,
    )
    message = None if silent else await _message(conn, view, served, body, now, request_id)
    application = (
        await coupons.application(conn, served.coupon_rule.application_id, now)
        if served.coupon_rule is not None
        else None
    )
    return s.DecisionResult(
        decision=served,
        superseded=None,
        audit_entry=entry,
        outbox_message=message,
        coupon_application=application,
    )


async def _message(
    conn: AsyncConnection,
    proposed: s.DecisionView,
    acted: s.DecisionView,
    body: s.DecisionRequest,
    now: datetime,
    request_id: str | None,
) -> s.OutboxMessage:
    """The message of the act: the sentences chosen (refused when not allowed for the decision
    as it stands), else the draft's selection when allowed, else the fallback selection. With a
    diminished-value claim it states the refund the claim leaves and the deduction."""
    assert body.message is not None
    claim = acted.diminished_value_claim
    refund = claim.refund() if claim is not None else None
    amount = Decimal(str(refund.value)) if refund is not None else None
    decision, _ = s.to_policy_decision(acted)
    base = acted.inputs.base_terms.to_policy()
    chosen = body.message.sentence_ids
    draft = await load_draft(conn, proposed)
    preferred = list(draft.message.sentence_ids) if draft is not None else None
    if chosen:
        _, expected = serialize_decision(decision, base, amount)
        problems = selection_problems(chosen, context_of(expected))
        if problems:
            raise ApiError(
                422,
                "invalid_request",
                "the sentences chosen are not allowed for the decision acted on (ADR-14 "
                "decision 8)",
                [("sentence_ids", p) for p in problems],
            )
        ids = list(chosen)
    else:
        ids = s.default_selection_for(decision, base, preferred, amount)
    from_draft = not chosen and preferred is not None and ids == preferred
    message = await compose(
        conn,
        acted,
        lang=body.message.lang,
        created_by=body.approver,
        created_at=now,
        sentence_ids=ids,
        generation_id=proposed.generation_id if from_draft else None,
        refund=refund,
    )
    return await queue(conn, message, acted, request_id)


async def _review(
    conn: AsyncConnection,
    serving: Serving,
    case: Shown,
    body: s.DecisionRequest,
    now: datetime,
    request_id: str | None,
) -> s.DecisionResult:
    old = case.view
    if old.state not in ("approved", "overridden") or not old.requires_approval:
        raise conflict(
            f"{old.decision_id} is a {old.state} version without a restriction: lift and renew "
            "review an approved or overridden restriction (US-607)"
        )
    assert old.review_at is not None
    if body.action == "renew" and s.utc_day(now) < old.review_at:
        raise conflict(
            f"{old.decision_id} cannot be renewed before its review date, "
            f"{old.review_at.isoformat()} (US-607)"
        )
    graph = GraphView.of(serving, [e for e in await exclusions(conn) if e.created_at <= now])
    following = await asyncio.to_thread(
        next_version,
        graph,
        case.record,
        created_at=now,
        assumptions=await current_assumptions(conn),
        coupon_rule_version=await current_coupon_rule(conn),
        version=old.version + 1,
    )
    proposal = following.proposal
    fresh = proposal.view
    base = fresh.inputs.base_terms.to_policy()
    approver = body.approver
    common: dict[str, Any] = {
        "revision_trigger": "review",
        "supersedes": old.decision_id,
        "approver": approver,
        "approved_at": now,
        "in_force": True,
    }
    if body.action == "renew":
        if not fresh.requires_approval:
            quadrant = fresh.quadrant.value if fresh.quadrant else "no quadrant"
            raise conflict(
                f"the engine's proposal for {old.return_id} at the review is not a restriction "
                f"({quadrant}): there is nothing to renew; lift the restriction"
            )
        approve(proposal.decision, proposal.request, base, approver)
        rule = following.coupon_rule
        renewed = None if rule is None else {
            **rule, "confirmed_by": approver, "state": "confirmed", "in_force": True
        }  # fmt: skip
        new = following.view(**common, state="approved", coupon_rule=renewed)
    else:
        standard = s.to_policy_levers([], base)
        new = following.view(
            **common,
            state="overridden",
            levers=[],
            lever_ids=[],
            requires_approval=False,
            review_at=None,
            future_terms=s.Terms.from_policy(standard.future_terms).model_dump(),
            override_reason=body.override_reason,
            override_category=body.override_category,
            coupon_rule=None,
        )
    await store.insert_version(conn, new, following.alternatives, proposal.raw_factors)
    lifted_block = old.coupon_rule if body.action == "lift" else None
    reversed_now = False
    if lifted_block is not None:
        assert body.override_reason is not None
        reversed_now = await coupons.reverse(
            conn, lifted_block.application_id, by=approver, at=now, reason=body.override_reason
        )
    superseded = await store.view_of(conn, case.row, now, line_price=case.line_price)
    row = await store.decision_row_by_id(conn, new.decision_id)
    assert row is not None
    served = await store.view_of(conn, row, now, line_price=case.line_price)
    entry = await record(
        conn,
        at=now,
        actor=approver,
        action=s.result_action(served),
        entity_type="decision",
        entity_id=served.decision_id,
        return_id=served.return_id,
        decision_id=served.decision_id,
        reason=served.override_reason,
        before=s.DecisionAuditState.from_view(superseded),
        after=s.DecisionAuditState.from_view(served),
        rules_version=served.rules_version,
        model_versions=served.model_versions,
        assumptions_version=served.assumptions_version,
        request_id=request_id,
    )
    application = None
    if lifted_block is not None:
        if reversed_now:
            assert body.override_reason is not None
            await coupons.log_reversal(
                conn, lifted_block.application_id, by=approver, at=now,
                reason=body.override_reason, return_id=old.return_id,
                decision_id=old.decision_id, request_id=request_id,
            )  # fmt: skip
        application = await coupons.application(conn, lifted_block.application_id, now)
    elif served.coupon_rule is not None:
        application = await coupons.application(conn, served.coupon_rule.application_id, now)
    return s.DecisionResult(
        decision=served,
        superseded=superseded,
        audit_entry=entry,
        outbox_message=None,
        coupon_application=application,
    )
