"""Operational state in PostgreSQL: SQLAlchemy Core tables with their invariants (ADR-15).

Spec section 7 (decisions, audit log, outbox live in PostgreSQL), 8.6 and US-303, US-306, US-307,
US-310, US-402, US-501..503, US-601..607. The same metadata runs on PostgreSQL (asyncpg) and on
SQLite (aiosqlite, used by the tests); every CHECK is written in the SQL both accept.

Conventions:

- Constraint names follow NAMING_CONVENTION, so a violated CHECK names itself
  (for example ck_decisions_approved_restriction).
- A CHECK passes when its expression is unknown (NULL); every rule below compares nullable
  columns with IS NULL / IS NOT NULL first, so a missing value cannot slip through.
- Every CHECK is one rule, written so that a row breaking one rule breaks one CHECK: a rule on
  a state is an implication ("state <> 'x' OR ..."), so an unknown state fails only the
  vocabulary CHECK. PostgreSQL reports the first violated CHECK in name order and SQLite the
  first in table order; with one violated CHECK both name the same one.
- Enumerations are strings with a CHECK on the vocabularies of contracts/vocab.py and
  policy/types.py.
- JSON columns are JSONB on PostgreSQL. A Python None is SQL NULL, never the JSON null, so the
  IS NULL rules see it. Levers are stored as lists of {id, value, scope} objects
  (contracts/levers.py), terms as {return_window_days, free_return_shipping, instant_refund,
  free_size_exchange, coupons_enabled}.
- Timestamps are TIMESTAMP WITH TIME ZONE. UtcDateTime refuses a naive datetime, stores UTC
  and reads back a timezone-aware UTC datetime on both databases (SQLite has no time zones).
  A CHECK that compares a timestamp with a date uses utc_date(), the UTC day on both databases.
- audit_log, simulator_assumptions and coupon_rules are append-only: triggers refuse UPDATE and
  DELETE (and TRUNCATE on PostgreSQL). A new version is a new row.

What a CHECK cannot see (the content of JSON columns, cross-table rules) is checked by the API
with policy.legal_guard and the validators of apps/api/schemas.py.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
    column,
    event,
    false,
    func,
    literal_column,
    or_,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.functions import FunctionElement

from contracts.vocab import (
    ASSUMPTION_BASES,
    AUDIT_ACTIONS,
    AUDIT_ENTITY_TYPES,
    CATALOG_TASK_KINDS,
    CATALOG_TASK_STATUSES,
    CLAUSE_KINDS,
    COUPON_APPLICATION_STATES,
    DECISION_AUDIT_ACTIONS,
    DECISION_STATUSES,
    EVIDENCE_KINDS,
    FALLBACK_REASONS,
    GENERATION_KINDS,
    GENERATION_PATHS,
    HUMAN_ONLY_ACTIONS,
    INSPECTION_STATES,
    LANGS,
    LLM_MODES,
    MIN_REASON_CHARS,
    OUTBOX_STATUSES,
    OVERRIDE_CATEGORIES,
    PHOTO_MEDIA_TYPES,
    REASON_REQUIRED_ACTIONS,
    REVISION_TRIGGERS,
    ROLES,
    SERVED_FROM,
    SYSTEM_ACTOR,
)
from policy.legal_guard import LEGAL_WITHDRAWAL_DAYS, MAX_REVIEW_DAYS
from policy.types import EvidenceQuality, Path, Quadrant, ReturnReason

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UtcDateTime(TypeDecorator[datetime]):
    """TIMESTAMP WITH TIME ZONE that only takes aware datetimes, stores them in UTC and reads them
    back aware in UTC: SQLite keeps no offset, so its naive results are UTC by construction."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"a timestamp needs a timezone (UTC), got the naive {value!r}")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# none_as_null: a Python None is stored as SQL NULL, so CHECKs written with IS NULL see it.
JSONDoc = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
Money = Numeric(12, 2)
TS = UtcDateTime()
Id = String(96)
ActorColumn = String(32)

REASONS = tuple(r.value for r in ReturnReason)
PATHS = tuple(p.value for p in Path)
EVIDENCE_QUALITIES = tuple(e.value for e in EvidenceQuality)
QUADRANTS = tuple(q.value for q in Quadrant)
RESTRICTED_QUADRANTS = (Quadrant.Q3.value, Quadrant.Q4.value)  # legal_guard.APPROVAL_QUADRANTS
RETURN_SOURCES = ("store", "adapter")  # the synthetic store, or the simulated shop adapter (F1)


def _sql_list(values: Iterable[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def one_of(column: str, values: Iterable[str], nullable: bool = False) -> str:
    rule = f"{column} IN ({_sql_list(values)})"
    return f"({column} IS NULL OR {rule})" if nullable else rule


def present(column: str, min_chars: int = 1) -> str:
    """Set and not blank: at least min_chars characters once trimmed."""
    return f"({column} IS NOT NULL AND length(trim({column})) >= {min_chars})"


def person(column: str) -> str:
    """A person's id: set, not blank, and not the system actor (rule 5)."""
    return f"({present(column)} AND {column} <> '{SYSTEM_ACTOR}')"


def _check(rule: str, name: str) -> CheckConstraint:
    return CheckConstraint(rule, name=name)


class utc_date(FunctionElement[date]):  # noqa: N801 - a SQL function, named as SQL names it
    """The UTC calendar day of a UtcDateTime column, the same on both databases: SQLite stores
    the UTC time as text (date() reads its day); PostgreSQL converts the timestamptz at UTC,
    whatever the TimeZone of the session."""

    type = Date()
    name = "utc_date"
    inherit_cache = True


@compiles(utc_date)
def _utc_date_sqlite(element: utc_date, compiler: SQLCompiler, **kw: Any) -> str:
    return f"date({compiler.process(element.clauses, **kw)})"


@compiles(utc_date, "postgresql")
def _utc_date_postgresql(element: utc_date, compiler: SQLCompiler, **kw: Any) -> str:
    return f"(({compiler.process(element.clauses, **kw)}) AT TIME ZONE 'UTC')::date"


class days_after(FunctionElement[date]):  # noqa: N801 - a SQL function, named as SQL names it
    """The day `days` days after a date expression, the same on both databases: SQLite adds
    a modifier to date(), PostgreSQL adds an integer to the date. `days` is a constant of the
    contract (MAX_REVIEW_DAYS), written into the DDL."""

    type = Date()
    name = "days_after"
    inherit_cache = True

    def __init__(self, expression: Any, days: int) -> None:
        super().__init__(expression, literal_column(str(int(days))))


@compiles(days_after)
def _days_after_sqlite(element: days_after, compiler: SQLCompiler, **kw: Any) -> str:
    expression, days = list(element.clauses)
    return f"date({compiler.process(expression, **kw)}, '+{compiler.process(days, **kw)} days')"


@compiles(days_after, "postgresql")
def _days_after_postgresql(element: days_after, compiler: SQLCompiler, **kw: Any) -> str:
    expression, days = list(element.clauses)
    return f"(({compiler.process(expression, **kw)}) + {compiler.process(days, **kw)})"


# ---- Return requests (F1: the simulated shop adapter publishes them, the API stores them) -----

return_requests = Table(
    "return_requests",
    metadata,
    Column("return_id", Id, primary_key=True),
    Column("order_id", Id, nullable=False),
    Column("line_id", Id, nullable=False),
    Column("customer_id", Id, nullable=False),
    Column("variant_id", Id, nullable=False),
    Column("reason", String(32), nullable=False),
    Column("customer_text", Text),  # the customer's words: data, never instructions
    Column("delivered_on", Date, nullable=False),
    Column("notice_on", Date, nullable=False),
    Column("requested_at", TS, nullable=False),
    Column("order_terms_snapshot_id", Id, nullable=False),
    Column("source", String(16), nullable=False),
    Column("received_at", TS, nullable=False),
    _check(one_of("reason", REASONS), "reason"),
    _check(one_of("source", RETURN_SOURCES), "source"),
    _check("notice_on >= delivered_on", "notice_after_delivery"),
)
Index(None, return_requests.c.customer_id)

# ---- LLM generations (spec 8.7: validated, regenerated once, or template) ----------------------

llm_generations = Table(
    "llm_generations",
    metadata,
    Column("generation_id", Id, primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("return_id", Id, ForeignKey("return_requests.return_id")),
    Column("decision_id", Id),  # no foreign key: decisions point here (no cycle)
    Column("model", String(128), nullable=False),
    Column("llm_mode", String(8), nullable=False),
    Column("served_from", String(8), nullable=False),
    Column("generation_path", String(16), nullable=False),  # llm.generate: model, regenerated...
    Column("fallback_reason", String(16)),  # why the template was used, if it was
    Column("model_calls", Integer, nullable=False),
    Column("prompt_version", String(64), nullable=False),
    Column("input_digest", String(64), nullable=False),  # sha256 of the case sent to the model
    # the llm_case sent (with its code-built facts) and the validated or template output
    Column("llm_case", JSONDoc, nullable=False),
    Column("output", JSONDoc, nullable=False),
    Column("validator_codes", JSONDoc, nullable=False),  # llm.validator codes found on the way
    Column("latency_ms", Integer),
    Column("prompt_tokens", Integer),
    Column("completion_tokens", Integer),
    Column("tokens_per_second", Float),
    Column("created_at", TS, nullable=False),
    _check(one_of("kind", GENERATION_KINDS), "kind"),
    _check(one_of("llm_mode", LLM_MODES), "llm_mode"),
    _check(one_of("served_from", SERVED_FROM), "served_from"),
    _check(one_of("generation_path", GENERATION_PATHS), "generation_path"),
    _check(one_of("fallback_reason", FALLBACK_REASONS, nullable=True), "fallback_reason"),
    _check("model_calls >= 0 AND model_calls <= 2", "calls_range"),
    # spec 8.7, llm.generate: model is validated on the first call, regenerated on the second
    _check(
        "(generation_path <> 'model' OR model_calls = 1)"
        " AND (generation_path <> 'regenerated' OR model_calls = 2)",
        "path_calls",
    ),
    # the template, and only it, records why the model output was not used
    _check(
        "(generation_path <> 'template' OR fallback_reason IS NOT NULL)"
        " AND (generation_path = 'template' OR fallback_reason IS NULL)",
        "template_fallback",
    ),
    _check("llm_mode <> 'mock' OR served_from = 'live'", "mock_is_live"),
    _check("length(input_digest) = 64", "input_digest"),
    _check(
        "(latency_ms IS NULL OR latency_ms >= 0)"
        " AND (prompt_tokens IS NULL OR prompt_tokens >= 0)"
        " AND (completion_tokens IS NULL OR completion_tokens >= 0)"
        " AND (tokens_per_second IS NULL OR tokens_per_second >= 0)",
        "non_negative",
    ),
)
Index(None, llm_generations.c.return_id)

# ---- Versioned rules and assumptions (append-only) -------------------------------------------

simulator_assumptions = Table(
    "simulator_assumptions",
    metadata,
    Column("version", String(64), primary_key=True),
    Column("seq", Integer, nullable=False, unique=True),
    Column("supersedes_version", String(64), ForeignKey("simulator_assumptions.version")),
    Column("period", Text, nullable=False),
    Column("logistics_returns_avoided", Integer, nullable=False),
    Column("logistics_cost_per_return_eur", Money, nullable=False),
    Column("undue_discounts_avoided", Integer, nullable=False),
    Column("avg_undue_discount_eur", Money, nullable=False),
    Column("minutes_saved", Numeric(10, 2), nullable=False),
    Column("hourly_cost_eur", Money, nullable=False),
    Column("incentives_eur", Money, nullable=False),
    Column("system_cost_eur", Money, nullable=False),
    Column("error_cost_eur", Money, nullable=False),
    Column("abandonment_margin_lost_eur", Money, nullable=False),
    Column("incremental_margin_eur", Money, nullable=False),
    Column("basis", String(16), nullable=False),
    Column("baseline", Text, nullable=False),
    Column("note", Text, nullable=False),
    Column("created_by", ActorColumn, nullable=False),
    Column("created_at", TS, nullable=False),
    _check(one_of("basis", ASSUMPTION_BASES), "basis"),
    _check(
        " AND ".join(
            f"{c} >= 0"
            for c in (
                "logistics_returns_avoided",
                "logistics_cost_per_return_eur",
                "undue_discounts_avoided",
                "avg_undue_discount_eur",
                "minutes_saved",
                "hourly_cost_eur",
                "incentives_eur",
                "system_cost_eur",
                "error_cost_eur",
                "abandonment_margin_lost_eur",
                "incremental_margin_eur",
            )
        ),
        "non_negative",  # the simulator applies the signs; no refund column (rule 9)
    ),
    _check(
        "(seq = 1 AND supersedes_version IS NULL) OR (seq > 1 AND supersedes_version IS NOT NULL)",
        "version_chain",
    ),
    _check(present("created_by"), "created_by"),
    _check(present("note"), "note"),
)

coupon_rules = Table(
    "coupon_rules",
    metadata,
    Column("rule_version", String(64), primary_key=True),
    Column("seq", Integer, nullable=False, unique=True),
    Column("supersedes_version", String(64), ForeignKey("coupon_rules.rule_version")),
    Column("text", Text, nullable=False),
    Column("approved_by", ActorColumn, nullable=False),
    Column("approved_at", TS, nullable=False),
    _check(person("approved_by"), "approved_by_person"),  # US-402: approved by the manager
    _check(
        "(seq = 1 AND supersedes_version IS NULL) OR (seq > 1 AND supersedes_version IS NOT NULL)",
        "version_chain",
    ),
    _check(present("text"), "text"),
)

# ---- Decisions (spec 8.6; one row per version of a case's decision, F7) ----------------------

decisions = Table(
    "decisions",
    metadata,
    Column("decision_id", Id, primary_key=True),
    Column("return_id", Id, ForeignKey("return_requests.return_id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("supersedes_decision_id", Id, ForeignKey("decisions.decision_id")),
    Column("revision_trigger", String(32), nullable=False),
    Column("customer_id", Id, nullable=False),
    Column("order_id", Id, nullable=False),
    # engine inputs (US-604: re-runnable)
    Column("risk", Float),  # null on the guarantee path: never scored (rule 4)
    Column("value_eur", Money),
    Column("cluster_id", Id),
    Column("risk_threshold", Float, nullable=False),
    Column("value_threshold_eur", Money, nullable=False),
    # {origin, period_start, period_end, nature} of the scores and thresholds above (rule 11):
    # an earlier version is served with the provenance it had (US-604)
    Column("inputs_provenance", JSONDoc, nullable=False),
    # the UTC day of the customer snapshot the engine read (value, evidence, cluster): the period
    # of the facts of the version's draft (contract 1.6.0, rule 11)
    Column("snapshot_on", Date, nullable=False),
    # the UTC day the risk was scored, at the request (the score's decided_at): the period of the
    # risk_score fact (contract 1.7.0, rule 11); null when there is no risk (rule 4)
    Column("scored_on", Date),
    # the top 3 contributions to the risk above (ADR-16): [{feature, value, contribution,
    # direction, phrase_en, phrase_it}] as ml.risk.explain writes shap_top3; the case card shows
    # them and the draft's facts cite them. Null when the return is not scored (rule 4, US-307)
    Column("score_factors", JSONDoc),
    Column("base_terms", JSONDoc, nullable=False),
    Column("order_terms", JSONDoc, nullable=False),
    # engine outputs
    Column("path", String(16), nullable=False),
    Column("evidence_quality", String(16), nullable=False),
    Column("missing_evidence", JSONDoc, nullable=False),
    Column("quadrant", String(2)),
    Column("proposed_levers", JSONDoc, nullable=False),
    Column("levers", JSONDoc, nullable=False),  # levers of the current state
    Column("requires_approval", Boolean, nullable=False),  # for `levers`
    Column("current_terms", JSONDoc, nullable=False),
    Column("future_terms", JSONDoc, nullable=False),
    # notice + 14 days on the withdrawal path; null on the guarantee path, which states no
    # refund deadline (contract 1.6.0; rule 4, NFR-17)
    Column("refund_due_by", Date),
    Column("decided_on", Date, nullable=False),
    Column("review_at", Date),
    Column("alternatives", JSONDoc, nullable=False),
    Column("expected_net_benefit_eur", Money, nullable=False),
    Column("coupon_rule_version", String(64), ForeignKey("coupon_rules.rule_version")),
    Column("coupon_application_id", Id),  # applications point here (no cycle)
    # human outcome
    Column("state", String(16), nullable=False, server_default="proposed"),
    Column("approver", ActorColumn),  # the person who approved, overrode or rejected
    Column("approved_at", TS),
    Column("override_reason", Text),
    Column("override_category", String(32)),
    # versions and generation
    Column("rules_version", String(64), nullable=False),
    Column("model_versions", JSONDoc, nullable=False),
    Column(
        "assumptions_version",
        String(64),
        ForeignKey("simulator_assumptions.version"),
        nullable=False,
    ),
    Column("generation_id", Id, ForeignKey("llm_generations.generation_id")),
    Column("generation_path", String(16)),
    Column("created_at", TS, nullable=False),
    UniqueConstraint("return_id", "version", name="uq_decisions_return_version"),
    # the key of the composite foreign key of coupon_rule_applications (round 4, E2)
    UniqueConstraint("decision_id", "cluster_id"),
    _check("version >= 1", "version_positive"),
    _check(
        "(version <> 1 OR (supersedes_decision_id IS NULL AND revision_trigger = 'initial'))"
        " AND (version <= 1 OR (supersedes_decision_id IS NOT NULL"
        " AND revision_trigger <> 'initial'))",
        "version_chain",
    ),
    _check(one_of("revision_trigger", REVISION_TRIGGERS), "revision_trigger"),
    _check(one_of("path", PATHS), "path"),
    _check(one_of("evidence_quality", EVIDENCE_QUALITIES), "evidence_quality"),
    _check(one_of("quadrant", QUADRANTS, nullable=True), "quadrant"),
    _check(one_of("state", DECISION_STATUSES), "state"),
    _check(one_of("override_category", OVERRIDE_CATEGORIES, nullable=True), "override_category"),
    _check(one_of("generation_path", GENERATION_PATHS, nullable=True), "generation_path"),
    _check("risk IS NULL OR (risk >= 0 AND risk <= 1)", "risk_range"),
    _check("risk_threshold > 0 AND risk_threshold <= 1", "risk_threshold_range"),
    # rule 4, US-309: the guarantee path is never scored and never restricted
    _check(
        "path <> 'guarantee' OR (quadrant IS NULL AND risk IS NULL AND NOT requires_approval)",
        "guarantee_unscored",
    ),
    # US-307: insufficient evidence has no quadrant
    _check("evidence_quality <> 'insufficient' OR quadrant IS NULL", "insufficient_no_quadrant"),
    # rule 7 (1.6.0): the engine reads a customer snapshot made by the decision day
    _check("snapshot_on <= decided_on", "snapshot_by_decision"),
    # rule 7 (1.7.0): the risk is scored at the request, after that snapshot, by the decision day
    _check(
        "(risk IS NULL AND scored_on IS NULL) OR (risk IS NOT NULL AND scored_on IS NOT NULL"
        " AND snapshot_on <= scored_on AND scored_on <= decided_on)",
        "risk_scored_by_decision",
    ),
    # rule 3, rule 4 (1.6.0): the 14-day refund deadline is a withdrawal's, and only a withdrawal's
    _check(
        "(path = 'withdrawal' AND refund_due_by IS NOT NULL)"
        " OR (path <> 'withdrawal' AND refund_due_by IS NULL)",
        "refund_due_on_withdrawal",
    ),
    # rule 5, spec 8.6: Q3 and Q4 always need a human approval while their levers stand
    _check(
        f"quadrant IS NULL OR quadrant NOT IN ({_sql_list(RESTRICTED_QUADRANTS)})"
        " OR state NOT IN ('proposed', 'approved') OR requires_approval",
        "q3_q4_need_approval",
    ),
    # US-607: every restriction has a review date, after the decision, and nothing else has one
    _check("NOT requires_approval OR review_at IS NOT NULL", "restriction_has_review"),
    _check("review_at IS NULL OR requires_approval", "review_only_restriction"),
    _check("review_at IS NULL OR review_at > decided_on", "review_after_decision"),
    CheckConstraint(
        or_(
            column("review_at").is_(None),
            column("review_at", Date) <= days_after(column("decided_on"), MAX_REVIEW_DAYS),
        ),
        name="review_within_max",
    ),
    # D1: the decision day is the UTC day of the act that created the version (or earlier, for
    # the intake of a return noticed before), and a review version is decided at the review
    CheckConstraint(
        column("decided_on", Date) <= utc_date(column("created_at")),
        name="decided_by_creation",
    ),
    # round 4, E4: every later version (a review or a recompute) is decided on the day it is
    # created, so the review rules count from the day of the act
    CheckConstraint(
        or_(
            column("revision_trigger") == "initial",
            column("decided_on", Date) == utc_date(column("created_at")),
        ),
        name="new_version_decided_on_creation",
    ),
    # E4: a person acts on a version that exists; the act of a review creates its version
    _check("approved_at IS NULL OR approved_at >= created_at", "approved_after_creation"),
    _check(
        "revision_trigger <> 'review' OR approved_at = created_at", "review_approved_at_creation"
    ),
    # E4: a restriction is approved only before its review date; a stale proposal is recomputed
    CheckConstraint(
        or_(
            column("review_at").is_(None),
            column("approved_at").is_(None),
            utc_date(column("approved_at")) < column("review_at", Date),
        ),
        name="approved_before_review",
    ),
    # rule 5: a person acted exactly when the state is not proposed. With the two review CHECKs
    # above, an approved or overridden restriction has a person as approver and a review date.
    _check(
        "(state = 'proposed' AND approver IS NULL AND approved_at IS NULL)"
        f" OR (state <> 'proposed' AND {person('approver')} AND approved_at IS NOT NULL)",
        "acted_has_approver",
    ),
    # US-306: an override (and a rejection) has a reason and a category; an approval has none
    _check(
        "(state NOT IN ('overridden', 'rejected')"
        f" OR ({present('override_reason', MIN_REASON_CHARS)}"
        " AND override_category IS NOT NULL))"
        " AND (state IN ('overridden', 'rejected')"
        " OR (override_reason IS NULL AND override_category IS NULL))",
        "override_has_reason",
    ),
    _check("state <> 'rejected' OR NOT requires_approval", "rejected_not_restrictive"),
    # US-607: a version created by a review lifts the restriction (action lift: overridden to
    # the standard levers) or renews it (action renew: the engine's fresh restriction, approved)
    _check(
        "revision_trigger <> 'review'"
        " OR (state = 'overridden' AND NOT requires_approval)"
        " OR (state = 'approved' AND requires_approval)",
        "review_outcome",
    ),
    _check(
        "coupon_application_id IS NULL OR coupon_rule_version IS NOT NULL",
        "coupon_application_has_rule",
    ),
    # LEG-09, E2: a cluster coupon block is about the cluster given to the engine
    _check(
        "coupon_application_id IS NULL OR cluster_id IS NOT NULL",
        "coupon_application_has_cluster",
    ),
    _check(
        "(generation_id IS NULL AND generation_path IS NULL)"
        " OR (generation_id IS NOT NULL AND generation_path IS NOT NULL)",
        "generation_pair",
    ),
)
Index(None, decisions.c.return_id)
Index(None, decisions.c.state)

coupon_rule_applications = Table(
    "coupon_rule_applications",
    metadata,
    Column("application_id", Id, primary_key=True),
    Column("rule_version", String(64), ForeignKey("coupon_rules.rule_version"), nullable=False),
    Column("cluster_id", Id, nullable=False),
    Column("decision_id", Id, nullable=False),
    Column("affected_customer_ids", JSONDoc, nullable=False),  # excluded accounts, visible
    Column("state", String(16), nullable=False),
    Column("proposed_at", TS, nullable=False),
    Column("confirmed_by", ActorColumn),
    Column("confirmed_at", TS),
    Column("review_at", Date, nullable=False),  # US-402, US-607: always a review date
    Column("reversible", Boolean, nullable=False, server_default=true()),
    Column("reversed_by", ActorColumn),
    Column("reversed_at", TS),
    Column("reversal_reason", Text),
    # E2: the application blocks the cluster of the decision that applied it, never another
    ForeignKeyConstraint(
        ["decision_id", "cluster_id"], ["decisions.decision_id", "decisions.cluster_id"]
    ),
    _check(one_of("state", COUPON_APPLICATION_STATES), "state"),
    _check("reversible", "always_reversible"),  # no permanent label (US-607)
    # D2: the review date is at most MAX_REVIEW_DAYS after the application is confirmed (or
    # proposed, while it waits for a person)
    CheckConstraint(
        column("review_at", Date)
        <= days_after(
            utc_date(func.coalesce(column("confirmed_at"), column("proposed_at"))),
            MAX_REVIEW_DAYS,
        ),
        name="review_within_max",
    ),
    # E4: confirmed before its review date (a lapsed block is never confirmed), and the review
    # date is after the day of the proposal
    CheckConstraint(
        or_(
            column("confirmed_at").is_(None),
            utc_date(column("confirmed_at")) < column("review_at", Date),
        ),
        name="confirmed_before_review",
    ),
    CheckConstraint(
        column("review_at", Date) > utc_date(column("proposed_at")),
        name="review_after_proposed",
    ),
    # US-402, LEG-09: an application is a proposal an operator confirms
    _check(
        "(state <> 'proposed' OR (confirmed_by IS NULL AND confirmed_at IS NULL))"
        " AND (state NOT IN ('confirmed', 'reversed')"
        f" OR ({person('confirmed_by')} AND confirmed_at IS NOT NULL))",
        "confirmed_by_person",
    ),
    _check(
        f"(state = 'reversed' AND {person('reversed_by')} AND reversed_at IS NOT NULL"
        f" AND {present('reversal_reason', MIN_REASON_CHARS)})"
        " OR (state <> 'reversed' AND reversed_by IS NULL AND reversed_at IS NULL"
        " AND reversal_reason IS NULL)",
        "reversal_fields",
    ),
)
Index(None, coupon_rule_applications.c.cluster_id)

# ---- Audit log and outbox ----------------------------------------------------------------------

audit_log = Table(
    "audit_log",
    metadata,
    Column(
        "entry_id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("at", TS, nullable=False),
    Column("actor", ActorColumn, nullable=False),
    Column("actor_role", String(16), nullable=False),
    Column("action", String(48), nullable=False),
    Column("entity_type", String(32), nullable=False),
    Column("entity_id", Id, nullable=False),
    Column("return_id", Id),  # no foreign keys: the log outlives what it describes
    Column("decision_id", Id),
    Column("reason", Text),
    Column("before", JSONDoc),
    Column("after", JSONDoc),
    Column("rules_version", String(64)),
    Column("model_versions", JSONDoc),
    Column("assumptions_version", String(64)),
    Column("request_id", Id),
    _check(present("actor"), "actor"),
    _check(one_of("actor_role", ROLES), "actor_role"),
    _check(one_of("action", AUDIT_ACTIONS), "action"),
    _check(one_of("entity_type", AUDIT_ENTITY_TYPES), "entity_type"),
    _check(
        f"(actor = '{SYSTEM_ACTOR}' AND actor_role = 'system')"
        f" OR (actor <> '{SYSTEM_ACTOR}' AND actor_role <> 'system')",
        "system_role",
    ),
    # rule 5, GDPR art. 22: approvals, overrides, disputes... are taken by a person
    _check(
        f"action NOT IN ({_sql_list(sorted(HUMAN_ONLY_ACTIONS))}) OR actor_role <> 'system'",
        "human_action",
    ),
    _check(
        f"action NOT IN ({_sql_list(sorted(REASON_REQUIRED_ACTIONS))})"
        f" OR {present('reason', MIN_REASON_CHARS)}",
        "reason_required",
    ),
    # US-604: the entries about a decision version, and only those, have entity_type decision
    _check(
        f"(entity_type = 'decision' AND action IN ({_sql_list(sorted(DECISION_AUDIT_ACTIONS))}))"
        " OR (entity_type <> 'decision'"
        f" AND action NOT IN ({_sql_list(sorted(DECISION_AUDIT_ACTIONS))}))",
        "decision_entity",
    ),
)
Index(None, audit_log.c.return_id)
Index(None, audit_log.c.at)

outbox = Table(
    "outbox",
    metadata,
    Column("message_id", Id, primary_key=True),
    Column("return_id", Id, ForeignKey("return_requests.return_id"), nullable=False),
    Column("decision_id", Id, ForeignKey("decisions.decision_id"), nullable=False),
    Column("customer_id", Id, nullable=False),
    Column("lang", String(2), nullable=False),
    Column("subject", String(300), nullable=False),
    # phrasebook ids (llm/phrasebook.py); the body is llm.phrasebook.render_selection of them
    Column("sentence_ids", JSONDoc, nullable=False),
    Column("body", Text, nullable=False),  # rendered text, legal clause included
    Column("legal_clause_kind", String(16)),
    Column("template_fields", JSONDoc, nullable=False),
    # the refund the {amount} field states, with its provenance (rule 11), when known
    Column("refund_eur", Money),
    Column("refund_provenance", JSONDoc),
    Column("generation_id", Id, ForeignKey("llm_generations.generation_id")),
    Column("status", String(16), nullable=False),
    Column("created_by", ActorColumn, nullable=False),
    Column("created_at", TS, nullable=False),
    _check(one_of("lang", LANGS), "lang"),
    _check(one_of("legal_clause_kind", CLAUSE_KINDS, nullable=True), "legal_clause_kind"),
    _check(one_of("status", OUTBOX_STATUSES), "status"),
    _check(present("body"), "body"),
    _check(
        "(refund_eur IS NULL OR refund_provenance IS NOT NULL)"
        " AND (refund_eur IS NOT NULL OR refund_provenance IS NULL)",
        "refund_provenance",
    ),
    _check("refund_eur IS NULL OR refund_eur >= 0", "refund_non_negative"),
    _check(person("created_by"), "created_by_person"),  # a message leaves after a person acts
)
Index(None, outbox.c.return_id)

# ---- Checkout: terms snapshots shown and confirmed (ADR-05, US-403, E2E-02) -------------------

TERM_COLUMNS = (
    "return_window_days",
    "free_return_shipping",
    "instant_refund",
    "free_size_exchange",
    "coupons_enabled",
)
SAME_AS_BASE = " AND ".join(f"{c} = base_{c}" for c in TERM_COLUMNS)
LESS_GENEROUS = " OR ".join(
    ["return_window_days < base_return_window_days"]
    + [f"(base_{c} AND NOT {c})" for c in TERM_COLUMNS[1:]]
)
# Contract 1.5.0 (ADR-15 section 15, H12): a confirmed cluster coupon block in force turns the
# coupons of the terms shown off, also on base terms; the block carries its own review date.
_COUPON_BLOCKED = "(coupon_application_id IS NOT NULL AND NOT coupons_enabled)"
SAME_AS_BASE_SHOWN = " AND ".join(
    [f"{c} = base_{c}" for c in TERM_COLUMNS if c != "coupons_enabled"]
    + [f"(coupons_enabled = base_coupons_enabled OR {_COUPON_BLOCKED})"]
)
LESS_GENEROUS_SHOWN = " OR ".join(
    ["return_window_days < base_return_window_days"]
    + [f"(base_{c} AND NOT {c})" for c in TERM_COLUMNS[1:] if c != "coupons_enabled"]
    + ["(base_coupons_enabled AND NOT coupons_enabled AND coupon_application_id IS NULL)"]
)

terms_snapshots = Table(
    "terms_snapshots",
    metadata,
    Column("terms_snapshot_id", Id, primary_key=True),
    Column("customer_id", Id, nullable=False),
    Column("order_id", Id, unique=True),  # set at /api/checkout/confirm
    Column("source", Id, nullable=False),  # "base" or the decision id, as store/terms_snapshots
    Column("source_decision_id", Id, ForeignKey("decisions.decision_id")),
    Column("quadrant_at_purchase", String(2)),
    Column("return_window_days", Integer, nullable=False),
    Column("free_return_shipping", Boolean, nullable=False),
    Column("instant_refund", Boolean, nullable=False),
    Column("free_size_exchange", Boolean, nullable=False),
    Column("coupons_enabled", Boolean, nullable=False),
    # the merchant base terms when the snapshot was shown, and the review date of the decision
    # that produced the terms (US-607)
    Column("base_return_window_days", Integer, nullable=False),
    Column("base_free_return_shipping", Boolean, nullable=False),
    Column("base_instant_refund", Boolean, nullable=False),
    Column("base_free_size_exchange", Boolean, nullable=False),
    Column("base_coupons_enabled", Boolean, nullable=False),
    Column("review_at", Date),
    Column("coupon_application_id", Id, ForeignKey("coupon_rule_applications.application_id")),
    Column("coupon_review_at", Date),  # the review date of the coupon block shown (1.5.0)
    Column("cart", JSONDoc, nullable=False),
    Column("shown_at", TS, nullable=False),
    Column("confirmed_at", TS),
    _check(f"return_window_days >= {LEGAL_WITHDRAWAL_DAYS}", "window_legal"),
    _check(one_of("quadrant_at_purchase", QUADRANTS, nullable=True), "quadrant"),
    _check(
        "(order_id IS NULL AND confirmed_at IS NULL)"
        " OR (order_id IS NOT NULL AND confirmed_at IS NOT NULL)",
        "confirmed_with_order",
    ),
    _check("confirmed_at IS NULL OR confirmed_at >= shown_at", "confirmed_after_shown"),
    # the quadrant is the engine's while its levers stand, none after a person's override (H3)
    _check(
        "(source = 'base' AND source_decision_id IS NULL AND quadrant_at_purchase IS NULL)"
        " OR (source <> 'base' AND source_decision_id IS NOT NULL"
        " AND source_decision_id = source)",
        "source",
    ),
    # base terms are exactly the merchant base terms (US-403), coupons off under a block
    _check(f"source <> 'base' OR ({SAME_AS_BASE_SHOWN})", "base_terms"),
    _check("source <> 'base' OR review_at IS NULL", "base_no_review"),
    # terms less generous than the base come from a decision with a review date (US-607);
    # LESS_GENEROUS mirrors policy.types.ReturnTerms.is_less_generous_than (a test checks it),
    # the coupons turned off by a coupon block aside: the block has its own review date
    _check(
        f"NOT ({LESS_GENEROUS_SHOWN}) OR (source <> 'base' AND review_at IS NOT NULL)",
        "restriction_has_review",
    ),
    # H12: a coupon block shown turns the coupons of the terms off and carries its review date
    _check(
        "(coupon_application_id IS NULL AND coupon_review_at IS NULL)"
        " OR (coupon_application_id IS NOT NULL AND coupon_review_at IS NOT NULL)",
        "coupon_block_review",
    ),
    _check("coupon_application_id IS NULL OR NOT coupons_enabled", "coupon_block_off"),
    # a coupon block lapses at its review date: shown before it, within 180 days of the day
    CheckConstraint(
        or_(
            column("coupon_review_at").is_(None),
            utc_date(column("shown_at")) < column("coupon_review_at", Date),
        ),
        name="coupon_review_after_shown",
    ),
    CheckConstraint(
        or_(
            column("coupon_review_at").is_(None),
            column("coupon_review_at", Date)
            <= days_after(utc_date(column("shown_at")), MAX_REVIEW_DAYS),
        ),
        name="coupon_review_within_max",
    ),
    # US-607: terms from a restriction are shown only before its review date, when it lapses
    # (the UTC day of shown_at, as DecisionView.in_force and the API models count it)
    CheckConstraint(
        or_(
            column("review_at").is_(None),
            utc_date(column("shown_at")) < column("review_at", Date),
        ),
        name="review_after_shown",
    ),
    # D2: a review date at most MAX_REVIEW_DAYS after the day the terms are shown
    CheckConstraint(
        or_(
            column("review_at").is_(None),
            column("review_at", Date) <= days_after(utc_date(column("shown_at")), MAX_REVIEW_DAYS),
        ),
        name="review_within_max",
    ),
    # D3: an order is bound to the terms of a restriction only before its review date
    CheckConstraint(
        or_(
            column("review_at").is_(None),
            column("confirmed_at").is_(None),
            utc_date(column("confirmed_at")) < column("review_at", Date),
        ),
        name="review_after_confirmed",
    ),
)
Index(None, terms_snapshots.c.customer_id)

# ---- Case review: favourable evidence and link disputes (US-310, F7) -------------------------

evidence_additions = Table(
    "evidence_additions",
    metadata,
    Column("evidence_id", Id, primary_key=True),
    Column("return_id", Id, ForeignKey("return_requests.return_id"), nullable=False),
    Column("decision_before_id", Id, ForeignKey("decisions.decision_id"), nullable=False),
    Column("decision_after_id", Id, ForeignKey("decisions.decision_id")),
    Column("kind", String(32), nullable=False),
    Column("note", Text, nullable=False),
    Column("target_link_ids", JSONDoc, nullable=False),  # list of link ids
    Column("author", ActorColumn, nullable=False),
    Column("created_at", TS, nullable=False),
    Column("before", JSONDoc),  # CaseSnapshot before and after the recompute
    Column("after", JSONDoc),
    _check(one_of("kind", EVIDENCE_KINDS), "kind"),
    _check(present("note", MIN_REASON_CHARS), "note"),
    _check(person("author"), "author_person"),
    _check(
        "decision_after_id IS NULL OR decision_after_id <> decision_before_id",
        "new_version",
    ),
)
Index(None, evidence_additions.c.return_id)

link_disputes = Table(
    "link_disputes",
    metadata,
    Column("dispute_id", Id, primary_key=True),
    Column("link_id", Id, nullable=False, unique=True),  # a link is disputed once
    Column("customer_a", Id, nullable=False),
    Column("customer_b", Id, nullable=False),
    Column("return_id", Id, ForeignKey("return_requests.return_id")),
    Column("reason", Text, nullable=False),
    Column("author", ActorColumn, nullable=False),
    Column("created_at", TS, nullable=False),
    Column("before", JSONDoc),
    Column("after", JSONDoc),
    _check("customer_a < customer_b", "ordered_pair"),
    _check("link_id = customer_a || '~' || customer_b", "link_id"),
    _check(present("reason", MIN_REASON_CHARS), "reason"),
    _check(person("author"), "author_person"),
)

# ---- Warehouse inspections (US-501..503, LEG-05) ----------------------------------------------

inspections = Table(
    "inspections",
    metadata,
    Column("inspection_id", Id, primary_key=True),
    Column("return_id", Id, ForeignKey("return_requests.return_id"), nullable=False),
    Column("decision_id", Id, ForeignKey("decisions.decision_id"), nullable=False),
    Column("inspector", ActorColumn, nullable=False),
    Column("photo_id", Id, nullable=False),
    Column("photo_media_type", String(16), nullable=False),
    Column("photo_sha256", String(64), nullable=False),
    Column("state", String(32), nullable=False),
    Column("checklist", JSONDoc, nullable=False),  # suggestions with model confidence
    Column("min_model_confidence", Float, nullable=False),  # declared, not calibrated
    Column("manual_review_reason", Text),
    Column("proposal", JSONDoc),
    Column("confirmed_observations", JSONDoc),
    Column("confirmed_by", ActorColumn),
    Column("confirmed_at", TS),
    Column("compensation_applied", Boolean, nullable=False, server_default=false()),
    Column("compensation_eur", Money),
    Column("compensation_rule_id", Id),
    Column("generation_id", Id, ForeignKey("llm_generations.generation_id")),
    Column("created_at", TS, nullable=False),
    _check(one_of("state", INSPECTION_STATES), "state"),
    _check(one_of("photo_media_type", PHOTO_MEDIA_TYPES), "photo_media_type"),
    _check("min_model_confidence >= 0 AND min_model_confidence <= 1", "confidence_range"),
    _check(person("inspector"), "inspector_person"),
    _check("state <> 'manual_review' OR manual_review_reason IS NOT NULL", "manual_reason"),
    # US-503: when the system is unsure there is no proposal
    _check("manual_review_reason IS NULL OR proposal IS NULL", "unsure_no_proposal"),
    _check(
        f"(state = 'confirmed' AND confirmed_observations IS NOT NULL AND {person('confirmed_by')}"
        " AND confirmed_at IS NOT NULL)"
        " OR (state <> 'confirmed' AND confirmed_observations IS NULL AND confirmed_by IS NULL"
        " AND confirmed_at IS NULL)",
        "confirmed_fields",
    ),
    # LEG-05: compensation only with photo, confirmation and an approved rule
    _check(
        "NOT compensation_applied OR (state = 'confirmed' AND compensation_eur > 0"
        " AND compensation_rule_id IS NOT NULL AND photo_id IS NOT NULL)",
        "compensation_evidence",
    ),
    _check(
        "compensation_applied OR (compensation_eur IS NULL AND compensation_rule_id IS NULL)",
        "compensation_pair",
    ),
)
Index(None, inspections.c.return_id)
# H13, H19 (contract 1.5.0): a diminished-value claim belongs to the return, once
Index(
    "uq_inspections_one_claim_per_return",
    inspections.c.return_id,
    unique=True,
    postgresql_where=inspections.c.compensation_applied,
    sqlite_where=inspections.c.compensation_applied,
)

# ---- Catalogue tasks (US-606, spec 8.9) ---------------------------------------------------------

catalog_tasks = Table(
    "catalog_tasks",
    metadata,
    Column("task_id", Id, primary_key=True),
    Column("anomaly_id", Id, nullable=False),  # variant_id~reason~week
    Column("variant_id", Id, nullable=False),
    Column("reason", String(32), nullable=False),
    Column("week", Date, nullable=False),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("note", Text, nullable=False),
    Column("created_by", ActorColumn, nullable=False),
    Column("created_at", TS, nullable=False),
    Column("updated_at", TS, nullable=False),
    Column("done_at", TS),
    Column("return_id", Id, ForeignKey("return_requests.return_id")),
    _check(one_of("kind", CATALOG_TASK_KINDS), "kind"),
    _check(one_of("status", CATALOG_TASK_STATUSES), "status"),
    _check(one_of("reason", REASONS), "reason"),
    _check(
        "(status = 'done' AND done_at IS NOT NULL) OR (status <> 'done' AND done_at IS NULL)",
        "done_at",
    ),
    _check(present("note", MIN_REASON_CHARS), "note"),
    _check(person("created_by"), "created_by_person"),
    _check("updated_at >= created_at", "updated_after_created"),
)
Index(None, catalog_tasks.c.anomaly_id)

# ---- Telemetry (US-905, NFR-12, spec 9) ---------------------------------------------------------
# One row per GPU and amd-smi sample, every 5 s, written by the sampler of
# apps/api/services/other/telemetry.py and kept 24 hours; a row with an error and no numbers when
# amd-smi gave none. Not a decision record: the demo reset empties it with the other tables.

telemetry_samples = Table(
    "telemetry_samples",
    metadata,
    Column(
        "sample_id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("sampled_at", TS, nullable=False),
    Column("device", String(64), nullable=False),
    Column("gpu_index", Integer, nullable=False),
    Column("utilization_percent", Float),
    Column("memory_used_gib", Float),
    Column("memory_total_gib", Float),
    Column("power_watts", Float),
    Column("source", String(32), nullable=False),
    Column("error", Text),
    _check(
        "utilization_percent IS NULL OR (utilization_percent >= 0 AND utilization_percent <= 100)",
        "utilization_range",
    ),
    _check(
        "(memory_used_gib IS NULL OR memory_used_gib >= 0)"
        " AND (memory_total_gib IS NULL OR memory_total_gib >= 0)"
        " AND (power_watts IS NULL OR power_watts >= 0)",
        "non_negative",
    ),
)
Index(None, telemetry_samples.c.sampled_at)

# ---- The demo reset and the id counters (ADR-15 section 15, contract 1.5.0) -------------------

# One row, written by the demo reset (python -m apps.api.reset, US-903): the instant of the
# serving snapshot the reset decided at, the wall time the demo clock started (DEMO_CLOCK, the
# end of the reset) and the golden cases of GET /api/demo/cases with their live ids.
demo_reset = Table(
    "demo_reset",
    metadata,
    Column("reset_id", Integer, primary_key=True, autoincrement=False),
    Column("snapshot_as_of", TS, nullable=False),
    Column("started_at", TS, nullable=False),  # wall clock, real time
    Column("cases", JSONDoc, nullable=False),  # DemoCasesResponse without as_of
    _check("reset_id = 1", "single"),
)

# The next number of each id kind (MSG, TS-CHK, ORD-CHK, INS, EV, LD, CT, GEN-AGENT and the
# drafts of each return): incremented in the transaction that writes the id, so two writers
# never take the same number (its row stays locked until the transaction ends) and a rolled
# back transaction gives its number back. Ids stay sequential and deterministic (H10, H18).
id_counters = Table(
    "id_counters",
    metadata,
    Column("kind", String(128), primary_key=True),
    Column("value", BigInteger, nullable=False),
    _check("value >= 0", "non_negative"),
)

TABLES: tuple[Table, ...] = tuple(metadata.sorted_tables)
APPEND_ONLY_TABLES: tuple[str, ...] = ("audit_log", "simulator_assumptions", "coupon_rules")

# ---- Append-only triggers ----------------------------------------------------------------------


def _ddl(statement: str) -> DDL:
    return DDL(statement)  # type: ignore[no-untyped-call]  # SQLAlchemy leaves DDL untyped


_PG_FUNCTION = _ddl(
    "CREATE OR REPLACE FUNCTION returnring_append_only() RETURNS trigger LANGUAGE plpgsql AS "
    "$fn$ BEGIN RAISE EXCEPTION '%% is append-only', TG_TABLE_NAME; END; $fn$"
)


def _append_only(table: Table) -> None:
    name = table.name
    for op in ("UPDATE", "DELETE"):
        event.listen(
            table,
            "after_create",
            _ddl(
                f"CREATE TRIGGER {name}_no_{op.lower()} BEFORE {op} ON {name} "
                f"BEGIN SELECT RAISE(ABORT, '{name} is append-only'); END"
            ).execute_if(dialect="sqlite"),
        )
    event.listen(table, "after_create", _PG_FUNCTION.execute_if(dialect="postgresql"))
    event.listen(
        table,
        "after_create",
        _ddl(
            f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE ON {name} "
            "FOR EACH ROW EXECUTE FUNCTION returnring_append_only()"
        ).execute_if(dialect="postgresql"),
    )
    event.listen(
        table,
        "after_create",
        _ddl(
            f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION returnring_append_only()"
        ).execute_if(dialect="postgresql"),
    )


for _name in APPEND_ONLY_TABLES:
    _append_only(metadata.tables[_name])

# ---- Helpers -----------------------------------------------------------------------------------


def enable_sqlite_foreign_keys(engine: Engine | AsyncEngine) -> None:
    """SQLite checks foreign keys only when asked, on every connection."""
    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    if sync_engine.dialect.name != "sqlite":
        return

    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(sync_engine, "connect", _on_connect)


async def create_all(engine: AsyncEngine) -> None:
    """Create every table, constraint and trigger (tests and the demo reset, US-903). On SQLite,
    call enable_sqlite_foreign_keys(engine) first, right after creating the engine."""
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
