"""The services of the API core (ADR-15 section 4, ADR-16): intake (flow F1), alternatives,
drafts in every mode, the audit and outbox writers, the demo reset and the MI300X batch.

Every decision the intake stores is policy.engine.decide on the inputs it stores (rule 1), passes
policy.legal_guard (rules 3 to 5), follows the path before any score (rule 4) and carries the
provenance of its inputs (rule 11). Every number here is synthetic (the tiny store of
tests/api_harness.py); none is a result.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.batch_generate import batch
from apps.api.db import make_engine
from apps.api.reset import reset
from apps.api.services import decisions as store
from apps.api.services.alternatives import ALTERNATIVE_ASSUMPTIONS
from apps.api.services.audit import entries, record
from apps.api.services.cases import IntakeError, intake, propose
from apps.api.services.generation import (
    CACHE_FORMAT,
    Generator,
    input_digest,
    llm_case,
    load_draft,
)
from apps.api.services.outbox import compose, message_of, queue
from apps.api.services.seeds import ASSUMPTIONS_V1, COUPON_RULE_V1, current_assumptions
from apps.api.serving import Serving
from apps.api.settings import Settings
from contracts.sql import audit_log, decisions, llm_generations, metadata, outbox
from llm.client import MockClient
from pipeline.generator.golden import ELENA, GIULIA, MARCO_ACCOUNTS, ROSSI_ACCOUNTS
from policy.engine import NAVIGLI_BASE_TERMS, REVIEW_DAYS, decide, guarantee_terms
from policy.legal_guard import is_restrictive
from policy.simulator import PROPOSAL_EXAMPLE, net_benefit
from policy.types import CaseScores, EvidenceQuality, Quadrant
from policy.types import Path as ReturnPath
from tests.api_harness import (
    CLOCK_AT,
    Api,
    golden_return,
    open_api,
    settings_for,
    sqlite_url,
    validate_body,
)

BASE = NAVIGLI_BASE_TERMS
V1 = s.Assumptions.from_policy(PROPOSAL_EXAMPLE)


async def _views(conn: AsyncConnection) -> list[tuple[s.DecisionView, Any]]:
    rows = (await conn.execute(select(decisions).order_by(decisions.c.return_id))).mappings().all()
    return [
        (await store.view_of(conn, r, CLOCK_AT, line_price=lambda _: Decimal(1)), r) for r in rows
    ]


def _rerun(view: s.DecisionView) -> Any:
    inputs = view.inputs
    request = store_request(view)
    scores = inputs.scores()
    rule = view.coupon_rule.rule_version if view.coupon_rule else COUPON_RULE_V1
    return decide(request, scores, BASE, inputs.thresholds.to_policy(), view.decided_on, rule)


def store_request(view: s.DecisionView) -> Any:
    from policy.types import ReturnRequest

    inputs = view.inputs
    return ReturnRequest(
        return_id=view.return_id,
        order_id=inputs.order_id,
        customer_id=inputs.customer_id,
        reason=inputs.reason,
        delivered_on=inputs.delivered_on,
        notice_on=inputs.notice_on,
        order_terms=inputs.order_terms.to_policy(),
    )


# ---- Intake (flow F1) ------------------------------------------------------------------------


def test_every_stored_decision_is_the_engine_output_on_its_stored_inputs(api: Api) -> None:
    """Rule 1 and US-604: re-running policy.engine.decide on the inputs of each version gives
    exactly the version; each view passes policy.legal_guard (DecisionView validator)."""
    views = api.run(_views)
    assert len(views) == len(api.serving.open_return_ids())
    for view, row in views:
        decision = _rerun(view)
        assert view.path is decision.path and view.quadrant is decision.quadrant
        assert [x.model_dump() for x in view.proposed_levers] == decision.levers.to_contract(BASE)
        assert view.requires_approval == decision.requires_approval
        assert view.review_at == decision.review_on
        assert view.refund_due_by == view.inputs.notice_on + timedelta(days=14)  # rule 3
        assert view.decided_on == api.serving.as_of.date()  # the day of the intake
        assert view.version == 1 and view.revision_trigger == "initial"
        assert view.state == "proposed" and view.approver is None
        assert view.assumptions_version == ASSUMPTIONS_V1
        assert view.inputs.provenance.nature == "synthetic"
        assert view.generation_id is not None and row["generation_path"] == "model"
        if view.quadrant in (Quadrant.Q3, Quadrant.Q4) or is_restrictive(decision.levers, BASE):
            assert view.requires_approval and view.review_at is not None  # rule 5, US-607
            assert view.review_at == view.decided_on + timedelta(days=REVIEW_DAYS)
            assert not view.in_force  # nothing restrictive applies before a person approves


def test_guarantee_returns_are_never_scored(
    serving: Serving, empty_api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 4, LEG-07: the path comes first; a defect never reaches the risk score."""
    return_id = golden_return(serving, ELENA)
    record = serving.store_return(return_id)
    assert record is not None and record.path is ReturnPath.GUARANTEE

    def no_score(_record: Any) -> Any:
        raise AssertionError("a guarantee return was sent to the risk score")

    monkeypatch.setattr(serving, "request_score", no_score)

    async def run(conn: AsyncConnection) -> s.DecisionView:
        return await intake(
            conn, serving, Generator(empty_api.settings), record, at=serving.as_of, source="store"
        )

    view = empty_api.run(run)
    assert view.path is ReturnPath.GUARANTEE and view.quadrant is None
    assert view.inputs.risk is None and view.inputs.value_eur is None
    assert not view.requires_approval and view.proposed_levers == []
    order_terms = view.inputs.order_terms.to_policy()
    assert view.current_return_terms.to_policy() == guarantee_terms(order_terms, BASE)


def test_golden_cases_get_the_decisions_of_the_demo(api: Api) -> None:
    """US-104 on the tiny store: Giulia's upgrade applies by itself; Marco's ring account gets
    the Q4 levers with the cluster coupon block proposed for its 7 linked accounts; the Rossi
    family is never in Q3 or Q4 (US-203, MOD-11)."""
    serving = api.serving
    views = {v.inputs.customer_id: v for v, _ in api.run(_views)}
    giulia = views[GIULIA]
    assert giulia.quadrant is Quadrant.Q1 and not giulia.requires_approval and giulia.in_force
    assert "return_window_days:60" in giulia.lever_ids
    marco = next(views[c] for c in MARCO_ACCOUNTS if c in views)
    assert marco.quadrant is Quadrant.Q4 and marco.requires_approval
    assert len(marco.proposed_levers) == 6
    rule = marco.coupon_rule
    assert rule is not None and rule.state == "proposed" and rule.confirmed_by is None
    assert rule.cluster_id == marco.inputs.cluster_id and not rule.in_force
    assert sorted(rule.affected_customer_ids) == list(MARCO_ACCOUNTS)
    assert not rule.cluster_id.startswith(("R-", "F-"))  # an opaque graph id (rule 12)
    for customer in ROSSI_ACCOUNTS:
        if customer in views:
            assert views[customer].quadrant not in (Quadrant.Q3, Quadrant.Q4)
    assert serving.open_return_ids()[0] == golden_return(serving, GIULIA)  # golden first


def test_intake_logs_the_proposal_and_the_draft(api: Api) -> None:
    """US-604, rule 5: the system proposes (never approves); the draft generation is logged with
    its path; the entries validate as the contract's AuditEntry."""
    return_id = golden_return(api.serving, GIULIA)

    async def run(conn: AsyncConnection) -> list[s.AuditEntry]:
        return await entries(conn, return_id=return_id)

    log = api.run(run)
    assert [e.action for e in log] == ["decision_proposed", "draft_generated"]
    proposed, drafted = log
    assert proposed.actor == "system" and proposed.actor_role == "system"
    assert isinstance(proposed.after, s.DecisionAuditState) and proposed.before is None
    assert proposed.after.state == "proposed" and proposed.after.version == 1
    assert isinstance(drafted.after, s.GenerationAuditState)
    assert drafted.after.generation_path == "model" and drafted.after.llm_mode == "mock"
    assert proposed.model_versions and proposed.assumptions_version == ASSUMPTIONS_V1


def test_a_return_is_taken_in_once(serving: Serving, api: Api) -> None:
    record = serving.store_return(golden_return(serving, GIULIA))
    assert record is not None

    async def run(conn: AsyncConnection) -> None:
        await intake(conn, serving, None, record, at=CLOCK_AT, source="store")

    with pytest.raises(IntakeError):
        api.run(run)


def test_an_adapter_return_is_decided_and_carded(serving: Serving, empty_api: Api) -> None:
    """Flow F1 for a request of the simulated shop: scored live at its instant, decided, its
    draft left pending (generation queue), its card served."""
    lines = serving.frame("store/order_lines")
    returns = serving.frame("store/returns")
    deliveries = serving.frame("store/deliveries").set_index("order_id")
    free = lines.loc[~lines["line_id"].isin(returns["line_id"])]
    free = free.loc[free["order_id"].isin(deliveries.index)]
    line = free.iloc[0]
    at = max(serving.as_of, deliveries.loc[line["order_id"], "available_at"].to_pydatetime())
    at += timedelta(hours=2)
    record = serving.adapter_return(
        "RET-ADAPTER-7", str(line["line_id"]), s.ReturnReason.SIZE, at, "La taglia è sbagliata."
    )

    async def run(conn: AsyncConnection) -> s.DecisionView:
        return await intake(conn, serving, None, record, at=at, source="adapter")

    view = empty_api.run(run)
    assert view.generation_id is None and view.path is ReturnPath.WITHDRAWAL
    empty_api.clock.set(at + timedelta(minutes=5))
    card = empty_api.client.get("/api/returns/RET-ADAPTER-7")
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["draft_status"] == "pending" and body["draft"] is None
    assert body["request"]["customer_text"] == "La taglia è sbagliata."
    assert any(
        line["return_id"] == "RET-ADAPTER-7"
        for o in body["orders"]["orders"]
        for line in o["lines"]
    )


# ---- Alternatives (US-308, rule 9) ------------------------------------------------------------


def test_alternatives_price_the_proposal_with_the_stored_unit_costs(api: Api) -> None:
    async def run(conn: AsyncConnection) -> tuple[list[Any], s.Assumptions]:
        return await _views(conn), await current_assumptions(conn)

    views, stored = api.run(run)
    kinds_by_path: dict[str, set[str]] = {}
    for view, row in views:
        options = store.alternatives_of(row)
        assert 1 <= len(options) <= 3
        recommended = [a for a in options if a.recommended]
        assert len(recommended) == 1 and recommended[0].kind == "proposal"
        assert recommended[0].levers == view.proposed_levers
        for a in options:
            assumptions = a.net_benefit.assumptions
            s.check_unit_costs(assumptions, stored)
            assert assumptions.version == f"{view.decision_id}/{a.kind}"
            nb = net_benefit(assumptions.as_policy())
            assert Decimal(str(a.net_benefit.total.value)) == nb.total
            assert a.net_benefit.total.provenance.nature == "simulated"
            assert ALTERNATIVE_ASSUMPTIONS.version in a.net_benefit.total.provenance.origin
            assert "refund" not in json.dumps(assumptions.model_dump())  # rule 9
        assert row["expected_net_benefit_eur"] == Decimal(
            str(recommended[0].net_benefit.total.value)
        )
        kinds_by_path.setdefault(view.path.value, set()).update(a.kind for a in options)
        if view.proposed_levers:
            assert "current_terms" in {a.kind for a in options}
    assert "product_report" in kinds_by_path.get("guarantee", {"product_report"})


# ---- Drafts: mock, vLLM cache, template -------------------------------------------------------


def _proposal_of(serving: Serving, return_id: str) -> Any:
    record = serving.store_return(return_id)
    assert record is not None
    return propose(
        serving,
        record,
        created_at=serving.as_of,
        assumptions=V1,
        coupon_rule_version=COUPON_RULE_V1,
    )


def _proposal(serving: Serving, customer: str) -> Any:
    return _proposal_of(serving, golden_return(serving, customer))


def test_mock_drafts_are_live_and_deterministic(
    serving: Serving, serving_dir: Path, tmp_path: Path
) -> None:
    proposal = _proposal(serving, GIULIA)
    generator = Generator(settings_for(serving_dir, tmp_path / "x.sqlite"))
    first = generator.generate(proposal.draft_inputs(), generation_id="GEN-1", created_at=CLOCK_AT)
    second = generator.generate(proposal.draft_inputs(), generation_id="GEN-1", created_at=CLOCK_AT)
    assert first.draft == second.draft and first.digest == second.digest
    info = first.info
    assert (info.generation_path, info.llm_mode, info.served_from, info.model) == (
        "model", "mock", "live", "mock"
    )  # fmt: skip
    assert info.latency_ms is None  # nothing is measured for the mock
    assert first.digest == input_digest(llm_case(proposal.draft_inputs()).case)


def test_cached_mode_serves_the_mi300x_output_and_labels_a_miss(
    serving: Serving, serving_dir: Path, tmp_path: Path
) -> None:
    """US-901, ADR-03: batch_generate writes the cache; cached mode serves it validated again,
    as cached vLLM output; a case the cache lacks gets the template, labelled live."""
    copy = tmp_path / "serving"
    shutil.copytree(serving_dir, copy)
    local = Serving.load(copy)
    summary = batch(local, MockClient(), "google/gemma-4-31B-it (test stand-in)", limit=2)
    assert not summary["failed"] and sum(summary["paths"].values()) == 2
    entries_written = sorted((copy / "llm_cache").glob("*.json"))
    assert len(entries_written) == 2
    entry = json.loads(entries_written[0].read_text(encoding="utf-8"))
    assert entry["format"] == CACHE_FORMAT and entry["latency_ms"] is not None
    cached = Generator(settings_for(copy, tmp_path / "x.sqlite", demo_mode="cached"))
    hit = cached.generate(
        _proposal(local, GIULIA).draft_inputs(), generation_id="GEN-2", created_at=CLOCK_AT
    )
    assert (hit.info.served_from, hit.info.llm_mode) == ("cached", "vllm")
    assert hit.info.model == "google/gemma-4-31B-it (test stand-in)"
    miss = cached.generate(
        _proposal_of(local, local.open_return_ids()[-1]).draft_inputs(),
        generation_id="GEN-3",
        created_at=CLOCK_AT,
    )
    assert (miss.info.generation_path, miss.info.fallback_reason) == ("template", "unavailable")
    assert miss.info.served_from == "live" and miss.info.model_calls == 0
    for path in entries_written:  # a broken entry is a miss, never an error
        path.write_text("{not json", encoding="utf-8")
    broken = cached.generate(
        _proposal(local, GIULIA).draft_inputs(), generation_id="GEN-4", created_at=CLOCK_AT
    )
    assert (broken.info.served_from, broken.info.generation_path) == ("live", "template")


def test_a_draft_is_stored_with_its_llm_case_and_served_again(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)

    async def run(conn: AsyncConnection) -> tuple[s.DraftView | None, Any]:
        found = await store.latest_view(conn, return_id, CLOCK_AT, line_price=lambda _: Decimal(1))
        assert found is not None
        view, _ = found
        row = (
            (
                await conn.execute(
                    select(llm_generations).where(
                        llm_generations.c.generation_id == view.generation_id
                    )
                )
            )
            .mappings()
            .one()
        )
        return await load_draft(conn, view), row

    draft, row = api.run(run)
    assert draft is not None and row["input_digest"] == input_digest(row["llm_case"])
    assert row["prompt_version"].startswith("prompts-")
    assert (
        draft.case_facts and [f.model_dump() for f in draft.case_facts] == row["llm_case"]["facts"]
    )
    assert {t.lang for t in draft.message.texts} == {"it", "en"}  # customer messages in both


# ---- Audit and outbox writers -----------------------------------------------------------------


def test_the_audit_writer_keeps_the_rules_of_the_log(empty_api: Api) -> None:
    async def human_as_system(conn: AsyncConnection) -> None:
        await record(
            conn, at=CLOCK_AT, actor="system", action="evidence_added",
            entity_type="evidence_addition", entity_id="EV-1", reason="household members",
        )  # fmt: skip

    async def plain_number(conn: AsyncConnection) -> None:
        await record(
            conn, at=CLOCK_AT, actor="ahmed", action="inspection_created",
            entity_type="inspection", entity_id="INS-1", after={"compensation": 12.5},
        )  # fmt: skip

    async def unknown_person(conn: AsyncConnection) -> None:
        await record(
            conn, at=CLOCK_AT, actor="mallory", action="catalog_task_opened",
            entity_type="catalog_task", entity_id="CT-1",
        )  # fmt: skip

    for bad in (human_as_system, plain_number):
        with pytest.raises(ValidationError):
            empty_api.run(bad)
    with pytest.raises(ValueError):
        empty_api.run(unknown_person)

    async def tamper(conn: AsyncConnection) -> None:
        await conn.execute(update(audit_log).values(actor="luca"))

    with pytest.raises(Exception, match="append-only"):
        empty_api.run(tamper)


def test_the_outbox_writer_stores_and_logs_a_composed_message(api: Api) -> None:
    """Rule 10: the message is llm.phrasebook.render_selection for the decision as it stands,
    with the fixed clause; it is stored, logged (message_queued) and read back unchanged."""
    return_id = golden_return(api.serving, GIULIA)

    async def run(conn: AsyncConnection) -> tuple[s.OutboxMessage, s.OutboxMessage, list[Any]]:
        found = await store.latest_view(conn, return_id, CLOCK_AT, line_price=lambda _: Decimal(1))
        assert found is not None
        view, _ = found
        draft = await load_draft(conn, view)
        message = await compose(
            conn, view, lang="it", created_by="sara", created_at=CLOCK_AT,
            preferred=draft.message.sentence_ids if draft else None,
        )  # fmt: skip
        await queue(conn, message, view, request_id="req-test")
        row = (await conn.execute(select(outbox))).mappings().one()
        return message, message_of(row), await entries(conn, return_id=return_id)

    message, stored, log = api.run(run)
    assert stored == message and message.message_id == "MSG-000001"
    assert message.created_by == "sara" and message.lang == "it"
    assert log[-1].action == "message_queued" and log[-1].request_id == "req-test"


# ---- Reset (US-903) ---------------------------------------------------------------------------


async def _dump(url: str) -> dict[str, list[dict[str, Any]]]:
    engine = make_engine(url)
    try:
        async with engine.connect() as conn:
            out = {}
            for table in metadata.sorted_tables:
                query = select(table).order_by(*table.primary_key.columns)
                rows = (await conn.execute(query)).mappings().all()
                out[table.name] = [dict(r) for r in rows]
            for row in out["demo_reset"]:  # the wall time the demo clock started: not a result
                row["started_at"] = None
            return out
    finally:
        await engine.dispose()


def test_the_reset_is_one_command_and_deterministic(
    serving: Serving, serving_dir: Path, seeded_db: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """US-903: drop, recreate, seed and pre-compute; two resets write the same rows."""
    path, summary = seeded_db
    assert summary["decided"] == len(serving.open_return_ids()) and not summary["failed"]
    assert summary["drafts"] == {"ready": summary["decided"]}
    again = tmp_path / "again.sqlite"
    shutil.copyfile(path, again)  # a used database: the reset drops it first

    async def run() -> dict[str, Any]:
        engine = make_engine(sqlite_url(again))
        try:
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE decisions SET state = state"))
            return await reset(engine, serving, Generator(settings_for(serving_dir, again)))
        finally:
            await engine.dispose()

    assert asyncio.run(run())["decided"] == summary["decided"]
    first, second = asyncio.run(_dump(sqlite_url(path))), asyncio.run(_dump(sqlite_url(again)))
    assert first == second
    assert first["simulator_assumptions"][0]["version"] == ASSUMPTIONS_V1
    assert first["coupon_rules"][0]["approved_by"] == "luca"


def test_the_card_of_a_reset_return_is_served_for_every_settings(
    serving: Serving, serving_dir: Path, seeded_db: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """The same database read by an app in cached mode keeps the drafts it stored."""
    database = tmp_path / "cached.sqlite"
    shutil.copyfile(seeded_db[0], database)
    settings = settings_for(serving_dir, database, demo_mode="cached")
    for api in open_api(serving, settings, database):
        response = api.client.get(f"/api/returns/{golden_return(serving, GIULIA)}")
        assert response.status_code == 200 and response.headers["X-ReturnRing-Mode"] == "cached"
        assert response.json()["draft"]["generation"]["served_from"] == "live"


def test_settings_type_is_frozen() -> None:
    settings = Settings(device="cpu", llm_mode="mock", demo_mode="live", seed=1)
    with pytest.raises(AttributeError):
        settings.seed = 2  # type: ignore[misc]


def test_evidence_and_scores_are_read_from_the_snapshot_before_the_request(
    serving: Serving,
) -> None:
    """MOD-08: the customer snapshot of a decision is strictly before its request."""
    for return_id in serving.open_return_ids():
        record = serving.store_return(return_id)
        assert record is not None
        context = serving.customer_context(record.customer_id, record.known_at)
        assert context.as_of < record.known_at
        assert context.evidence_quality in (
            EvidenceQuality.SUFFICIENT,
            EvidenceQuality.INSUFFICIENT,
        )
        scores = CaseScores(0.5, context.value_eur, context.evidence_quality, context.cluster_id)
        assert scores.value_eur == Decimal(str(context.value["value_eur"]))


POSTGRES_URL = os.environ.get("RETURNRING_TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="RETURNRING_TEST_POSTGRES_URL is not set")
def test_the_reset_and_the_cards_on_postgresql(
    serving: Serving, serving_dir: Path, seeded_db: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """The same reset writes the same rows on PostgreSQL (asyncpg) as on SQLite, every CHECK
    and trigger included, and every card is served from them."""
    assert POSTGRES_URL
    settings = settings_for(serving_dir, tmp_path / "unused.sqlite", database_url=POSTGRES_URL)

    async def run() -> dict[str, Any]:
        engine = make_engine(POSTGRES_URL or "")
        try:
            return await reset(engine, serving, Generator(settings))
        finally:
            await engine.dispose()

    summary = asyncio.run(run())
    assert not summary["failed"] and summary["decided"] == seeded_db[1]["decided"]
    on_pg = asyncio.run(_dump(POSTGRES_URL))
    on_sqlite = asyncio.run(_dump(sqlite_url(seeded_db[0])))
    for table, rows in on_sqlite.items():
        assert on_pg[table] == rows, table
    for api in open_api(serving, settings, tmp_path / "unused.sqlite"):
        for return_id in serving.open_return_ids():
            response = api.client.get(f"/api/returns/{return_id}")
            assert response.status_code == 200, response.text
            validate_body("case_card", response.json())
