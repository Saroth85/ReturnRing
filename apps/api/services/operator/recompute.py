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
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Literal

import pandas as pd
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.schema import Table

from apps.api import schemas as s
from apps.api.errors import ApiError, conflict, invalid_request, not_found
from apps.api.services import decisions as store
from apps.api.services.audit import record as audit
from apps.api.services.cases import Proposal, record_of
from apps.api.services.generation import DraftInputs, Generator, next_generation_id
from apps.api.services.generation import store as store_generation
from apps.api.services.operator import coupons
from apps.api.services.operator.graph import (
    Exclusion,
    GraphView,
    exclusions,
    version_exclusions,
)
from apps.api.services.operator.queue import latest_rows
from apps.api.services.operator.shown import Shown, shown
from apps.api.services.operator.versions import next_version
from apps.api.services.provenance import from_file, provenance
from apps.api.services.seeds import current_assumptions, current_coupon_rule
from apps.api.serving import Serving
from contracts.sql import evidence_additions, link_disputes
from contracts.vocab import LINK_EXPLAINING_EVIDENCE
from policy.types import EvidenceQuality
from policy.types import Path as ReturnPath

Trigger = Literal["evidence_added", "link_disputed"]
GRAPH_NOTE: Final = "account graph without the {n} links disputed or explained by people (US-310)"


def _ts(value: Any) -> datetime:
    moment: datetime = pd.Timestamp(value).to_pydatetime()
    return moment


async def _next_id(conn: AsyncConnection, table: Table, prefix: str) -> str:
    count = await conn.scalar(select(func.count()).select_from(table))
    return f"{prefix}-{int(count or 0) + 1:06d}"


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


async def recompute_case(
    conn: AsyncConnection,
    serving: Serving,
    generator: Generator,
    case: Shown,
    *,
    trigger: Trigger,
    made_before: Sequence[Exclusion],
    made_after: Sequence[Exclusion],
    author: str,
    reason: str,
    act_entry: s.AuditEntry,
    now: datetime,
    request_id: str | None,
    evidence_id: str | None = None,
    dispute_id: str | None = None,
) -> s.RecomputeResponse:
    """The next version of `case` on the graph without the links of `made_after`, its draft,
    the reversal of a confirmed coupon block of the version superseded, and the log."""
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
    ids = [act_entry.entry_id, recomputed.entry_id]
    if block is not None and reversed_now:
        reversal = await coupons.log_reversal(
            conn, block.application_id, by=author, at=now, reason=reason,
            return_id=old.return_id, decision_id=old.decision_id, request_id=request_id,
        )  # fmt: skip
        ids.append(reversal.entry_id)

    draft_inputs = DraftInputs(
        view=new,
        request=proposal.request,
        factors=proposal.factors,
        cluster=proposal.cluster,
        customer_text=record.customer_text,
    )
    generation_id = await next_generation_id(conn, new.return_id)
    outcome = await asyncio.to_thread(
        generator.generate, draft_inputs, generation_id=generation_id, created_at=now
    )
    drafted = await store_generation(conn, outcome, new, request_id=request_id)
    ids.append(drafted.entry_id)
    row = await store.decision_row_by_id(conn, new.decision_id)
    assert row is not None
    served = await store.view_of(conn, row, now, line_price=case.line_price)

    context = proposal.context
    cluster = served.inputs.cluster_id
    after_ids = {e.link_id for e in made_after}
    after_size = len(graph.cluster_members(cluster, context.as_of)) if cluster else None
    old_factors = store.factors_of(case.row) if _scored(old) else []
    return s.RecomputeResponse(
        return_id=served.return_id,
        trigger=trigger,
        evidence_id=evidence_id,
        dispute_id=dispute_id,
        before=_snapshot(old, old_factors, before_size, before_active),
        after=_snapshot(
            served,
            proposal.factors,
            after_size,
            active_links(graph, cluster, context.as_of, after_ids),
        ),
        decision=served,
        draft=outcome.draft,
        coupon_application=(
            await coupons.application(conn, block.application_id, now) if block else None
        ),
        audit_entry_ids=ids,
        provenance=from_file(
            serving.provenance["scores/request_scores"],
            f"recompute of {served.return_id} on {now:%Y-%m-%d}: the request model of the "
            f"snapshot on the {GRAPH_NOTE.format(n=len(made_after))}",
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
    generator: Generator,
    return_id: str,
    body: s.EvidenceRequest,
    now: datetime,
    request_id: str | None = None,
) -> s.RecomputeResponse:
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
    response = await recompute_case(
        conn, serving, generator, case, trigger="evidence_added", made_before=made,
        made_after=[*made, *added], author=body.author, reason=body.note, act_entry=act_entry,
        now=now, request_id=request_id, evidence_id=evidence_id,
    )  # fmt: skip
    await conn.execute(
        insert(evidence_additions).values(
            evidence_id=evidence_id,
            return_id=return_id,
            decision_before_id=case.view.decision_id,
            decision_after_id=response.decision.decision_id,
            kind=body.kind,
            note=body.note,
            target_link_ids=targets,
            author=body.author,
            created_at=now,
            before=response.before.model_dump(mode="json"),
            after=response.after.model_dump(mode="json"),
        )
    )
    return response


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


async def dispute(
    conn: AsyncConnection,
    serving: Serving,
    generator: Generator,
    link: str,
    body: s.LinkDisputeRequest,
    now: datetime,
    request_id: str | None = None,
) -> s.LinkDisputeResponse:
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
    case = (
        await shown(conn, serving, body.return_id, body.decision_id, now, lock=True)
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
    if case is not None and case.view.return_id not in affected:
        raise invalid_request(
            f"{link} is not a link of the cluster given to the engine for {case.view.decision_id}"
        )
    dispute_id = await _next_id(conn, link_disputes, "LD")
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
    recomputed: dict[str, s.RecomputeResponse] = {}
    for return_id in affected:
        this = (
            case
            if case is not None and case.view.return_id == return_id
            else await shown(conn, serving, return_id, None, now, lock=True)
        )
        recomputed[return_id] = await recompute_case(
            conn, serving, generator, this, trigger="link_disputed", made_before=made,
            made_after=[*made, added], author=body.author, reason=body.reason,
            act_entry=act_entry, now=now, request_id=request_id, dispute_id=dispute_id,
        )  # fmt: skip
    mine = recomputed.get(case.view.return_id) if case is not None else None
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
    day = _ts(instant).date()
    return s.LinkDisputeResponse(
        link=_link_view(shown_row, info),
        affected_return_ids=affected,
        recompute=mine,
        provenance=from_file(serving.provenance["features/links"], f"account graph at {day}", day),
    )
