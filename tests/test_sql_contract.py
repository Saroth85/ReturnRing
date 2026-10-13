"""Persistence contract (contracts/sql.py, ADR-15): tables, CHECK constraints, append-only logs.

Runs on SQLite through aiosqlite. Set RETURNRING_TEST_POSTGRES_URL (postgresql+asyncpg://...) to
run the same tests on PostgreSQL 16; without it the PostgreSQL DDL is only compiled and inspected.

Each invalid row breaks exactly one named CHECK, and the test proves it: the same row is accepted
once that one CHECK is removed. So SQLite and PostgreSQL (which reports the first violated CHECK
in name order) name the same constraint.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import MetaData, Table, create_mock_engine, delete, func, select, update
from sqlalchemy.exc import DBAPIError, StatementError
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
    REVISION_TRIGGERS,
)
from policy.engine import NAVIGLI_BASE_TERMS
from policy.types import ReturnTerms

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples" / "api"
POSTGRES_URL = os.environ.get("RETURNRING_TEST_POSTGRES_URL")
T0 = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)
DAY = date(2026, 9, 28)
BASE_TERMS = s.Terms.from_policy(NAVIGLI_BASE_TERMS).model_dump()
BASE_COLUMNS = {f"base_{k}": v for k, v in BASE_TERMS.items()}
Q4_LEVERS = [
    {"id": "return_window_days", "value": 14, "scope": "future_orders"},
    {"id": "transparency_notice", "value": True, "scope": "communication"},
]
SCORES_PROVENANCE = {
    "origin": "risk-xgb-0.1.0 and value-0.1.0 on decision-time features, matrix thresholds",
    "period_start": "2025-10-01",
    "period_end": "2026-09-28",
    "nature": "synthetic",
}
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
        "inputs_provenance": SCORES_PROVENANCE,
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


def audit_state(state: str, approver: str | None) -> Row:
    """The typed before/after payload of an audit entry about a decision (DecisionAuditState)."""
    payload = {
        "decision_id": "DEC-000102-1",
        "version": 1,
        "state": state,
        "quadrant": "Q4",
        "risk": {"value": 0.87, "unit": "probability", "provenance": SCORES_PROVENANCE},
        "lever_ids": ["return_window_days:14", "transparency_notice:true"],
        "requires_approval": True,
        "review_at": (DAY + timedelta(days=90)).isoformat(),
        "approver": approver,
        "override_category": None,
        "coupon_application_id": "CRA-000031",
    }
    return s.DecisionAuditState.model_validate(payload).model_dump(mode="json")


SEEDS: list[tuple[str, list[Row]]] = [
    ("return_requests", [return_request(f"RET-{n:06d}") for n in range(101, 113)]),
    ("llm_generations", [generation("GEN-1")]),
    ("simulator_assumptions", [assumptions("assumptions-v1", 1)]),
    ("coupon_rules", [coupon_rule("coupon-rule-v1", 1)]),
    ("decisions", [decision("DEC-000102-1", "RET-000102")]),
]
UNCONFIRMED: Row = {"confirmed_observations": None, "confirmed_by": None, "confirmed_at": None}
NO_COMPENSATION: Row = {
    "compensation_applied": False,
    "compensation_eur": None,
    "compensation_rule_id": None,
}
SECOND_VERSION: Row = {"version": 2, "supersedes_decision_id": "DEC-000102-1"}
STANDARD_Q2: Row = {
    "quadrant": "Q2",
    "proposed_levers": [],
    "levers": [],
    "requires_approval": False,
    "review_at": None,
    "future_terms": BASE_TERMS,
}
BASE_SNAPSHOT: Row = {
    "source": "base",
    "source_decision_id": None,
    "quadrant_at_purchase": None,
    "review_at": None,
    **BASE_TERMS,
}


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
            "before": audit_state("proposed", None),
            "after": audit_state("approved", "sara"),
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
            "connective_text": "Ciao, il rimborso arriverà entro il {deadline}.",
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
            **BASE_COLUMNS,
            "review_at": DAY + timedelta(days=90),
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


async def _session(url: str, action: Action, metadata: MetaData | None) -> None:
    engine = create_async_engine(url)
    sql.enable_sqlite_foreign_keys(engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(sql.metadata.drop_all)
        if metadata is None:
            await sql.create_all(engine)
        else:
            async with engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
        async with engine.begin() as conn:
            for name, rows in SEEDS:
                await conn.execute(sql.metadata.tables[name].insert(), rows)
        await action(engine)
    finally:
        await engine.dispose()


def run(url: str, action: Action, metadata: MetaData | None = None) -> None:
    """Run `action` on fresh tables (sql.metadata, or `metadata`) holding the SEEDS."""
    asyncio.run(_session(url, action, metadata))


async def insert(engine: AsyncEngine, table: str, row: Row) -> None:
    async with engine.begin() as conn:
        await conn.execute(sql.metadata.tables[table].insert(), [row])


def without_check(table: str, name: str) -> MetaData:
    """A copy of the tables without one named CHECK (and without the append-only triggers)."""
    copy = MetaData(naming_convention=sql.NAMING_CONVENTION)
    for t in sql.metadata.sorted_tables:
        t.to_metadata(copy)
    target = copy.tables[table]
    found = [
        c
        for c in target.constraints
        if isinstance(c, CheckConstraint) and c.name == f"ck_{table}_{name}"
    ]
    assert len(found) == 1, f"ck_{table}_{name} is not declared"
    target.constraints.discard(found[0])
    return copy


def assert_rejected(url: str, table: str, row: Row, name: str) -> None:
    async def action(engine: AsyncEngine) -> None:
        with pytest.raises(DBAPIError) as caught:
            await insert(engine, table, row)
        assert f"ck_{table}_{name}" in str(caught.value), str(caught.value)

    run(url, action)


def assert_accepted(url: str, table: str, row: Row, metadata: MetaData | None = None) -> None:
    async def action(engine: AsyncEngine) -> None:
        await insert(engine, table, row)

    run(url, action, metadata)


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


def valid_rows_with_nulls() -> list[tuple[str, Row]]:
    """Valid rows whose nullable JSON and timestamp columns are an explicit None."""
    rows = fresh_rows()
    awaiting = {**rows["inspections"], "state": "awaiting_confirmation", **UNCONFIRMED}
    awaiting.update(NO_COMPENSATION)
    manual = {**awaiting, "state": "manual_review", "proposal": None}
    manual["manual_review_reason"] = "Photo too dark to check the tag"
    proposed = {**rows["decisions"], "state": "proposed", "approver": None, "approved_at": None}
    proposed.update(generation_id=None, generation_path=None)
    shown = {**rows["terms_snapshots"], "order_id": None, "confirmed_at": None}
    application = {**rows["coupon_rule_applications"], "state": "proposed"}
    application.update(confirmed_by=None, confirmed_at=None)
    audit = {**rows["audit_log"], "action": "message_queued", "entity_type": "outbox_message"}
    audit.update(before=None, after=None, model_versions=None, actor="system", actor_role="system")
    return [
        ("inspections", awaiting),
        ("inspections", manual),
        ("decisions", proposed),
        ("terms_snapshots", shown),
        ("terms_snapshots", {**shown, "terms_snapshot_id": "TS-CHK-2", **BASE_SNAPSHOT}),
        ("coupon_rule_applications", application),
        ("audit_log", audit),
        ("link_disputes", {**rows["link_disputes"], "before": None, "after": None}),
    ]


@pytest.mark.parametrize(
    ("table", "row"), valid_rows_with_nulls(), ids=lambda x: x if isinstance(x, str) else ""
)
def test_valid_rows_with_explicit_nulls_are_accepted(
    database_url: str, table: str, row: Row
) -> None:
    """A Python None in a JSON column is SQL NULL, not the JSON null the CHECKs would refuse."""
    assert_accepted(database_url, table, row)


def test_timestamps_round_trip_as_aware_utc(database_url: str) -> None:
    """Every timestamp reads back timezone-aware in UTC, so rows validate as API models."""

    async def action(engine: AsyncEngine) -> None:
        rows = fresh_rows()
        cest = timezone(timedelta(hours=2))
        await insert(engine, "audit_log", {**rows["audit_log"], "at": T0.astimezone(cest)})
        await insert(engine, "coupon_rule_applications", rows["coupon_rule_applications"])
        async with engine.connect() as conn:
            entry = (await conn.execute(select(sql.audit_log))).mappings().one()
            app = (
                (
                    await conn.execute(
                        select(sql.coupon_rule_applications).where(
                            sql.coupon_rule_applications.c.application_id == "CRA-000032"
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert entry["at"] == T0 and entry["at"].utcoffset() == timedelta(0)
        assert app["confirmed_at"].tzinfo is not None
        assert s.AuditEntry.model_validate(dict(entry)).at == T0
        # as_of and in_force are computed when the application is served, not stored
        served = {**dict(app), "as_of": T0 + timedelta(hours=1), "in_force": True}
        assert s.CouponRuleApplication.model_validate(served).confirmed_at == T0

    run(database_url, action)


def test_naive_timestamps_are_refused(database_url: str) -> None:
    async def action(engine: AsyncEngine) -> None:
        row = {**fresh_rows()["audit_log"], "at": T0.replace(tzinfo=None)}
        with pytest.raises(StatementError, match="timezone"):
            await insert(engine, "audit_log", row)

    run(database_url, action)


VIOLATIONS: list[tuple[str, dict[str, Any], str]] = [
    # decisions: spec 8.6, rules 3 to 5, US-306, US-307, US-607
    ("decisions", {"path": "guarantee"}, "guarantee_unscored"),
    ("decisions", {"path": "guarantee", "quadrant": None, "risk": None}, "guarantee_unscored"),
    ("decisions", {"evidence_quality": "insufficient"}, "insufficient_no_quadrant"),
    ("decisions", {"requires_approval": False, "review_at": None}, "q3_q4_need_approval"),
    ("decisions", {"review_at": None}, "restriction_has_review"),
    (
        "decisions",
        {"quadrant": "Q2", "proposed_levers": [], "levers": [], "requires_approval": False},
        "review_only_restriction",
    ),
    ("decisions", {"review_at": DAY - timedelta(days=1)}, "review_after_decision"),
    ("decisions", {"approver": None}, "acted_has_approver"),
    ("decisions", {"approver": "system"}, "acted_has_approver"),
    ("decisions", {"approver": "  "}, "acted_has_approver"),
    ("decisions", {"approved_at": None}, "acted_has_approver"),
    ("decisions", {"state": "proposed"}, "acted_has_approver"),
    ("decisions", {"state": "overridden"}, "override_has_reason"),
    (
        "decisions",
        {"state": "overridden", "override_reason": "  ", "override_category": "other"},
        "override_has_reason",
    ),
    (
        "decisions",
        {"state": "overridden", "override_reason": "a real reason", "override_category": None},
        "override_has_reason",
    ),
    (
        "decisions",
        {"override_reason": "a real reason", "override_category": "other"},
        "override_has_reason",
    ),
    (
        "decisions",
        {"state": "rejected", "override_reason": "not justified", "override_category": "other"},
        "rejected_not_restrictive",
    ),
    ("decisions", {"state": "archived"}, "state"),
    ("decisions", {"path": "exchange"}, "path"),
    ("decisions", {"evidence_quality": "partial"}, "evidence_quality"),
    ("decisions", {"quadrant": "Q5"}, "quadrant"),
    (
        "decisions",
        {"state": "overridden", "override_reason": "a real reason", "override_category": "mood"},
        "override_category",
    ),
    ("decisions", {"risk": 1.5}, "risk_range"),
    ("decisions", {"risk_threshold": 0.0}, "risk_threshold_range"),
    ("decisions", {"version": 0}, "version_positive"),
    ("decisions", {"version": 2}, "version_chain"),
    ("decisions", {**SECOND_VERSION, "revision_trigger": "manual"}, "revision_trigger"),
    (
        "decisions",
        {**SECOND_VERSION, "revision_trigger": "review", **STANDARD_Q2},
        "review_outcome",
    ),  # a review version that neither lifts nor renews: approved without a restriction
    (
        "decisions",
        {
            **SECOND_VERSION,
            "revision_trigger": "review",
            **STANDARD_Q2,
            "state": "rejected",
            "override_reason": "not justified",
            "override_category": "other",
        },
        "review_outcome",
    ),
    ("decisions", {"coupon_application_id": "CRA-X"}, "coupon_application_has_rule"),
    ("decisions", {"generation_id": None}, "generation_pair"),
    ("decisions", {"generation_path": "free_text"}, "generation_path"),
    # audit_log: rule 5, US-604
    ("audit_log", {"actor": "system"}, "system_role"),
    ("audit_log", {"actor_role": "system", "action": "decision_proposed"}, "system_role"),
    ("audit_log", {"actor": "system", "actor_role": "system"}, "human_action"),
    ("audit_log", {"action": "decision_overridden"}, "reason_required"),
    (
        "audit_log",
        {"action": "link_disputed", "entity_type": "link_dispute", "reason": "no"},
        "reason_required",
    ),
    ("audit_log", {"actor": " "}, "actor"),
    ("audit_log", {"action": "row_deleted", "entity_type": "outbox_message"}, "action"),
    ("audit_log", {"entity_type": "customer", "action": "message_queued"}, "entity_type"),
    ("audit_log", {"actor_role": "admin"}, "actor_role"),
    ("audit_log", {"entity_type": "outbox_message"}, "decision_entity"),
    # outbox
    ("outbox", {"created_by": "system"}, "created_by_person"),
    ("outbox", {"lang": "fr"}, "lang"),
    ("outbox", {"body": " "}, "body"),
    ("outbox", {"connective_text": " "}, "connective_text"),
    ("outbox", {"legal_clause_kind": "marketing"}, "legal_clause_kind"),
    ("outbox", {"status": "bounced"}, "status"),
    # terms_snapshots: rule 3, ADR-05, E2E-02, US-607
    ("terms_snapshots", {"return_window_days": 7}, "window_legal"),
    ("terms_snapshots", {"confirmed_at": None}, "confirmed_with_order"),
    ("terms_snapshots", {"order_id": None}, "confirmed_with_order"),
    ("terms_snapshots", {"confirmed_at": T0 - timedelta(hours=1)}, "confirmed_after_shown"),
    ("terms_snapshots", {"source_decision_id": None}, "source"),
    ("terms_snapshots", {"quadrant_at_purchase": None}, "source"),
    ("terms_snapshots", {"quadrant_at_purchase": "Q9"}, "quadrant"),
    ("terms_snapshots", {**BASE_SNAPSHOT, "return_window_days": 60}, "base_terms"),
    (
        "terms_snapshots",
        {**BASE_SNAPSHOT, "review_at": DAY + timedelta(days=90)},
        "base_no_review",
    ),
    ("terms_snapshots", {"review_at": None}, "restriction_has_review"),
    # US-607: a restriction is shown at checkout only before its review date (UTC day)
    ("terms_snapshots", {"review_at": date(2026, 9, 1)}, "review_after_shown"),
    ("terms_snapshots", {"review_at": T0.date()}, "review_after_shown"),
    # inspections: US-501..503, LEG-05
    ("inspections", {"compensation_rule_id": None}, "compensation_evidence"),
    ("inspections", {"compensation_eur": Decimal("0")}, "compensation_evidence"),
    ("inspections", {"compensation_applied": False}, "compensation_pair"),
    ("inspections", {"state": "awaiting_confirmation", **NO_COMPENSATION}, "confirmed_fields"),
    ("inspections", {"confirmed_by": None}, "confirmed_fields"),
    ("inspections", {"manual_review_reason": "Photo too dark"}, "unsure_no_proposal"),
    (
        "inspections",
        {"state": "manual_review", **UNCONFIRMED, **NO_COMPENSATION, "proposal": None},
        "manual_reason",
    ),
    ("inspections", {"min_model_confidence": 1.2}, "confidence_range"),
    ("inspections", {"state": "lost", **UNCONFIRMED, **NO_COMPENSATION}, "state"),
    ("inspections", {"inspector": "system"}, "inspector_person"),
    ("inspections", {"photo_media_type": "image/gif"}, "photo_media_type"),
    # link_disputes and evidence: US-310
    (
        "link_disputes",
        {"customer_a": "C-20871", "customer_b": "C-10233", "link_id": "C-20871~C-10233"},
        "ordered_pair",
    ),
    ("link_disputes", {"link_id": "C-10233-C-20871"}, "link_id"),
    ("link_disputes", {"reason": "no"}, "reason"),
    ("link_disputes", {"author": "system"}, "author_person"),
    ("evidence_additions", {"kind": "rumour"}, "kind"),
    ("evidence_additions", {"note": "ok"}, "note"),
    ("evidence_additions", {"author": "system"}, "author_person"),
    ("evidence_additions", {"decision_after_id": "DEC-000102-1"}, "new_version"),
    # catalog_tasks: US-606
    ("catalog_tasks", {"done_at": None}, "done_at"),
    ("catalog_tasks", {"status": "open"}, "done_at"),
    ("catalog_tasks", {"note": ""}, "note"),
    ("catalog_tasks", {"created_by": "system"}, "created_by_person"),
    ("catalog_tasks", {"updated_at": T0 - timedelta(days=1)}, "updated_after_created"),
    ("catalog_tasks", {"kind": "rewrite"}, "kind"),
    ("catalog_tasks", {"status": "paused", "done_at": None}, "status"),
    ("catalog_tasks", {"reason": "bored"}, "reason"),
    # coupon rules and applications: US-402, LEG-09, US-607
    ("coupon_rule_applications", {"confirmed_by": None}, "confirmed_by_person"),
    ("coupon_rule_applications", {"confirmed_by": "system"}, "confirmed_by_person"),
    ("coupon_rule_applications", {"state": "proposed"}, "confirmed_by_person"),
    ("coupon_rule_applications", {"reversible": False}, "always_reversible"),
    ("coupon_rule_applications", {"state": "expired"}, "state"),
    ("coupon_rule_applications", {"state": "reversed"}, "reversal_fields"),
    (
        "coupon_rule_applications",
        {"state": "reversed", "reversed_by": "luca", "reversed_at": T0, "reversal_reason": "ok"},
        "reversal_fields",
    ),
    ("coupon_rules", {"approved_by": "system"}, "approved_by_person"),
    ("coupon_rules", {"supersedes_version": None}, "version_chain"),
    ("coupon_rules", {"text": " "}, "text"),
    # simulator assumptions: rule 9 (no refund column, every input non-negative)
    ("simulator_assumptions", {"incentives_eur": Decimal("-1")}, "non_negative"),
    ("simulator_assumptions", {"logistics_returns_avoided": -3}, "non_negative"),
    ("simulator_assumptions", {"basis": "guessed"}, "basis"),
    ("simulator_assumptions", {"supersedes_version": None}, "version_chain"),
    ("simulator_assumptions", {"created_by": ""}, "created_by"),
    ("simulator_assumptions", {"note": "  "}, "note"),
    # llm generations: spec 8.7
    ("llm_generations", {"attempts": 2}, "path_attempts"),
    ("llm_generations", {"attempts": 3, "generation_path": "template"}, "attempts_range"),
    ("llm_generations", {"served_from": "cached"}, "mock_is_live"),
    ("llm_generations", {"input_digest": "abc"}, "input_digest"),
    ("llm_generations", {"latency_ms": -1}, "non_negative"),
    ("llm_generations", {"generation_path": "guess"}, "generation_path"),
    ("llm_generations", {"kind": "chat"}, "kind"),
    ("llm_generations", {"llm_mode": "openai"}, "llm_mode"),
    ("llm_generations", {"served_from": "edge", "llm_mode": "vllm"}, "served_from"),
    ("llm_generations", {"tone": "angry"}, "tone"),
    # return requests
    ("return_requests", {"notice_on": DAY - timedelta(days=30)}, "notice_after_delivery"),
    ("return_requests", {"reason": "bored"}, "reason"),
    ("return_requests", {"source": "email"}, "source"),
]


@pytest.mark.parametrize(
    ("table", "changes", "name"),
    VIOLATIONS,
    ids=[f"{t}-{n}-{i}" for i, (t, _, n) in enumerate(VIOLATIONS)],
)
def test_check_constraint_rejects_an_invalid_row(
    database_url: str, table: str, changes: dict[str, Any], name: str
) -> None:
    row = {**fresh_rows()[table], **changes}
    assert_rejected(database_url, table, row, name)
    # exactly this CHECK: without it the row is valid, whatever order the database checks in
    assert_accepted(database_url, table, row, without_check(table, name))


def test_every_named_check_is_exercised() -> None:
    exercised = {(t, n) for t, _, n in VIOLATIONS}
    declared = {
        (table.name, c.name.removeprefix(f"ck_{table.name}_"))
        for table in sql.TABLES
        for c in table.constraints
        if isinstance(c, CheckConstraint) and isinstance(c.name, str)
    }
    assert declared - exercised == set()
    assert exercised - declared == set()


def test_terms_snapshot_restriction_check_mirrors_the_policy(database_url: str) -> None:
    """ck_terms_snapshots_restriction_has_review fires exactly when policy.types says the terms
    are less generous than the base terms stored with the snapshot."""
    combos = [
        ReturnTerms(window, *flags)
        for window in (14, 30, 60)
        for flags in product((True, False), repeat=4)
    ]

    async def action(engine: AsyncEngine) -> None:
        for n, terms in enumerate(combos):
            row = {**fresh_rows()["terms_snapshots"], **asdict(terms), "review_at": None}
            row.update(terms_snapshot_id=f"TS-X-{n}", order_id=f"ORD-X-{n}")
            if terms.is_less_generous_than(NAVIGLI_BASE_TERMS):
                with pytest.raises(DBAPIError, match="restriction_has_review"):
                    await insert(engine, "terms_snapshots", row)
            else:
                await insert(engine, "terms_snapshots", row)

    run(database_url, action)


def test_terms_snapshot_review_is_compared_on_the_utc_day(database_url: str) -> None:
    """ck_terms_snapshots_review_after_shown compares the UTC day of shown_at with review_at, as
    the API models do, whatever the offset of the timestamp or the session time zone."""
    review = date(2026, 12, 27)
    cest = timezone(timedelta(hours=2))
    accepted = (
        datetime(2026, 12, 26, 23, 59, tzinfo=UTC),
        datetime(2026, 12, 27, 1, 30, tzinfo=cest),  # 26 December, 23:30 UTC
    )
    refused = (
        datetime(2026, 12, 27, 0, 0, tzinfo=UTC),
        datetime(2026, 12, 26, 21, 0, tzinfo=timezone(timedelta(hours=-5))),  # 27 Dec, 02:00 UTC
    )

    async def insert_in_new_york(engine: AsyncEngine, row: Row) -> None:
        async with engine.connect() as conn:  # a session time zone other than UTC
            if engine.dialect.name == "postgresql":
                await conn.exec_driver_sql("SET TIME ZONE 'America/New_York'")
            await conn.execute(sql.terms_snapshots.insert(), [row])
            await conn.commit()

    async def action(engine: AsyncEngine) -> None:
        for n, shown in enumerate(accepted + refused):
            row = {**fresh_rows()["terms_snapshots"], "review_at": review, "shown_at": shown}
            row.update(terms_snapshot_id=f"TS-U-{n}", order_id=None, confirmed_at=None)
            if shown in accepted:
                await insert_in_new_york(engine, row)
            else:
                with pytest.raises(DBAPIError, match="review_after_shown"):
                    await insert_in_new_york(engine, row)

    run(database_url, action)
    for shown in accepted:
        assert shown.astimezone(UTC).date() < review
    for shown in refused:
        assert shown.astimezone(UTC).date() >= review


@pytest.mark.parametrize("outcome", ["lift", "renew"])
def test_review_versions_fit_the_table(database_url: str, outcome: str) -> None:
    """A lift (overridden to the standard levers) and a renewal (approved restriction) of a
    restriction due for review are both stored as version 2, with their audit entry."""
    from scripts import build_api_examples as bae

    lorenzo = bae.simple_case(bae.SIMPLE_BY_KEY["lorenzo"])
    v1 = lorenzo.card.decision
    _, result = bae.review_result(
        lorenzo.engine, v1, outcome, "luca", T0 + timedelta(minutes=70),
        entry_id=14, message_id="MSG-000088-2",
        reason="Reviewed at the due date" if outcome == "lift" else None,
        category="periodic_review" if outcome == "lift" else None,
    )  # fmt: skip
    entry = result.audit_entry

    async def action(engine: AsyncEngine) -> None:
        await insert(engine, "return_requests", return_request(v1.return_id))
        for view in (v1, result.decision):
            await insert(engine, "decisions", decision_row(view))
        row = entry.model_dump(exclude={"entry_id"})
        for key in ("before", "after"):
            row[key] = getattr(entry, key).model_dump(mode="json")
        await insert(engine, "audit_log", row)

    run(database_url, action)


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


# ---- Example documents as rows (the mapping phase 4 implements) ----------------------------------


def decision_row(view: s.DecisionView) -> Row:
    """The decisions row of an API DecisionView."""
    inputs = view.inputs
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


def snapshot_row(view: s.TermsSnapshotView) -> Row:
    """The terms_snapshots row of an API TermsSnapshotView."""
    return {
        "terms_snapshot_id": view.terms_snapshot_id,
        "customer_id": view.customer_id,
        "order_id": view.order_id,
        "source": view.source,
        "source_decision_id": None if view.source == "base" else view.source,
        "quadrant_at_purchase": view.quadrant_at_purchase,
        **view.terms.model_dump(),
        **{f"base_{k}": v for k, v in view.base_terms.model_dump().items()},
        "review_at": view.review_at,
        "coupon_application_id": None,
        "cart": [],
        "shown_at": view.shown_at,
        "confirmed_at": view.confirmed_at,
    }


def example_models() -> list[Any]:
    found = []
    for path in sorted(EXAMPLES.glob("*.json")):
        name = path.name.split(".")[0]
        if name != "index":
            found.append(s.API_MODELS[name].model_validate_json(path.read_text(encoding="utf-8")))
    return found


def example_decisions() -> list[s.DecisionView]:
    """Every decision of the examples, latest state per decision id, versions in order."""
    found: dict[str, s.DecisionView] = {}
    for model in example_models():
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


def example_snapshots() -> list[s.TermsSnapshotView]:
    found: dict[str, s.TermsSnapshotView] = {}
    for model in example_models():
        for field in ("order_terms", "terms_snapshot"):
            view = getattr(model, field, None)
            if isinstance(view, s.TermsSnapshotView):
                found[view.terms_snapshot_id] = view
    return list(found.values())


def test_every_example_decision_and_snapshot_fits_its_table(database_url: str) -> None:
    views = example_decisions()
    snapshots = example_snapshots()
    assert {v.state for v in views} == {"proposed", "approved", "overridden", "rejected"}
    assert {v.revision_trigger for v in views} >= {"initial", "evidence_added", "review"}
    assert {x.source == "base" for x in snapshots} == {True, False}
    ids = {v.decision_id for v in views}
    assert all(v.supersedes in ids for v in views if v.supersedes), "every version chain is whole"

    async def action(engine: AsyncEngine) -> None:
        async with engine.begin() as conn:
            await conn.execute(sql.decisions.delete())
            known = {r["return_id"] for _, rows in SEEDS[:1] for r in rows}
            missing = sorted({v.return_id for v in views} - known)
            await conn.execute(
                sql.return_requests.insert(), [return_request(rid) for rid in missing]
            )
        for view in views:
            await insert(engine, "decisions", decision_row(view))
        for snapshot in snapshots:
            await insert(engine, "terms_snapshots", snapshot_row(snapshot))

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
        ("decisions", "revision_trigger", REVISION_TRIGGERS),
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
