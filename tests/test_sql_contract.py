"""Persistence contract (contracts/sql.py, ADR-15): tables, CHECK constraints, append-only logs.

Runs on SQLite through aiosqlite. Set RETURNRING_TEST_POSTGRES_URL (postgresql+asyncpg://...) to
run the same tests on PostgreSQL; without it the PostgreSQL DDL is only compiled and inspected.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Table, create_mock_engine, delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CheckConstraint

from apps.api import schemas as s
from contracts import sql
from contracts.vocab import (
    AUDIT_ACTIONS,
    AUDIT_ENTITY_TYPES,
    CATALOG_TASK_STATUSES,
    COUPON_APPLICATION_STATES,
    DECISION_STATUSES,
    EVIDENCE_KINDS,
    GENERATION_PATHS,
    INSPECTION_STATES,
    OUTBOX_STATUSES,
    OVERRIDE_CATEGORIES,
)
from policy.engine import NAVIGLI_BASE_TERMS

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples" / "api"
POSTGRES_URL = os.environ.get("RETURNRING_TEST_POSTGRES_URL")
T0 = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)
DAY = date(2026, 9, 28)
BASE_TERMS = s.Terms.from_policy(NAVIGLI_BASE_TERMS).model_dump()
Q4_LEVERS = [
    {"id": "return_window_days", "value": 14, "scope": "future_orders"},
    {"id": "transparency_notice", "value": True, "scope": "communication"},
]
Row = dict[str, Any]


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


# ---- Rows ----------------------------------------------------------------------------------------


def return_request(return_id: str) -> Row:
    return {
        "return_id": return_id,
        "order_id": f"ORD-{return_id[-6:]}",
        "line_id": f"L-{return_id[-6:]}-1",
        "customer_id": "C-MARCO-1",
        "variant_id": "V-COA-1180",
        "reason": "changed_mind",
        "customer_text": "Non mi piace più.",
        "delivered_on": DAY - timedelta(days=6),
        "notice_on": DAY,
        "requested_at": T0 - timedelta(days=2),
        "order_terms_snapshot_id": f"TS-{return_id[-6:]}",
        "source": "store",
        "received_at": T0 - timedelta(days=2),
    }


def generation(generation_id: str) -> Row:
    return {
        "generation_id": generation_id,
        "kind": "case_draft",
        "return_id": "RET-000102",
        "decision_id": None,
        "model": "mock",
        "llm_mode": "mock",
        "served_from": "live",
        "generation_path": "llm",
        "attempts": 1,
        "prompt_version": "prompt-0.1.0",
        "input_digest": "0" * 64,
        "tone": "warm",
        "output": {"decision_echo": {"path": "withdrawal"}},
        "validator_errors": [],
        "latency_ms": 14,
        "prompt_tokens": 1200,
        "completion_tokens": 310,
        "tokens_per_second": 52.5,
        "created_at": T0,
    }


def assumptions(version: str, seq: int) -> Row:
    return {
        "version": version,
        "seq": seq,
        "supersedes_version": None if seq == 1 else f"assumptions-v{seq - 1}",
        "period": "month, 1,000 return requests (illustrative)",
        "logistics_returns_avoided": 80,
        "logistics_cost_per_return_eur": Decimal("8.00"),
        "undue_discounts_avoided": 25,
        "avg_undue_discount_eur": Decimal("12.00"),
        "minutes_saved": Decimal("240"),
        "hourly_cost_eur": Decimal("22.00"),
        "incentives_eur": Decimal("200.00"),
        "system_cost_eur": Decimal("120.00"),
        "error_cost_eur": Decimal("100.00"),
        "abandonment_margin_lost_eur": Decimal("240.00"),
        "incremental_margin_eur": Decimal("0"),
        "basis": "simulated",
        "baseline": "Navigli base policy on the same period",
        "note": "Seeded with the proposals example",
        "created_by": "system" if seq == 1 else "luca",
        "created_at": T0,
    }


def coupon_rule(version: str, seq: int) -> Row:
    return {
        "rule_version": version,
        "seq": seq,
        "supersedes_version": None if seq == 1 else f"coupon-rule-v{seq - 1}",
        "text": "Coupons paused for a Q4 cluster once an operator confirms it",
        "approved_by": "luca",
        "approved_at": T0 - timedelta(days=1),
    }


def decision(decision_id: str, return_id: str, **changes: Any) -> Row:
    """An approved Q4 decision with a cluster coupon application, as the engine produces it."""
    row: Row = {
        "decision_id": decision_id,
        "return_id": return_id,
        "version": 1,
        "supersedes_decision_id": None,
        "revision_trigger": "initial",
        "customer_id": "C-MARCO-1",
        "order_id": "ORD-005230",
        "risk": 0.87,
        "value_eur": Decimal("42.10"),
        "cluster_id": "CL-000417",
        "risk_threshold": 0.6,
        "value_threshold_eur": Decimal("185.00"),
        "base_terms": BASE_TERMS,
        "order_terms": BASE_TERMS,
        "path": "withdrawal",
        "evidence_quality": "sufficient",
        "missing_evidence": [],
        "quadrant": "Q4",
        "proposed_levers": Q4_LEVERS,
        "levers": Q4_LEVERS,
        "requires_approval": True,
        "current_terms": BASE_TERMS,
        "future_terms": {**BASE_TERMS, "return_window_days": 14},
        "refund_due_by": DAY + timedelta(days=14),
        "decided_on": DAY,
        "review_at": DAY + timedelta(days=90),
        "alternatives": [],
        "expected_net_benefit_eur": Decimal("123.50"),
        "coupon_rule_version": "coupon-rule-v1",
        "coupon_application_id": "CRA-000031",
        "state": "approved",
        "approver": "sara",
        "approved_at": T0 + timedelta(minutes=20),
        "override_reason": None,
        "override_category": None,
        "rules_version": "policy-2026-10-12",
        "model_versions": {"risk": "risk-xgb-0.1.0"},
        "assumptions_version": "assumptions-v1",
        "generation_id": "GEN-1",
        "generation_path": "llm",
        "created_at": T0 - timedelta(days=2),
    }
    return {**row, **changes}


SEEDS: list[tuple[str, list[Row]]] = [
    ("return_requests", [return_request(f"RET-{n:06d}") for n in range(101, 113)]),
    ("llm_generations", [generation("GEN-1")]),
    ("simulator_assumptions", [assumptions("assumptions-v1", 1)]),
    ("coupon_rules", [coupon_rule("coupon-rule-v1", 1)]),
    ("decisions", [decision("DEC-000102-1", "RET-000102")]),
]


def fresh_rows() -> dict[str, Row]:
    """One valid row per table, not yet inserted (the seeds hold its parents)."""
    return {
        "return_requests": return_request("RET-000199"),
        "llm_generations": generation("GEN-2"),
        "simulator_assumptions": assumptions("assumptions-v2", 2),
        "coupon_rules": coupon_rule("coupon-rule-v2", 2),
        "decisions": decision(
            "DEC-000103-1",
            "RET-000103",
            quadrant="Q3",
            proposed_levers=[{"id": "instant_refund", "value": False, "scope": "future_orders"}],
            levers=[{"id": "instant_refund", "value": False, "scope": "future_orders"}],
            coupon_rule_version=None,
            coupon_application_id=None,
        ),
        "coupon_rule_applications": {
            "application_id": "CRA-000032",
            "rule_version": "coupon-rule-v1",
            "cluster_id": "CL-000417",
            "decision_id": "DEC-000102-1",
            "affected_customer_ids": ["C-MARCO-1", "C-MARCO-2"],
            "state": "confirmed",
            "proposed_at": T0 - timedelta(days=2),
            "confirmed_by": "sara",
            "confirmed_at": T0,
            "review_at": DAY + timedelta(days=90),
            "reversible": True,
            "reversed_by": None,
            "reversed_at": None,
            "reversal_reason": None,
        },
        "audit_log": {
            "at": T0,
            "actor": "sara",
            "actor_role": "operator",
            "action": "decision_approved",
            "entity_type": "decision",
            "entity_id": "DEC-000102-1",
            "return_id": "RET-000102",
            "decision_id": "DEC-000102-1",
            "reason": None,
            "before": {"state": "proposed"},
            "after": {"state": "approved"},
            "rules_version": "policy-2026-10-12",
            "model_versions": {"risk": "risk-xgb-0.1.0"},
            "assumptions_version": "assumptions-v1",
            "request_id": "req-000003",
        },
        "outbox": {
            "message_id": "MSG-1",
            "return_id": "RET-000102",
            "decision_id": "DEC-000102-1",
            "customer_id": "C-MARCO-1",
            "lang": "it",
            "subject": "Il tuo reso RET-000102",
            "body": "Ciao...",
            "legal_clause_kind": "withdrawal",
            "template_fields": {"terms": "...", "deadline": "12 ottobre 2026"},
            "generation_id": "GEN-1",
            "status": "sent_simulated",
            "created_by": "sara",
            "created_at": T0,
        },
        "terms_snapshots": {
            "terms_snapshot_id": "TS-CHK-1",
            "customer_id": "C-MARCO-1",
            "order_id": "ORD-CHK-1",
            "source": "DEC-000102-1",
            "source_decision_id": "DEC-000102-1",
            "quadrant_at_purchase": "Q4",
            "return_window_days": 14,
            "free_return_shipping": False,
            "instant_refund": False,
            "free_size_exchange": True,
            "coupons_enabled": False,
            "coupon_application_id": None,
            "cart": [{"variant_id": "V-JKT-2207", "size": "M", "quantity": 1}],
            "shown_at": T0,
            "confirmed_at": T0 + timedelta(minutes=2),
        },
        "evidence_additions": {
            "evidence_id": "EV-1",
            "return_id": "RET-000102",
            "decision_before_id": "DEC-000102-1",
            "decision_after_id": None,
            "kind": "household_members",
            "note": "Two people in one household",
            "target_link_ids": ["C-MARCO-1~C-MARCO-2"],
            "author": "sara",
            "created_at": T0,
            "before": None,
            "after": None,
        },
        "link_disputes": {
            "dispute_id": "LD-1",
            "link_id": "C-10233~C-20871",
            "customer_a": "C-10233",
            "customer_b": "C-20871",
            "return_id": "RET-000106",
            "reason": "Card used once for a gift",
            "author": "sara",
            "created_at": T0,
            "before": {"quadrant": "Q3"},
            "after": {"quadrant": "Q1"},
        },
        "inspections": {
            "inspection_id": "INS-1",
            "return_id": "RET-000102",
            "decision_id": "DEC-000102-1",
            "inspector": "ahmed",
            "photo_id": "PH-1",
            "photo_media_type": "image/jpeg",
            "photo_sha256": "f" * 64,
            "state": "confirmed",
            "checklist": [{"item": "visible_damage", "suggestion": "yes"}],
            "min_model_confidence": 0.71,
            "manual_review_reason": None,
            "proposal": {"rule_id": "DV-RULE-1", "amount_eur": 17.98},
            "confirmed_observations": [{"item": "visible_damage", "observation": "yes"}],
            "confirmed_by": "ahmed",
            "confirmed_at": T0,
            "compensation_applied": True,
            "compensation_eur": Decimal("17.98"),
            "compensation_rule_id": "DV-RULE-1",
            "generation_id": "GEN-1",
            "created_at": T0 - timedelta(minutes=6),
        },
        "catalog_tasks": {
            "task_id": "CT-1",
            "anomaly_id": "V-JKT-DEF~defective~2026-09-21",
            "variant_id": "V-JKT-DEF",
            "reason": "defective",
            "week": date(2026, 9, 21),
            "kind": "batch_check",
            "status": "done",
            "note": "Sleeves checked against the size guide",
            "created_by": "luca",
            "created_at": T0,
            "updated_at": T0 + timedelta(days=3),
            "done_at": T0 + timedelta(days=3),
            "return_id": "RET-000105",
        },
    }


# ---- Database harness ----------------------------------------------------------------------------

Action = Callable[[AsyncEngine], Awaitable[None]]


@pytest.fixture(params=["sqlite", *(["postgresql"] if POSTGRES_URL else [])])
def database_url(request: pytest.FixtureRequest, tmp_path: Path) -> str:
    if request.param == "postgresql":
        assert POSTGRES_URL
        return POSTGRES_URL
    return f"sqlite+aiosqlite:///{tmp_path / 'returnring.db'}"


async def _session(url: str, action: Action) -> None:
    engine = create_async_engine(url)
    sql.enable_sqlite_foreign_keys(engine)
    try:
        if engine.dialect.name == "postgresql":
            async with engine.begin() as conn:
                await conn.run_sync(sql.metadata.drop_all)
        await sql.create_all(engine)
        async with engine.begin() as conn:
            for name, rows in SEEDS:
                await conn.execute(sql.metadata.tables[name].insert(), rows)
        await action(engine)
    finally:
        await engine.dispose()


def run(url: str, action: Action) -> None:
    asyncio.run(_session(url, action))


async def insert(engine: AsyncEngine, table: str, row: Row) -> None:
    async with engine.begin() as conn:
        await conn.execute(sql.metadata.tables[table].insert(), [row])


def assert_rejected(url: str, table: str, row: Row, names: tuple[str, ...]) -> None:
    async def action(engine: AsyncEngine) -> None:
        with pytest.raises(DBAPIError) as caught:
            await insert(engine, table, row)
        message = str(caught.value)
        assert any(f"ck_{table}_{n}" in message for n in names), message

    run(url, action)


# ---- Tests ---------------------------------------------------------------------------------------


def test_create_all_creates_every_table_and_accepts_valid_rows(database_url: str) -> None:
    async def action(engine: AsyncEngine) -> None:
        rows = fresh_rows()
        for table in sql.TABLES:
            await insert(engine, table.name, rows[table.name])
        async with engine.connect() as conn:
            for table in sql.TABLES:
                count = (await conn.execute(select(func.count()).select_from(table))).scalar()
                assert count and count >= 1, table.name

    run(database_url, action)
    assert {t.name for t in sql.TABLES} == set(fresh_rows())


VIOLATIONS: list[tuple[str, dict[str, Any], tuple[str, ...]]] = [
    # decisions: spec 8.6, rules 3 to 5, US-306, US-307, US-607
    ("decisions", {"path": "guarantee"}, ("guarantee_unscored",)),
    ("decisions", {"path": "guarantee", "quadrant": None, "risk": None}, ("guarantee_unscored",)),
    ("decisions", {"evidence_quality": "insufficient"}, ("insufficient_no_quadrant",)),
    ("decisions", {"requires_approval": False, "review_at": None}, ("q3_q4_need_approval",)),
    ("decisions", {"review_at": None}, ("restriction_has_review", "approved_restriction")),
    ("decisions", {"review_at": DAY - timedelta(days=1)}, ("review_after_decision",)),
    ("decisions", {"approver": None}, ("approved_restriction", "acted_has_approver")),
    ("decisions", {"approver": "system"}, ("approved_restriction", "acted_has_approver")),
    ("decisions", {"approver": "  "}, ("approved_restriction", "acted_has_approver")),
    ("decisions", {"approved_at": None}, ("approved_restriction", "acted_has_approver")),
    ("decisions", {"state": "proposed"}, ("acted_has_approver",)),
    ("decisions", {"state": "overridden"}, ("override_has_reason",)),
    (
        "decisions",
        {"state": "overridden", "override_reason": "  ", "override_category": "other"},
        ("override_has_reason",),
    ),
    (
        "decisions",
        {"state": "overridden", "override_reason": "a real reason", "override_category": None},
        ("override_has_reason",),
    ),
    (
        "decisions",
        {"override_reason": "a real reason", "override_category": "other"},
        ("override_has_reason",),
    ),
    (
        "decisions",
        {"state": "rejected", "override_reason": "not justified", "override_category": "other"},
        ("rejected_not_restrictive",),
    ),
    ("decisions", {"state": "archived"}, ("state",)),
    ("decisions", {"path": "exchange"}, ("path", "guarantee_unscored")),
    ("decisions", {"evidence_quality": "partial"}, ("evidence_quality",)),
    ("decisions", {"quadrant": "Q5"}, ("quadrant",)),
    ("decisions", {"override_category": "mood"}, ("override_category", "override_has_reason")),
    ("decisions", {"risk": 1.5}, ("risk_range",)),
    ("decisions", {"risk_threshold": 0.0}, ("risk_threshold_range",)),
    ("decisions", {"version": 0}, ("version_positive", "version_chain")),
    ("decisions", {"version": 2}, ("version_chain",)),
    ("decisions", {"revision_trigger": "manual"}, ("revision_trigger", "version_chain")),
    ("decisions", {"coupon_application_id": "CRA-X"}, ("coupon_application_has_rule",)),
    ("decisions", {"generation_id": None}, ("generation_pair",)),
    ("decisions", {"generation_path": "free_text"}, ("generation_path",)),
    # audit_log: rule 5, US-604
    ("audit_log", {"actor": "system"}, ("system_role",)),
    ("audit_log", {"actor_role": "system"}, ("system_role",)),
    (
        "audit_log",
        {"actor": "system", "actor_role": "system"},
        ("human_action",),
    ),
    ("audit_log", {"action": "decision_overridden"}, ("reason_required",)),
    ("audit_log", {"action": "link_disputed", "reason": "no"}, ("reason_required",)),
    ("audit_log", {"actor": " "}, ("actor",)),
    ("audit_log", {"action": "row_deleted"}, ("action",)),
    ("audit_log", {"entity_type": "customer"}, ("entity_type",)),
    ("audit_log", {"actor_role": "admin"}, ("actor_role",)),
    # outbox
    ("outbox", {"created_by": "system"}, ("created_by_person",)),
    ("outbox", {"lang": "fr"}, ("lang",)),
    ("outbox", {"body": " "}, ("body",)),
    ("outbox", {"legal_clause_kind": "marketing"}, ("legal_clause_kind",)),
    ("outbox", {"status": "bounced"}, ("status",)),
    # terms_snapshots: rule 3, ADR-05, E2E-02
    ("terms_snapshots", {"return_window_days": 7}, ("window_legal",)),
    ("terms_snapshots", {"confirmed_at": None}, ("confirmed_with_order",)),
    ("terms_snapshots", {"order_id": None}, ("confirmed_with_order",)),
    ("terms_snapshots", {"confirmed_at": T0 - timedelta(hours=1)}, ("confirmed_after_shown",)),
    ("terms_snapshots", {"source": "base"}, ("source",)),
    ("terms_snapshots", {"quadrant_at_purchase": None}, ("source",)),
    ("terms_snapshots", {"quadrant_at_purchase": "Q9"}, ("quadrant",)),
    # inspections: US-501..503, LEG-05
    ("inspections", {"compensation_rule_id": None}, ("compensation_evidence",)),
    ("inspections", {"compensation_eur": Decimal("0")}, ("compensation_evidence",)),
    ("inspections", {"compensation_applied": False}, ("compensation_pair",)),
    (
        "inspections",
        {"state": "awaiting_confirmation"},
        ("confirmed_fields", "compensation_evidence"),
    ),
    ("inspections", {"confirmed_by": None}, ("confirmed_fields",)),
    ("inspections", {"manual_review_reason": "Photo too dark"}, ("unsure_no_proposal",)),
    (
        "inspections",
        {
            "state": "manual_review",
            "confirmed_observations": None,
            "confirmed_by": None,
            "confirmed_at": None,
            "compensation_applied": False,
            "compensation_eur": None,
            "compensation_rule_id": None,
            "proposal": None,
        },
        ("manual_reason",),
    ),
    ("inspections", {"min_model_confidence": 1.2}, ("confidence_range",)),
    ("inspections", {"state": "lost"}, ("state", "confirmed_fields")),
    ("inspections", {"inspector": "system"}, ("inspector_person",)),
    ("inspections", {"photo_media_type": "image/gif"}, ("photo_media_type",)),
    # link_disputes and evidence: US-310
    (
        "link_disputes",
        {"customer_a": "C-20871", "customer_b": "C-10233", "link_id": "C-20871~C-10233"},
        ("ordered_pair",),
    ),
    ("link_disputes", {"link_id": "C-10233-C-20871"}, ("link_id",)),
    ("link_disputes", {"reason": "no"}, ("reason",)),
    ("link_disputes", {"author": "system"}, ("author_person",)),
    ("evidence_additions", {"kind": "rumour"}, ("kind",)),
    ("evidence_additions", {"note": "ok"}, ("note",)),
    ("evidence_additions", {"author": "system"}, ("author_person",)),
    ("evidence_additions", {"decision_after_id": "DEC-000102-1"}, ("new_version",)),
    # catalog_tasks: US-606
    ("catalog_tasks", {"done_at": None}, ("done_at",)),
    ("catalog_tasks", {"status": "open"}, ("done_at",)),
    ("catalog_tasks", {"note": ""}, ("note",)),
    ("catalog_tasks", {"created_by": "system"}, ("created_by_person",)),
    ("catalog_tasks", {"updated_at": T0 - timedelta(days=1)}, ("updated_after_created",)),
    ("catalog_tasks", {"kind": "rewrite"}, ("kind",)),
    ("catalog_tasks", {"status": "paused", "done_at": None}, ("status",)),
    ("catalog_tasks", {"reason": "bored"}, ("reason",)),
    # coupon rules and applications: US-402, LEG-09, US-607
    ("coupon_rule_applications", {"confirmed_by": None}, ("confirmed_by_person",)),
    ("coupon_rule_applications", {"confirmed_by": "system"}, ("confirmed_by_person",)),
    ("coupon_rule_applications", {"state": "proposed"}, ("confirmed_by_person",)),
    ("coupon_rule_applications", {"reversible": False}, ("always_reversible",)),
    ("coupon_rule_applications", {"state": "expired"}, ("state", "confirmed_by_person")),
    ("coupon_rule_applications", {"state": "reversed"}, ("reversal_fields",)),
    (
        "coupon_rule_applications",
        {
            "state": "reversed",
            "reversed_by": "luca",
            "reversed_at": T0,
            "reversal_reason": "ok",
        },
        ("reversal_fields",),
    ),
    ("coupon_rules", {"approved_by": "system"}, ("approved_by_person",)),
    ("coupon_rules", {"supersedes_version": None}, ("version_chain",)),
    ("coupon_rules", {"text": " "}, ("text",)),
    # simulator assumptions: rule 9 (no refund column, every input non-negative)
    ("simulator_assumptions", {"incentives_eur": Decimal("-1")}, ("non_negative",)),
    ("simulator_assumptions", {"logistics_returns_avoided": -3}, ("non_negative",)),
    ("simulator_assumptions", {"basis": "guessed"}, ("basis",)),
    ("simulator_assumptions", {"supersedes_version": None}, ("version_chain",)),
    ("simulator_assumptions", {"created_by": ""}, ("created_by",)),
    ("simulator_assumptions", {"note": "  "}, ("note",)),
    # llm generations: spec 8.7
    ("llm_generations", {"attempts": 2}, ("path_attempts",)),
    ("llm_generations", {"attempts": 3}, ("attempts_range", "path_attempts")),
    ("llm_generations", {"served_from": "cached"}, ("mock_is_live",)),
    ("llm_generations", {"input_digest": "abc"}, ("input_digest",)),
    ("llm_generations", {"latency_ms": -1}, ("non_negative",)),
    ("llm_generations", {"generation_path": "guess"}, ("generation_path", "path_attempts")),
    ("llm_generations", {"kind": "chat"}, ("kind",)),
    ("llm_generations", {"llm_mode": "openai"}, ("llm_mode", "mock_is_live")),
    ("llm_generations", {"served_from": "edge", "llm_mode": "vllm"}, ("served_from",)),
    ("llm_generations", {"tone": "angry"}, ("tone",)),
    # return requests
    ("return_requests", {"notice_on": DAY - timedelta(days=30)}, ("notice_after_delivery",)),
    ("return_requests", {"reason": "bored"}, ("reason",)),
    ("return_requests", {"source": "email"}, ("source",)),
]


@pytest.mark.parametrize(
    ("table", "changes", "names"),
    VIOLATIONS,
    ids=[f"{t}-{'-'.join(n)}-{i}" for i, (t, _, n) in enumerate(VIOLATIONS)],
)
def test_check_constraint_rejects_an_invalid_row(
    database_url: str, table: str, changes: dict[str, Any], names: tuple[str, ...]
) -> None:
    row = {**fresh_rows()[table], **changes}
    assert_rejected(database_url, table, row, names)


def test_every_named_check_is_exercised() -> None:
    exercised = {(t, n) for t, _, names in VIOLATIONS for n in names}
    declared = {
        (table.name, c.name.removeprefix(f"ck_{table.name}_"))
        for table in sql.TABLES
        for c in table.constraints
        if isinstance(c, CheckConstraint) and isinstance(c.name, str)
    }
    assert declared - exercised == set()


@pytest.mark.parametrize("table", sql.APPEND_ONLY_TABLES)
def test_append_only_tables_refuse_update_and_delete(database_url: str, table: str) -> None:
    async def action(engine: AsyncEngine) -> None:
        rows = fresh_rows()
        if table == "audit_log":
            await insert(engine, table, rows[table])
        target: Table = sql.metadata.tables[table]
        first = next(iter(target.primary_key.columns))
        for statement in (
            update(target).values({first.name: first.table.c[first.name]}),
            delete(target),
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                async with engine.begin() as conn:
                    await conn.execute(statement)
        async with engine.connect() as conn:
            assert (await conn.execute(select(func.count()).select_from(target))).scalar() == 1

    run(database_url, action)


def test_foreign_keys_and_unique_keys_are_enforced(database_url: str) -> None:
    async def action(engine: AsyncEngine) -> None:
        with pytest.raises(DBAPIError):
            await insert(engine, "decisions", decision("DEC-X-1", "RET-NOPE"))
        with pytest.raises(DBAPIError):  # one row per (return, version)
            await insert(engine, "decisions", decision("DEC-000102-9", "RET-000102"))
        rows = fresh_rows()
        await insert(engine, "link_disputes", rows["link_disputes"])
        with pytest.raises(DBAPIError):  # a link is disputed once
            await insert(engine, "link_disputes", {**rows["link_disputes"], "dispute_id": "LD-2"})
        with pytest.raises(DBAPIError):  # an assumption version supersedes an existing one
            await insert(
                engine,
                "simulator_assumptions",
                {**rows["simulator_assumptions"], "supersedes_version": "assumptions-v9"},
            )

    run(database_url, action)


def decision_row(view: s.DecisionView) -> Row:
    """The decisions row of an API DecisionView (the mapping phase 4 implements)."""
    inputs = view.inputs
    number = view.return_id.removeprefix("RET-")
    return {
        "decision_id": view.decision_id,
        "return_id": view.return_id,
        "version": view.version,
        "supersedes_decision_id": None if view.version == 1 else f"DEC-{number}-{view.version - 1}",
        "revision_trigger": "initial" if view.version == 1 else "evidence_added",
        "customer_id": "C-ANY",
        "order_id": "ORD-ANY",
        "risk": inputs.risk,
        "value_eur": None if inputs.value_eur is None else Decimal(str(inputs.value_eur)),
        "cluster_id": inputs.cluster_id,
        "risk_threshold": inputs.thresholds.risk,
        "value_threshold_eur": Decimal(str(inputs.thresholds.value_eur)),
        "base_terms": inputs.base_terms.model_dump(),
        "order_terms": inputs.order_terms.model_dump(),
        "path": view.path.value,
        "evidence_quality": view.evidence_quality.value,
        "missing_evidence": view.missing_evidence,
        "quadrant": view.quadrant.value if view.quadrant else None,
        "proposed_levers": [x.model_dump() for x in view.proposed_levers],
        "levers": [x.model_dump() for x in view.levers],
        "requires_approval": view.requires_approval,
        "current_terms": view.current_return_terms.model_dump(),
        "future_terms": view.future_terms.model_dump(),
        "refund_due_by": view.refund_due_by,
        "decided_on": view.decided_on,
        "review_at": view.review_at,
        "alternatives": [],
        "expected_net_benefit_eur": Decimal("0"),
        "coupon_rule_version": view.coupon_rule.rule_version if view.coupon_rule else None,
        "coupon_application_id": view.coupon_rule.application_id if view.coupon_rule else None,
        "state": view.state,
        "approver": view.approver,
        "approved_at": view.approved_at,
        "override_reason": view.override_reason,
        "override_category": view.override_category,
        "rules_version": view.rules_version,
        "model_versions": view.model_versions,
        "assumptions_version": view.assumptions_version,
        "generation_id": "GEN-1" if view.generation_id else None,
        "generation_path": "llm" if view.generation_id else None,
        "created_at": view.created_at,
    }


def example_decisions() -> list[s.DecisionView]:
    """Every decision of the examples, latest state per decision id, versions in order."""
    found: dict[str, s.DecisionView] = {}
    for path in sorted(EXAMPLES.glob("*.json")):
        name = path.name.split(".")[0]
        if name == "index":
            continue
        model = s.API_MODELS[name].model_validate_json(path.read_text(encoding="utf-8"))
        views = [getattr(model, "decision", None)]
        recompute = getattr(model, "recompute", None)
        if recompute is not None:
            views.append(recompute.decision)
        for view in views:
            if isinstance(view, s.DecisionView):
                previous = found.get(view.decision_id)
                if previous is None or previous.state == "proposed":
                    found[view.decision_id] = view
    return sorted(found.values(), key=lambda v: (v.version, v.decision_id))


def test_every_example_decision_fits_the_decisions_table(database_url: str) -> None:
    views = example_decisions()
    assert {v.state for v in views} == {"proposed", "approved", "overridden", "rejected"}

    async def action(engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            await conn.execute(sql.decisions.delete())
        for view in views:
            row = decision_row(view)
            supersedes = row["supersedes_decision_id"]
            if supersedes and supersedes not in {v.decision_id for v in views}:
                base = {**row, "decision_id": supersedes, "version": 1}
                base.update(supersedes_decision_id=None, revision_trigger="initial")
                await insert(engine, "decisions", base)
            await insert(engine, "decisions", row)

    run(database_url, action)


# ---- PostgreSQL DDL ------------------------------------------------------------------------------


def postgres_ddl() -> list[str]:
    statements: list[str] = []
    engine = create_mock_engine(
        "postgresql+asyncpg://",
        lambda element, *a, **kw: statements.append(str(element.compile(dialect=engine.dialect))),
    )
    sql.metadata.create_all(engine, checkfirst=False)
    return statements


def test_postgresql_ddl_has_jsonb_checks_and_append_only_triggers() -> None:
    ddl = postgres_ddl()
    text = "\n".join(ddl)
    for table in sql.TABLES:
        assert f"CREATE TABLE {table.name} " in text, table.name
        for c in table.constraints:
            if isinstance(c, CheckConstraint):
                assert f"CONSTRAINT {c.name} CHECK" in text, c.name
    assert "JSONB" in text and "TIMESTAMP WITH TIME ZONE" in text
    assert "CREATE OR REPLACE FUNCTION returnring_append_only()" in text
    assert "RAISE EXCEPTION '% is append-only'" in text
    for name in sql.APPEND_ONLY_TABLES:
        assert f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE ON {name}" in text
        assert f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {name}" in text
    assert "RAISE(ABORT" not in text  # the SQLite triggers stay on SQLite


@pytest.mark.parametrize(
    ("table", "column", "values"),
    [
        ("decisions", "state", DECISION_STATUSES),
        ("decisions", "override_category", OVERRIDE_CATEGORIES),
        ("decisions", "generation_path", GENERATION_PATHS),
        ("audit_log", "action", AUDIT_ACTIONS),
        ("audit_log", "entity_type", AUDIT_ENTITY_TYPES),
        ("outbox", "status", OUTBOX_STATUSES),
        ("inspections", "state", INSPECTION_STATES),
        ("catalog_tasks", "status", CATALOG_TASK_STATUSES),
        ("coupon_rule_applications", "state", COUPON_APPLICATION_STATES),
        ("evidence_additions", "kind", EVIDENCE_KINDS),
    ],
)
def test_checks_use_the_api_vocabularies(table: str, column: str, values: tuple[str, ...]) -> None:
    constraint = next(
        c
        for c in sql.metadata.tables[table].constraints
        if isinstance(c, CheckConstraint) and c.name == f"ck_{table}_{column}"
    )
    rule = str(constraint.sqltext)
    assert all(f"'{v}'" in rule for v in values), rule
