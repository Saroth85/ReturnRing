"""Favourable evidence and link disputes: a case recomputed (US-310, US-203, flow F7; ADR-15
section 4 "Recompute"; rules 1, 5, 7, 11 and 12).

- POST /api/returns/{return_id}/evidence records the evidence on the version shown. Evidence of
  the kinds household_members, shared_payment_explained and collective_address names active
  links of the cluster given to the engine (422 otherwise): those links leave the cluster
  features. customer_contact and other change no feature. The case is recomputed.
- POST /api/links/{link_id}/dispute marks a link of features/links (404 if there is none; one
  dispute per link and none on a link already explained: 409) and recomputes every queued case
  whose cluster, as given to the engine, holds the link. A dispute from a case card names the
  case and the version shown (409 stale_decision), and the link must be in its cluster (422).

A recompute is a new version (version + 1, trigger evidence_added or link_disputed, decided on
the day of the act): services.cases.propose on the account graph as people left it
(services.operator.graph, pipeline.graph.dispute_link), so the risk is the request model's on
the new cluster features (ml/, rule 1) and the decision policy.engine's. The response shows the
case before and after (the risk given to the engine with the provenance of its inputs, the
approval flag, the evidence, the cluster size and the active links), the draft llm/ makes again
for the new version, and the cluster coupon application of the version superseded when a person
had confirmed it: reversed in the same transaction by the author of the act, with their note or
reason. A new proposal that blocks the cluster's coupons waits for a person again (rule 5).

The audit log keeps every version (F7): the act (evidence_added or link_disputed, by its
author, with the reason), case_recomputed by the system (before and after: two versions), the
reversal of a coupon application (by the author) and the generation of the new draft.

Contract 1.5.0 (ADR-15 section 15):

- no model call inside a transaction (H20): the act and the new versions are written in one
  transaction (recompute_version), the drafts made on the runtime's model-call pool, then stored
  in a second transaction (finish) that builds the responses;
- a dispute locks the named case and every case it recomputes in one sorted pass, so two
  disputes of one cluster wait for each other instead of deadlocking, and a second dispute of
  the same link is a 409, also when two arrive together (H22, H10);
- ids come from the id counters (EV-, LD-), never count(*) + 1;
- the provenance of a response says the guarantee path has no request model (H4).
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

import pandas as pd
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import ApiError, conflict, invalid_request, not_found
from apps.api.services import decisions as store
from apps.api.services.audit import record as audit
from apps.api.services.cases import Proposal, record_of
from apps.api.services.generation import DraftInputs, Generator, Outcome
from apps.api.services.generation import store as store_generation
from apps.api.services.ids import allocate
from apps.api.services.operator import coupons
from apps.api.services.operator.graph import (
    Exclusion,
    GraphView,
    exclusions,
    version_exclusions,
)
from apps.api.services.operator.queue import latest_rows
from apps.api.services.operator.shown import Shown, return_of, shown
from apps.api.services.operator.versions import next_version
from apps.api.services.provenance import from_file, provenance
from apps.api.services.seeds import current_assumptions, current_coupon_rule
from apps.api.serving import Serving
from contracts.sql import evidence_additions, link_disputes
from contracts.vocab import LINK_EXPLAINING_EVIDENCE
from policy.types import EvidenceQuality
from policy.types import Path as ReturnPath

Trigger = Literal["evidence_added", "link_disputed", "stale_proposal"]
GRAPH_NOTE: Final = "account graph without the {n} links disputed or explained by people (US-310)"


def _ts(value: Any) -> datetime:
    moment: datetime = pd.Timestamp(value).to_pydatetime()
    return moment


async def _next_id(conn: AsyncConnection, table: Any, prefix: str) -> str:
    """EV-<n>, LD-<n>: the next number of the id counter of `prefix` (H10)."""
    return f"{prefix}-{await allocate(conn, prefix, table):06d}"


def active_links(
    graph: Serving, cluster_id: str | None, as_of: datetime, excluded: Collection[str]
) -> int:
    """The links of a cluster at `as_of` neither disputed nor explained."""
    if cluster_id is None:
        return 0
    links = graph.cluster_links(graph.cluster_members(cluster_id, as_of), as_of)
    return sum(
        1
        for a, b, disputed in zip(links["customer_a"], links["customer_b"], links["disputed"],
                                  strict=True)
        if not disputed and s.link_id(a, b) not in excluded
    )  # fmt: skip


def _scored(view: s.DecisionView) -> bool:
    return view.path is ReturnPath.WITHDRAWAL and (
        view.evidence_quality is EvidenceQuality.SUFFICIENT
    )


def _snapshot(
    view: s.DecisionView,
    factors: Sequence[s.ScoreContribution],
    cluster_size: int | None,
    active: int,
) -> s.CaseSnapshot:
    risk = view.inputs.risk
    return s.CaseSnapshot(
        decision_id=view.decision_id,
        version=view.version,
        state=view.state,
        quadrant=view.quadrant,
        evidence_quality=view.evidence_quality,
        risk=(
            None
            if risk is None
            else s.Metric(value=risk, unit="probability", provenance=view.inputs.provenance)
        ),
        top_factors=list(factors),
        lever_ids=view.lever_ids,
        requires_approval=view.requires_approval,
        cluster_size=cluster_size,
        active_links=active,
    )


def _inputs_provenance(proposal: Proposal, trigger: Trigger, n: int) -> s.Provenance:
    """The provenance of the engine inputs of a recompute: the risk scored on the account graph
    without the links people disputed or explained (rule 11)."""
    score, context = proposal.score, proposal.context
    base = proposal.view.inputs.provenance
    risk = (
        f"risk {score.model_version} ({score.source})"
        if score is not None and score.risk is not None
        else "no risk (guarantee path, rule 4)"
    )
    snapshot = "computed at the request" if context.live else f"of {context.as_of:%Y-%m-%d}"
    origin = (
        f"engine inputs of a recompute ({trigger}): {risk} on the {GRAPH_NOTE.format(n=n)}; "
        f"customer snapshot {snapshot}; thresholds of the batch scoring; Navigli Fashion "
        "synthetic store"
    )
    return provenance(origin, base.period_start, base.period_end, base.nature)


@dataclass(frozen=True)
class Recomputed:
    """A new version written by recompute_version, waiting for its draft (finish)."""

    case: Shown
    new: s.DecisionView
    proposal: Proposal
    trigger: Trigger
    before: s.CaseSnapshot
    after: s.CaseSnapshot
    block: s.CouponRuleRef | None
    entry_ids: list[int]
    evidence_id: str | None
    dispute_id: str | None
    graph_note: str

    def draft_inputs(self) -> DraftInputs:
        return DraftInputs(
            view=self.new,
            request=self.proposal.request,
            factors=self.proposal.factors,
            cluster=self.proposal.cluster,
            customer_text=self.case.record.customer_text,
        )


async def recompute_version(
    conn: AsyncConnection,
    serving: Serving,
    case: Shown,
    *,
    trigger: Trigger,
    made_before: Sequence[Exclusion],
    made_after: Sequence[Exclusion],
    author: str,
    reason: str,
    act_entry: s.AuditEntry | None,
    now: datetime,
    request_id: str | None,
    evidence_id: str | None = None,
    dispute_id: str | None = None,
) -> Recomputed:
    """The next version of `case` on the graph without the links of `made_after`, the reversal
    of a confirmed coupon block of the version superseded, and the log; the draft follows, out
    of the transaction (finish)."""
    old, record = case.view, case.record
    old_graph = GraphView.of(serving, version_exclusions(case.row, made_before))
    old_context = old_graph.customer_context(record.customer_id, record.known_at)
    old_cluster = old.inputs.cluster_id
    before_ids = {e.link_id for e in made_before}
    before_size = (
        len(old_graph.cluster_members(old_cluster, old_context.as_of)) if old_cluster else None
    )
    before_active = active_links(old_graph, old_cluster, old_context.as_of, before_ids)

    graph = GraphView.of(serving, made_after)
    following = await asyncio.to_thread(
        next_version,
        graph,
        record,
        created_at=now,
        assumptions=await current_assumptions(conn),
        coupon_rule_version=await current_coupon_rule(conn),
        version=old.version + 1,
    )
    proposal = following.proposal
    inputs = proposal.view.inputs.model_copy(
        update={"provenance": _inputs_provenance(proposal, trigger, len(made_after))}
    )
    new = following.view(
        revision_trigger=trigger, supersedes=old.decision_id, inputs=inputs.model_dump()
    )
    await store.insert_version(conn, new, following.alternatives, proposal.raw_factors)

    block = old.coupon_rule if old.state in ("approved", "overridden") else None
    reversed_now = (
        await coupons.reverse(conn, block.application_id, by=author, at=now, reason=reason)
        if block is not None
        else False
    )
    superseded = await store.view_of(conn, case.row, now, line_price=case.line_price)
    recomputed = await audit(
        conn,
        at=now,
        actor="system",
        action="case_recomputed",
        entity_type="decision",
        entity_id=new.decision_id,
        return_id=new.return_id,
        decision_id=new.decision_id,
        before=s.DecisionAuditState.from_view(superseded),
        after=s.DecisionAuditState.from_view(new),
        rules_version=new.rules_version,
        model_versions=new.model_versions,
        assumptions_version=new.assumptions_version,
        request_id=request_id,
    )
    ids = [*([act_entry.entry_id] if act_entry is not None else []), recomputed.entry_id]
    if block is not None and reversed_now:
        reversal = await coupons.log_reversal(
            conn, block.application_id, by=author, at=now, reason=reason,
            return_id=old.return_id, decision_id=old.decision_id, request_id=request_id,
        )  # fmt: skip
        ids.append(reversal.entry_id)
    row = await store.decision_row_by_id(conn, new.decision_id)
    assert row is not None
    served = await store.view_of(conn, row, now, line_price=case.line_price)
    context = proposal.context
    cluster = served.inputs.cluster_id
    after_ids = {e.link_id for e in made_after}
    after_size = len(graph.cluster_members(cluster, context.as_of)) if cluster else None
    old_factors = store.factors_of(case.row) if _scored(old) else []
    return Recomputed(
        case=case,
        new=new,
        proposal=proposal,
        trigger=trigger,
        before=_snapshot(old, old_factors, before_size, before_active),
        after=_snapshot(
            served,
            proposal.factors,
            after_size,
            active_links(graph, cluster, context.as_of, after_ids),
        ),
        block=block,
        entry_ids=ids,
        evidence_id=evidence_id,
        dispute_id=dispute_id,
        graph_note=GRAPH_NOTE.format(n=len(made_after)),
    )


def make_drafts(
    generator: Generator, pending: Sequence[Recomputed], now: datetime
) -> list[Outcome]:
    """The drafts of the new versions, with at most LLM_CONCURRENCY model calls in flight (run
    it with no transaction open, on the runtime's model-call pool)."""
    return generator.generate_many([p.draft_inputs() for p in pending], created_at=now)


async def store_draft(
    conn: AsyncConnection,
    pending: Recomputed,
    outcome: Outcome,
    now: datetime,
    request_id: str | None,
) -> tuple[s.AuditEntry, Outcome, s.DecisionView]:
    """Store the draft of a recomputed version (the version keeps it even if a person acted on
    the case meanwhile); the entry, the draft as stored and the version served now."""
    case, new = pending.case, pending.new
    await return_of(conn, new.return_id, lock=True)
    drafted, stored = await store_generation(conn, outcome, new, request_id=request_id)
    row = await store.decision_row_by_id(conn, new.decision_id)
    assert row is not None
    served = await store.view_of(conn, row, now, line_price=case.line_price)
    return drafted, stored, served


async def finish(
    conn: AsyncConnection,
    serving: Serving,
    pending: Recomputed,
    outcome: Outcome,
    now: datetime,
    request_id: str | None,
) -> s.RecomputeResponse:
    """Store the draft of a recomputed version and answer the recompute."""
    drafted, stored, served = await store_draft(conn, pending, outcome, now, request_id)
    block = pending.block
    path = (
        f"the request model of the snapshot on the {pending.graph_note}"
        if served.path is ReturnPath.WITHDRAWAL
        else "no request model on the guarantee path (rule 4)"
    )
    assert pending.trigger != "stale_proposal", "a stale proposal answers 409, not a recompute"
    return s.RecomputeResponse(
        return_id=served.return_id,
        trigger=pending.trigger,
        evidence_id=pending.evidence_id,
        dispute_id=pending.dispute_id,
        before=pending.before,
        after=pending.after,
        decision=served,
        draft=stored.draft,
        coupon_application=(
            await coupons.application(conn, block.application_id, now) if block else None
        ),
        audit_entry_ids=[*pending.entry_ids, drafted.entry_id],
        provenance=from_file(
            serving.provenance["scores/request_scores"],
            f"recompute of {served.return_id} on {now:%Y-%m-%d}: {path}",
            now.date(),
        ),
    )


# ---- evidence ------------------------------------------------------------------------------------


def _active_in_case(serving: Serving, case: Shown, made: Sequence[Exclusion]) -> set[str]:
    """The active links of the cluster the engine was given for the version shown."""
    cluster = case.view.inputs.cluster_id
    if cluster is None:
        return set()
    graph = GraphView.of(serving, version_exclusions(case.row, made))
    context = graph.customer_context(case.record.customer_id, case.record.known_at)
    links = graph.cluster_links(graph.cluster_members(cluster, context.as_of), context.as_of)
    excluded = {e.link_id for e in made}
    out = set()
    for a, b, disputed in zip(links["customer_a"], links["customer_b"], links["disputed"],
                              strict=True):  # fmt: skip
        lid = s.link_id(a, b)
        if not disputed and lid not in excluded:
            out.add(lid)
    return out


async def add_evidence(
    conn: AsyncConnection,
    serving: Serving,
    return_id: str,
    body: s.EvidenceRequest,
    now: datetime,
    request_id: str | None = None,
) -> Recomputed:
    """POST /api/returns/{return_id}/evidence, first transaction: the evidence and the new
    version; finish() stores its draft and answers."""
    case = await shown(conn, serving, return_id, body.decision_id, now, lock=True)
    made = [e for e in await exclusions(conn) if e.created_at <= now]
    targets = list(dict.fromkeys(body.target_link_ids))
    if body.kind in LINK_EXPLAINING_EVIDENCE:
        active = _active_in_case(serving, case, made)
        wrong = [t for t in targets if t not in active]
        if wrong:
            raise ApiError(
                422,
                "invalid_request",
                f"{', '.join(wrong)}: not an active link of the cluster given to the engine for "
                f"{case.view.decision_id}",
                [
                    ("target_link_ids", f"{t}: not an active link of the case's cluster")
                    for t in wrong
                ],
            )
    evidence_id = await _next_id(conn, evidence_additions, "EV")
    act_entry = await audit(
        conn,
        at=now,
        actor=body.author,
        action="evidence_added",
        entity_type="evidence_addition",
        entity_id=evidence_id,
        return_id=return_id,
        decision_id=case.view.decision_id,
        reason=body.note,
        after={"kind": body.kind, "target_link_ids": targets},
        request_id=request_id,
    )
    added = [
        Exclusion(t, *s.split_link_id(t), kind="explained", source_id=evidence_id, created_at=now)
        for t in targets
    ]
    pending = await recompute_version(
        conn, serving, case, trigger="evidence_added", made_before=made,
        made_after=[*made, *added], author=body.author, reason=body.note, act_entry=act_entry,
        now=now, request_id=request_id, evidence_id=evidence_id,
    )  # fmt: skip
    await conn.execute(
        insert(evidence_additions).values(
            evidence_id=evidence_id,
            return_id=return_id,
            decision_before_id=case.view.decision_id,
            decision_after_id=pending.new.decision_id,
            kind=body.kind,
            note=body.note,
            target_link_ids=targets,
            author=body.author,
            created_at=now,
            before=pending.before.model_dump(mode="json"),
            after=pending.after.model_dump(mode="json"),
        )
    )
    return pending


# ---- disputes ------------------------------------------------------------------------------------


async def affected_cases(
    conn: AsyncConnection, serving: Serving, a: str, b: str, made: Sequence[Exclusion]
) -> list[str]:
    """The queued cases whose cluster, as given to the engine of their latest version, holds
    the link between `a` and `b` (sorted by return id)."""
    out = []
    for row, ret in await latest_rows(conn):
        cluster = row["cluster_id"]
        if cluster is None:
            continue
        record = record_of(serving, ret)
        graph = GraphView.of(serving, version_exclusions(row, made))
        context = graph.customer_context(record.customer_id, record.known_at)
        if context.live:
            continue
        members = graph.cluster_members(cluster, context.as_of)
        if a not in members or b not in members:
            continue
        links = graph.cluster_links(members, context.as_of)
        if ((links["customer_a"] == a) & (links["customer_b"] == b)).any():
            out.append(str(row["return_id"]))
    return sorted(out)


def _link_view(row: Mapping[str, Any], info: s.LinkDisputeInfo) -> s.AccountLink:
    return s.AccountLink(
        link_id=s.link_id(row["customer_a"], row["customer_b"]),
        customer_a=row["customer_a"],
        customer_b=row["customer_b"],
        kinds=list(row["kinds"]),
        weight=float(row["weight"]),
        weight_decayed=float(row["weight_decayed"]),
        first_shared_at=_ts(row["first_shared_at"]),
        last_shared_at=_ts(row["last_shared_at"]),
        n_shared_events=int(row["n_shared_events"]),
        max_sharers=int(row["max_sharers"]),
        disputed=True,
        dispute=info,
        explained_by_evidence_id=None,
    )


@dataclass(frozen=True)
class PendingDispute:
    """A dispute written by dispute(), its recomputes waiting for their drafts."""

    link_row: dict[str, Any]
    info: s.LinkDisputeInfo
    affected: list[str]
    recomputes: list[Recomputed]
    named: str | None  # the return the dispute was sent from
    day: Any

    def mine(self) -> Recomputed | None:
        return next((r for r in self.recomputes if r.case.view.return_id == self.named), None)


async def dispute(
    conn: AsyncConnection,
    serving: Serving,
    link: str,
    body: s.LinkDisputeRequest,
    now: datetime,
    request_id: str | None = None,
) -> PendingDispute:
    """POST /api/links/{link_id}/dispute, first transaction: the dispute and the new version of
    every case it recomputes, the cases locked in return id order; finish_dispute() stores the
    drafts and answers."""
    try:
        a, b = s.split_link_id(link)
    except ValueError:
        raise invalid_request(f"{link!r} is not a link id (customer_a~customer_b, a < b)") from None
    frame = serving.frame("features/links")
    rows = frame.loc[
        (frame["customer_a"] == a)
        & (frame["customer_b"] == b)
        & (frame["as_of"] <= pd.Timestamp(now))
    ]
    if rows.empty:
        raise not_found(f"link {link}")
    # Lock the named case and the cases the link holds, in one sorted pass (H22).
    candidates = await affected_cases(conn, serving, a, b, await exclusions(conn))
    locked = sorted({*candidates, *([body.return_id] if body.return_id else [])})
    for return_id in locked:
        await return_of(conn, return_id, lock=True)
    case = (
        await shown(conn, serving, body.return_id, body.decision_id, now)
        if body.return_id is not None
        else None
    )
    earlier = await conn.scalar(
        select(link_disputes.c.dispute_id).where(link_disputes.c.link_id == link)
    )
    if earlier is not None:
        raise conflict(f"{link} is already disputed ({earlier}): a link is disputed once")
    made = [e for e in await exclusions(conn) if e.created_at <= now]
    explained = next((e for e in made if e.link_id == link), None)
    if explained is not None:
        raise conflict(
            f"{link} is explained by evidence {explained.source_id}: it is already out of the "
            "cluster features"
        )
    affected = await affected_cases(conn, serving, a, b, made)
    for return_id in sorted(set(affected) - set(locked)):  # changed while waiting for the locks
        await return_of(conn, return_id, lock=True)
    if case is not None and case.view.return_id not in affected:
        raise invalid_request(
            f"{link} is not a link of the cluster given to the engine for {case.view.decision_id}"
        )
    dispute_id = await _next_id(conn, link_disputes, "LD")
    try:
        await conn.execute(
            insert(link_disputes).values(
                dispute_id=dispute_id,
                link_id=link,
                customer_a=a,
                customer_b=b,
                return_id=body.return_id,
                reason=body.reason,
                author=body.author,
                created_at=now,
            )
        )
    except IntegrityError:
        raise conflict(f"{link} is already disputed: a link is disputed once") from None
    act_entry = await audit(
        conn,
        at=now,
        actor=body.author,
        action="link_disputed",
        entity_type="link_dispute",
        entity_id=dispute_id,
        return_id=body.return_id,
        decision_id=body.decision_id,
        reason=body.reason,
        before={"disputed": False},
        after={"disputed": True, "link_id": link},
        request_id=request_id,
    )
    added = Exclusion(link, a, b, "disputed", dispute_id, now)
    recomputes: list[Recomputed] = []
    for return_id in affected:
        this = (
            case
            if case is not None and case.view.return_id == return_id
            else await shown(conn, serving, return_id, None, now)
        )
        recomputes.append(
            await recompute_version(
                conn, serving, this, trigger="link_disputed", made_before=made,
                made_after=[*made, added], author=body.author, reason=body.reason,
                act_entry=act_entry, now=now, request_id=request_id, dispute_id=dispute_id,
            )
        )  # fmt: skip
    named = case.view.return_id if case is not None else None
    mine = next((r for r in recomputes if r.case.view.return_id == named), None)
    if mine is not None:
        await conn.execute(
            update(link_disputes)
            .where(link_disputes.c.dispute_id == dispute_id)
            .values(
                before=mine.before.model_dump(mode="json"),
                after=mine.after.model_dump(mode="json"),
            )
        )
    if case is not None:
        context = serving.customer_context(case.record.customer_id, case.record.known_at)
        instant = pd.Timestamp(context.as_of)
    else:
        instant = rows["as_of"].max()
    shown_row = rows.loc[rows["as_of"] == instant].iloc[0].to_dict()
    info = s.LinkDisputeInfo(
        dispute_id=dispute_id, reason=body.reason, author=body.author, created_at=now
    )
    return PendingDispute(shown_row, info, affected, recomputes, named, _ts(instant).date())


async def finish_dispute(
    conn: AsyncConnection,
    serving: Serving,
    pending: PendingDispute,
    outcomes: Sequence[Outcome],
    now: datetime,
    request_id: str | None,
) -> s.LinkDisputeResponse:
    """The second transaction of a dispute: the drafts of the recomputed versions, in return id
    order, and the response."""
    answers = [
        await finish(conn, serving, r, o, now, request_id)
        for r, o in zip(pending.recomputes, outcomes, strict=True)
    ]
    mine = next((x for x in answers if x.return_id == pending.named), None)
    day = pending.day
    return s.LinkDisputeResponse(
        link=_link_view(pending.link_row, pending.info),
        affected_return_ids=pending.affected,
        recompute=mine,
        provenance=from_file(serving.provenance["features/links"], f"account graph at {day}", day),
    )
