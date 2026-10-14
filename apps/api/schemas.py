"""HTTP API contract: request and response models of every endpoint (ADR-15, spec section 9).

The API implementation (phase 4) and the React UI (phase 5) build against this module, the JSON
Schemas exported from it (contracts/json/api/, scripts/export_api_schemas.py) and the examples in
contracts/examples/api/. Rules that hold for every model:

- Every model forbids unknown fields. Datetimes carry a timezone (UTC, "Z").
- Every figure the UI displays (scores, rates, counts, euros, net benefit, benchmark numbers) is a
  Metric {value, unit, provenance}; provenance is {origin, period_start, period_end, nature in
  real | synthetic | simulated} (rule 11, US-906, NFR-16). A plain number is allowed only as an
  attribute of a record (an order's price, a link's weight) inside a section that carries its own
  Provenance, which then covers every plain number of the section.
- Levers are the {id, value, scope} objects of contracts/levers.py, listed as differences from the
  standard levers on the merchant base terms, in catalogue order (ADR-13). Decision fields are
  exactly what policy.engine produces; the validators below check the engine's invariants again.
- Every DecisionView passes policy.legal_guard under the mapping of to_policy_decision (ADR-15
  section 8): an override is a person's approved decision on the operator's levers.
- The LLM never decides, and the LLM contract has one source, llm/ (ADR-14): a draft is the
  output of llm.generate for the engine's decision, accepted by llm.validator against the
  code-built facts its statements cite; customer messages are phrasebook ids composed by
  llm.phrasebook with the template fields of llm.messages and the fixed clause of
  llm/legal_clause.py (rules 1 and 10). Nothing here renders customer text of its own.
- "Linked accounts", never "same person" (rule 11).

The JSON Schemas are structural. The model validators add the semantic rules listed in ADR-15;
the examples pass both.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal, Self, get_args

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    RootModel,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    WithJsonSchema,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from contracts.io import Nature
from contracts.levers import (
    LEVERS_BY_ID,
    LeverScope,
    check_lever_objects,
    format_lever_id,
    lever_json_schema,
    lever_list_json_schema,
    parse_lever_id,
)
from contracts.parquet import is_forbidden_feature
from contracts.vocab import (
    ACTOR_PATTERN,
    CHECKLIST_ITEMS,
    DECISION_AUDIT_ACTIONS,
    DEMO_ROLES,
    GENERATION_AUDIT_ACTIONS,
    HUMAN_ONLY_ACTIONS,
    LANGS,
    LINK_EXPLAINING_EVIDENCE,
    MIN_REASON_CHARS,
    REASON_REQUIRED_ACTIONS,
    SYSTEM_ACTOR,
    AssumptionBasis,
    AuditAction,
    AuditEntityType,
    CatalogTaskKind,
    CatalogTaskStatus,
    ChecklistItemId,
    ClauseKind,
    CouponApplicationState,
    DecisionAction,
    DecisionStatus,
    EvidenceKind,
    FallbackReason,
    GenerationKind,
    GenerationPath,
    InspectionState,
    Lang,
    LlmMode,
    Observation,
    OutboxStatus,
    OverrideCategory,
    PhotoMediaType,
    RevisionTrigger,
    Role,
    ServedFrom,
    SizeAdviceKind,
    ValidatorCode,
)
from llm.case import MAX_FACTORS, CaseError, ClusterSummary, ScoreFactor, build_case, factor_entity
from llm.case import Entity as FactEntity
from llm.decision import serialize_decision
from llm.generate import MAX_MODEL_CALLS, Generation
from llm.legal_clause import LegalClauseError, assert_clause
from llm.legal_clause import required_clause as llm_required_clause
from llm.messages import template_fields
from llm.phrasebook import (
    COUPON_BLOCK_TEXT,
    MAX_SENTENCES,
    MIN_SENTENCES,
    PHRASEBOOK,
    SENTENCE_IDS,
    context_of,
    render_selection,
    selection_problems,
    size_advice_text,
    subject,
)
from llm.templates import default_selection
from llm.text import banned_terms
from llm.validator import (
    MAX_FACTS_PER_STATEMENT,
    MAX_LEVER_IDS,
    MAX_STATEMENT_CHARS,
    MAX_STATEMENTS,
    LlmOutput,
    validate,
)
from policy.engine import REVIEW_DAYS, guarantee_terms, quadrant_for
from policy.engine import Thresholds as PolicyThresholds
from policy.legal_guard import (
    APPROVAL_QUADRANTS,
    LEGAL_REFUND_DAYS,
    LEGAL_WITHDRAWAL_DAYS,
    MAX_REVIEW_DAYS,
    is_restrictive,
)
from policy.legal_guard import check as legal_guard_check
from policy.simulator import Assumptions as SimAssumptions
from policy.simulator import net_benefit, sensitivity
from policy.types import (
    CaseScores,
    Decision,
    DecisionState,
    DiminishedValueClaim,
    EvidenceQuality,
    Levers,
    Path,
    Quadrant,
    ReturnReason,
    ReturnRequest,
    ReturnTerms,
)
from policy.types import CouponRuleApplication as PolicyCouponRule

# 1.1.0: first review round of ADR-15 (lift, versions, history); 1.2.0: second review round
# (lapse at checkout and of coupon applications, renew, typed links, input provenance, labels);
# 1.3.0: third round (the LLM contract of llm/ in drafts and messages, decision day, bounded
# review dates, typed generation records, payload metrics, realized KPI sets); 1.4.0: fourth
# round (no unapproved restriction announced, coupon blocks tied to their cluster, the facts of
# the llm_case on every draft, the deduction stated with its reason, error scenarios, tone gone);
# 1.4.1: the LLM layer's closing rounds merged (no lever fact, validator code TERMS_TOPIC; ADR-15
# section 14)
API_CONTRACT_VERSION = "1.4.1"
MODE_HEADER = "X-ReturnRing-Mode"  # every response: "live" or "cached" (US-901)
REQUEST_ID_HEADER = "X-Request-Id"  # every response; also in the audit log (NFR-12)
CachedLabel = Literal["Precomputed on AMD MI300X"]
CACHED_LABEL: CachedLabel = get_args(CachedLabel)[0]
ValueLegend = Literal[
    "Customer value orients benefits and service. It is never a signal of abuse and never feeds "
    "the risk score."
]
VALUE_LEGEND: ValueLegend = get_args(ValueLegend)[0]
FollowUpCaveat = Literal[
    "A decrease alone does not prove the effect of the correction: that needs a controlled "
    "comparison."
]
FOLLOW_UP_CAVEAT: FollowUpCaveat = get_args(FollowUpCaveat)[0]
ResidualNote = Literal[
    "An indicator of returns beyond what the products predict, not a probability"
]
RESIDUAL_NOTE: ResidualNote = get_args(ResidualNote)[0]
# Customer value is never a risk feature (rule 6): these names never appear among score factors.
VALUE_FEATURES = frozenset({"value_eur", "customer_value", "customer_value_eur", "net_margin_12m"})
MAX_AGENT_STEPS = 5  # spec 8.8
_RISK_SHOWN = "risk is shown exactly on the withdrawal path with sufficient evidence (US-307)"
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:~-]*$"
LINK_SEPARATOR = "~"

# ---- Base and shared types ----------------------------------------------------------------------


class ApiModel(BaseModel):
    """Base of every API model: unknown fields are rejected, and every field of a response is
    required in the exported serialization schema (the API always sends it, null included)."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


def _human(actor: str) -> str:
    if actor == SYSTEM_ACTOR:
        raise ValueError("a person is required here, not the system (rule 5)")
    return actor


def _eur(x: float) -> float:
    if not math.isfinite(x) or abs(round(x, 2) - x) > 1e-9:
        raise ValueError(f"a euro amount has at most 2 decimals, got {x!r}")
    return x


def _monday(d: date) -> date:
    if d.weekday() != 0:
        raise ValueError(f"a week starts on Monday, got {d.isoformat()}")
    return d


def utc_day(moment: datetime) -> date:
    """The UTC calendar day of an aware datetime: the day a review date is compared with (a
    restriction lapses from its review date on; contracts.sql.utc_date is the same in SQL)."""
    return moment.astimezone(UTC).date()


Id = Annotated[str, StringConstraints(min_length=1, max_length=96, pattern=ID_PATTERN)]
Actor = Annotated[str, StringConstraints(pattern=ACTOR_PATTERN)]
HumanActor = Annotated[
    str,
    StringConstraints(pattern=ACTOR_PATTERN),
    AfterValidator(_human),
    Field(json_schema_extra={"not": {"const": SYSTEM_ACTOR}}),
]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
# At least MIN_REASON_CHARS characters once trimmed: the pattern says it to JSON Schema too.
Reason = Annotated[
    str,
    StringConstraints(min_length=MIN_REASON_CHARS, max_length=1000, pattern=r"\S[\s\S]+\S"),
]
Version = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Eur = Annotated[float, AfterValidator(_eur)]
NonNegativeEur = Annotated[float, Field(ge=0), AfterValidator(_eur)]
Probability = Annotated[float, Field(ge=0, le=1)]
Week = Annotated[date, AfterValidator(_monday)]
LeverIdText = Annotated[str, StringConstraints(min_length=3, max_length=64)]


def _blank(text: str | None) -> bool:
    return text is None or len(text.strip()) < MIN_REASON_CHARS


def _same_amount(a: float, b: float) -> bool:
    return abs(Decimal(str(a)) - Decimal(str(b))) < Decimal("0.005")


def link_id(customer_a: str, customer_b: str) -> str:
    """Id of the link between two accounts: the two customer ids in order, joined by "~"."""
    if customer_a == customer_b or LINK_SEPARATOR in customer_a + customer_b:
        raise ValueError("a link joins two distinct customer ids without '~'")
    first, second = sorted((customer_a, customer_b))
    return f"{first}{LINK_SEPARATOR}{second}"


def split_link_id(text: str) -> tuple[str, str]:
    parts = text.split(LINK_SEPARATOR)
    if len(parts) != 2 or link_id(parts[0], parts[1]) != text:
        raise ValueError(f"{text!r} is not a link id (customer_a~customer_b, a < b)")
    return parts[0], parts[1]


def anomaly_id(variant_id: str, reason: ReturnReason, week: date) -> str:
    """Id of a product anomaly: the key of features/product_anomalies joined by "~"."""
    return f"{variant_id}{LINK_SEPARATOR}{reason.value}{LINK_SEPARATOR}{week.isoformat()}"


# ---- Provenance and figures (rule 11, US-906, NFR-16) --------------------------------------------

Unit = Literal[
    "eur",
    "usd",
    "count",
    "expected_count",
    "ratio",
    "probability",
    "percent",
    "percentage_points",
    "z_score",
    "score",
    "days",
    "seconds",
    "milliseconds",
    "tokens_per_second",
    "gib",
    "watts",
]
_NON_NEGATIVE_UNITS = frozenset(
    {"expected_count", "days", "seconds", "milliseconds", "tokens_per_second", "gib", "watts"}
)


class Provenance(ApiModel):
    """Where a number comes from: origin, period and nature (same fields as contracts.io)."""

    origin: ShortText
    period_start: date
    period_end: date
    nature: Nature

    @model_validator(mode="after")
    def _period(self) -> Self:
        if self.period_start > self.period_end:
            raise ValueError("period_start is after period_end")
        return self


class Metric(ApiModel):
    """A figure shown in the UI: its value, its unit and where it comes from."""

    value: StrictInt | StrictFloat
    unit: Unit
    provenance: Provenance

    @model_validator(mode="before")
    @classmethod
    def _number(cls, data: Any) -> Any:
        if isinstance(data, dict):
            value = data.get("value")
            if isinstance(value, bool):
                raise ValueError("a metric value is a number, not a boolean")
            if isinstance(value, Decimal):  # convenience for code that computes in Decimal
                whole = data.get("unit") == "count" and value == value.to_integral_value()
                data = {**data, "value": int(value) if whole else float(value)}
        return data

    @model_validator(mode="after")
    def _unit(self) -> Self:
        v, unit = self.value, self.unit
        if not math.isfinite(v):
            raise ValueError("a metric value must be finite")
        if unit == "count" and (not isinstance(v, int) or v < 0):
            raise ValueError("a count is a non-negative integer")
        if unit in ("ratio", "probability") and not 0 <= v <= 1:
            raise ValueError(f"a {unit} is in [0, 1]")
        if unit == "percent" and not 0 <= v <= 100:
            raise ValueError("a percent is in [0, 100]")
        if unit == "percentage_points" and not -100 <= v <= 100:
            raise ValueError("percentage points are in [-100, 100]")
        if unit in ("eur", "usd"):
            _eur(float(v))
        if unit in _NON_NEGATIVE_UNITS and v < 0:
            raise ValueError(f"{unit} cannot be negative")
        return self


def _same_unit(*metrics: Metric | None, unit: Unit) -> None:
    for m in metrics:
        if m is not None and m.unit != unit:
            raise ValueError(f"expected unit {unit}, got {m.unit}")


# ---- Levers and terms (contracts/levers.py, policy.types) ---------------------------------------


class Lever(ApiModel):
    """One lever as the contract object {id, value, scope} of the catalogue (ADR-13)."""

    id: str
    value: StrictBool | StrictInt
    scope: LeverScope

    @model_validator(mode="after")
    def _catalogue(self) -> Self:
        format_lever_id(self.id, self.value)  # unknown id, wrong type, window below 14 days
        if self.scope != LEVERS_BY_ID[self.id].scope:
            raise ValueError(f"{self.id} has scope {LEVERS_BY_ID[self.id].scope}, not {self.scope}")
        return self

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return lever_json_schema()  # the catalogue schema, as in llm_case (ADR-13)

    def lever_id(self) -> str:
        """The "id:value" form, for example "instant_refund:false"."""
        return format_lever_id(self.id, self.value)


def _lever_list(levers: list[Lever]) -> list[Lever]:
    """A list of levers names each catalogue lever at most once (contracts.levers)."""
    check_lever_objects(x.model_dump() for x in levers)
    return levers


# Every lever array of the contract: lever objects, each lever at most once, at most one per
# catalogue lever; its JSON Schema is contracts.levers.lever_list_json_schema (ADR-13, ADR-14).
LeverList = Annotated[
    list[Lever], AfterValidator(_lever_list), WithJsonSchema(lever_list_json_schema())
]


class Terms(ApiModel):
    """Return terms (policy.types.ReturnTerms): the window never goes below 14 days (rule 3)."""

    return_window_days: int = Field(ge=LEGAL_WITHDRAWAL_DAYS)
    free_return_shipping: bool
    instant_refund: bool
    free_size_exchange: bool
    coupons_enabled: bool

    @classmethod
    def from_policy(cls, terms: ReturnTerms) -> Terms:
        return cls(
            return_window_days=terms.return_window_days,
            free_return_shipping=terms.free_return_shipping,
            instant_refund=terms.instant_refund,
            free_size_exchange=terms.free_size_exchange,
            coupons_enabled=terms.coupons_enabled,
        )

    def to_policy(self) -> ReturnTerms:
        return ReturnTerms(
            return_window_days=self.return_window_days,
            free_return_shipping=self.free_return_shipping,
            instant_refund=self.instant_refund,
            free_size_exchange=self.free_size_exchange,
            coupons_enabled=self.coupons_enabled,
        )


def levers_from_policy(levers: Levers, base: ReturnTerms) -> list[Lever]:
    """The contract lever list of policy Levers: differences from the standard, catalogue order."""
    return [Lever.model_validate(dict(obj)) for obj in levers.to_contract(base)]


def to_policy_levers(levers: Sequence[Lever], base: ReturnTerms) -> Levers:
    """Inverse of levers_from_policy: rebuild policy Levers from a contract lever list. Rejects a
    lever given twice or equal to its standard value (a list holds only differences)."""
    values: dict[str, int | bool] = Levers.standard(base).values()
    seen: set[str] = set()
    for lever in levers:
        if lever.id in seen:
            raise ValueError(f"lever {lever.id} is given twice")
        seen.add(lever.id)
        if values[lever.id] == lever.value:
            raise ValueError(f"lever {lever.id} equals its standard value: list only differences")
        values[lever.id] = lever.value
    return Levers(
        future_terms=ReturnTerms(
            return_window_days=int(values["return_window_days"]),
            free_return_shipping=bool(values["free_return_shipping"]),
            instant_refund=bool(values["instant_refund"]),
            free_size_exchange=bool(values["free_size_exchange"]),
            coupons_enabled=bool(values["coupons_enabled"]),
        ),
        cluster_coupons_disabled=bool(values["cluster_coupons_disabled"]),
        transparency_notice=bool(values["transparency_notice"]),
        size_advice=bool(values["size_advice"]),
        priority_size_exchange=bool(values["priority_size_exchange"]),
    )


def _catalogue_order(levers: Sequence[Lever], what: str) -> None:
    order = list(LEVERS_BY_ID)
    positions = [order.index(lever.id) for lever in levers]
    if positions != sorted(set(positions)):
        raise ValueError(f"{what} must be unique and in catalogue order")


# ---- Errors -------------------------------------------------------------------------------------

ErrorCode = Literal[
    "invalid_request",  # 422: body, path or query invalid (pydantic errors as violations)
    "not_found",  # 404
    "conflict",  # 409: already decided, already confirmed, approve with different levers
    "stale_decision",  # 409: the decision_id sent is not the latest version of the case
    "legal_guard",  # 422: policy.legal_guard violations, with their codes
    "forbidden_role",  # 403: the actor's role may not take this action (ADR-15 role table)
    "rate_limited",  # 429: agent endpoint
    "llm_unavailable",  # 503: only where no template or cache exists
    "internal",  # 500
]


class ViolationView(ApiModel):
    code: ShortText  # a legal_guard code (WINDOW_BELOW_LEGAL...) or a pydantic error type
    message: ShortText


class ErrorResponse(ApiModel):
    """Body of every non-2xx response."""

    code: ErrorCode
    message: ShortText
    violations: list[ViolationView] = Field(default_factory=list)
    request_id: Id | None = None


# ---- Economic simulator (spec 8.10, policy/simulator.py) ---------------------------------------

NetBenefitComponentId = Literal[
    "logistics_costs_avoided",
    "undue_discounts_avoided",
    "time_saved_value",
    "incremental_margin",
    "incentives",
    "system_cost",
    "error_cost",
    "abandonment_margin_lost",
]
NET_BENEFIT_COMPONENTS: tuple[NetBenefitComponentId, ...] = get_args(NetBenefitComponentId)
SensitivityId = Literal["base", "logistics_halved", "abandonment_doubled", "both"]
SENSITIVITY_IDS: tuple[SensitivityId, ...] = get_args(SensitivityId)


def _dec(x: float) -> Decimal:
    return Decimal(str(x))


class AssumptionInputs(ApiModel):
    """The inputs of policy.simulator.Assumptions. Every value is non-negative: the simulator
    applies the signs. There is no refund field: the refund owed is never a saving (rule 9)."""

    period: ShortText
    logistics_returns_avoided: int = Field(ge=0)
    logistics_cost_per_return_eur: NonNegativeEur
    undue_discounts_avoided: int = Field(ge=0)
    avg_undue_discount_eur: NonNegativeEur
    minutes_saved: float = Field(ge=0)
    hourly_cost_eur: NonNegativeEur
    incentives_eur: NonNegativeEur
    system_cost_eur: NonNegativeEur
    error_cost_eur: NonNegativeEur
    abandonment_margin_lost_eur: NonNegativeEur
    incremental_margin_eur: NonNegativeEur

    def to_policy(self, version: str, basis: AssumptionBasis) -> SimAssumptions:
        return SimAssumptions(
            period=self.period,
            logistics_returns_avoided=self.logistics_returns_avoided,
            logistics_cost_per_return_eur=_dec(self.logistics_cost_per_return_eur),
            undue_discounts_avoided=self.undue_discounts_avoided,
            avg_undue_discount_eur=_dec(self.avg_undue_discount_eur),
            minutes_saved=_dec(self.minutes_saved),
            hourly_cost_eur=_dec(self.hourly_cost_eur),
            incentives_eur=_dec(self.incentives_eur),
            system_cost_eur=_dec(self.system_cost_eur),
            error_cost_eur=_dec(self.error_cost_eur),
            abandonment_margin_lost_eur=_dec(self.abandonment_margin_lost_eur),
            incremental_margin_eur=_dec(self.incremental_margin_eur),
            nature=basis,
            version=version,
        )


# Prices of one unit: a derived assumption set takes them from the stored version it names.
UNIT_COST_FIELDS: tuple[str, ...] = (
    "logistics_cost_per_return_eur",
    "avg_undue_discount_eur",
    "hourly_cost_eur",
)


class Assumptions(AssumptionInputs):
    """An assumption set; basis is policy.simulator's nature: simulated or realized.

    One label, one set of values. A stored version (GET and PUT /api/simulator/assumptions) has
    `based_on` null. Any other set (the alternatives of a case, a week of KPIs, a sensitivity
    scenario) has its own label in `version` and names in `based_on` the stored version whose
    unit costs it uses: the alternatives of a decision version are labelled
    "<decision_id>/<alternative kind>", a sensitivity scenario "<version>/<scenario>"."""

    version: Version
    basis: AssumptionBasis
    based_on: Version | None

    def as_policy(self) -> SimAssumptions:
        return self.to_policy(self.version, self.basis)

    def derive(self, version: str, period: str, **quantities: int | float) -> Assumptions:
        """A set with the unit costs of this stored version and its own quantities (the other
        inputs default to 0), labelled `version` and based on this one."""
        if self.based_on is not None:
            raise ValueError("derive from a stored version, not from a derived set")
        unknown = set(quantities) - set(AssumptionInputs.model_fields) | (
            set(quantities) & set(UNIT_COST_FIELDS)
        )
        if unknown:
            raise ValueError(f"not quantities of a derived set: {sorted(unknown)}")
        values: dict[str, Any] = {
            name: 0 for name in AssumptionInputs.model_fields if name != "period"
        }
        values.update({name: getattr(self, name) for name in UNIT_COST_FIELDS})
        values.update(quantities)
        return Assumptions(
            period=period, version=version, basis=self.basis, based_on=self.version, **values
        )

    @classmethod
    def from_policy(cls, a: SimAssumptions, based_on: str | None = None) -> Assumptions:
        return cls(
            period=a.period,
            logistics_returns_avoided=a.logistics_returns_avoided,
            logistics_cost_per_return_eur=float(a.logistics_cost_per_return_eur),
            undue_discounts_avoided=a.undue_discounts_avoided,
            avg_undue_discount_eur=float(a.avg_undue_discount_eur),
            minutes_saved=float(a.minutes_saved),
            hourly_cost_eur=float(a.hourly_cost_eur),
            incentives_eur=float(a.incentives_eur),
            system_cost_eur=float(a.system_cost_eur),
            error_cost_eur=float(a.error_cost_eur),
            abandonment_margin_lost_eur=float(a.abandonment_margin_lost_eur),
            incremental_margin_eur=float(a.incremental_margin_eur),
            version=a.version,
            basis=a.nature,
            based_on=based_on,
        )


REALIZED_SUFFIX = "/realized"


def is_realized_set(a: Assumptions) -> bool:
    """A set of observed quantities ("<label>/realized", basis realized): it prices outcomes
    with the unit costs of the stored version its simulated counterpart uses (US-602, US-603)."""
    return a.basis == "realized" and a.version.endswith(REALIZED_SUFFIX)


def check_unit_costs(derived: Assumptions, stored: Assumptions) -> None:
    """A derived set uses exactly the unit costs and the basis of the stored version it names
    (the API calls it when it builds a net benefit; the tests run it on every example). A
    realized set ("<label>/realized") keeps its own basis: it measures what happened, with the
    unit costs of the stored version (finding D8)."""
    if stored.based_on is not None or derived.based_on != stored.version:
        raise ValueError(f"{derived.version} is not based on the stored version {stored.version}")
    for name in UNIT_COST_FIELDS:
        if getattr(derived, name) != getattr(stored, name):
            raise ValueError(f"{derived.version}: {name} differs from {stored.version}")
    if derived.basis != stored.basis and not is_realized_set(derived):
        raise ValueError(f"{derived.version}: basis differs from {stored.version}")


def _same_unit_costs(sets: Sequence[Assumptions], based_on: str, what: str) -> None:
    """The sets derived from one stored version share its unit costs and its basis (as
    check_unit_costs requires of each set against the stored version itself)."""
    labels = [a.version for a in sets]
    if len(set(labels)) != len(labels):
        raise ValueError(f"{what}: one label per assumption set")
    for a in sets:
        if a.based_on != based_on:
            raise ValueError(f"{what}: every set is based on {based_on}")
        if any(getattr(a, f) != getattr(sets[0], f) for f in UNIT_COST_FIELDS):
            raise ValueError(f"{what}: the unit costs of {based_on} are the same for every set")
        if a.basis != sets[0].basis:
            raise ValueError(f"{what}: every set has the basis of {based_on}")


class NetBenefitComponent(ApiModel):
    id: NetBenefitComponentId
    amount: Metric  # eur, signed: costs are negative


class NetBenefitView(ApiModel):
    """Net benefit with disjoint components, exactly as policy.simulator.net_benefit computes it
    from `assumptions` (a validator recomputes it)."""

    assumptions: Assumptions
    components: list[NetBenefitComponent]
    total: Metric

    @model_validator(mode="after")
    def _simulator(self) -> Self:
        computed = net_benefit(self.assumptions.as_policy())
        ids = [c.id for c in self.components]
        if ids != list(computed.components):
            raise ValueError(f"components must be {list(computed.components)} in this order")
        for component in self.components:
            _same_unit(component.amount, unit="eur")
            if component.amount.provenance != self.total.provenance:
                raise ValueError("every component carries the provenance of the total")
            if _dec(component.amount.value) != computed.components[component.id]:
                raise ValueError(f"{component.id} differs from policy.simulator")
        _same_unit(self.total, unit="eur")
        if _dec(self.total.value) != computed.total:
            raise ValueError(f"total differs from policy.simulator ({computed.total})")
        nature = self.total.provenance.nature
        if self.assumptions.basis == "simulated" and nature != "simulated":
            raise ValueError("a simulated net benefit has provenance nature simulated")
        if self.assumptions.basis == "realized" and nature == "simulated":
            raise ValueError("a realized net benefit is measured: nature real or synthetic")
        if self.assumptions.based_on == self.assumptions.version:
            raise ValueError("a derived assumption set has its own label")
        return self

    @classmethod
    def build(
        cls, assumptions: SimAssumptions | Assumptions, provenance: Provenance
    ) -> NetBenefitView:
        """The view of policy.simulator.net_benefit: from a stored version's policy set, or from
        an Assumptions (for example one made with Assumptions.derive)."""
        if isinstance(assumptions, Assumptions):
            view, policy_set = assumptions, assumptions.as_policy()
        else:
            view, policy_set = Assumptions.from_policy(assumptions), assumptions
        nb = net_benefit(policy_set)
        return cls.model_validate(
            {
                "assumptions": view,
                "components": [
                    {
                        "id": key,
                        "amount": Metric(value=float(v), unit="eur", provenance=provenance),
                    }
                    for key, v in nb.components.items()
                ],
                "total": Metric(value=float(nb.total), unit="eur", provenance=provenance),
            }
        )


class SensitivityScenario(ApiModel):
    id: SensitivityId
    net_benefit: NetBenefitView


# ---- Queue (US-301) -----------------------------------------------------------------------------

QuadrantFilter = Literal["Q1", "Q2", "Q3", "Q4", "none"]  # none: guarantee or insufficient
QueueSort = Literal[
    "-expected_net_benefit", "expected_net_benefit", "-requested_at", "requested_at", "-risk"
]
DraftStatus = Literal["ready", "pending"]


class QueueQuery(ApiModel):
    """Query string of GET /api/returns; list filters repeat the parameter (?quadrant=Q3&...).

    review_due=true keeps the cases whose latest decision is an approved or overridden
    restriction at or past its review date (US-607): it no longer applies (in_force false) and
    waits for a person to renew it (action renew) or to lift it (action lift). q matches a part
    of the return, order or customer id or of the display name, ignoring case."""

    state: list[DecisionStatus] | None = None
    quadrant: list[QuadrantFilter] | None = None
    path: list[Path] | None = None
    evidence_quality: EvidenceQuality | None = None
    requires_approval: bool | None = None
    review_due: bool | None = None
    q: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    sort: QueueSort = "-expected_net_benefit"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)

    def matches(self, item: QueueItem) -> bool:
        """Whether a queue item passes the filters (the reference the API's query must equal)."""
        quadrant: QuadrantFilter = item.quadrant.value if item.quadrant else "none"
        text = self.q.lower() if self.q else None
        fields = (item.return_id, item.order_id, item.customer_id, item.display_name or "")
        return (
            (self.state is None or item.state in self.state)
            and (self.quadrant is None or quadrant in self.quadrant)
            and (self.path is None or item.path in self.path)
            and (self.evidence_quality is None or item.evidence_quality is self.evidence_quality)
            and (self.requires_approval is None or item.requires_approval == self.requires_approval)
            and (self.review_due is None or item.review_due == self.review_due)
            and (text is None or any(text in x.lower() for x in fields))
        )


def _check_scoring(
    path: Path,
    evidence: EvidenceQuality,
    quadrant: Quadrant | None,
    state: DecisionStatus,
    requires_approval: bool,
) -> None:
    """Invariants of policy.engine.decide and policy.legal_guard on the scoring fields."""
    if path is Path.GUARANTEE and (quadrant is not None or requires_approval):
        raise ValueError("the guarantee path has no quadrant and no restriction (rule 4)")
    if evidence is EvidenceQuality.INSUFFICIENT and quadrant is not None:
        raise ValueError("insufficient evidence has no quadrant (US-307)")
    in_effect = state in ("proposed", "approved")
    if quadrant in APPROVAL_QUADRANTS and in_effect and not requires_approval:
        raise ValueError("Q3 and Q4 always require a human approval (rule 5)")


class QueueItem(ApiModel):
    return_id: Id
    order_id: Id
    customer_id: Id
    display_name: ShortText | None  # UI only, never a feature
    requested_at: AwareDatetime
    reason: ReturnReason
    path: Path
    evidence_quality: EvidenceQuality
    quadrant: Quadrant | None
    decision_id: Id
    state: DecisionStatus
    requires_approval: bool
    lever_ids: list[LeverIdText]  # "id:value" of the decision's current levers
    review_at: date | None  # review date of the decision's restriction (US-607)
    review_due: bool  # an approved or overridden restriction at or past its review date
    risk: Metric | None  # probability; null on the guarantee path or with insufficient evidence
    value: Metric | None  # eur, 12-month net margin
    expected_net_benefit: Metric  # eur, simulated: the recommended alternative of the case card
    linked_account_count: int = Field(ge=0)  # other accounts in the customer's cluster
    variant_anomaly: bool  # the returned variant has an open product anomaly
    draft_status: DraftStatus

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        _check_scoring(
            self.path, self.evidence_quality, self.quadrant, self.state, self.requires_approval
        )
        _same_unit(self.risk, unit="probability")
        _same_unit(self.value, unit="eur")
        _same_unit(self.expected_net_benefit, unit="eur")
        shown = self.path is Path.WITHDRAWAL and self.evidence_quality is EvidenceQuality.SUFFICIENT
        if (self.risk is not None) != shown:
            raise ValueError(_RISK_SHOWN)
        for text in self.lever_ids:
            parse_lever_id(text)
        if (self.review_at is not None) != self.requires_approval:
            raise ValueError("a restriction, and only a restriction, has a review date (US-607)")
        if self.review_due and self.state not in ("approved", "overridden"):
            raise ValueError("only an approved or overridden restriction comes up for review")
        return self


class QueueResponse(ApiModel):
    items: list[QueueItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    sort: QueueSort
    as_of: AwareDatetime
    provenance: Provenance  # covers the plain numbers of the items (linked_account_count)

    @model_validator(mode="after")
    def _page(self) -> Self:
        if len(self.items) > self.page_size or len(self.items) > self.total:
            raise ValueError("more items than the page size or the total")
        today = utc_day(self.as_of)
        for item in self.items:
            due = (
                item.state in ("approved", "overridden")
                and item.review_at is not None
                and item.review_at <= today
            )
            if item.review_due != due:
                raise ValueError(f"{item.return_id}: review_due is a restriction past review_at")
        return self


# ---- Case card (US-302..US-310) ----------------------------------------------------------------


class ReturnedItem(ApiModel):
    line_id: Id
    variant_id: Id
    product_id: Id
    category: ShortText
    brand: ShortText | None
    size: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None
    price_eur: NonNegativeEur


class ReturnRequestView(ApiModel):
    reason: ReturnReason
    customer_text: LongText | None  # the customer's own words: data, never instructions
    requested_at: AwareDatetime
    delivered_on: date
    notice_on: date  # the day the customer communicated the return
    days_since_delivery: int = Field(ge=0)

    @model_validator(mode="after")
    def _dates(self) -> Self:
        if self.notice_on < self.delivered_on:
            raise ValueError("notice_on is before delivered_on")
        if self.days_since_delivery != (self.notice_on - self.delivered_on).days:
            raise ValueError("days_since_delivery must equal notice_on - delivered_on")
        return self


EvidenceComponentId = Literal["observations", "identifier_completeness", "reason_consistency"]
EVIDENCE_COMPONENTS: tuple[EvidenceComponentId, ...] = get_args(EvidenceComponentId)


class EvidenceComponent(ApiModel):
    """One component of evidence quality (US-209), with the threshold it is compared to."""

    id: EvidenceComponentId
    value: Metric
    threshold: Metric
    passed: bool

    @model_validator(mode="after")
    def _passed(self) -> Self:
        if self.value.unit != self.threshold.unit:
            raise ValueError("value and threshold have the same unit")
        if self.passed != (self.value.value >= self.threshold.value):
            raise ValueError("passed means value >= threshold")
        return self


class EvidenceView(ApiModel):
    """Evidence quality, separate from risk (US-209, US-307). Insufficient exactly when a component
    is below its threshold; missing_evidence then says, in plain words, what is missing."""

    quality: EvidenceQuality
    components: list[EvidenceComponent]
    missing_evidence: list[ShortText]

    @model_validator(mode="after")
    def _quality(self) -> Self:
        if [c.id for c in self.components] != list(EVIDENCE_COMPONENTS):
            raise ValueError(f"components are {list(EVIDENCE_COMPONENTS)}, in this order")
        insufficient = self.quality is EvidenceQuality.INSUFFICIENT
        if insufficient == all(c.passed for c in self.components):
            raise ValueError("evidence is insufficient exactly when a component fails")
        if insufficient != bool(self.missing_evidence):
            raise ValueError("missing_evidence is non-empty exactly when evidence is insufficient")
        return self


StatementLabel = Literal["OBSERVED", "SCORE", "HYPOTHESIS"]
FactId = Annotated[str, StringConstraints(pattern=r"^F[1-9][0-9]?$")]  # F1, F2... (llm/case.py)


def _known_sentence(sentence_id: str) -> str:
    if sentence_id not in PHRASEBOOK:
        raise ValueError(f"unknown phrasebook sentence {sentence_id!r}")
    return sentence_id


# A sentence id of the customer phrasebook (llm/phrasebook.py, ADR-14 decision 8).
SentenceId = Annotated[
    str,
    AfterValidator(_known_sentence),
    WithJsonSchema({"type": "string", "enum": list(SENTENCE_IDS)}),
]
EntityKind = Literal[
    "return",
    "order",
    "order_line",
    "customer",
    "link",
    "cluster",
    "variant",
    "anomaly",
    "catalog_task",
    "terms_snapshot",
    "decision",
]


class EntityRef(ApiModel):
    """A link target in the UI (US-701: direct link to the order or link cited)."""

    kind: EntityKind
    id: Id


class ScoreContribution(ApiModel):
    """One of the top 3 SHAP contributions: a contribution to the score, never a cause (US-204)."""

    label: Literal["SCORE"] = "SCORE"
    feature: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    feature_value: StrictBool | StrictInt | StrictFloat | ShortText
    contribution: float  # signed SHAP value on the model's log-odds scale
    direction: Literal["raises", "lowers"]
    phrase: ShortText  # fixed feature -> phrase table, not generated

    @model_validator(mode="after")
    def _feature(self) -> Self:
        if is_forbidden_feature(self.feature):
            raise ValueError(f"{self.feature} is a forbidden feature (rule 6)")
        if self.feature in VALUE_FEATURES:
            raise ValueError("customer value never feeds the risk score (rule 6)")
        if not math.isfinite(self.contribution):
            raise ValueError("contribution must be finite")
        if (self.direction == "raises") != (self.contribution > 0):
            raise ValueError("direction raises exactly when the contribution is positive")
        return self


class EvidencePoint(ApiModel):
    """An element in favour of or against the customer, shown side by side (US-302)."""

    label: StatementLabel
    statement: ShortText
    refs: list[EntityRef] = Field(default_factory=list)


class RiskView(ApiModel):
    score: Metric  # probability, calibrated
    threshold: Metric  # probability: the risk threshold of the matrix
    band: Literal["high", "low"]
    top_factors: list[ScoreContribution] = Field(min_length=3, max_length=3)  # NFR-05
    residual_z: Metric | None  # z_score of spec 8.2; product/cohort reference model (rule 8)
    residual_note: ResidualNote = RESIDUAL_NOTE
    favourable: list[EvidencePoint]
    unfavourable: list[EvidencePoint]
    model_version: Version
    provenance: Provenance  # covers the plain numbers of top_factors (values and SHAP values)

    @model_validator(mode="after")
    def _band(self) -> Self:
        _same_unit(self.score, self.threshold, unit="probability")
        _same_unit(self.residual_z, unit="z_score")
        if (self.band == "high") != (self.score.value >= self.threshold.value):
            raise ValueError("band is high exactly when score >= threshold")
        return self


class ValueView(ApiModel):
    """Customer value, 12-month net margin (spec 8.5): kept margin minus return logistics minus
    coupon discounts. Never a risk signal."""

    value_eur: Metric
    threshold: Metric  # eur: 70th percentile
    band: Literal["high", "low"]
    kept_margin_eur: Metric
    return_logistics_eur: Metric
    coupon_discounts_eur: Metric
    legend: ValueLegend = VALUE_LEGEND

    @model_validator(mode="after")
    def _formula(self) -> Self:
        parts = (self.kept_margin_eur, self.return_logistics_eur, self.coupon_discounts_eur)
        _same_unit(self.value_eur, self.threshold, *parts, unit="eur")
        if (self.band == "high") != (self.value_eur.value >= self.threshold.value):
            raise ValueError("band is high exactly when value >= threshold")
        computed = (
            _dec(self.kept_margin_eur.value)
            - _dec(self.return_logistics_eur.value)
            - _dec(self.coupon_discounts_eur.value)
        )
        if computed != _dec(self.value_eur.value):
            raise ValueError("value_eur must equal kept margin - return logistics - coupons")
        return self


IdentifierKind = Literal["card", "email", "device", "address", "phone", "ip_prefix"]


class AccountNode(ApiModel):
    customer_id: Id
    display_name: ShortText | None
    created_at: AwareDatetime
    is_case_customer: bool
    welcome_coupon_redeemed_at: AwareDatetime | None
    orders_12m: int = Field(ge=0)
    returns_12m: int = Field(ge=0)


class LinkDisputeInfo(ApiModel):
    dispute_id: Id
    reason: Reason
    author: HumanActor
    created_at: AwareDatetime


class AccountLink(ApiModel):
    """A link between two accounts (features/links, spec 8.3): kinds, dates and frequency. A link
    is a shared identifier, not a finding about who uses the accounts."""

    link_id: Id
    customer_a: Id
    customer_b: Id
    kinds: list[IdentifierKind] = Field(min_length=1)
    weight: Probability  # w_ij of spec 8.3
    weight_decayed: Probability  # with time and common-node decay
    first_shared_at: AwareDatetime
    last_shared_at: AwareDatetime
    n_shared_events: int = Field(ge=1)  # frequency
    max_sharers: int = Field(ge=2)  # largest number of accounts sharing one of the values
    disputed: bool
    dispute: LinkDisputeInfo | None
    explained_by_evidence_id: Id | None  # favourable evidence that explains it (US-310)

    @model_validator(mode="after")
    def _link(self) -> Self:
        if self.link_id != link_id(self.customer_a, self.customer_b):
            raise ValueError("link_id is customer_a~customer_b with customer_a < customer_b")
        if self.customer_a >= self.customer_b:
            raise ValueError("customer_a < customer_b")
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError("kinds are unique")
        if self.weight_decayed > self.weight:
            raise ValueError("weight_decayed <= weight")
        if self.first_shared_at > self.last_shared_at:
            raise ValueError("first_shared_at <= last_shared_at")
        if self.disputed != (self.dispute is not None):
            raise ValueError("a disputed link carries its dispute")
        return self


class ClusterFeatures(ApiModel):
    """Cluster features of spec 8.3 that tell a ring from a family."""

    n_distinct_cards: Metric  # count
    creation_spread_days: Metric  # days
    welcome_coupons_per_account: Metric  # ratio
    cluster_return_rate: Metric | None  # ratio; null before any delivered line
    strong_id_share: Metric  # ratio

    @model_validator(mode="after")
    def _units(self) -> Self:
        _same_unit(self.n_distinct_cards, unit="count")
        _same_unit(self.creation_spread_days, unit="days")
        _same_unit(
            self.welcome_coupons_per_account,
            self.cluster_return_rate,
            self.strong_id_share,
            unit="ratio",
        )
        return self


class LinkedAccountsView(ApiModel):
    """The customer's cluster: linked accounts and the links between them (US-203, US-302)."""

    cluster_id: Id  # an opaque graph id, never a ground-truth ring or family id (rule 12)
    cluster_size: int = Field(ge=2)  # accounts in the cluster, the case customer included
    accounts: list[AccountNode]
    links: list[AccountLink] = Field(min_length=1)
    features: ClusterFeatures
    as_of: AwareDatetime
    provenance: Provenance  # covers the plain numbers of accounts and links

    @model_validator(mode="after")
    def _graph(self) -> Self:
        if self.cluster_id.startswith(("R-", "F-")):
            raise ValueError(
                "cluster ids are opaque graph ids, not ring or family ids of the generator"
            )
        ids = [a.customer_id for a in self.accounts]
        if len(ids) != self.cluster_size or len(set(ids)) != len(ids):
            raise ValueError("accounts lists each account of the cluster once")
        if sum(a.is_case_customer for a in self.accounts) != 1:
            raise ValueError("exactly one account is the case customer")
        link_ids = [x.link_id for x in self.links]
        if len(set(link_ids)) != len(link_ids):
            raise ValueError("link ids are unique")
        if any(x.customer_a not in ids or x.customer_b not in ids for x in self.links):
            raise ValueError("every link joins two accounts of the cluster")
        return self


CouponKind = Literal["welcome", "referral", "promo"]


class OrderLineView(ApiModel):
    line_id: Id
    variant_id: Id
    category: ShortText
    size: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None
    price_eur: NonNegativeEur
    returned: bool
    return_id: Id | None
    return_reason: ReturnReason | None

    @model_validator(mode="after")
    def _returned(self) -> Self:
        if self.returned != (self.return_id is not None) or self.returned != (
            self.return_reason is not None
        ):
            raise ValueError("a returned line carries its return id and reason, and only then")
        return self


class OrderView(ApiModel):
    order_id: Id
    placed_at: AwareDatetime
    delivered_on: date | None
    total_eur: NonNegativeEur
    coupon_code: Id | None
    coupon_kind: CouponKind | None
    terms_snapshot_id: Id
    lines: list[OrderLineView] = Field(min_length=1)

    @model_validator(mode="after")
    def _coupon(self) -> Self:
        if (self.coupon_code is None) != (self.coupon_kind is None):
            raise ValueError("coupon_code and coupon_kind go together")
        return self


class OrderHistory(ApiModel):
    """The customer's orders in the 12 months to as_of, complete (the summary is computed on it)."""

    orders: list[OrderView]
    lines: Metric  # count
    returned_lines: Metric  # count
    return_rate: Metric  # ratio: returned_lines / lines
    provenance: Provenance  # covers the plain numbers of the orders

    @model_validator(mode="after")
    def _summary(self) -> Self:
        _same_unit(self.lines, self.returned_lines, unit="count")
        _same_unit(self.return_rate, unit="ratio")
        all_lines = [line for order in self.orders for line in order.lines]
        returned = sum(line.returned for line in all_lines)
        if self.lines.value != len(all_lines) or self.returned_lines.value != returned:
            raise ValueError("lines and returned_lines must count the lines of the orders")
        rate = returned / len(all_lines) if all_lines else 0.0
        if abs(self.return_rate.value - rate) > 1e-4:
            raise ValueError("return_rate must be returned_lines / lines (4 decimals)")
        return self


def _check_terms_source(
    terms: Terms, base_terms: Terms, source: str, review_at: date | None, shown_at: datetime
) -> None:
    """Terms shown at checkout (US-403, US-607): base terms are exactly the merchant base with no
    review date; terms less generous than the base come from a decision with a review date; and
    a decision's terms are shown only before its review date, when the restriction lapses."""
    if source == "base" and (terms != base_terms or review_at is not None):
        raise ValueError("terms from base are the merchant base terms, with no review date")
    if terms.to_policy().is_less_generous_than(base_terms.to_policy()) and (
        source == "base" or review_at is None
    ):
        raise ValueError("terms less generous than the base come from a decision with a review")
    if review_at is not None and review_at <= utc_day(shown_at):
        raise ValueError(
            "a restriction lapses at its review date: its terms are shown only before the "
            "review date (US-607)"
        )
    if review_at is not None and review_at > utc_day(shown_at) + timedelta(days=MAX_REVIEW_DAYS):
        raise ValueError(
            "a review date is at most MAX_REVIEW_DAYS days after the day the terms are shown"
        )


class TermsSnapshotView(ApiModel):
    """Terms shown at checkout and stored with the order (ADR-05). order_id is null while the
    snapshot is shown and not confirmed (POST /api/checkout/evaluate). base_terms are the
    merchant base terms when the snapshot was shown; review_at is the review date of the
    restriction in force that produced the terms, if any."""

    terms_snapshot_id: Id
    order_id: Id | None
    customer_id: Id
    source: Id  # "base" or the id of the decision whose levers produced the terms
    quadrant_at_purchase: Quadrant | None
    terms: Terms
    base_terms: Terms
    review_at: date | None
    shown_at: AwareDatetime
    confirmed_at: AwareDatetime | None

    @model_validator(mode="after")
    def _confirmed(self) -> Self:
        if (self.order_id is None) != (self.confirmed_at is None):
            raise ValueError("a snapshot gets its order id when it is confirmed, not before")
        if self.confirmed_at is not None and self.confirmed_at < self.shown_at:
            raise ValueError("confirmed_at is before shown_at")
        if (
            self.confirmed_at is not None
            and self.review_at is not None
            and utc_day(self.confirmed_at) >= self.review_at
        ):
            raise ValueError(
                "a restriction lapses at its review date: an order is bound to its terms only "
                "before the review date (/confirm answers 409, the client evaluates again)"
            )
        if (self.source == "base") != (self.quadrant_at_purchase is None):
            raise ValueError("base terms have no quadrant; terms from a decision have one")
        _check_terms_source(self.terms, self.base_terms, self.source, self.review_at, self.shown_at)
        return self


class DecisionThresholds(ApiModel):
    risk: float = Field(gt=0, le=1)
    value_eur: Eur

    def to_policy(self) -> PolicyThresholds:
        return PolicyThresholds(risk=self.risk, value_eur=_dec(self.value_eur))


class DecisionInputs(ApiModel):
    """The inputs given to policy.engine.decide, kept for audit (US-604) and re-runnable: the
    return request (with order_id and customer_id), the scores, the terms and the thresholds.
    provenance covers the plain numbers of the inputs (risk, value_eur, thresholds): the scores
    at decision time, stored with the version so an earlier one is served as it was (rule 11)."""

    order_id: Id
    customer_id: Id
    reason: ReturnReason
    delivered_on: date
    notice_on: date
    order_terms: Terms  # the order's snapshot
    base_terms: Terms  # the merchant base policy
    risk: Probability | None  # null on the guarantee path: never scored (rule 4)
    value_eur: Eur | None
    evidence_quality: EvidenceQuality
    cluster_id: Id | None
    thresholds: DecisionThresholds
    provenance: Provenance

    def scores(self) -> CaseScores:
        """The engine's CaseScores; on the guarantee path risk and value are not read."""
        return CaseScores(
            risk=self.risk if self.risk is not None else 0.0,
            value_eur=_dec(self.value_eur) if self.value_eur is not None else Decimal(0),
            evidence_quality=self.evidence_quality,
            cluster_id=self.cluster_id,
        )


class CouponRuleRef(ApiModel):
    """The application of the general coupon rule to the cluster of the decision (US-402): the
    cluster given to the engine and its linked accounts, the case customer among them. state and
    in_force are those of the application when the decision is served (round 4, E26): proposed
    with a proposed decision; confirmed by the person who acted, or reversed afterwards; in force
    while confirmed, before the review date and while the decision is in force."""

    rule_version: Version
    application_id: Id
    cluster_id: Id
    affected_customer_ids: list[Id] = Field(min_length=1)  # excluded accounts are visible
    confirmed_by: HumanActor | None
    state: CouponApplicationState
    in_force: bool


class ClaimView(ApiModel):
    """A diminished-value claim (art. 14(2)): only from confirmed observations and a rule. The
    refund the customer gets is the price of the returned line less the claim (round 4, E12)."""

    inspection_id: Id
    amount: Metric  # eur, withheld from the refund
    line_price: Metric  # eur: the price of the returned line, the base of the claim and refund
    photo_evidence_ids: list[Id] = Field(min_length=1)
    confirmed_by: HumanActor
    rule_id: Id

    @model_validator(mode="after")
    def _amount(self) -> Self:
        _same_unit(self.amount, self.line_price, unit="eur")
        if not 0 < self.amount.value <= self.line_price.value:
            raise ValueError("a claim amount is positive and at most the price of the line")
        return self

    def refund_eur(self) -> Decimal:
        """The refund left after the claim: the price of the line less the claim."""
        return _dec(float(self.line_price.value)) - _dec(float(self.amount.value))


class DecisionView(ApiModel):
    """A decision version: the engine's proposal and the human outcome (spec 8.6, US-303/306).

    levers are the levers of the current state: the proposal while proposed or approved, the
    operator's levers when overridden, none when rejected. requires_approval and review_at refer
    to them: a restriction needs a person and has a review date, and nothing else has one.

    in_force says whether the levers apply to future orders at as_of (the time the view was
    served): approved, overridden, or proposed without a need for approval; never once a later
    version supersedes this one; never from the review date on, counted in UTC days (US-607: a
    restriction lapses unless a person renews it).

    A version created by a review (trigger review) is policy.engine.decide() on the latest
    scores at the day of the review (inputs, decided_on, quadrant, proposed_levers), with the
    person's outcome: action lift overrides it to the standard levers; action renew approves the
    fresh restriction, with a new review date. Every later version (a review or a recompute) is
    decided on the UTC day it is created, and a restriction is approved only before its review
    date: a stale proposal is recomputed (409, round 4, E4).

    A cluster coupon block applies to the cluster given to the engine, and its linked accounts
    include the case customer (LEG-09, round 4, E2).

    Every view passes policy.legal_guard under to_policy_decision (ADR-15 section 8)."""

    decision_id: Id
    return_id: Id
    version: int = Field(ge=1)
    revision_trigger: RevisionTrigger
    supersedes: Id | None  # the previous version (version - 1), null for version 1
    superseded_by: Id | None  # the next version, once there is one: then this one is history
    created_at: AwareDatetime
    as_of: AwareDatetime  # when the view was served: in_force refers to it
    decided_on: date
    state: DecisionStatus
    path: Path
    evidence_quality: EvidenceQuality
    quadrant: Quadrant | None
    missing_evidence: list[ShortText]
    proposed_levers: LeverList
    levers: LeverList
    lever_ids: list[LeverIdText]
    requires_approval: bool
    in_force: bool
    current_return_terms: Terms  # terms of this return: the snapshot, never worse (LEG-04)
    future_terms: Terms  # base terms with `levers` applied
    refund_due_by: date
    review_at: date | None
    approver: HumanActor | None
    approved_at: AwareDatetime | None
    override_reason: Reason | None
    override_category: OverrideCategory | None
    coupon_rule: CouponRuleRef | None
    diminished_value_claim: ClaimView | None
    inputs: DecisionInputs
    rules_version: Version
    model_versions: dict[str, Version]
    assumptions_version: Version  # the stored assumption version of the alternatives
    generation_id: Id | None

    @model_validator(mode="after")
    def _invariants(self) -> Self:  # noqa: C901 - one rule per line, kept together on purpose
        base = self.inputs.base_terms.to_policy()
        _catalogue_order(self.proposed_levers, "proposed_levers")
        _catalogue_order(self.levers, "levers")
        if self.lever_ids != [x.lever_id() for x in self.levers]:
            raise ValueError("lever_ids are the id:value strings of levers, in order")
        policy_levers = to_policy_levers(self.levers, base)
        if self.future_terms.to_policy() != policy_levers.future_terms:
            raise ValueError("future_terms must be the base terms with the levers applied")
        _check_scoring(
            self.path, self.evidence_quality, self.quadrant, self.state, self.requires_approval
        )
        if self.path is Path.GUARANTEE:
            if self.proposed_levers:
                raise ValueError("guarantee: no lever applies (US-309)")
            if self.inputs.risk is not None or self.inputs.value_eur is not None:
                raise ValueError("guarantee: the return is never scored (rule 4)")
        elif self.inputs.risk is None or self.inputs.value_eur is None:
            raise ValueError("withdrawal: the engine needs risk and value")
        insufficient = self.evidence_quality is EvidenceQuality.INSUFFICIENT
        if insufficient and self.proposed_levers:
            raise ValueError("insufficient evidence: standard terms (US-307)")
        if insufficient != bool(self.missing_evidence):
            raise ValueError("missing_evidence is non-empty exactly when evidence is insufficient")
        engine_levers = self.state in ("proposed", "approved")
        needs_approval = is_restrictive(policy_levers, base) or (
            engine_levers and self.quadrant in APPROVAL_QUADRANTS
        )
        if self.requires_approval != needs_approval:
            raise ValueError("requires_approval: restrictive levers, or Q3 and Q4 (rule 5)")
        if (self.review_at is not None) != self.requires_approval:
            raise ValueError("a restriction has a review date (US-607), and nothing else has one")
        if self.review_at is not None and not (
            self.decided_on < self.review_at <= self.decided_on + timedelta(days=MAX_REVIEW_DAYS)
        ):
            raise ValueError("review_at must be after decided_on and within the maximum")
        if self.refund_due_by != self.inputs.notice_on + timedelta(days=LEGAL_REFUND_DAYS):
            raise ValueError("the refund is due 14 days after the notice (rule 3)")
        order_terms = self.inputs.order_terms.to_policy()
        expected_terms = (
            guarantee_terms(order_terms, base) if self.path is Path.GUARANTEE else order_terms
        )
        if self.current_return_terms.to_policy() != expected_terms:
            raise ValueError("current terms: the order snapshot, or the guarantee terms (LEG-04)")
        self._check_state()
        self._check_versions()
        blocks = any(x.id == "cluster_coupons_disabled" and x.value for x in self.levers)
        if blocks != (self.coupon_rule is not None):
            raise ValueError("a cluster coupon block carries its coupon rule application")
        self._check_coupon_rule()
        if self.diminished_value_claim is not None and self.path is Path.GUARANTEE:
            raise ValueError("no diminished-value claim on a defect")
        violations = legal_guard_check(*to_policy_decision(self), base)
        if violations:
            found = "; ".join(f"{v.code}: {v.message}" for v in violations)
            raise ValueError(f"policy.legal_guard refuses this decision: {found}")
        return self

    def _check_coupon_rule(self) -> None:
        """E2, E26: the block is about the cluster of the engine inputs and the case customer;
        its state and in_force are those of the application when the view is served."""
        rule, inputs = self.coupon_rule, self.inputs
        if rule is None:
            return
        if inputs.cluster_id is None or rule.cluster_id != inputs.cluster_id:
            raise ValueError(
                "a cluster coupon block applies to the cluster given to the engine (LEG-09)"
            )
        ids = rule.affected_customer_ids
        if inputs.customer_id not in ids or len(set(ids)) != len(ids):
            raise ValueError(
                "the linked accounts of a coupon block include the case customer, each once"
            )
        if (rule.state == "proposed") != (self.state == "proposed"):
            raise ValueError(
                "a coupon application is proposed with its decision, then confirmed by the person "
                "who acts and possibly reversed"
            )
        in_force = (
            rule.state == "confirmed"
            and self.in_force
            and self.review_at is not None
            and utc_day(self.as_of) < self.review_at
        )
        if rule.in_force != in_force:
            raise ValueError(
                "a coupon application is in force while confirmed, before its review date and "
                "while its decision is in force"
            )

    def _check_state(self) -> None:
        acted = self.state != "proposed"
        if acted != (self.approver is not None) or acted != (self.approved_at is not None):
            raise ValueError("approver and approved_at are set exactly when a person acted")
        if self.approved_at is not None and not (self.created_at <= self.approved_at <= self.as_of):
            raise ValueError("approved_at is between created_at and as_of")
        if (
            self.approved_at is not None
            and self.review_at is not None
            and utc_day(self.approved_at) >= self.review_at
        ):
            raise ValueError(
                "a restriction is approved before its review date: a stale proposal is recomputed "
                "(409, US-607)"
            )
        departs = self.state in ("overridden", "rejected")
        if departs != (self.override_reason is not None) or departs != (
            self.override_category is not None
        ):
            raise ValueError("override and reject carry a reason and a category, approve none")
        if departs and _blank(self.override_reason):
            raise ValueError("the reason must not be blank")
        if self.state in ("proposed", "approved") and self.levers != self.proposed_levers:
            raise ValueError("a proposed or approved decision keeps the proposed levers")
        if self.state == "rejected" and self.levers:
            raise ValueError("a rejected proposal leaves no lever in force")
        rule = self.coupon_rule
        if acted and rule is not None and rule.confirmed_by != self.approver:
            raise ValueError("the person who approves or overrides confirms the coupon rule")
        if self.state == "proposed" and rule is not None and rule.confirmed_by is not None:
            raise ValueError("a proposed coupon rule application is not confirmed yet")

    def _check_versions(self) -> None:
        first = self.version == 1
        if first != (self.supersedes is None) or first != (self.revision_trigger == "initial"):
            raise ValueError("version 1 is the initial version; later ones supersede another")
        if self.decision_id in (self.supersedes, self.superseded_by):
            raise ValueError("a version supersedes, and is superseded by, another version")
        lifted = self.state == "overridden" and not self.levers
        renewed = self.state == "approved" and self.requires_approval
        if self.revision_trigger == "review" and not (lifted or renewed):
            raise ValueError(
                "a version created by a review (trigger review) lifts the restriction "
                "(overridden to the standard levers) or renews it (an approved restriction)"
            )
        if self.as_of < self.created_at:
            raise ValueError("as_of is not before created_at")
        created_day = utc_day(self.created_at)
        if self.decided_on > created_day:
            raise ValueError("decided_on is no later than the UTC day of created_at")
        if self.revision_trigger != "initial" and self.decided_on != created_day:
            raise ValueError(
                "decided_on of a later version (a review or a recompute) is the UTC day of its "
                "created_at"
            )
        if self.revision_trigger == "review" and self.approved_at != self.created_at:
            raise ValueError(
                "a review version is created by the person's act: approved_at is created_at"
            )
        lapsed = self.review_at is not None and utc_day(self.as_of) >= self.review_at
        expected = (
            (self.state in ("approved", "overridden"))
            or (self.state == "proposed" and not self.requires_approval)
        ) and not (lapsed or self.superseded_by is not None)
        if self.in_force != expected:
            raise ValueError(
                "in_force: approved, overridden, or proposed without approval; not superseded; "
                "before the review date"
            )


_POLICY_STATES: dict[DecisionStatus, DecisionState] = {
    "proposed": DecisionState.PROPOSED,
    "approved": DecisionState.APPROVED,
    "overridden": DecisionState.APPROVED,  # a person took it, on the operator's levers
    "rejected": DecisionState.REJECTED,
}


def proposal_audit_state(
    view: DecisionView, coupon_application_id: str | None
) -> DecisionAuditState:
    """The audit state of the engine's proposal of a version, before a person acted on it
    (approve, override and reject record it as before, finding D6): the version's quadrant,
    risk and proposed levers, with the approval flag and the review date the engine gave them
    (policy.engine: review_on is decided_on + REVIEW_DAYS for a restriction)."""
    base = view.inputs.base_terms.to_policy()
    levers = to_policy_levers(view.proposed_levers, base)
    needs = is_restrictive(levers, base) or view.quadrant in APPROVAL_QUADRANTS
    risk = view.inputs.risk
    return DecisionAuditState(
        decision_id=view.decision_id,
        version=view.version,
        state="proposed",
        quadrant=view.quadrant,
        risk=(
            Metric(value=risk, unit="probability", provenance=view.inputs.provenance)
            if risk is not None
            else None
        ),
        lever_ids=[x.lever_id() for x in view.proposed_levers],
        requires_approval=needs,
        review_at=view.decided_on + timedelta(days=REVIEW_DAYS) if needs else None,
        approver=None,
        override_category=None,
        coupon_application_id=coupon_application_id,
    )


def to_policy_decision(view: DecisionView) -> tuple[Decision, ReturnRequest]:
    """The policy Decision and ReturnRequest a DecisionView stands for, to run
    policy.legal_guard.check on it (ADR-15 section 8). The exact mapping:

    - state: proposed and approved stay; overridden becomes APPROVED (a person took it);
      rejected stays REJECTED;
    - quadrant: the engine's quadrant while its levers stand (proposed, approved); None once a
      person replaced them (overridden, rejected), so the guard judges the levers chosen;
    - levers, requires_approval, review_on, approver: those of the current state;
    - coupon_rule: as shown; when approved or overridden the approver has confirmed it;
    - the request: the engine inputs, with the order snapshot."""
    inputs = view.inputs
    claim = view.diminished_value_claim
    rule = view.coupon_rule
    decision = Decision(
        return_id=view.return_id,
        decided_on=view.decided_on,
        path=view.path,
        evidence_quality=view.evidence_quality,
        quadrant=view.quadrant if view.state in ("proposed", "approved") else None,
        levers=to_policy_levers(view.levers, inputs.base_terms.to_policy()),
        current_return_terms=view.current_return_terms.to_policy(),
        refund_due_by=view.refund_due_by,
        requires_approval=view.requires_approval,
        state=_POLICY_STATES[view.state],
        approver=view.approver,
        review_on=view.review_at,
        diminished_value_claim=(
            DiminishedValueClaim(
                amount_eur=_dec(float(claim.amount.value)),
                photo_evidence_ids=tuple(claim.photo_evidence_ids),
                confirmed_by=claim.confirmed_by,
                rule_id=claim.rule_id,
            )
            if claim is not None
            else None
        ),
        coupon_rule=(
            PolicyCouponRule(
                rule_version=rule.rule_version,
                confirmed_by=rule.confirmed_by,
                cluster_id=rule.cluster_id,
            )
            if rule is not None
            else None
        ),
        rules_version=view.rules_version,
    )
    request = ReturnRequest(
        return_id=view.return_id,
        order_id=inputs.order_id,
        customer_id=inputs.customer_id,
        reason=inputs.reason,
        delivered_on=inputs.delivered_on,
        notice_on=inputs.notice_on,
        order_terms=inputs.order_terms.to_policy(),
    )
    return decision, request


class DecisionAuditState(ApiModel):
    """The before and after of an audit entry about a decision version (entity_type decision):
    what changed, with the id that serves the whole version (GET /api/decisions/{decision_id})."""

    decision_id: Id
    version: int = Field(ge=1)
    state: DecisionStatus
    quadrant: Quadrant | None
    risk: Metric | None  # probability: the risk given to the engine, with its provenance
    lever_ids: list[LeverIdText]
    requires_approval: bool
    review_at: date | None
    approver: HumanActor | None
    override_category: OverrideCategory | None
    coupon_application_id: Id | None

    @model_validator(mode="after")
    def _risk(self) -> Self:
        _same_unit(self.risk, unit="probability")
        if (self.review_at is not None) != self.requires_approval:
            raise ValueError("a restriction, and only a restriction, has a review date (US-607)")
        if (self.state != "proposed") != (self.approver is not None):
            raise ValueError("a person acted exactly when the state is not proposed (rule 5)")
        departs = self.state in ("overridden", "rejected")
        if departs != (self.override_category is not None):
            raise ValueError("an override and a rejection, and only they, have a category")
        if self.state == "rejected" and self.lever_ids:
            raise ValueError("a rejected proposal leaves no lever in force")
        blocks = "cluster_coupons_disabled:true" in self.lever_ids
        if blocks != (self.coupon_application_id is not None):
            raise ValueError(
                "a cluster coupon block names its coupon application, and only a block does"
            )
        return self

    @classmethod
    def from_view(cls, view: DecisionView) -> DecisionAuditState:
        risk = view.inputs.risk
        return cls(
            decision_id=view.decision_id,
            version=view.version,
            state=view.state,
            quadrant=view.quadrant,
            risk=(
                Metric(value=risk, unit="probability", provenance=view.inputs.provenance)
                if risk is not None
                else None
            ),
            lever_ids=view.lever_ids,
            requires_approval=view.requires_approval,
            review_at=view.review_at,
            approver=view.approver,
            override_category=view.override_category,
            coupon_application_id=view.coupon_rule.application_id if view.coupon_rule else None,
        )


AlternativeKind = Literal[
    "proposal", "current_terms", "size_exchange", "verification", "product_report"
]


class Alternative(ApiModel):
    """An admissible action with its net benefit from the simulator (US-308)."""

    kind: AlternativeKind
    summary: ShortText
    recommended: bool
    levers: LeverList
    net_benefit: NetBenefitView

    @model_validator(mode="after")
    def _levers(self) -> Self:
        _catalogue_order(self.levers, "levers")
        return self


def alternative_label(decision_id: str, kind: AlternativeKind) -> str:
    """The label of the assumption set of an alternative: one label, one set of values."""
    return f"{decision_id}/{kind}"


def _check_alternatives(alternatives: Sequence[Alternative], decision: DecisionView) -> None:
    """US-308: distinct kinds, exactly one recommended, carrying the engine's proposal. US-602:
    every net benefit is labelled <decision_id>/<kind> and uses the unit costs and the basis of
    the decision's stored assumption version."""
    kinds = [a.kind for a in alternatives]
    if len(set(kinds)) != len(kinds) or sum(a.recommended for a in alternatives) != 1:
        raise ValueError("alternatives have distinct kinds and exactly one recommended")
    recommended = next(a for a in alternatives if a.recommended)
    if recommended.levers != decision.proposed_levers:
        raise ValueError("the recommended alternative carries the engine's proposed levers")
    for a in alternatives:
        label = alternative_label(decision.decision_id, a.kind)
        if a.net_benefit.assumptions.version != label:
            raise ValueError(f"the assumptions of the {a.kind} alternative are labelled {label}")
    sets = [a.net_benefit.assumptions for a in alternatives]
    _same_unit_costs(sets, decision.assumptions_version, "alternatives")


class CitedFact(ApiModel):
    """A fact of the llm_case, built by code from the engine inputs (llm/case.py, ADR-14) and
    stored with the draft as it was at generation time: the UI shows its entity, metric, value,
    period and nature next to the statement that cites it (US-305)."""

    id: FactId
    entity: FactEntity
    metric: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    value: StrictInt | StrictFloat
    period: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    nature: Nature


class ExplanationStatement(ApiModel):
    """A statement of the operator explanation (US-305, ADR-14): its label (observed data, a
    score contribution or a hypothesis to verify), the ids of the code-built facts it cites, its
    text, and the cited facts themselves, in the order of fact_ids. Every number of the text is
    the value of a cited fact: llm.validator checks it on every draft (_check_draft)."""

    label: StatementLabel
    fact_ids: list[FactId] = Field(max_length=MAX_FACTS_PER_STATEMENT)
    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_STATEMENT_CHARS)]
    facts: list[CitedFact] = Field(max_length=MAX_FACTS_PER_STATEMENT)

    @model_validator(mode="after")
    def _cited(self) -> Self:
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("a statement cites a fact once")
        if [f.id for f in self.facts] != self.fact_ids:
            raise ValueError("facts are the facts the statement cites, in the order of fact_ids")
        return self


class TemplateFields(ApiModel):
    """The values code fills into the placeholders of one language (ADR-08, rule 10):
    llm.messages.template_fields of the decision. A placeholder the decision does not have
    ({amount} without a known refund, {terms} when the future terms do not change, {deduction}
    without a diminished-value claim) is null. {deadline} carries its article in Italian ("il
    12 ottobre 2026", "l'11 ottobre 2026")."""

    terms: ShortText | None
    amount: ShortText | None
    deadline: ShortText | None
    deduction: ShortText | None  # the amount withheld for a diminished value (round 4, E17)

    @classmethod
    def of(
        cls, decision: Decision, base: ReturnTerms, lang: Lang, refund_eur: Decimal | None = None
    ) -> TemplateFields:
        values = template_fields(decision, base, lang, refund_eur)
        return cls(
            terms=values.get("terms"),
            amount=values.get("amount"),
            deadline=values.get("deadline"),
            deduction=values.get("deduction"),
        )

    def filled(self) -> list[str]:
        values = (self.terms, self.amount, self.deadline, self.deduction)
        return [x for x in values if x is not None]


MessageText = Annotated[str, StringConstraints(min_length=1, max_length=4000)]


def _check_selection_shape(ids: Sequence[str]) -> None:
    """The slots of a phrasebook selection, whatever the decision: one opening, up to three
    extras, one closing, each id once (llm/phrasebook.py; selection_problems checks the rest
    against the decision)."""
    if len(set(ids)) != len(ids):
        raise ValueError("a phrasebook sentence is used once")
    slots = [PHRASEBOOK[i].slot for i in ids]
    if slots != ["opening", *["extra"] * (len(ids) - 2), "closing"]:
        raise ValueError("a selection is one opening, up to three extras and one closing")


def _check_rendered(
    ids: Sequence[str], fields: TemplateFields, kind: ClauseKind | None, lang: Lang, text: str
) -> None:
    """What a rendered message shows without its decision: the chosen sentences in order, the
    field values, and exactly the fixed legal clause at the end (llm.legal_clause)."""
    try:
        assert_clause(text, kind, lang)
    except LegalClauseError as exc:
        raise ValueError(str(exc)) from None
    position = 0
    for sentence_id in ids:
        found = text.find(PHRASEBOOK[sentence_id].text(lang), position)
        if found < 0:
            raise ValueError(f"the message shows the {lang} text of {sentence_id}, in order")
        position = found
    if any(value not in text for value in fields.filled()):
        raise ValueError("the message shows its template fields")


def check_rendering(
    ids: Sequence[str],
    fields: TemplateFields,
    kind: ClauseKind | None,
    lang: Lang,
    text: str,
    decision: Decision,
    base: ReturnTerms,
    refund_eur: Decimal | None = None,
) -> None:
    """A customer message is llm.phrasebook.render_selection of its sentence ids for the
    decision: ids allowed for it, the fields of llm.messages.template_fields, the clause of
    llm.legal_clause.required_clause and exactly the composed text (rules 1, 10, ADR-14)."""
    _, expected = serialize_decision(decision, base, refund_eur)
    problems = selection_problems(ids, context_of(expected))
    if problems:
        raise ValueError(f"phrasebook ids not allowed for the decision: {'; '.join(problems)}")
    if fields != TemplateFields.of(decision, base, lang, refund_eur):
        raise ValueError("the template fields are llm.messages.template_fields of the decision")
    if kind != llm_required_clause(decision, base):
        raise ValueError("the message carries the legal clause the decision requires")
    if text != render_selection(ids, decision, base, lang, refund_eur):
        raise ValueError("the text is llm.phrasebook.render_selection of the ids")


def default_selection_for(
    decision: Decision,
    base: ReturnTerms,
    preferred: Sequence[str] | None = None,
    refund_eur: Decimal | None = None,
) -> list[str]:
    """The phrasebook ids of a message about the decision: `preferred` (the draft's selection)
    when it is allowed for this decision, otherwise the fallback selection of llm.templates."""
    _, expected = serialize_decision(decision, base, refund_eur)
    ctx = context_of(expected)
    if preferred is not None and not selection_problems(preferred, ctx):
        return list(preferred)
    return default_selection(ctx)


class RenderedMessage(ApiModel):
    """The message of one language: the template fields and the text code composed."""

    lang: Lang
    fields: TemplateFields
    text: MessageText


class CustomerMessageView(ApiModel):
    """The customer message of a draft (ADR-14 decision 8): the phrasebook ids the model chose,
    whether the fixed legal clause is appended (and which), and for each language the template
    fields and the text llm.phrasebook.render_selection composed: opening, the mandatory body
    written by code from the decision, the extras, the closing, then the clause."""

    sentence_ids: list[SentenceId] = Field(min_length=MIN_SENTENCES, max_length=MAX_SENTENCES)
    legal_clause_kind: ClauseKind | None
    texts: list[RenderedMessage] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _render(self) -> Self:
        _check_selection_shape(self.sentence_ids)
        langs = [t.lang for t in self.texts]
        if len(set(langs)) != len(langs):
            raise ValueError("one text per language")
        for t in self.texts:
            _check_rendered(self.sentence_ids, t.fields, self.legal_clause_kind, t.lang, t.text)
        return self


class DecisionEchoView(ApiModel):
    path: Path
    quadrant: Quadrant | None
    lever_ids: list[LeverIdText] = Field(max_length=MAX_LEVER_IDS)


def check_generation_path(
    path: GenerationPath,
    fallback: FallbackReason | None,
    calls: int,
    codes: Sequence[ValidatorCode],
) -> None:
    """The paths of llm.generate (spec 8.7): model, validated on the first call; regenerated,
    validated on the second call after validator errors; template, the fixed fallback, with
    its reason (the model was unavailable, echoed another decision or failed twice)."""
    if len(set(codes)) != len(codes):
        raise ValueError("each validator code is listed once")
    if (path == "template") != (fallback is not None):
        raise ValueError("a template output, and only it, has a fallback reason")
    if path == "model" and (calls != 1 or codes):
        raise ValueError("model: validated on the first call")
    if path == "regenerated" and (calls != MAX_MODEL_CALLS or not codes):
        raise ValueError("regenerated: validated on the second call, after validator errors")
    if fallback == "unavailable" and bool(codes) != (calls > 0):
        raise ValueError("unavailable: the codes of the calls made before the model failed")
    if fallback == "decision_echo" and (calls == 0 or "DECISION_ECHO" not in codes):
        raise ValueError("decision_echo: a call answered with another decision")
    if fallback == "invalid_twice" and (calls != MAX_MODEL_CALLS or not codes):
        raise ValueError("invalid_twice: both calls failed the validator")


class GenerationInfo(ApiModel):
    """How an LLM output was produced (spec 8.7, US-901 cached label): the path of llm.generate,
    the model calls, the codes of llm.validator found on the way and, for the template, why."""

    generation_id: Id
    generation_path: GenerationPath
    fallback_reason: FallbackReason | None
    llm_mode: LlmMode
    served_from: ServedFrom
    model: ShortText  # the served model id, "mock" in LLM_MODE=mock
    model_calls: int = Field(ge=0, le=MAX_MODEL_CALLS)
    validator_codes: list[ValidatorCode]
    created_at: AwareDatetime
    latency_ms: int | None = Field(ge=0)

    @model_validator(mode="after")
    def _path(self) -> Self:
        check_generation_path(
            self.generation_path, self.fallback_reason, self.model_calls, self.validator_codes
        )
        if self.llm_mode == "mock" and self.served_from == "cached":
            raise ValueError("cached outputs were produced by vLLM on the MI300X, not by the mock")
        return self

    @classmethod
    def from_generation(
        cls,
        result: Generation,
        *,
        generation_id: str,
        llm_mode: LlmMode,
        served_from: ServedFrom,
        model: str,
        created_at: datetime,
        latency_ms: int | None,
    ) -> GenerationInfo:
        """The record of an llm.generate.generate result."""
        codes = list(dict.fromkeys(issue.code.value for issue in result.issues))
        return cls.model_validate(
            {
                "generation_id": generation_id,
                "generation_path": result.path.value,
                "fallback_reason": result.fallback_reason,
                "llm_mode": llm_mode,
                "served_from": served_from,
                "model": model,
                "model_calls": result.model_calls,
                "validator_codes": codes,
                "created_at": created_at,
                "latency_ms": latency_ms,
            }
        )


MAX_CASE_FACTS = 20  # maxItems of facts in contracts/json/llm_case.schema.json


class DraftView(ApiModel):
    """The draft of a decision version (US-304, US-305, ADR-14): the output of llm.generate for
    the engine's decision of the version (path, quadrant, proposed levers), validated by
    llm.validator, stored with the facts of the llm_case it answered (`case_facts`, round 4,
    E10 and E11: the models validate the stored draft against exactly the facts llm.generate
    used), the facts each statement cites and the messages composed by llm.phrasebook.
    Generated at intake, or again on request (regenerate)."""

    generation: GenerationInfo
    decision_echo: DecisionEchoView
    case_facts: list[CitedFact] = Field(min_length=1, max_length=MAX_CASE_FACTS)
    explanation: list[ExplanationStatement] = Field(min_length=1, max_length=MAX_STATEMENTS)
    message: CustomerMessageView
    provenance: Provenance  # the engine inputs the facts were built from (rule 11)

    @model_validator(mode="after")
    def _cited(self) -> Self:
        facts = {fact.id: fact for fact in self.case_facts}
        for statement in self.explanation:
            if any(facts.get(fact.id) != fact for fact in statement.facts):
                raise ValueError("a statement cites facts of the llm_case, as stored with it")
        return self

    def llm_output(self) -> dict[str, Any]:
        """The output as the model gave it (contracts/json/llm_output.schema.json)."""
        echo = self.decision_echo
        return {
            "decision_echo": {
                "path": echo.path.value,
                "quadrant": echo.quadrant.value if echo.quadrant else None,
                "lever_ids": list(echo.lever_ids),
            },
            "operator_explanation": [
                {"label": x.label, "fact_ids": list(x.fact_ids), "text": x.text}
                for x in self.explanation
            ],
            "customer_message": {"sentence_ids": list(self.message.sentence_ids)},
        }

    @classmethod
    def build(
        cls,
        generation: GenerationInfo,
        output: LlmOutput,
        case: Mapping[str, Any],
        decision: Decision,
        base: ReturnTerms,
        provenance: Provenance,
    ) -> DraftView:
        """The draft of a validated output for the llm_case it answers (llm.case.build_case of
        `decision`): the facts of the case and the facts each statement cites, the messages in
        the case languages, and the provenance of the engine inputs the facts were built
        from."""
        facts = {fact["id"]: fact for fact in case["facts"]}
        ids = list(output.customer_message.sentence_ids)
        texts = [
            RenderedMessage(
                lang=lang,
                fields=TemplateFields.of(decision, base, lang),
                text=render_selection(ids, decision, base, lang),
            )
            for lang in case["languages"]
        ]
        return cls.model_validate(
            {
                "generation": generation,
                "decision_echo": output.decision_echo.model_dump(),
                "case_facts": list(case["facts"]),
                "explanation": [
                    {
                        "label": x.label,
                        "fact_ids": x.fact_ids,
                        "text": x.text,
                        "facts": [facts[i] for i in x.fact_ids],
                    }
                    for x in output.operator_explanation
                ],
                "message": {
                    "sentence_ids": ids,
                    "legal_clause_kind": llm_required_clause(decision, base),
                    "texts": texts,
                },
                "provenance": provenance,
            }
        )


AnomalyStatus = Literal["open", "task_opened", "resolved"]


class WeeklyReturnRate(ApiModel):
    week: Week
    sales: int = Field(ge=0)
    returns: int = Field(ge=0)
    return_rate: Probability

    @model_validator(mode="after")
    def _rate(self) -> Self:
        rate = self.returns / self.sales if self.sales else 0.0
        if self.returns > self.sales or abs(self.return_rate - rate) > 1e-4:
            raise ValueError("return_rate is returns / sales (4 decimals)")
        return self


class ProductAnomaly(ApiModel):
    """Returns of a variant for one reason in one week above the category baseline (spec 8.9)."""

    anomaly_id: Id
    variant_id: Id
    product_id: Id
    category: ShortText
    brand: ShortText | None
    reason: ReturnReason
    week: Week
    observed: Metric  # count
    expected: Metric  # expected_count, from the category baseline
    z: Metric  # z_score: (observed - expected) / sqrt(expected)
    sales: Metric  # count, sales of the variant in the week
    status: AnomalyStatus
    catalog_task_id: Id | None
    return_ids: list[Id]
    trend: list[WeeklyReturnRate]  # the variant's weekly returns for this reason

    @model_validator(mode="after")
    def _anomaly(self) -> Self:
        if self.anomaly_id != anomaly_id(self.variant_id, self.reason, self.week):
            raise ValueError("anomaly_id is variant_id~reason~week")
        _same_unit(self.observed, self.sales, unit="count")
        _same_unit(self.expected, unit="expected_count")
        _same_unit(self.z, unit="z_score")
        e = float(self.expected.value)
        if e <= 0 or abs(self.z.value - (self.observed.value - e) / math.sqrt(e)) > 0.01:
            raise ValueError("z must be (observed - expected) / sqrt(expected), 2 decimals")
        if (self.status == "open") != (self.catalog_task_id is None):
            raise ValueError("an open anomaly has no task; task_opened and resolved have one")
        return self


class RevisionSummary(ApiModel):
    """A version of the case's decision (F7: the log keeps every version); GET
    /api/decisions/{decision_id} serves the whole version (US-604)."""

    decision_id: Id
    version: int = Field(ge=1)
    trigger: RevisionTrigger
    created_at: AwareDatetime
    quadrant: Quadrant | None
    risk: Metric | None  # probability given to the engine; null when not scored
    lever_ids: list[LeverIdText]
    state: DecisionStatus

    @model_validator(mode="after")
    def _risk(self) -> Self:
        _same_unit(self.risk, unit="probability")
        return self


def proposal_decision(view: DecisionView) -> Decision:
    """The policy Decision of the engine's proposal of a version, which its draft explains:
    path, evidence quality, quadrant and proposed levers, as policy.engine.decide gave them
    (whatever a person did with them afterwards)."""
    decision, _ = to_policy_decision(view)
    base = view.inputs.base_terms.to_policy()
    levers = to_policy_levers(view.proposed_levers, base)
    needs = is_restrictive(levers, base) or view.quadrant in APPROVAL_QUADRANTS
    return replace(
        decision,
        quadrant=view.quadrant,
        levers=levers,
        requires_approval=needs,
        state=DecisionState.PROPOSED,
        approver=None,
        review_on=view.decided_on + timedelta(days=REVIEW_DAYS) if needs else None,
        coupon_rule=None,
        diminished_value_claim=None,
    )


def _fact_number(value: float | int) -> Decimal:
    """A number as llm.case writes it in a fact: rounded half-up to 2 decimals."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# The facts llm.case.build_case writes in this order: risk, value, the score factors, the cluster,
# the days since delivery. A factor named like the cluster or the days fact takes its place (the
# first fact of a metric is kept). The levers are not facts (ADR-14, round 3, finding T7): a
# window lever is never a fact, nor a score factor.
_FACT_RANKS = {"risk_score": 0, "value_eur_12m": 1, "days_since_delivery": 3}
_NEVER_FACTORS = frozenset({"risk_score", "value_eur_12m", "return_window_days"})


def _scored(view: DecisionView) -> bool:
    return view.path is Path.WITHDRAWAL and view.evidence_quality is EvidenceQuality.SUFFICIENT


def _check_case_facts(draft: DraftView, view: DecisionView) -> None:  # noqa: C901 - one per rule
    """The facts of the llm_case stored with a draft are the facts llm.case.build_case writes for
    the engine's decision of its version (round 4, E3 and E10): ids F1, F2... in order, one fact
    per metric, the nature of the engine inputs, and each metric with the entity, the period
    and, where the version holds it, the value build_case gives it ("as of {decided_on}", "12
    months to {decided_on}", "{delivered_on}/{notice_on}"); a lever is never a fact. A case
    that is not scored has only the days since delivery (rule 4, US-307); the cluster is a fact
    only of a customer in a cluster; a score factor is a feature that may be shown and never
    customer value (rule 6). The values of the factors and of the cluster size are checked
    where the response shows them (CaseCard, RecomputeResponse)."""
    inputs, facts = view.inputs, draft.case_facts
    if draft.provenance != inputs.provenance:
        raise ValueError("the facts of a draft are built from the engine inputs of its version")
    if [f.id for f in facts] != [f"F{i}" for i in range(1, len(facts) + 1)]:
        raise ValueError("the facts of the llm_case are F1, F2... in order")
    metrics = [f.metric for f in facts]
    if len(set(metrics)) != len(metrics):
        raise ValueError("the llm_case has one fact per metric")
    if any(f.nature != inputs.provenance.nature for f in facts):
        raise ValueError("a fact has the nature of the engine inputs (rule 11)")
    day = view.decided_on.isoformat()
    as_of = f"as of {day}"
    scored = _scored(view)
    clustered = scored and inputs.cluster_id is not None
    delivered = f"{inputs.delivered_on.isoformat()}/{inputs.notice_on.isoformat()}"
    fixed: dict[str, tuple[str, str, float | int | None]] = {
        "days_since_delivery": ("return", delivered, (inputs.notice_on - inputs.delivered_on).days)
    }
    if scored:
        fixed["risk_score"] = ("customer", as_of, inputs.risk)
        fixed["value_eur_12m"] = ("customer", f"12 months to {day}", inputs.value_eur)
    if clustered:
        fixed["cluster_size"] = ("cluster", as_of, None)
    ranks: list[int] = []
    factors = 0
    for fact in facts:
        expected = fixed.get(fact.metric)
        if expected is not None and (fact.entity, fact.period) == expected[:2]:
            value = expected[2]
            if value is not None and _fact_number(value) != Decimal(str(fact.value)):
                raise ValueError(f"fact {fact.id} ({fact.metric}) is not the value of the version")
            ranks.append(_FACT_RANKS.get(fact.metric, 2 if fact.metric == "cluster_size" else 4))
            continue
        if not scored:
            raise ValueError(
                f"fact {fact.metric}: a case on the guarantee path or with insufficient evidence "
                "is not scored (rule 4, US-307)"
            )
        if fact.metric in _NEVER_FACTORS:
            raise ValueError(f"fact {fact.id} ({fact.metric}) is not written as llm.case writes it")
        if is_forbidden_feature(fact.metric) or fact.metric in VALUE_FEATURES:
            raise ValueError(f"{fact.metric} is never a score factor (rule 6)")
        if (fact.entity, fact.period) != (factor_entity(fact.metric, clustered), as_of):
            raise ValueError(
                f"fact {fact.id} ({fact.metric}): a score factor is a fact of the customer, or of "
                "its cluster for a cluster feature, as of the decision day"
            )
        factors += 1
        ranks.append(2)
    if ranks != sorted(ranks) or factors > MAX_FACTORS:
        raise ValueError("the facts are in the order of llm.case, with at most three factors")
    if not set(fixed) <= set(metrics):
        raise ValueError(f"the llm_case of the version has the facts {sorted(fixed)}")
    if not scored and any(s.label == "SCORE" for s in draft.explanation):
        raise ValueError("a case that is not scored has no SCORE statement (rule 4, US-307)")


def expected_case_facts(
    view: DecisionView, factors: Sequence[ScoreContribution], cluster_size: int | None
) -> list[dict[str, Any]]:
    """The facts llm.case.build_case writes for the engine's decision of a version, with the
    score factors and the cluster size the response shows with it (as the API builds the case of
    a draft: each factor with factor_entity, the cluster of the engine inputs)."""
    inputs = view.inputs
    scored = _scored(view)
    clustered = scored and inputs.cluster_id is not None
    score_factors: list[ScoreFactor] = []
    for factor in factors if scored else ():
        value = factor.feature_value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"the score factor {factor.feature} of a draft is a number")
        entity = factor_entity(factor.feature, clustered)
        score_factors.append(ScoreFactor(factor.feature, value, factor.phrase, entity))
    cluster = None
    if clustered:
        if inputs.cluster_id is None or cluster_size is None:
            raise ValueError("a customer in a cluster is shown with the size of the cluster")
        cluster = ClusterSummary(inputs.cluster_id, cluster_size, ())
    request = ReturnRequest(
        return_id=view.return_id,
        order_id=inputs.order_id,
        customer_id=inputs.customer_id,
        reason=inputs.reason,
        delivered_on=inputs.delivered_on,
        notice_on=inputs.notice_on,
        order_terms=inputs.order_terms.to_policy(),
    )
    try:
        case = build_case(
            proposal_decision(view),
            request,
            inputs.base_terms.to_policy(),
            policy_text="",
            scores=inputs.scores() if scored else None,
            factors=score_factors,
            cluster=cluster,
            missing_evidence=view.missing_evidence,
            nature=inputs.provenance.nature,
        )
    except CaseError as exc:
        raise ValueError(f"llm.case.build_case refuses the version: {exc}") from None
    facts: list[dict[str, Any]] = case["facts"]
    return facts


def _check_draft(
    draft: DraftView,
    view: DecisionView,
    shown: tuple[Sequence[ScoreContribution], int | None] | None = None,
) -> None:
    """A draft is the validated output of llm/ for the engine's decision of its version (rule 1):
    its case facts are those llm.case.build_case writes (equal to a rebuild when the response
    shows the score factors and the cluster size: `shown`), llm.validator accepts it against
    the same case llm.generate validated (echo, labels, grounding of every number, banned
    terms, script, phrasebook ids: one verdict, round 4, E11), and each message is
    llm.phrasebook.render_selection of its ids, with the fields and the clause of the
    decision."""
    _check_case_facts(draft, view)
    if shown is not None:
        rebuilt = expected_case_facts(view, *shown)
        if [fact.model_dump() for fact in draft.case_facts] != rebuilt:
            raise ValueError(
                "the facts of the draft are the facts llm.case.build_case writes for the version, "
                "its score factors and its cluster as shown"
            )
    decision = proposal_decision(view)
    base = view.inputs.base_terms.to_policy()
    levers, expected = serialize_decision(decision, base)
    case: dict[str, Any] = {
        "case_id": view.return_id,
        "path": expected.path,
        "evidence_quality": expected.evidence_quality,
        "quadrant": expected.quadrant,
        "levers": levers,
        "placeholders": {
            "available": sorted(expected.placeholders),
            "required": ["terms"] if expected.requires_terms else [],
        },
        "facts": [fact.model_dump() for fact in draft.case_facts],
    }
    if _scored(view) and view.inputs.cluster_id is not None:
        case["cluster"] = {"id": view.inputs.cluster_id}  # an identifier, never a value
    result = validate(draft.llm_output(), expected, case)
    if result.errors:
        found = "; ".join(f"{e.code.value} {e.where}: {e.detail}" for e in result.errors)
        raise ValueError(f"the draft fails llm.validator: {found}")
    message = draft.message
    for t in message.texts:
        check_rendering(
            message.sentence_ids, t.fields, message.legal_clause_kind, t.lang, t.text,
            decision, base,
        )  # fmt: skip


def check_outbox_message(message: OutboxMessage, view: DecisionView) -> None:
    """An outbox message about a decision version: its return and customer, and the text
    llm.phrasebook.render_selection composes for the decision as it stands (the levers a person
    approved or chose). It states a refund exactly when a diminished-value claim leaves one: the
    price of the line less the claim, with the provenance of the claim (round 4, E12)."""
    if (message.decision_id, message.return_id, message.customer_id) != (
        view.decision_id,
        view.return_id,
        view.inputs.customer_id,
    ):
        raise ValueError("the message is about this decision, its return and its customer")
    claim, refund = view.diminished_value_claim, message.refund
    if (refund is None) != (claim is None):
        raise ValueError("a message states a refund exactly when a diminished-value claim sets one")
    if (
        refund is not None
        and claim is not None
        and (
            refund.provenance != claim.amount.provenance or _dec(refund.value) != claim.refund_eur()
        )
    ):
        raise ValueError(
            "the refund stated is the price of the line less the claim, with its provenance"
        )
    decision, _ = to_policy_decision(view)
    stated = _dec(float(refund.value)) if refund is not None else None
    check_rendering(
        message.sentence_ids, message.fields, message.legal_clause_kind, message.lang,
        message.body, decision, view.inputs.base_terms.to_policy(), stated,
    )  # fmt: skip


class CaseCard(ApiModel):
    """GET /api/returns/{return_id}: everything Sara needs to decide one return (US-302).

    The decision is the latest version of the case. provenance covers the plain numbers of item,
    request and product_anomaly.trend (weekly sales and returns of the shop); every other section
    carries its own provenance or Metrics."""

    return_id: Id
    order_id: Id
    customer_id: Id
    display_name: ShortText | None
    as_of: AwareDatetime
    item: ReturnedItem
    request: ReturnRequestView
    path: Path
    evidence: EvidenceView
    risk: RiskView | None  # null on the guarantee path or with insufficient evidence
    value: ValueView | None
    linked_accounts: LinkedAccountsView | None  # null when the customer is in no cluster
    orders: OrderHistory
    order_terms: TermsSnapshotView  # the snapshot of the order of this return (ADR-05)
    decision: DecisionView
    alternatives: list[Alternative] = Field(min_length=1, max_length=3)
    draft_status: DraftStatus
    draft: DraftView | None
    product_anomaly: ProductAnomaly | None  # an anomaly on the returned variant (US-606)
    revisions: list[RevisionSummary] = Field(min_length=1)
    provenance: Provenance  # covers the plain numbers of item and request

    @model_validator(mode="after")
    def _consistent(self) -> Self:  # noqa: C901 - cross-section rules, kept together
        d, inputs = self.decision, self.decision.inputs
        if d.return_id != self.return_id or d.path is not self.path:
            raise ValueError("the decision belongs to this return and has its path")
        if (inputs.order_id, inputs.customer_id) != (self.order_id, self.customer_id):
            raise ValueError("the decision inputs name the order and the customer of the return")
        if d.superseded_by is not None or d.as_of != self.as_of:
            raise ValueError("the card shows the latest version, as of the card")
        if (inputs.reason, inputs.delivered_on, inputs.notice_on) != (
            self.request.reason,
            self.request.delivered_on,
            self.request.notice_on,
        ):
            raise ValueError("decision inputs must match the return request")
        snapshot = self.order_terms
        if snapshot.order_id != self.order_id or snapshot.terms != inputs.order_terms:
            raise ValueError("order_terms is the snapshot of this order, given to the engine")
        if self.evidence.quality is not d.evidence_quality:
            raise ValueError("evidence quality of the card and of the decision differ")
        if self.evidence.missing_evidence != d.missing_evidence:
            raise ValueError("missing evidence of the card and of the decision differ")
        scored = self.path is Path.WITHDRAWAL and d.evidence_quality is EvidenceQuality.SUFFICIENT
        if (self.risk is not None) != scored:
            raise ValueError(_RISK_SHOWN)
        if self.risk is not None and (
            self.risk.score.value != inputs.risk
            or self.risk.threshold.value != inputs.thresholds.risk
        ):
            raise ValueError("the risk shown is the risk given to the engine")
        if (
            self.value is not None
            and inputs.value_eur is not None
            and (
                self.value.value_eur.value != inputs.value_eur
                or self.value.threshold.value != inputs.thresholds.value_eur
            )
        ):
            raise ValueError("the value shown is the value given to the engine")
        linked = self.linked_accounts
        cluster = linked.cluster_id if linked else None
        if cluster != inputs.cluster_id:
            raise ValueError("the cluster shown is the cluster given to the engine")
        rule = d.coupon_rule
        if rule is not None and (
            linked is None
            or not set(rule.affected_customer_ids) <= {a.customer_id for a in linked.accounts}
        ):
            raise ValueError("a coupon block applies only to the linked accounts shown (LEG-09)")
        claim = d.diminished_value_claim
        if claim is not None and not _same_amount(claim.line_price.value, self.item.price_eur):
            raise ValueError("a claim is computed on the price of the returned line")
        _check_alternatives(self.alternatives, d)
        if (self.draft_status == "ready") != (self.draft is not None):
            raise ValueError("a ready draft is present, a pending one is not")
        if self.draft is not None:
            factors = self.risk.top_factors if self.risk is not None else []
            _check_draft(self.draft, d, (factors, linked.cluster_size if linked else None))
        if self.product_anomaly is not None and (
            self.product_anomaly.variant_id != self.item.variant_id
        ):
            raise ValueError("the anomaly shown is on the returned variant")
        last = self.revisions[-1]
        if (last.decision_id, last.version, last.trigger, last.state) != (
            d.decision_id,
            d.version,
            d.revision_trigger,
            d.state,
        ):
            raise ValueError("the last revision is the decision shown")
        if [r.version for r in self.revisions] != list(range(1, d.version + 1)):
            raise ValueError("revisions list versions 1..n in order")
        return self


class DecisionRecord(ApiModel):
    """GET /api/decisions/{decision_id}: one version of a case's decision as stored, the latest
    or an earlier one (US-604): the engine inputs (with their provenance), the alternatives with
    their net benefits, the human outcome, the versions of rules, models and assumptions, and the
    version that superseded it. The audit log names these ids (DecisionAuditState)."""

    decision: DecisionView
    alternatives: list[Alternative] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _alternatives(self) -> Self:
        _check_alternatives(self.alternatives, self.decision)
        return self


# ---- Decision action (US-303, US-306, spec 19.B) -----------------------------------------------


class MessageChoice(ApiModel):
    """The customer message to send after approve, override or reject: its language and,
    optionally, a selection of phrasebook ids (llm/phrasebook.py) other than the default one.
    The operator chooses reviewed sentences, never free text (ADR-14 decision 8). The API
    composes the message for the decision acted on with llm.phrasebook.render_selection (the
    mandatory body, the template fields and the legal clause come from the decision) and
    refuses a selection that is not allowed for it (422 invalid_request). Without sentence_ids:
    the draft's selection when it is allowed for the decision acted on, otherwise the fallback
    selection of llm.templates (default_selection_for)."""

    lang: Lang
    sentence_ids: (
        Annotated[list[SentenceId], Field(min_length=MIN_SENTENCES, max_length=MAX_SENTENCES)]
        | None
    ) = None

    @model_validator(mode="after")
    def _ids(self) -> Self:
        if self.sentence_ids is not None:
            _check_selection_shape(self.sentence_ids)
        return self


_MESSAGE_ACTIONS: tuple[DecisionAction, ...] = ("approve", "override", "reject")
_REASON_ACTIONS: tuple[DecisionAction, ...] = ("override", "reject", "lift")
_NO_LEVER_ACTIONS: tuple[DecisionAction, ...] = ("reject", "lift", "renew")


def _has(field: str, schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema: the field is present and matches `schema` (for if/then/else rules)."""
    return {"required": [field], "properties": {field: schema}}


def _action_is(actions: Sequence[str]) -> dict[str, Any]:
    return _has("action", {"enum": list(actions)})


class DecisionRequest(ApiModel):
    """POST /api/returns/{return_id}/decision (spec 19.B, plus decision_id and the category).

    approve: confirm the proposal (levers, if sent, must equal it: 409 otherwise); override:
    replace the levers, with a reason and a category, checked by the legal guard (422); reject:
    discard the proposal (no lever applies, the order's terms stay; the return itself is never
    refused), with a reason and a category. The three act on a proposed version.

    lift and renew (US-607) review an approved or overridden restriction, the latest version
    (409 if it is not a restriction). Each creates the next version (trigger review) from
    policy.engine.decide() on the latest scores at the day of the review. lift ends the
    restriction, before or after its review date (a review, or a rectification the customer
    asked for), with a reason and a category: the new version is overridden to the standard
    levers, and the cluster coupon application of the restriction, if any, is reversed. renew
    keeps a restriction, at or after its review date (409 before), with no levers and no reason:
    the person approves the fresh proposal, which gets a new review date (409 if the fresh
    proposal is not a restriction: lift it).

    approve, override and reject send one message to the outbox (`message` required). lift and
    renew send none (`message` null), as decided on 13 Oct 2026 (ADR-15 section 12, spec 19.B):
    the customer sees the terms of a lift or a renewal at checkout, before each purchase
    (US-403). A restriction is approved before its review date: a stale proposal answers 409
    and the case is recomputed."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": _action_is(_REASON_ACTIONS),
                    "then": {
                        "required": ["override_reason", "override_category"],
                        "properties": {
                            "override_reason": {"type": "string"},
                            "override_category": {"type": "string"},
                        },
                    },
                    "else": {
                        "properties": {
                            "override_reason": {"type": "null"},
                            "override_category": {"type": "null"},
                        }
                    },
                },
                {
                    "if": _action_is(("override",)),
                    "then": {"required": ["levers"], "properties": {"levers": {"type": "array"}}},
                },
                {
                    "if": _action_is(_NO_LEVER_ACTIONS),
                    "then": {"properties": {"levers": {"type": "null"}}},
                },
                {
                    "if": _action_is(_MESSAGE_ACTIONS),
                    "then": {
                        "required": ["message"],
                        "properties": {"message": {"type": "object"}},
                    },
                    "else": {"properties": {"message": {"type": "null"}}},
                },
            ]
        }
    )

    decision_id: Id  # the version the operator saw: 409 stale_decision if a newer one exists
    action: DecisionAction
    levers: LeverList | None = None
    message: MessageChoice | None = None
    override_reason: Reason | None = None
    override_category: OverrideCategory | None = None
    approver: HumanActor

    @model_validator(mode="after")
    def _action(self) -> Self:
        departs = self.action in _REASON_ACTIONS
        if departs and (_blank(self.override_reason) or self.override_category is None):
            raise ValueError(f"{self.action} needs override_reason and override_category")
        if not departs and (self.override_reason is not None or self.override_category):
            raise ValueError(f"{self.action} takes no override_reason or override_category")
        if self.action == "override" and self.levers is None:
            raise ValueError("override needs the levers chosen (an empty list: standard terms)")
        if self.action in _NO_LEVER_ACTIONS and self.levers is not None:
            raise ValueError(f"{self.action} takes no levers")
        if self.levers is not None and len({x.id for x in self.levers}) != len(self.levers):
            raise ValueError("a lever is given twice")
        if (self.action in _MESSAGE_ACTIONS) != (self.message is not None):
            raise ValueError("approve, override and reject send a message; lift and renew none")
        return self


# The state each decision action leaves its version in (lifted and renewed: the new version).
_AUDIT_STATES: dict[str, DecisionStatus] = {
    "decision_proposed": "proposed",
    "decision_approved": "approved",
    "decision_overridden": "overridden",
    "decision_rejected": "rejected",
    "restriction_lifted": "overridden",
    "restriction_renewed": "approved",
}
_REVIEW_ACTIONS = ("restriction_lifted", "restriction_renewed")  # a person's review: lift, renew
_NEW_VERSION_ACTIONS = (*_REVIEW_ACTIONS, "case_recomputed")


_NUMBER_TEXT_RE = re.compile(r"\s*[-+]?(?:[0-9]+(?:[.,][0-9]*)?|[.,][0-9]+)\s*")


def _check_payload_numbers(node: Any) -> None:
    """Rule 11 in the untyped payload of an audit entry: a number is a Metric {value, unit,
    provenance} (a compensation in euros, for example), never a plain number (finding D7), a
    Decimal or a number written as text (round 4, E7)."""
    if isinstance(node, dict):
        if set(node) == {"value", "unit", "provenance"}:
            Metric.model_validate(node)
            return
        for value in node.values():
            _check_payload_numbers(value)
    elif isinstance(node, list):
        for value in node:
            _check_payload_numbers(value)
    elif (isinstance(node, int | float | Decimal) and not isinstance(node, bool)) or (
        isinstance(node, str) and _NUMBER_TEXT_RE.fullmatch(node)
    ):
        raise ValueError(
            "a number in an audit payload is a Metric {value, unit, provenance} (rule 11)"
        )


_GENERATION_KINDS: dict[AuditAction, GenerationKind] = {
    "draft_generated": "case_draft",
    "draft_regenerated": "regenerate",
}


class GenerationAuditState(ApiModel):
    """The after of an audit entry about a draft generation (entity_type llm_generation): the
    path llm.generate took (model, regenerated, template), why it fell back, the model calls
    and the llm.validator codes found on the way (spec 8.7, ADR-14), with the decision version
    the draft explains."""

    generation_id: Id
    kind: GenerationKind
    decision_id: Id
    generation_path: GenerationPath
    fallback_reason: FallbackReason | None
    model_calls: int = Field(ge=0, le=MAX_MODEL_CALLS)
    validator_codes: list[ValidatorCode]
    llm_mode: LlmMode
    served_from: ServedFrom
    model: ShortText

    @model_validator(mode="after")
    def _path(self) -> Self:
        check_generation_path(
            self.generation_path, self.fallback_reason, self.model_calls, self.validator_codes
        )
        return self

    @classmethod
    def from_info(
        cls, info: GenerationInfo, kind: GenerationKind, decision_id: str
    ) -> GenerationAuditState:
        return cls(
            generation_id=info.generation_id,
            kind=kind,
            decision_id=decision_id,
            generation_path=info.generation_path,
            fallback_reason=info.fallback_reason,
            model_calls=info.model_calls,
            validator_codes=info.validator_codes,
            llm_mode=info.llm_mode,
            served_from=info.served_from,
            model=info.model,
        )


class AuditEntry(ApiModel):
    """One row of the append-only audit log (US-604).

    The entries about a decision version (entity_type decision, actions DECISION_AUDIT_ACTIONS)
    name its return and carry a typed before and after (DecisionAuditState). approve, override
    and reject change a proposed version; restriction_lifted, restriction_renewed and
    case_recomputed create the next version, so before and after are two versions.

    The entries about a draft generation (entity_type llm_generation, actions
    GENERATION_AUDIT_ACTIONS) carry a typed after (GenerationAuditState): the generation path
    and the validator codes are part of the audit record (spec 8.7)."""

    entry_id: int = Field(ge=1)
    at: AwareDatetime
    actor: Actor
    actor_role: Role
    action: AuditAction
    entity_type: AuditEntityType
    entity_id: Id
    return_id: Id | None
    decision_id: Id | None
    reason: Reason | None
    before: DecisionAuditState | GenerationAuditState | dict[str, Any] | None
    after: DecisionAuditState | GenerationAuditState | dict[str, Any] | None
    rules_version: Version | None
    model_versions: dict[str, Version] | None
    assumptions_version: Version | None
    request_id: Id | None

    @model_validator(mode="before")
    @classmethod
    def _typed_payloads(cls, data: Any) -> Any:
        typed: dict[str, type[BaseModel]] = {
            "decision": DecisionAuditState,
            "llm_generation": GenerationAuditState,
        }
        model = typed.get(str(data.get("entity_type"))) if isinstance(data, dict) else None
        if model is not None:
            data = dict(data)
            for key in ("before", "after"):
                if isinstance(data.get(key), dict):
                    data[key] = model.model_validate(data[key])
        return data

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        target = handler.resolve_ref_schema(schema)
        refs = [x for x in target["properties"]["after"]["anyOf"] if "$ref" in x]
        # the refs are in field order (DecisionAuditState, then GenerationAuditState); pydantic
        # names them only once the whole schema is generated
        decision, generation = refs
        actions = {"enum": sorted(DECISION_AUDIT_ACTIONS)}
        drafts = {"enum": sorted(GENERATION_AUDIT_ACTIONS)}
        target["allOf"] = [
            {
                "if": _has("entity_type", {"const": "decision"}),
                "then": {
                    "properties": {
                        "action": actions,
                        "before": {"anyOf": [decision, {"type": "null"}]},
                        "after": decision,
                    }
                },
                "else": {"properties": {"action": {"not": actions}}},
            },
            {
                "if": _has("entity_type", {"const": "llm_generation"}),
                "then": {
                    "properties": {
                        "action": drafts,
                        "before": {"type": "null"},
                        "after": generation,
                    }
                },
                "else": {"properties": {"action": {"not": drafts}}},
            },
        ]
        return schema

    @model_validator(mode="after")
    def _actor(self) -> Self:
        if (self.actor == SYSTEM_ACTOR) != (self.actor_role == "system"):
            raise ValueError("the system actor, and only it, has the system role")
        if self.action in HUMAN_ONLY_ACTIONS and self.actor_role == "system":
            raise ValueError(f"{self.action} is taken by a person (rule 5)")
        if self.action in REASON_REQUIRED_ACTIONS and _blank(self.reason):
            raise ValueError(f"{self.action} needs a reason")
        if (self.entity_type == "decision") != (self.action in DECISION_AUDIT_ACTIONS):
            raise ValueError("the entries about a decision version have entity_type decision")
        if (self.entity_type == "llm_generation") != (self.action in GENERATION_AUDIT_ACTIONS):
            raise ValueError("the entries about a draft generation have entity_type llm_generation")
        if self.entity_type == "decision":
            self._check_decision_payloads()
        elif self.entity_type == "llm_generation":
            self._check_generation_payloads()
        elif not all(isinstance(x, dict | None) for x in (self.before, self.after)):
            raise ValueError("only decision and generation entries carry a typed payload")
        else:
            for payload in (self.before, self.after):
                _check_payload_numbers(payload)
        return self

    def _check_renewal(self, before: DecisionAuditState, after: DecisionAuditState) -> None:
        """US-607: a renewal is made at or after the review date of the restriction it renews,
        and sets a new review date after the day of the renewal, within the maximum."""
        day = utc_day(self.at)
        if not after.requires_approval or after.review_at is None:
            raise ValueError("a renewal keeps a restriction, with a new review date")
        if before.review_at is None or day < before.review_at:
            raise ValueError("a restriction is renewed at or after its review date (US-607)")
        if not day < after.review_at <= day + timedelta(days=MAX_REVIEW_DAYS):
            raise ValueError(
                "a renewal sets a new review date after the day of the renewal, within the maximum"
            )

    def _check_generation_payloads(self) -> None:
        after = self.after
        if self.before is not None or not isinstance(after, GenerationAuditState):
            raise ValueError("a generation entry has a typed after (the record) and no before")
        if not (self.entity_id == after.generation_id and self.decision_id == after.decision_id):
            raise ValueError("a generation entry is about the generation and the decision it names")
        if self.return_id is None:
            raise ValueError("a generation entry names the return of the decision")
        if after.kind != _GENERATION_KINDS[self.action]:
            raise ValueError(f"{self.action} records a {_GENERATION_KINDS[self.action]} generation")

    def _check_decision_payloads(self) -> None:  # noqa: C901 - one rule per action, together
        before, after = self.before, self.after
        if not isinstance(after, DecisionAuditState) or not isinstance(
            before, DecisionAuditState | None
        ):
            raise ValueError("a decision entry has a typed after, and a typed before or none")
        if not (self.entity_id == self.decision_id == after.decision_id):
            raise ValueError("a decision entry is about the version in its after")
        if self.return_id is None:
            raise ValueError("a decision entry names the return of the decision")
        expected = _AUDIT_STATES.get(self.action)
        if expected is not None and after.state != expected:
            raise ValueError(f"{self.action} leaves the version {expected}")
        if self.action in HUMAN_ONLY_ACTIONS and after.approver != self.actor:
            raise ValueError("the person who acts is the approver of the version (rule 5)")
        if after.review_at is not None and after.review_at > utc_day(self.at) + timedelta(
            days=MAX_REVIEW_DAYS
        ):
            raise ValueError("a review date is at most MAX_REVIEW_DAYS days after the entry")
        if self.action == "decision_proposed":
            if before is not None or after.version != 1:
                raise ValueError("decision_proposed records a new case: version 1, no before")
        elif before is None:
            raise ValueError(f"{self.action} records the state before it")
        elif self.action in _NEW_VERSION_ACTIONS:
            if before.decision_id == after.decision_id or after.version != before.version + 1:
                raise ValueError(
                    f"{self.action} creates a new version: the next version number and another "
                    "decision id"
                )
            if self.action in _REVIEW_ACTIONS and (
                before.state not in ("approved", "overridden") or not before.requires_approval
            ):
                raise ValueError(
                    "a lift or a renewal reviews an approved or overridden restriction"
                )
            if self.action == "restriction_lifted" and (after.lever_ids or after.requires_approval):
                raise ValueError("a lift ends the restriction with the standard levers")
            if self.action == "restriction_renewed":
                self._check_renewal(before, after)
        elif (
            before.decision_id != after.decision_id
            or before.version != after.version
            or before.state != "proposed"
        ):
            raise ValueError(f"{self.action} acts on a proposed version, which keeps its number")


AUDIT_CSV_COLUMNS: tuple[str, ...] = (
    "entry_id",
    "at",
    "actor",
    "actor_role",
    "action",
    "entity_type",
    "entity_id",
    "return_id",
    "decision_id",
    "reason",
    "before",
    "after",
    "rules_version",
    "model_versions",
    "assumptions_version",
    "request_id",
)


class OutboxMessage(ApiModel):
    """A message in the simulated outbox (spec 4: no real email), sent after a person acted: the
    subject (llm.phrasebook.subject), the phrasebook ids, the template fields of its language,
    the refund the {amount} field states with the {deduction} that leaves it (both null without
    a diminished-value claim), and the body llm.phrasebook.render_selection composed for the
    decision acted on, ending with the fixed legal clause it requires. The models that hold the
    decision check the body against it (check_outbox_message)."""

    message_id: Id
    return_id: Id
    decision_id: Id
    customer_id: Id
    lang: Lang
    subject: ShortText
    sentence_ids: list[SentenceId] = Field(min_length=MIN_SENTENCES, max_length=MAX_SENTENCES)
    fields: TemplateFields
    refund: Metric | None  # eur: the refund after a diminished-value claim, when stated
    body: MessageText
    legal_clause_kind: ClauseKind | None
    generation_id: Id | None
    status: OutboxStatus
    created_by: HumanActor
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _render(self) -> Self:
        _check_selection_shape(self.sentence_ids)
        _same_unit(self.refund, unit="eur")
        if self.refund is not None and self.refund.value < 0:
            raise ValueError("a refund is not negative")
        if self.fields.amount is not None and self.refund is None:
            raise ValueError("the {amount} field states the refund the message carries")
        if (self.refund is None) != (self.fields.deduction is None):
            raise ValueError("a refund is stated with the deduction that leaves it (E17)")
        if self.subject != subject(self.return_id, self.lang):
            raise ValueError("the subject is the fixed subject of llm.phrasebook (rule 10)")
        _check_rendered(
            self.sentence_ids, self.fields, self.legal_clause_kind, self.lang, self.body
        )
        return self

    @classmethod
    def compose(
        cls,
        view: DecisionView,
        *,
        message_id: str,
        lang: Lang,
        sentence_ids: Sequence[str],
        created_by: str,
        created_at: datetime,
        generation_id: str | None = None,
        refund: Metric | None = None,
    ) -> OutboxMessage:
        """The message of llm.phrasebook.render_selection for the decision as it stands."""
        decision, _ = to_policy_decision(view)
        base = view.inputs.base_terms.to_policy()
        amount = _dec(float(refund.value)) if refund is not None else None
        return cls(
            message_id=message_id,
            return_id=view.return_id,
            decision_id=view.decision_id,
            customer_id=view.inputs.customer_id,
            lang=lang,
            subject=subject(view.return_id, lang),
            sentence_ids=list(sentence_ids),
            fields=TemplateFields.of(decision, base, lang, amount),
            refund=refund,
            body=render_selection(sentence_ids, decision, base, lang, amount),
            legal_clause_kind=llm_required_clause(decision, base),
            generation_id=generation_id,
            status="sent_simulated",
            created_by=created_by,
            created_at=created_at,
        )


class CouponRuleApplication(ApiModel):
    """The general coupon rule applied to one cluster (US-402, LEG-09): proposed with a
    decision, confirmed by the person who approves it, always reversible, with the review date
    of that decision (US-607).

    in_force says whether the block applies to the linked accounts at as_of (the time the
    application was served): confirmed, not reversed, and before its review date (UTC days). It
    lapses at the review date like the restriction that applied it. The API reverses it when the
    version that applied it is lifted (DecisionResult.coupon_application) or superseded by a
    recompute; a person reverses it on request (rectification). as_of and in_force are computed
    when served, not stored."""

    application_id: Id
    rule_version: Version
    cluster_id: Id
    decision_id: Id
    affected_customer_ids: list[Id] = Field(min_length=1)
    state: CouponApplicationState
    proposed_at: AwareDatetime
    confirmed_by: HumanActor | None
    confirmed_at: AwareDatetime | None
    review_at: date
    reversible: Literal[True] = True
    reversed_by: HumanActor | None
    reversed_at: AwareDatetime | None
    reversal_reason: Reason | None
    as_of: AwareDatetime
    in_force: bool

    @model_validator(mode="after")
    def _state(self) -> Self:
        confirmed = self.state in ("confirmed", "reversed")
        if confirmed != (self.confirmed_by is not None) or confirmed != (
            self.confirmed_at is not None
        ):
            raise ValueError("a confirmed application names who confirmed it and when")
        reversed_ = self.state == "reversed"
        fields = (self.reversed_by, self.reversed_at, self.reversal_reason)
        if any((x is not None) != reversed_ for x in fields):
            raise ValueError("a reversal names who reversed it, when and why")
        times = [x for x in (self.proposed_at, self.confirmed_at, self.reversed_at) if x]
        if times != sorted(times) or times[-1] > self.as_of:
            raise ValueError("proposed, confirmed and reversed in this order, before as_of")
        if self.review_at <= utc_day(self.proposed_at):
            raise ValueError("the review date of an application is after the day it is proposed")
        if self.confirmed_at is not None and utc_day(self.confirmed_at) >= self.review_at:
            raise ValueError(
                "an application is confirmed before its review date: a stale proposal is "
                "recomputed (409, US-607)"
            )
        start = utc_day(self.confirmed_at or self.proposed_at)
        if self.review_at > start + timedelta(days=MAX_REVIEW_DAYS):
            raise ValueError(
                "the review date of an application is at most MAX_REVIEW_DAYS days after it is "
                "confirmed (or proposed)"
            )
        in_force = self.state == "confirmed" and utc_day(self.as_of) < self.review_at
        if self.in_force != in_force:
            raise ValueError(
                "in_force: confirmed, not reversed, and before the review date (US-607)"
            )
        return self


_RESULT_ACTIONS: dict[DecisionStatus, AuditAction] = {
    "approved": "decision_approved",
    "overridden": "decision_overridden",
    "rejected": "decision_rejected",
}


def result_action(decision: DecisionView) -> AuditAction:
    """The audit action of the act that produced a decision result (US-604)."""
    if decision.revision_trigger == "review":
        return "restriction_renewed" if decision.state == "approved" else "restriction_lifted"
    return _RESULT_ACTIONS[decision.state]


class DecisionResult(ApiModel):
    """The outcome of POST .../decision: the version acted on (for a lift or a renewal, the new
    version, and `superseded`, the restriction it supersedes as served after the act), its audit
    entry, its outbox message (approve, override and reject; none for a lift or a renewal: the
    customer sees the terms at checkout), and the cluster coupon application the act changed:
    confirmed by approve, override or renew of levers that block the cluster's coupons (the
    decision's own application), or reversed by a lift of a version that had one (US-402,
    US-607). The entry and the message are about this decision, its return and its customer;
    the message is llm.phrasebook.render_selection for the decision as it stands
    (check_outbox_message). The before of a lift or a renewal is the superseded version, so the
    early-renewal rule rests on a verified review date (round 4, E5)."""

    decision: DecisionView
    superseded: DecisionView | None  # the restriction a lift or a renewal supersedes
    audit_entry: AuditEntry
    outbox_message: OutboxMessage | None
    coupon_application: CouponRuleApplication | None

    @model_validator(mode="after")
    def _linked(self) -> Self:  # noqa: C901 - one rule per line, kept together on purpose
        d, entry, message = self.decision, self.audit_entry, self.outbox_message
        if d.state == "proposed":
            raise ValueError("a decision result is an approved, overridden or rejected decision")
        action = result_action(d)
        if entry.action != action:
            raise ValueError(f"the audit entry of this result is {action}")
        if (entry.decision_id, entry.return_id) != (d.decision_id, d.return_id):
            raise ValueError("the audit entry is about this decision and its return")
        if entry.after != DecisionAuditState.from_view(d):
            raise ValueError("the audit entry records the decision as returned")
        if entry.reason != d.override_reason:
            raise ValueError("the audit entry carries the reason of the decision")
        if entry.actor != d.approver:
            raise ValueError("the person who acted is the actor of the audit entry")
        if entry.at != d.approved_at:
            raise ValueError("the audit entry is written at the time of the act (approved_at)")
        versions = (entry.rules_version, entry.model_versions, entry.assumptions_version)
        if versions != (d.rules_version, d.model_versions, d.assumptions_version):
            raise ValueError("the audit entry records the rules, model and assumption versions")
        if (message is None) != (action in _REVIEW_ACTIONS):
            raise ValueError("approve, override and reject send a message; lift and renew none")
        if message is not None:
            if message.created_by != d.approver or message.created_at != d.approved_at:
                raise ValueError("the person who acted sends the message, at the time of the act")
            check_outbox_message(message, d)
        before = entry.before
        if action not in _REVIEW_ACTIONS:
            if not isinstance(before, DecisionAuditState):
                raise ValueError("the audit entry records the proposal acted on as before")
            app = d.coupon_rule.application_id if d.coupon_rule else before.coupon_application_id
            if before != proposal_audit_state(d, app):
                raise ValueError(
                    "the audit entry records the proposal acted on as before: the engine's "
                    "quadrant, risk, levers, approval and review date, no person yet"
                )
        if action in _REVIEW_ACTIONS:
            self._check_superseded(action, before)
        elif self.superseded is not None:
            raise ValueError("only a lift or a renewal supersedes a version")
        self._check_coupon_application(action, before)
        return self

    def _check_superseded(
        self,
        action: AuditAction,
        before: DecisionAuditState | GenerationAuditState | dict[str, Any] | None,
    ) -> None:
        d, old = self.decision, self.superseded
        if old is None:
            raise ValueError("a lift or a renewal carries the version it supersedes")
        if (old.decision_id, old.return_id, old.version, old.superseded_by, old.as_of) != (
            d.supersedes,
            d.return_id,
            d.version - 1,
            d.decision_id,
            d.as_of,
        ):
            raise ValueError(
                "the superseded version is the one before, superseded by this one, served as of it"
            )
        if old.state not in ("approved", "overridden") or not old.requires_approval:
            raise ValueError("a lift or a renewal reviews an approved or overridden restriction")
        if before != DecisionAuditState.from_view(old):
            raise ValueError("a lift or a renewal records the version it supersedes as before")
        if action == "restriction_renewed" and (
            old.review_at is None or d.decided_on < old.review_at
        ):
            raise ValueError("a restriction is renewed at or after its review date (US-607)")

    def _check_coupon_application(
        self,
        action: AuditAction,
        before: DecisionAuditState | GenerationAuditState | dict[str, Any] | None,
    ) -> None:
        d, app = self.decision, self.coupon_application
        if action == "restriction_lifted":
            expected = (
                before.coupon_application_id if isinstance(before, DecisionAuditState) else None
            )
        else:
            expected = d.coupon_rule.application_id if d.coupon_rule is not None else None
        if (app is None) != (expected is None) or (app and app.application_id != expected):
            raise ValueError(
                "the result carries the coupon application the act confirmed or reversed "
                f"({expected})"
            )
        if app is None:
            return
        if app.as_of != d.as_of:
            raise ValueError("the coupon application is served as of the decision")
        if action == "restriction_lifted":
            old = self.superseded.coupon_rule if self.superseded is not None else None
            if (
                app.state != "reversed"
                or app.decision_id != d.supersedes
                or old is None
                or (old.state, old.rule_version, old.cluster_id, old.affected_customer_ids)
                != ("reversed", app.rule_version, app.cluster_id, app.affected_customer_ids)
            ):
                raise ValueError("a lift reverses the coupon application of the restriction")
            assert app.reversed_at is not None and d.approved_at is not None
            by_this_lift = (app.reversed_at, app.reversed_by, app.reversal_reason) == (
                d.approved_at,
                d.approver,
                d.override_reason,
            )
            if not by_this_lift and app.reversed_at >= d.approved_at:
                raise ValueError(
                    "the coupon application is reversed by the lift (who, when, why), or was "
                    "reversed before it"
                )
            return
        rule = d.coupon_rule
        assert rule is not None
        confirmed = (app.state, app.confirmed_by, app.confirmed_at, app.in_force) == (
            rule.state,
            d.approver,
            d.approved_at,
            rule.in_force,
        ) and rule.state == "confirmed"
        same = (
            app.decision_id,
            app.rule_version,
            app.cluster_id,
            app.affected_customer_ids,
            app.review_at,
        ) == (
            d.decision_id,
            rule.rule_version,
            rule.cluster_id,
            rule.affected_customer_ids,
            d.review_at,
        )
        if not (confirmed and same):
            raise ValueError(
                "the coupon application is the decision's, confirmed by the person who acted, "
                "with the decision's review date"
            )


# ---- Regeneration, evidence and disputes (US-304, US-310, F7) ----------------------------------


class RegenerateRequest(ApiModel):
    """A new draft in other languages. No free-text instruction: the prompt only takes data
    (NFR-11). The response is plain JSON, not a stream: the validator checks the whole output
    before it reaches the UI. There is no tone: llm/ has none to apply (round 4, E14)."""

    decision_id: Id
    languages: list[Lang] = Field(default_factory=lambda: list(LANGS), min_length=1, max_length=2)
    requested_by: HumanActor

    @model_validator(mode="after")
    def _langs(self) -> Self:
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("languages are unique")
        return self


class RegenerateResponse(ApiModel):
    """The new draft of the latest decision version, checked against it like the draft of a case
    card: the echo, the legal clause and the template fields (rules 1 and 10)."""

    return_id: Id
    decision: DecisionView  # the version the draft explains, as of the regeneration
    draft: DraftView

    @model_validator(mode="after")
    def _draft(self) -> Self:
        if self.decision.return_id != self.return_id or self.decision.superseded_by is not None:
            raise ValueError("the draft is for the latest decision version of this return")
        _check_draft(self.draft, self.decision)
        return self


class EvidenceRequest(ApiModel):
    """Favourable evidence added by the operator (US-310). Kinds that explain links name them:
    the explained links leave the cluster features and the case is recomputed."""

    decision_id: Id
    kind: EvidenceKind
    note: Reason
    target_link_ids: list[Id] = Field(default_factory=list)
    author: HumanActor

    @model_validator(mode="after")
    def _targets(self) -> Self:
        if (self.kind in LINK_EXPLAINING_EVIDENCE) != bool(self.target_link_ids):
            raise ValueError("link-explaining evidence names its links; other kinds name none")
        for text in self.target_link_ids:
            split_link_id(text)
        return self


class CaseSnapshot(ApiModel):
    """The figures that change when a case is recomputed: shown before and after (US-310)."""

    decision_id: Id
    version: int = Field(ge=1)
    state: DecisionStatus
    quadrant: Quadrant | None
    evidence_quality: EvidenceQuality
    risk: Metric | None  # probability
    top_factors: list[ScoreContribution] = Field(max_length=3)
    lever_ids: list[LeverIdText]
    requires_approval: bool
    cluster_size: int | None = Field(ge=2)
    active_links: int = Field(ge=0)  # links neither disputed nor explained

    @model_validator(mode="after")
    def _units(self) -> Self:
        _same_unit(self.risk, unit="probability")
        return self


class RecomputeResponse(ApiModel):
    """A case recomputed after evidence or a dispute: a new decision version (F7).

    coupon_application is the cluster coupon application of the version superseded, when that
    version blocked the cluster's coupons and a person had confirmed it (approved or
    overridden): the recompute reverses it in the same transaction (the author of the evidence
    or of the dispute, at the time of the new version), unless it was reversed before. It is
    null otherwise (finding D4)."""

    return_id: Id
    trigger: Literal["evidence_added", "link_disputed"]
    evidence_id: Id | None
    dispute_id: Id | None
    before: CaseSnapshot
    after: CaseSnapshot
    decision: DecisionView  # the new version
    draft: DraftView  # regenerated for the new version
    coupon_application: CouponRuleApplication | None
    audit_entry_ids: list[int] = Field(min_length=1)
    provenance: Provenance  # covers the plain numbers of before and after

    @model_validator(mode="after")
    def _versions(self) -> Self:
        if (self.trigger == "evidence_added") != (self.evidence_id is not None) or (
            self.trigger == "link_disputed"
        ) != (self.dispute_id is not None):
            raise ValueError("the trigger names its evidence or its dispute")
        if self.after.version != self.before.version + 1:
            raise ValueError("a recompute creates the next version")
        if self.decision.return_id != self.return_id:
            raise ValueError("the new version is a decision of this return")
        if (self.decision.revision_trigger, self.decision.supersedes) != (
            self.trigger,
            self.before.decision_id,
        ):
            raise ValueError("the new version records its trigger and supersedes the one before")
        if (self.decision.decision_id, self.decision.version) != (
            self.after.decision_id,
            self.after.version,
        ):
            raise ValueError("decision is the after version")
        if self.after.lever_ids != self.decision.lever_ids:
            raise ValueError("after shows the levers of the new decision")
        if (self.after.state, self.after.quadrant) != (self.decision.state, self.decision.quadrant):
            raise ValueError("after shows the state and the quadrant of the new decision")
        self._check_after()
        _check_draft(self.draft, self.decision, (self.after.top_factors, self.after.cluster_size))
        self._check_coupon_application()
        return self

    def _check_after(self) -> None:
        """Round 4, E6: the after panel shows the figures the engine used for the new version:
        its risk with the provenance of the inputs, the approval flag, the evidence quality and
        the cluster; a dispute removes an active link, and evidence never adds one."""
        d, before, after = self.decision, self.before, self.after
        inputs = d.inputs
        risk = after.risk
        if (risk is None) != (inputs.risk is None) or (
            risk is not None and (risk.value != inputs.risk or risk.provenance != inputs.provenance)
        ):
            raise ValueError("after shows the risk given to the engine, with its provenance")
        if (after.requires_approval, after.evidence_quality) != (
            d.requires_approval,
            d.evidence_quality,
        ):
            raise ValueError("after shows the approval flag and the evidence of the new version")
        if (after.cluster_size is None) != (inputs.cluster_id is None):
            raise ValueError("after shows a cluster size exactly when the engine had a cluster")
        if self.trigger == "link_disputed" and not after.active_links < before.active_links:
            raise ValueError("a dispute removes an active link")
        if after.active_links > before.active_links:
            raise ValueError("evidence never adds an active link")

    def _check_coupon_application(self) -> None:
        before, app = self.before, self.coupon_application
        confirmed_block = "cluster_coupons_disabled:true" in before.lever_ids and before.state in (
            "approved",
            "overridden",
        )
        if confirmed_block != (app is not None):
            raise ValueError(
                "a recompute that supersedes a confirmed cluster coupon block carries its "
                "coupon application, reversed; otherwise none"
            )
        if app is None:
            return
        if app.state != "reversed" or app.decision_id != before.decision_id:
            raise ValueError("the coupon application of the superseded version, reversed")
        assert app.reversed_at is not None
        if app.reversed_at > self.decision.created_at or app.as_of != self.decision.as_of:
            raise ValueError(
                "the coupon application is reversed by the recompute or before it, and served "
                "as of the new version"
            )


class LinkDisputeRequest(ApiModel):
    """A dispute of a link (US-310). From a case card it names the case and the decision version
    shown (409 stale_decision if a newer one exists); its recompute is returned."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": _has("return_id", {"type": "string"}),
                    "then": _has("decision_id", {"type": "string"}),
                    "else": {"properties": {"decision_id": {"type": "null"}}},
                }
            ]
        }
    )

    reason: Reason
    author: HumanActor
    return_id: Id | None = None  # the case being reviewed: its recompute is returned
    decision_id: Id | None = None  # the version of that case shown: required with return_id

    @model_validator(mode="after")
    def _case(self) -> Self:
        if (self.return_id is None) != (self.decision_id is None):
            raise ValueError("return_id and decision_id go together: the case and its decision_id")
        return self


class LinkDisputeResponse(ApiModel):
    link: AccountLink  # now disputed
    affected_return_ids: list[Id]  # open cases of the cluster, all recomputed
    recompute: RecomputeResponse | None  # the case of return_id, when given
    provenance: Provenance  # covers the plain numbers of link

    @model_validator(mode="after")
    def _disputed(self) -> Self:
        if not self.link.disputed or self.link.dispute is None:
            raise ValueError("the link is disputed")
        if self.recompute is not None:
            if self.recompute.dispute_id != self.link.dispute.dispute_id:
                raise ValueError("the recompute is the one of this dispute")
            if self.recompute.return_id not in self.affected_return_ids:
                raise ValueError("the recomputed case is among the affected cases")
        return self


# ---- Checkout (US-401..403, spec 19.B, F2) -----------------------------------------------------


class CartLine(ApiModel):
    variant_id: Id
    size: Annotated[str, StringConstraints(min_length=1, max_length=16)]
    quantity: int = Field(default=1, ge=1, le=5)


class CheckoutEvaluateRequest(ApiModel):
    customer_id: Id
    lines: list[CartLine] = Field(min_length=1, max_length=20)
    coupon_code: Id | None = None


class CustomerText(ApiModel):
    """A fixed customer text in Italian and English (rule 14), written by llm/phrasebook.py:
    the simulated shop shows the language of the customer (round 4, E25)."""

    it: MessageText
    en: MessageText


class SizeAdvice(ApiModel):
    """US-401: a specific size only with measurement and exchange data; otherwise the size guide
    or a plain statement that no reliable recommendation exists. The text is the fixed text of
    llm.phrasebook.size_advice_text."""

    kind: SizeAdviceKind
    suggested_size: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None
    text: CustomerText

    @model_validator(mode="after")
    def _size(self) -> Self:
        if (self.kind == "specific") != (self.suggested_size is not None):
            raise ValueError("only a specific advice suggests a size")
        expected = {lang: size_advice_text(self.kind, lang, self.suggested_size) for lang in LANGS}
        if self.text.model_dump() != expected:
            raise ValueError("the size advice is the fixed text of llm.phrasebook")
        return self

    @classmethod
    def of(cls, kind: SizeAdviceKind, suggested_size: str | None = None) -> SizeAdvice:
        texts = {lang: size_advice_text(kind, lang, suggested_size) for lang in LANGS}
        return cls(kind=kind, suggested_size=suggested_size, text=CustomerText(**texts))


class CheckoutLine(ApiModel):
    variant_id: Id
    size: Annotated[str, StringConstraints(min_length=1, max_length=16)]
    p_return: Probability  # personalized predictor (spec 8.1); provenance of the response
    size_advice: SizeAdvice | None  # present exactly when p_return >= p_return_threshold


COUPON_BLOCK = CustomerText(**COUPON_BLOCK_TEXT)


class CouponBlock(ApiModel):
    """Why coupons are off for this customer: a confirmed cluster rule application in force
    (US-402), shown only before its review date (US-607). The text is the fixed, neutral text
    of llm.phrasebook (the wording of the transparency notice): it never names the linked
    accounts or the cluster to the customer (rule 11, round 4, E25)."""

    rule_version: Version
    application_id: Id
    review_at: date
    text: CustomerText

    @model_validator(mode="after")
    def _text(self) -> Self:
        if self.text != COUPON_BLOCK:
            raise ValueError("the coupon block shows the fixed text of llm.phrasebook")
        return self


class CheckoutEvaluateResponse(ApiModel):
    """Spec 19.B, plus the provenance of the numbers, the source of the terms and the shown
    snapshot: the API stores the terms shown before answering, and /confirm binds them to the
    order (E2E-02: terms shown = snapshot stored)."""

    customer_id: Id
    quadrant: Quadrant | None  # of the decision in force for the customer; null: base terms
    lines: list[CheckoutLine] = Field(min_length=1)
    terms: Terms
    base_terms: Terms  # the merchant base terms: terms_source base shows exactly these
    coupon_eligible: bool  # coupons may be used by this customer on this order
    coupon_block: CouponBlock | None
    terms_snapshot_id: Id
    terms_source: Id  # "base" or the id of the decision whose levers are in force
    review_at: date | None  # review date of a restriction in force: after the day shown (US-607)
    shown_at: AwareDatetime
    p_return_threshold: Probability
    model_version: Version
    provenance: Provenance

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if self.coupon_block is not None and self.coupon_eligible:
            raise ValueError("a coupon block makes coupons ineligible")
        if not self.terms.coupons_enabled and self.coupon_eligible:
            raise ValueError("coupons disabled by the terms make them ineligible")
        if (self.terms_source == "base") != (self.quadrant is None):
            raise ValueError("base terms have no quadrant; decision terms have one")
        _check_terms_source(
            self.terms, self.base_terms, self.terms_source, self.review_at, self.shown_at
        )
        block = self.coupon_block
        if block is not None and block.review_at <= utc_day(self.shown_at):
            raise ValueError(
                "a coupon block lapses at its review date: it is shown only before it (US-607)"
            )
        if block is not None and block.review_at > utc_day(self.shown_at) + timedelta(
            days=MAX_REVIEW_DAYS
        ):
            raise ValueError("a coupon block has a review date within MAX_REVIEW_DAYS days")
        for line in self.lines:
            if (line.size_advice is not None) != (line.p_return >= self.p_return_threshold):
                raise ValueError("size advice is shown exactly above the threshold (F2)")
        return self


class CheckoutConfirmRequest(ApiModel):
    terms_snapshot_id: Id
    customer_id: Id
    lines: list[CartLine] = Field(min_length=1, max_length=20)
    coupon_code: Id | None = None


class CheckoutConfirmResponse(ApiModel):
    order_id: Id
    terms_snapshot: TermsSnapshotView
    coupon_applied: bool

    @model_validator(mode="after")
    def _bound(self) -> Self:
        if self.terms_snapshot.order_id != self.order_id:
            raise ValueError("the snapshot is bound to the new order")
        return self


# ---- Warehouse inspection (US-501..503, F3) ----------------------------------------------------

_MAX_PHOTO_BASE64 = 7_000_000  # about 5 MB decoded


class PhotoUpload(ApiModel):
    media_type: PhotoMediaType
    data_base64: Annotated[str, StringConstraints(min_length=4, max_length=_MAX_PHOTO_BASE64)]


class InspectionCreateRequest(ApiModel):
    return_id: Id
    inspector: HumanActor
    photo: PhotoUpload


class ChecklistSuggestion(ApiModel):
    """A candidate observation from the vision model: Ahmed confirms it (spec 8.8)."""

    item: ChecklistItemId
    suggestion: Observation
    model_confidence: Probability  # declared by the model, not calibrated
    note: ShortText | None


class CompensationProposal(ApiModel):
    """A diminished-value proposal from an approved rule; it holds only if Ahmed confirms the
    observations it rests on, and the amount is recomputed from the confirmed ones (US-502)."""

    rule_id: Id
    amount: Metric  # eur
    basis: list[ChecklistItemId] = Field(min_length=1)
    text: ShortText

    @model_validator(mode="after")
    def _amount(self) -> Self:
        _same_unit(self.amount, unit="eur")
        if self.amount.value <= 0:
            raise ValueError("a proposed amount is positive")
        return self


class ConfirmedObservation(ApiModel):
    item: ChecklistItemId
    observation: Observation


def _three_items(items: Sequence[ChecklistItemId]) -> None:
    if list(items) != list(CHECKLIST_ITEMS):
        raise ValueError(f"the checklist is {list(CHECKLIST_ITEMS)}, in this order")


class InspectionView(ApiModel):
    inspection_id: Id
    return_id: Id
    decision_id: Id
    path: Path
    inspector: HumanActor
    photo_id: Id
    created_at: AwareDatetime
    state: InspectionState
    checklist: list[ChecklistSuggestion]
    confidence_is_calibrated: Literal[False] = False
    manual_review_reason: ShortText | None
    proposal: CompensationProposal | None
    confirmed_observations: list[ConfirmedObservation] | None
    confirmed_by: HumanActor | None
    confirmed_at: AwareDatetime | None
    generation: GenerationInfo
    provenance: Provenance  # covers the plain numbers of checklist (model confidences)

    @model_validator(mode="after")
    def _state(self) -> Self:
        _three_items([c.item for c in self.checklist])
        if self.state == "manual_review" and self.manual_review_reason is None:
            raise ValueError("manual review states its reason (US-503)")
        if self.state == "awaiting_confirmation" and self.manual_review_reason is not None:
            raise ValueError("an inspection awaiting confirmation is not under manual review")
        if self.manual_review_reason is not None and self.proposal is not None:
            raise ValueError("no proposal when the system is unsure (US-503)")
        if self.path is Path.GUARANTEE and self.proposal is not None:
            raise ValueError("no diminished-value proposal on a defect")
        confirmed = self.state == "confirmed"
        fields = (self.confirmed_observations, self.confirmed_by, self.confirmed_at)
        if any((x is not None) != confirmed for x in fields):
            raise ValueError("confirmed observations, author and time exist once confirmed")
        if self.confirmed_observations is not None:
            _three_items([o.item for o in self.confirmed_observations])
        return self


class InspectionConfirmRequest(ApiModel):
    inspector: HumanActor
    observations: list[ConfirmedObservation]
    apply_compensation: bool
    note: ShortText | None = None

    @model_validator(mode="after")
    def _items(self) -> Self:
        _three_items([o.item for o in self.observations])
        return self


class InspectionConfirmResponse(ApiModel):
    """The confirmed inspection, the claim attached to the decision and the message to the
    customer. The message states the refund left after the claim ({amount}) and the amount
    withheld with its reason ({deduction}, round 4, E17). It is sent exactly when a claim
    applies and no restriction awaits a person's approval: an unapproved Q3 or Q4 proposal is
    never announced to the customer (rule 5, round 4, E1); the refund and the deduction are then
    stated by the message of the approval, override or rejection (DecisionResult)."""

    inspection: InspectionView
    claim: ClaimView | None
    decision: DecisionView
    outbox_message: OutboxMessage | None

    @model_validator(mode="after")
    def _claim(self) -> Self:
        if self.inspection.state != "confirmed" or self.inspection.confirmed_observations is None:
            raise ValueError("a confirm response holds a confirmed inspection")
        d = self.decision
        if (self.inspection.decision_id, self.inspection.return_id) != (d.decision_id, d.return_id):
            raise ValueError("the inspection is about this decision and its return")
        if self.decision.diminished_value_claim != self.claim:
            raise ValueError("the claim is attached to the decision")
        if self.claim is not None:
            observed = {o.item: o.observation for o in self.inspection.confirmed_observations}
            if observed["visible_damage"] != "yes":
                raise ValueError("a claim rests on confirmed visible damage, never on vision alone")
            if self.claim.confirmed_by != self.inspection.confirmed_by:
                raise ValueError("the inspector who confirmed signs the claim")
        awaiting = d.state == "proposed" and d.requires_approval
        message = self.outbox_message
        if awaiting and message is not None:
            raise ValueError(
                "a restriction no person has approved is never announced to the customer (rule "
                "5): the refund and the deduction are sent with the approval"
            )
        if (message is not None) != (self.claim is not None and not awaiting):
            raise ValueError(
                "a claim is communicated with the refund it leaves, unless the decision awaits "
                "a person's approval; no claim, no message"
            )
        if message is not None:
            if (message.created_by, message.created_at) != (
                self.inspection.confirmed_by,
                self.inspection.confirmed_at,
            ):
                raise ValueError("the inspector sends the message at the confirmation")
            check_outbox_message(message, d)
        return self


# ---- Portfolio matrix (US-601, ADR-04) ---------------------------------------------------------


class MatrixThresholds(ApiModel):
    risk: Metric  # probability: precision >= 0.8 on validation
    value_eur: Metric  # eur: 70th percentile of customer value
    risk_basis: ShortText
    value_basis: ShortText

    @model_validator(mode="after")
    def _units(self) -> Self:
        _same_unit(self.risk, unit="probability")
        _same_unit(self.value_eur, unit="eur")
        return self


class QuadrantSummary(ApiModel):
    quadrant: Quadrant
    customers: Metric  # count
    value_eur: Metric  # eur: total customer value in the quadrant
    returns_12m: Metric  # count


class MatrixPoint(ApiModel):
    """One scored customer: what the browser simulator needs to move the thresholds (ADR-04).
    With insufficient evidence there is no risk and no quadrant: the point stays outside the
    matrix (US-307)."""

    customer_id: Id
    display_name: ShortText | None
    risk: Probability | None  # null exactly when the evidence is insufficient
    value_eur: Eur
    evidence_quality: EvidenceQuality
    quadrant: Quadrant | None  # at the thresholds of the response; null if insufficient
    cluster_id: Id | None
    returns_12m: int = Field(ge=0)
    welcome_coupons: int = Field(ge=0)

    @model_validator(mode="after")
    def _scored(self) -> Self:
        insufficient = self.evidence_quality is EvidenceQuality.INSUFFICIENT
        if (self.risk is None) != insufficient:
            raise ValueError("risk is null exactly when the evidence is insufficient (US-307)")
        if insufficient and self.quadrant is not None:
            raise ValueError("insufficient evidence has no quadrant (US-307)")
        return self


class PortfolioMatrix(ApiModel):
    """GET /api/portfolio/matrix: every scored customer, the thresholds and the quadrant totals.
    The quadrant totals are computed on the points (a validator checks it)."""

    as_of: AwareDatetime
    thresholds: MatrixThresholds
    quadrants: list[QuadrantSummary]
    insufficient_evidence: Metric  # count: customers outside the matrix (US-307)
    points: list[MatrixPoint]
    points_provenance: Provenance
    legend: ValueLegend = VALUE_LEGEND

    @model_validator(mode="after")
    def _totals(self) -> Self:
        if [q.quadrant for q in self.quadrants] != list(Quadrant):
            raise ValueError("quadrants are Q1..Q4 in order")
        thresholds = PolicyThresholds(
            risk=float(self.thresholds.risk.value),
            value_eur=_dec(self.thresholds.value_eur.value),
        )
        by_quadrant: dict[Quadrant, list[MatrixPoint]] = {q: [] for q in Quadrant}
        insufficient = 0
        for p in self.points:
            if p.risk is None:
                insufficient += 1
                continue
            scores = CaseScores(p.risk, _dec(p.value_eur), p.evidence_quality, p.cluster_id)
            if p.quadrant is not quadrant_for(scores, thresholds):
                raise ValueError(f"{p.customer_id}: quadrant differs from policy.engine")
            by_quadrant[p.quadrant].append(p)
        if self.insufficient_evidence.value != insufficient:
            raise ValueError("insufficient_evidence must count the points without a quadrant")
        for summary in self.quadrants:
            points = by_quadrant[summary.quadrant]
            _same_unit(summary.customers, summary.returns_12m, unit="count")
            _same_unit(summary.value_eur, unit="eur")
            total = sum((_dec(p.value_eur) for p in points), Decimal(0))
            if (
                summary.customers.value != len(points)
                or summary.returns_12m.value != sum(p.returns_12m for p in points)
                or _dec(summary.value_eur.value) != total
            ):
                raise ValueError(f"{summary.quadrant.value} totals differ from its points")
        return self


# ---- Simulator assumptions (US-602) ------------------------------------------------------------


class AssumptionVersion(ApiModel):
    version: Version
    created_by: Actor
    created_at: AwareDatetime
    note: ShortText
    supersedes: Version | None
    baseline: ShortText  # the policy compared against, on the same period (spec 8.10)
    net_benefit: NetBenefitView
    sensitivity: list[SensitivityScenario]

    @model_validator(mode="after")
    def _scenarios(self) -> Self:
        stored = self.net_benefit.assumptions
        if stored.version != self.version or stored.based_on is not None:
            raise ValueError("the net benefit is computed on this stored version")
        expected = sensitivity(stored.as_policy())
        if [s.id for s in self.sensitivity] != list(expected):
            raise ValueError(f"sensitivity scenarios are {list(expected)} in order")
        for scenario in self.sensitivity:
            if _dec(scenario.net_benefit.total.value) != expected[scenario.id].total:
                raise ValueError(f"scenario {scenario.id} differs from policy.simulator")
            a = scenario.net_benefit.assumptions
            if scenario.id != "base" and (
                a.version != f"{self.version}/{scenario.id}" or a.based_on != self.version
            ):
                raise ValueError(f"scenario {scenario.id} is labelled {self.version}/{scenario.id}")
        if self.sensitivity[0].net_benefit != self.net_benefit:
            raise ValueError("the base scenario is the net benefit of the version")
        return self


class SimulatorAssumptionsResponse(ApiModel):
    """GET and PUT /api/simulator/assumptions: the current version and the history."""

    current: AssumptionVersion
    history: list[AssumptionVersion] = Field(min_length=1)  # newest first, current included

    @model_validator(mode="after")
    def _history(self) -> Self:
        if self.history[0] != self.current:
            raise ValueError("history starts with the current version")
        versions = [h.version for h in self.history]
        if len(set(versions)) != len(versions):
            raise ValueError("versions are unique")
        return self


class SimulatorAssumptionsUpdate(ApiModel):
    """PUT body: a new version from base_version (409 if base_version is not current)."""

    base_version: Version
    assumptions: AssumptionInputs
    note: ShortText
    author: HumanActor


# ---- KPI and fairness (US-603, US-605; numbers from the offline evaluation job, rule 12) -------

CohortId = Literal["all", "new_customers", "households", "low_data", "high_value"]


class KpiQuery(ApiModel):
    week_start: Week | None = None  # default: the last complete week


class CohortRate(ApiModel):
    cohort: CohortId
    false_positive_rate: Metric  # ratio
    customers: Metric  # count


class DecisionCounts(ApiModel):
    total: Metric
    approved: Metric
    overridden: Metric
    rejected: Metric
    pending: Metric

    @model_validator(mode="after")
    def _sum(self) -> Self:
        parts = (self.approved, self.overridden, self.rejected, self.pending)
        _same_unit(self.total, *parts, unit="count")
        if self.total.value != sum(p.value for p in parts):
            raise ValueError("total is the sum of the states")
        return self


class OverrideReasonCount(ApiModel):
    category: OverrideCategory
    count: Metric


class KpiResponse(ApiModel):
    """KPIs of a week (US-603). False positives need labels, so they come from the offline
    evaluation report (evaluation_report_id): the API never reads data/truth/ (rule 12)."""

    period_start: Week
    period_end: date
    net_benefit_simulated: NetBenefitView
    net_benefit_realized: NetBenefitView | None  # null until outcomes are observed
    evaluation_report_id: Id
    false_positives: list[CohortRate] = Field(min_length=1)
    decision_time_p50: Metric  # seconds, from proposal to the operator's action
    decision_time_p95: Metric
    abstentions_insufficient_evidence: Metric  # count (US-307)
    abstention_rate: Metric  # ratio
    decisions: DecisionCounts
    override_reasons: list[OverrideReasonCount]
    open_catalog_tasks: Metric  # count

    @model_validator(mode="after")
    def _week(self) -> Self:
        if self.period_end != self.period_start + timedelta(days=6):
            raise ValueError("a KPI period is one week, Monday to Sunday")
        _same_unit(self.decision_time_p50, self.decision_time_p95, unit="seconds")
        if self.decision_time_p50.value > self.decision_time_p95.value:
            raise ValueError("p50 <= p95")
        if self.net_benefit_simulated.assumptions.basis != "simulated":
            raise ValueError("net_benefit_simulated has basis simulated")
        if self.net_benefit_simulated.assumptions.based_on is None:
            raise ValueError("the week's quantities name the stored version of their unit costs")
        week = self.period_start.isoformat()
        if self.net_benefit_simulated.assumptions.version != f"kpi/{week}":
            raise ValueError(f"the week's simulated set is labelled kpi/{week} (US-602)")
        if (
            self.net_benefit_realized is not None
            and self.net_benefit_realized.assumptions.version != f"kpi/{week}/realized"
        ):
            raise ValueError(f"the week's realized set is labelled kpi/{week}/realized (US-602)")
        if (
            self.net_benefit_realized is not None
            and self.net_benefit_realized.assumptions.basis != "realized"
        ):
            raise ValueError("net_benefit_realized has basis realized")
        realized = self.net_benefit_realized
        simulated = self.net_benefit_simulated.assumptions
        if realized is not None and (
            realized.assumptions.based_on != simulated.based_on
            or any(
                getattr(realized.assumptions, f) != getattr(simulated, f) for f in UNIT_COST_FIELDS
            )
        ):
            raise ValueError(
                "the realized set prices the week's outcomes with the stored version and the "
                "unit costs of the simulated set (US-602)"
            )
        return self


FairnessDimension = Literal[
    "new_customers", "households", "low_data", "tenure", "price_band", "category"
]


class FairnessSegment(ApiModel):
    segment: ShortText
    false_positive_rate: Metric  # ratio
    customers: Metric  # count


class FairnessDimensionReport(ApiModel):
    dimension: FairnessDimension
    segments: list[FairnessSegment] = Field(min_length=2)
    max_gap: Metric  # percentage_points: largest minus smallest false positive rate
    warning: bool


class FairnessResponse(ApiModel):
    """US-605, NFR-04: false positives by segment, with a warning above the gap threshold."""

    report_id: Id
    scenario: Literal["main", "ood"]
    model_version: Version
    max_gap_threshold: Metric  # percentage_points (NFR-04: 5)
    dimensions: list[FairnessDimensionReport] = Field(min_length=1)

    @model_validator(mode="after")
    def _gaps(self) -> Self:
        _same_unit(self.max_gap_threshold, unit="percentage_points")
        for report in self.dimensions:
            _same_unit(report.max_gap, unit="percentage_points")
            rates = [s.false_positive_rate.value for s in report.segments]
            gap = (max(rates) - min(rates)) * 100
            if abs(report.max_gap.value - gap) > 0.01:
                raise ValueError(f"{report.dimension}: max_gap is (max - min) x 100")
            if report.warning != (report.max_gap.value > self.max_gap_threshold.value):
                raise ValueError(f"{report.dimension}: warning exactly above the threshold")
        return self


# ---- Audit and outbox (US-604, E2E-01) -----------------------------------------------------------


class AuditQuery(ApiModel):
    """Query of GET /api/audit; format=csv returns text/csv with AUDIT_CSV_COLUMNS."""

    return_id: Id | None = None
    decision_id: Id | None = None
    actor: Actor | None = None
    action: list[AuditAction] | None = None
    entity_type: AuditEntityType | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    format: Literal["json", "csv"] = "json"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)


class AuditPage(ApiModel):
    items: list[AuditEntry]  # newest first
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=500)


class OutboxQuery(ApiModel):
    return_id: Id | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class OutboxResponse(ApiModel):
    items: list[OutboxMessage]  # newest first
    total: int = Field(ge=0)


# ---- Product anomalies and catalogue tasks (US-208, US-606, spec 8.9) --------------------------


class AnomaliesQuery(ApiModel):
    status: list[AnomalyStatus] | None = None
    variant_id: Id | None = None


class AnomalyThresholds(ApiModel):
    z: float = Field(gt=0)  # spec 8.9: 3
    min_sales: int = Field(ge=1)  # spec 8.9: 20


class ProductAnomaliesResponse(ApiModel):
    as_of: AwareDatetime
    thresholds: AnomalyThresholds
    items: list[ProductAnomaly]
    provenance: Provenance  # covers the plain numbers of the trends

    @model_validator(mode="after")
    def _flagged(self) -> Self:
        for item in self.items:
            if item.z.value <= self.thresholds.z or item.sales.value < self.thresholds.min_sales:
                raise ValueError(f"{item.anomaly_id} is below the anomaly thresholds")
        return self


class CatalogTaskCreate(ApiModel):
    anomaly_id: Id
    kind: CatalogTaskKind  # spec 8.9: description, size guide, batch check
    note: Reason
    author: HumanActor
    return_id: Id | None = None  # the case that led to the task, if any (Elena)


class CatalogTaskUpdate(ApiModel):
    status: Literal["in_progress", "done", "cancelled"]
    note: Reason | None = None
    author: HumanActor


class FollowUp(ApiModel):
    """The variant's return rate for the reason before and after the change (US-606)."""

    baseline_rate: Metric  # ratio, the weeks before the change
    after_rate: Metric | None  # ratio, null until a full week has passed
    weeks_after: int = Field(ge=0)
    caveat: FollowUpCaveat = FOLLOW_UP_CAVEAT

    @model_validator(mode="after")
    def _after(self) -> Self:
        _same_unit(self.baseline_rate, self.after_rate, unit="ratio")
        if (self.after_rate is None) != (self.weeks_after == 0):
            raise ValueError("after_rate exists from the first full week after the change")
        return self


class CatalogTask(ApiModel):
    task_id: Id
    anomaly_id: Id
    variant_id: Id
    reason: ReturnReason
    kind: CatalogTaskKind
    status: CatalogTaskStatus
    note: Reason
    created_by: HumanActor
    created_at: AwareDatetime
    updated_at: AwareDatetime
    done_at: AwareDatetime | None
    return_id: Id | None
    follow_up: FollowUp | None

    @model_validator(mode="after")
    def _done(self) -> Self:
        if (self.status == "done") != (self.done_at is not None):
            raise ValueError("done_at is set exactly when the task is done")
        if self.follow_up is not None and self.status != "done":
            raise ValueError("the follow-up starts when the change is done")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at is before created_at")
        return self


class CatalogTasksResponse(ApiModel):
    items: list[CatalogTask]


# ---- Coupon rules (US-402, LEG-09, US-607) -------------------------------------------------------


class CouponRule(ApiModel):
    """A version of the general rule approved by the manager. The engine applies it to a Q4 case
    of a customer in a cluster: coupons off for every account of the cluster, future orders."""

    rule_version: Version
    text: LongText
    approved_by: HumanActor
    approved_at: AwareDatetime
    supersedes: Version | None
    active: bool


class CouponRulesResponse(ApiModel):
    current: CouponRule
    history: list[CouponRule] = Field(min_length=1)  # newest first, current included
    applications: list[CouponRuleApplication]  # each with in_force at as_of
    as_of: AwareDatetime

    @model_validator(mode="after")
    def _current(self) -> Self:
        if self.history[0] != self.current or not self.current.active:
            raise ValueError("the current rule is active and heads the history")
        if sum(r.active for r in self.history) != 1:
            raise ValueError("exactly one rule version is active")
        if any(a.as_of != self.as_of for a in self.applications):
            raise ValueError("every application is served as of the response")
        return self


class CouponRuleCreate(ApiModel):
    """A new version of the general rule, approved by the manager (role checked by the API)."""

    text: LongText
    approved_by: HumanActor


class CouponApplicationReverse(ApiModel):
    author: HumanActor
    reason: Reason


# ---- Agent (US-701, US-702, spec 8.8, F5) ------------------------------------------------------

AgentTool = Literal["get_case", "get_orders", "get_cluster", "get_score_factors", "get_policy_text"]


class AgentAskRequest(ApiModel):
    """A question about one case. The answer streams as server-sent events (AgentEvent)."""

    return_id: Id
    question: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    lang: Lang = "en"
    asked_by: HumanActor


class ToolCallEvent(ApiModel):
    type: Literal["tool_call"]
    seq: int = Field(ge=0)
    step: int = Field(ge=1, le=MAX_AGENT_STEPS)
    tool: AgentTool
    arguments: dict[str, str]


class ToolResultEvent(ApiModel):
    type: Literal["tool_result"]
    seq: int = Field(ge=0)
    step: int = Field(ge=1, le=MAX_AGENT_STEPS)
    tool: AgentTool
    ok: bool
    summary: ShortText
    refs: list[EntityRef]


class TokenEvent(ApiModel):
    type: Literal["token"]
    seq: int = Field(ge=0)
    text: Annotated[str, StringConstraints(min_length=1, max_length=400)]


class AnswerEvent(ApiModel):
    """The final answer, with the evidence consulted and the evidence missing side by side."""

    type: Literal["answer"]
    seq: int = Field(ge=0)
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    evidence_consulted: list[EntityRef]
    evidence_missing: list[ShortText]
    refs: list[EntityRef]

    @model_validator(mode="after")
    def _words(self) -> Self:
        if banned_terms(self.text):
            raise ValueError('say "linked accounts", never "same person" (rule 11)')
        return self


class RefusalEvent(ApiModel):
    type: Literal["refusal"]
    seq: int = Field(ge=0)
    reason: Literal["write_request", "out_of_scope", "step_limit"]
    text: ShortText


class ErrorEvent(ApiModel):
    type: Literal["error"]
    seq: int = Field(ge=0)
    code: Literal["llm_unavailable", "timeout", "internal"]
    message: ShortText


class DoneEvent(ApiModel):
    type: Literal["done"]
    seq: int = Field(ge=0)
    steps_used: int = Field(ge=0, le=MAX_AGENT_STEPS)
    generation: GenerationInfo


AgentEventModel = (
    ToolCallEvent
    | ToolResultEvent
    | TokenEvent
    | AnswerEvent
    | RefusalEvent
    | ErrorEvent
    | DoneEvent
)
AgentEvent = Annotated[AgentEventModel, Field(discriminator="type")]
_TERMINAL = ("answer", "refusal", "error")


def sse_frame(event: AgentEventModel) -> str:
    """One server-sent event on the wire: event type, id (seq), the event as JSON, blank line."""
    return f"event: {event.type}\nid: {event.seq}\ndata: {event.model_dump_json()}\n\n"


class AgentStream(RootModel[list[AgentEvent]]):
    """A whole agent stream, in order. Wire format (text/event-stream), one event per message:
    `event: <type>`, `id: <seq>`, `data: <the event as JSON>`, then a blank line.

    Order: tool_call/tool_result pairs for steps 1..n (n <= 5), then tokens, then exactly one of
    answer, refusal or error, then done. seq counts from 0 without gaps; the answer text is the
    concatenation of the tokens."""

    @model_validator(mode="after")
    def _sequence(self) -> Self:  # noqa: C901 - a small state machine
        events = self.root
        if [e.seq for e in events] != list(range(len(events))):
            raise ValueError("seq counts from 0 without gaps")
        if not events or events[-1].type != "done":
            raise ValueError("a stream ends with done")
        if sum(e.type in _TERMINAL for e in events) != 1:
            raise ValueError("exactly one answer, refusal or error")
        step = 0
        pending: AgentTool | None = None
        tokens = ""
        terminal_seen = False
        for e in events[:-1]:
            if e.type == "done":
                raise ValueError("done is the last event")
            if terminal_seen:
                raise ValueError("nothing but done follows the answer, refusal or error")
            if isinstance(e, ToolCallEvent):
                if pending is not None or tokens or e.step != step + 1:
                    raise ValueError("tool calls come first, one step at a time")
                step, pending = e.step, e.tool
            elif isinstance(e, ToolResultEvent):
                if pending != e.tool or e.step != step:
                    raise ValueError("a tool result answers the pending tool call")
                pending = None
            elif isinstance(e, TokenEvent):
                if pending is not None:
                    raise ValueError("tokens follow the tool results")
                tokens += e.text
            else:
                terminal_seen = True
                if isinstance(e, AnswerEvent) and tokens and e.text != tokens:
                    raise ValueError("the answer text is the concatenation of the tokens")
        done = events[-1]
        if isinstance(done, DoneEvent) and done.steps_used != step:
            raise ValueError("done.steps_used counts the tool calls")
        return self


# ---- Benchmarks and telemetry (US-902, US-905, NFR-12) -----------------------------------------

BenchmarkId = Literal["B1", "B2", "B3", "B4"]
BenchmarkPhase = Literal["cold_start", "training", "scoring", "generation", "pipeline", "cost"]
BenchmarkDevice = Literal["cpu", "mi300x"]
# measured: real measurements; pending: not run yet; illustrative: contract examples and mock
# servers only, never served by the API, always shown as "Illustrative, not measured".
BenchmarkStatus = Literal["measured", "pending", "illustrative"]


class BenchmarkResult(ApiModel):
    metric: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    label: ShortText
    device: BenchmarkDevice
    phase: BenchmarkPhase
    value: Metric
    concurrency: int | None = Field(ge=1)
    batch_size: int | None = Field(ge=1)
    protocol: Literal["paper", "decision_time"] | None  # ADR-10 label for ASOS models


class Benchmark(ApiModel):
    id: BenchmarkId
    title: ShortText
    status: BenchmarkStatus
    measured_at: AwareDatetime | None
    dataset: ShortText
    versions: dict[str, Version]
    config: dict[str, str | int | float | bool]
    results: list[BenchmarkResult]
    notes: list[ShortText]

    @model_validator(mode="after")
    def _status(self) -> Self:
        natures = {r.value.provenance.nature for r in self.results}
        if self.status == "pending" and (self.results or self.measured_at is not None):
            raise ValueError("a pending benchmark has no results")
        if self.status == "measured" and (
            not self.results or self.measured_at is None or natures != {"real"}
        ):
            raise ValueError("a measured benchmark has real results and a measurement time")
        if self.status == "illustrative" and (
            not self.results
            or natures != {"simulated"}
            or any("illustrative" not in r.value.provenance.origin.lower() for r in self.results)
        ):
            raise ValueError("illustrative results are simulated and labelled illustrative")
        return self


class BenchmarksResponse(ApiModel):
    items: list[Benchmark]

    @model_validator(mode="after")
    def _ids(self) -> Self:
        ids = [b.id for b in self.items]
        if ids != sorted(set(ids)):
            raise ValueError("benchmarks are unique and ordered by id")
        return self


class GpuSample(ApiModel):
    sampled_at: AwareDatetime
    utilization: Metric  # percent
    memory_used: Metric  # gib
    memory_total: Metric  # gib
    power: Metric | None  # watts

    @model_validator(mode="after")
    def _units(self) -> Self:
        _same_unit(self.utilization, unit="percent")
        _same_unit(self.memory_used, self.memory_total, unit="gib")
        _same_unit(self.power, unit="watts")
        if self.memory_used.value > self.memory_total.value:
            raise ValueError("memory_used <= memory_total")
        return self


class LlmStats(ApiModel):
    window_s: int = Field(ge=1)
    requests: Metric  # count
    latency_p50: Metric  # milliseconds
    latency_p95: Metric  # milliseconds
    tokens_per_second: Metric


class TelemetryResponse(ApiModel):
    """GET /api/telemetry, sampled from amd-smi every 5 s (US-905). In cached mode the GPU is off:
    gpu and llm are null and the note says so."""

    mode: ServedFrom
    sampled_at: AwareDatetime
    interval_s: Literal[5] = 5
    device: ShortText | None
    gpu: GpuSample | None
    gpu_history: list[GpuSample]  # oldest first, for sparklines
    llm: LlmStats | None
    api_latency_p95: Metric | None  # milliseconds
    note: ShortText | None

    @model_validator(mode="after")
    def _mode(self) -> Self:
        if self.mode == "cached" and (self.gpu is not None or self.llm is not None):
            raise ValueError("cached mode has no live GPU or LLM numbers")
        if (self.gpu is None) != (self.note is not None):
            raise ValueError("a note explains why there is no GPU sample, and only then")
        return self


# ---- Health (US-901) ----------------------------------------------------------------------------

ComponentName = Literal["database", "scores", "models", "vllm", "demo_cache", "telemetry"]


class HealthzResponse(ApiModel):
    status: Literal["ok"]


class ComponentCheck(ApiModel):
    component: ComponentName
    status: Literal["ok", "unavailable", "disabled"]
    detail: ShortText | None


class ReadyzResponse(ApiModel):
    status: Literal["ready", "degraded", "not_ready"]
    mode: ServedFrom
    llm_mode: LlmMode
    device: Literal["cpu", "gpu"]
    label: CachedLabel | None  # shown on every page in cached mode
    checks: list[ComponentCheck] = Field(min_length=1)
    contract_version: Version
    rules_version: Version
    assumptions_version: Version
    model_versions: dict[str, Version]

    @model_validator(mode="after")
    def _status(self) -> Self:
        if (self.mode == "cached") != (self.label is not None):
            raise ValueError("cached mode carries its label, live mode none")
        statuses = {c.component: c.status for c in self.checks}
        if self.status == "ready" and "unavailable" in statuses.values():
            raise ValueError("ready means no component is unavailable")
        if (self.status == "not_ready") != (statuses.get("database") != "ok"):
            raise ValueError("not_ready exactly when the database is not ok")
        return self


# ---- Registry: every model with an exported schema, and every endpoint ------------------------

API_MODELS: dict[str, type[BaseModel]] = {
    "error": ErrorResponse,
    "healthz_response": HealthzResponse,
    "readyz_response": ReadyzResponse,
    "queue_query": QueueQuery,
    "queue_response": QueueResponse,
    "case_card": CaseCard,
    "decision_record": DecisionRecord,
    "decision_request": DecisionRequest,
    "decision_result": DecisionResult,
    "regenerate_request": RegenerateRequest,
    "regenerate_response": RegenerateResponse,
    "evidence_request": EvidenceRequest,
    "recompute_response": RecomputeResponse,
    "link_dispute_request": LinkDisputeRequest,
    "link_dispute_response": LinkDisputeResponse,
    "checkout_evaluate_request": CheckoutEvaluateRequest,
    "checkout_evaluate_response": CheckoutEvaluateResponse,
    "checkout_confirm_request": CheckoutConfirmRequest,
    "checkout_confirm_response": CheckoutConfirmResponse,
    "inspection_create_request": InspectionCreateRequest,
    "inspection": InspectionView,
    "inspection_confirm_request": InspectionConfirmRequest,
    "inspection_confirm_response": InspectionConfirmResponse,
    "portfolio_matrix": PortfolioMatrix,
    "simulator_assumptions": SimulatorAssumptionsResponse,
    "simulator_assumptions_update": SimulatorAssumptionsUpdate,
    "kpi_query": KpiQuery,
    "kpi": KpiResponse,
    "fairness": FairnessResponse,
    "audit_query": AuditQuery,
    "audit_page": AuditPage,
    "outbox_query": OutboxQuery,
    "outbox": OutboxResponse,
    "anomalies_query": AnomaliesQuery,
    "product_anomalies": ProductAnomaliesResponse,
    "catalog_task_create": CatalogTaskCreate,
    "catalog_task_update": CatalogTaskUpdate,
    "catalog_task": CatalogTask,
    "catalog_tasks": CatalogTasksResponse,
    "coupon_rules": CouponRulesResponse,
    "coupon_rule_create": CouponRuleCreate,
    "coupon_application_reverse": CouponApplicationReverse,
    "coupon_rule_application": CouponRuleApplication,
    "agent_ask_request": AgentAskRequest,
    "agent_stream": AgentStream,
    "benchmarks": BenchmarksResponse,
    "telemetry": TelemetryResponse,
}

HttpMethod = Literal["GET", "POST", "PUT", "PATCH"]
JSON = "application/json"
SSE = "text/event-stream"
CSV = "text/csv"


CASE_ROLES: tuple[Role, ...] = ("operator", "manager")  # case actions (ADR-15 role table)
WAREHOUSE: tuple[Role, ...] = ("warehouse",)
MANAGER: tuple[Role, ...] = ("manager",)


@dataclass(frozen=True)
class Endpoint:
    """One endpoint of the contract. Model names are keys of API_MODELS. Every error response
    has the body `error` (ErrorResponse); 422 invalid_request applies to every endpoint with a
    body, a path parameter or a query. roles are the demo roles that may call it: the others get
    403 forbidden_role (the checkout is called by the simulated shop, with no role check)."""

    method: HttpMethod
    path: str
    summary: str
    stories: tuple[str, ...]
    response: str
    request: str | None = None
    query: str | None = None
    status: int = 200
    media_type: str = JSON
    alt_media_types: tuple[str, ...] = ()
    errors: tuple[int, ...] = ()
    roles: tuple[Role, ...] = DEMO_ROLES

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "stories": list(self.stories),
            "query": self.query,
            "request": self.request,
            "response": self.response,
            "status": self.status,
            "media_type": self.media_type,
            "alt_media_types": list(self.alt_media_types),
            "errors": list(self.errors),
            "roles": list(self.roles),
        }


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("GET", "/healthz", "Liveness", ("US-001",), "healthz_response"),
    Endpoint("GET", "/readyz", "Readiness and mode (live, cached)", ("US-901",), "readyz_response"),
    Endpoint(
        "GET",
        "/api/returns",
        "Return queue sorted by expected net benefit, with filters and pages",
        ("US-301",),
        "queue_response",
        query="queue_query",
    ),
    Endpoint(
        "GET",
        "/api/returns/{return_id}",
        "Case card: path, evidence, risk, value, linked accounts, orders, decision, draft",
        ("US-302", "US-303", "US-304", "US-305", "US-307", "US-308", "US-309"),
        "case_card",
        errors=(404,),
    ),
    Endpoint(
        "POST",
        "/api/returns/{return_id}/decision",
        "Approve, override or reject the proposal; lift or renew a restriction under review",
        ("US-303", "US-306", "US-607"),
        "decision_result",
        request="decision_request",
        errors=(403, 404, 409, 422),
        roles=CASE_ROLES,
    ),
    Endpoint(
        "GET",
        "/api/decisions/{decision_id}",
        "One decision version, latest or earlier: inputs, alternatives, outcome, versions",
        ("US-604",),
        "decision_record",
        errors=(404,),
    ),
    Endpoint(
        "POST",
        "/api/returns/{return_id}/regenerate",
        "New draft in other languages",
        ("US-304", "US-305"),
        "regenerate_response",
        request="regenerate_request",
        errors=(403, 404, 409, 503),
        roles=CASE_ROLES,
    ),
    Endpoint(
        "POST",
        "/api/returns/{return_id}/evidence",
        "Add favourable evidence and recompute the case (before and after)",
        ("US-310",),
        "recompute_response",
        request="evidence_request",
        errors=(403, 404, 409),
        roles=CASE_ROLES,
    ),
    Endpoint(
        "POST",
        "/api/links/{link_id}/dispute",
        "Dispute a link between accounts and recompute the affected cases",
        ("US-310", "US-203"),
        "link_dispute_response",
        request="link_dispute_request",
        errors=(403, 404, 409),
        roles=CASE_ROLES,
    ),
    Endpoint(
        "POST",
        "/api/checkout/evaluate",
        "Return probability per line, size advice, terms shown and stored as a shown snapshot",
        ("US-401", "US-402", "US-403"),
        "checkout_evaluate_response",
        request="checkout_evaluate_request",
        errors=(404,),
    ),
    Endpoint(
        "POST",
        "/api/checkout/confirm",
        "Bind the shown snapshot to a new order",
        ("US-403",),
        "checkout_confirm_response",
        request="checkout_confirm_request",
        status=201,
        errors=(404, 409),
    ),
    Endpoint(
        "POST",
        "/api/inspections",
        "Photo in, suggested checklist out",
        ("US-501", "US-503"),
        "inspection",
        request="inspection_create_request",
        status=201,
        errors=(403, 404),
        roles=WAREHOUSE,
    ),
    Endpoint(
        "POST",
        "/api/inspections/{inspection_id}/confirm",
        "Confirm the observations and, optionally, the compensation",
        ("US-502",),
        "inspection_confirm_response",
        request="inspection_confirm_request",
        errors=(403, 404, 409, 422),
        roles=WAREHOUSE,
    ),
    Endpoint(
        "GET",
        "/api/portfolio/matrix",
        "Risk-value pairs, thresholds and quadrant totals",
        ("US-601", "US-602"),
        "portfolio_matrix",
    ),
    Endpoint(
        "GET",
        "/api/simulator/assumptions",
        "Current and past assumption versions with net benefit and sensitivity",
        ("US-602",),
        "simulator_assumptions",
    ),
    Endpoint(
        "PUT",
        "/api/simulator/assumptions",
        "Create a new assumption version",
        ("US-602",),
        "simulator_assumptions",
        request="simulator_assumptions_update",
        errors=(403, 409),
        roles=MANAGER,
    ),
    Endpoint("GET", "/api/kpi", "KPIs of a week", ("US-603",), "kpi", query="kpi_query"),
    Endpoint("GET", "/api/fairness", "Fairness report", ("US-605",), "fairness"),
    Endpoint(
        "GET",
        "/api/audit",
        "Audit log; format=csv exports it",
        ("US-604",),
        "audit_page",
        query="audit_query",
        alt_media_types=(CSV,),
    ),
    Endpoint("GET", "/api/outbox", "Simulated outbox", ("US-304",), "outbox", query="outbox_query"),
    Endpoint(
        "GET",
        "/api/products/anomalies",
        "Product anomalies by variant, reason and week",
        ("US-208", "US-606"),
        "product_anomalies",
        query="anomalies_query",
    ),
    Endpoint("GET", "/api/catalog-tasks", "Catalogue tasks", ("US-606",), "catalog_tasks"),
    Endpoint(
        "POST",
        "/api/catalog-tasks",
        "Open a catalogue task from an anomaly",
        ("US-606",),
        "catalog_task",
        request="catalog_task_create",
        status=201,
        errors=(403, 404, 409),
        roles=CASE_ROLES,
    ),
    Endpoint(
        "PATCH",
        "/api/catalog-tasks/{task_id}",
        "Move a catalogue task (in progress, done, cancelled)",
        ("US-606",),
        "catalog_task",
        request="catalog_task_update",
        errors=(403, 404, 409),
        roles=CASE_ROLES,
    ),
    Endpoint(
        "GET",
        "/api/coupon-rules",
        "General coupon rule versions and their cluster applications",
        ("US-402", "US-607"),
        "coupon_rules",
    ),
    Endpoint(
        "POST",
        "/api/coupon-rules",
        "Approve a new version of the general coupon rule",
        ("US-402",),
        "coupon_rules",
        request="coupon_rule_create",
        status=201,
        errors=(403,),
        roles=MANAGER,
    ),
    Endpoint(
        "POST",
        "/api/coupon-rules/applications/{application_id}/reverse",
        "Reverse a cluster application (rectification)",
        ("US-402", "US-607"),
        "coupon_rule_application",
        request="coupon_application_reverse",
        errors=(403, 404, 409),
        roles=CASE_ROLES,
    ),
    Endpoint(
        "POST",
        "/api/agent/ask",
        "Question to the read-only agent, answer streamed as server-sent events",
        ("US-701", "US-702"),
        "agent_stream",
        request="agent_ask_request",
        media_type=SSE,
        errors=(403, 404, 429, 503),
        roles=CASE_ROLES,
    ),
    Endpoint("GET", "/api/benchmarks", "Benchmarks B1-B4", ("US-902",), "benchmarks"),
    Endpoint("GET", "/api/telemetry", "GPU, LLM and API telemetry", ("US-905",), "telemetry"),
)
