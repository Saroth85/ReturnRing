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
- The LLM never decides: a draft must echo the decision shown; amounts, terms and deadlines are
  template fields; the legal clause is the fixed text of llm/legal_clause.py (rules 1 and 10).
- "Linked accounts", never "same person" (rule 11).

The JSON Schemas are structural. The model validators add the semantic rules listed in ADR-15;
the examples pass both.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
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
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from contracts.io import Nature
from contracts.levers import (
    LEVERS_BY_ID,
    LeverScope,
    format_lever_id,
    lever_json_schema,
    parse_lever_id,
)
from contracts.parquet import is_forbidden_feature
from contracts.vocab import (
    ACTOR_PATTERN,
    CHECKLIST_ITEMS,
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
    Tone,
)
from llm.legal_clause import clause
from llm.messages import PLACEHOLDERS, placeholders_in, render_customer_message
from llm.validator import BANNED_EVERYWHERE, BANNED_FOR_CUSTOMER, MAX_CUSTOMER_WORDS
from policy.engine import Thresholds as PolicyThresholds
from policy.engine import guarantee_terms, quadrant_for
from policy.legal_guard import (
    APPROVAL_QUADRANTS,
    LEGAL_REFUND_DAYS,
    LEGAL_WITHDRAWAL_DAYS,
    MAX_REVIEW_DAYS,
    is_restrictive,
)
from policy.simulator import Assumptions as SimAssumptions
from policy.simulator import net_benefit, sensitivity
from policy.types import (
    CaseScores,
    EvidenceQuality,
    Levers,
    Path,
    Quadrant,
    ReturnReason,
    ReturnTerms,
)

API_CONTRACT_VERSION = "1.0.0"
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
Reason = Annotated[str, StringConstraints(min_length=MIN_REASON_CHARS, max_length=1000)]
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


class Assumptions(AssumptionInputs):
    """A versioned assumption set. basis is policy.simulator's nature: simulated or realized."""

    version: Version
    basis: AssumptionBasis

    def as_policy(self) -> SimAssumptions:
        return self.to_policy(self.version, self.basis)

    @classmethod
    def from_policy(cls, a: SimAssumptions) -> Assumptions:
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
        )


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
        return self

    @classmethod
    def build(cls, assumptions: SimAssumptions, provenance: Provenance) -> NetBenefitView:
        nb = net_benefit(assumptions)
        return cls.model_validate(
            {
                "assumptions": Assumptions.from_policy(assumptions),
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
    """Query string of GET /api/returns; list filters repeat the parameter (?quadrant=Q3&...)."""

    state: list[DecisionStatus] | None = None
    quadrant: list[QuadrantFilter] | None = None
    path: list[Path] | None = None
    evidence_quality: EvidenceQuality | None = None
    requires_approval: bool | None = None
    q: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    sort: QueueSort = "-expected_net_benefit"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


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
        return self


class QueueResponse(ApiModel):
    items: list[QueueItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    sort: QueueSort
    as_of: AwareDatetime

    @model_validator(mode="after")
    def _page(self) -> Self:
        if len(self.items) > self.page_size or len(self.items) > self.total:
            raise ValueError("more items than the page size or the total")
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
            raise ValueError("cluster ids are opaque graph ids, not truth ring or family ids")
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
        if self.features.n_distinct_cards.value > self.cluster_size:
            raise ValueError("more distinct cards than accounts is not a cluster feature")
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


class TermsSnapshotView(ApiModel):
    """Terms shown at checkout and stored with the order (ADR-05). order_id is null while the
    snapshot is shown and not confirmed (POST /api/checkout/evaluate)."""

    terms_snapshot_id: Id
    order_id: Id | None
    customer_id: Id
    source: Id  # "base" or the id of the decision whose levers produced the terms
    quadrant_at_purchase: Quadrant | None
    terms: Terms
    shown_at: AwareDatetime
    confirmed_at: AwareDatetime | None

    @model_validator(mode="after")
    def _confirmed(self) -> Self:
        if (self.order_id is None) != (self.confirmed_at is None):
            raise ValueError("a snapshot gets its order id when it is confirmed, not before")
        if self.confirmed_at is not None and self.confirmed_at < self.shown_at:
            raise ValueError("confirmed_at is before shown_at")
        if self.source == "base" and self.quadrant_at_purchase is not None:
            raise ValueError("base terms have no quadrant")
        return self


class DecisionThresholds(ApiModel):
    risk: float = Field(gt=0, le=1)
    value_eur: Eur

    def to_policy(self) -> PolicyThresholds:
        return PolicyThresholds(risk=self.risk, value_eur=_dec(self.value_eur))


class DecisionInputs(ApiModel):
    """The inputs given to policy.engine.decide, kept for audit (US-604) and re-runnable."""

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

    def scores(self) -> CaseScores:
        """The engine's CaseScores; on the guarantee path risk and value are not read."""
        return CaseScores(
            risk=self.risk if self.risk is not None else 0.0,
            value_eur=_dec(self.value_eur) if self.value_eur is not None else Decimal(0),
            evidence_quality=self.evidence_quality,
            cluster_id=self.cluster_id,
        )


class CouponRuleRef(ApiModel):
    """A proposed or confirmed application of the general coupon rule to a cluster (US-402)."""

    rule_version: Version
    application_id: Id
    cluster_id: Id
    affected_customer_ids: list[Id] = Field(min_length=1)  # excluded accounts are visible
    confirmed_by: HumanActor | None


class ClaimView(ApiModel):
    """A diminished-value claim (art. 14(2)): only from confirmed observations and a rule."""

    inspection_id: Id
    amount: Metric  # eur
    photo_evidence_ids: list[Id] = Field(min_length=1)
    confirmed_by: HumanActor
    rule_id: Id

    @model_validator(mode="after")
    def _amount(self) -> Self:
        _same_unit(self.amount, unit="eur")
        if self.amount.value <= 0:
            raise ValueError("a claim amount is positive")
        return self


class DecisionView(ApiModel):
    """A decision version: the engine's proposal and the human outcome (spec 8.6, US-303/306).

    levers are the levers of the current state: the proposal while proposed or approved, the
    operator's levers when overridden, none when rejected. requires_approval refers to them.
    in_force says whether they apply to future orders now."""

    decision_id: Id
    return_id: Id
    version: int = Field(ge=1)
    created_at: AwareDatetime
    decided_on: date
    state: DecisionStatus
    path: Path
    evidence_quality: EvidenceQuality
    quadrant: Quadrant | None
    missing_evidence: list[ShortText]
    proposed_levers: list[Lever]
    levers: list[Lever]
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
    assumptions_version: Version
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
        restrictive = is_restrictive(policy_levers, base)
        if restrictive and not self.requires_approval:
            raise ValueError("restrictive levers require a human approval (rule 5)")
        if self.requires_approval and self.review_at is None:
            raise ValueError("a restriction has a review date (US-607)")
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
        expected_in_force = self.state in ("approved", "overridden") or (
            self.state == "proposed" and not self.requires_approval
        )
        if self.in_force != expected_in_force:
            raise ValueError("in_force: approved, overridden, or proposed without approval")
        blocks = any(x.id == "cluster_coupons_disabled" and x.value for x in self.levers)
        if blocks != (self.coupon_rule is not None):
            raise ValueError("a cluster coupon block carries its coupon rule application")
        if self.diminished_value_claim is not None and self.path is Path.GUARANTEE:
            raise ValueError("no diminished-value claim on a defect")
        return self

    def _check_state(self) -> None:
        acted = self.state != "proposed"
        if acted != (self.approver is not None) or acted != (self.approved_at is not None):
            raise ValueError("approver and approved_at are set exactly when a person acted")
        departs = self.state in ("overridden", "rejected")
        if departs != (self.override_reason is not None) or departs != (
            self.override_category is not None
        ):
            raise ValueError("override and reject carry a reason and a category, approve none")
        if departs and _blank(self.override_reason):
            raise ValueError("the reason must not be blank")
        if self.state in ("proposed", "approved") and self.levers != self.proposed_levers:
            raise ValueError("a proposed or approved decision keeps the proposed levers")
        if self.state == "rejected" and (self.levers or self.requires_approval):
            raise ValueError("a rejected proposal leaves no lever in force")
        rule = self.coupon_rule
        if self.state == "approved" and rule is not None and rule.confirmed_by != self.approver:
            raise ValueError("the approver confirms the coupon rule application")
        if self.state == "proposed" and rule is not None and rule.confirmed_by is not None:
            raise ValueError("a proposed coupon rule application is not confirmed yet")


AlternativeKind = Literal[
    "proposal", "current_terms", "size_exchange", "verification", "product_report"
]


class Alternative(ApiModel):
    """An admissible action with its net benefit from the simulator (US-308)."""

    kind: AlternativeKind
    summary: ShortText
    recommended: bool
    levers: list[Lever]
    net_benefit: NetBenefitView

    @model_validator(mode="after")
    def _levers(self) -> Self:
        _catalogue_order(self.levers, "levers")
        return self


class ExplanationStatement(ApiModel):
    """A statement of the operator explanation: observed data, a score contribution or a
    hypothesis to verify, with entity, metric, value and period when it cites a number (US-305)."""

    label: StatementLabel
    text: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    entity: Id | None
    metric: ShortText | None
    value: ShortText | None
    period: ShortText | None

    @model_validator(mode="after")
    def _words(self) -> Self:
        if any(term in self.text.lower() for term in BANNED_EVERYWHERE):
            raise ValueError('say "linked accounts", never "same person" (rule 11)')
        return self


class TemplateFields(ApiModel):
    """Values the code fills into the message placeholders (ADR-08): never written by the LLM."""

    terms: ShortText
    amount: ShortText | None
    deadline: ShortText | None

    def as_mapping(self) -> dict[str, str]:
        out = {"terms": self.terms}
        if self.amount is not None:
            out["amount"] = self.amount
        if self.deadline is not None:
            out["deadline"] = self.deadline
        return out


def check_connective_text(text: str) -> None:
    """Rules of the connective text of a customer message, written by the LLM or edited by an
    operator: known placeholders only, no digit outside them (amounts, terms and deadlines are
    template fields, rule 10), no accusatory term, no "same person", at most 120 words."""
    unknown = placeholders_in(text) - PLACEHOLDERS
    if unknown:
        raise ValueError(f"unknown placeholders {sorted(unknown)}")
    lower = text.lower()
    if any(term in lower for term in BANNED_FOR_CUSTOMER + BANNED_EVERYWHERE):
        raise ValueError("no accusatory term and no 'same person' in a customer message")
    stripped = text
    for name in PLACEHOLDERS:
        stripped = stripped.replace("{" + name + "}", "")
    if any(ch.isdigit() for ch in stripped):
        raise ValueError("amounts, terms and deadlines come from template fields (rule 10)")
    if len(text.split()) > MAX_CUSTOMER_WORDS:
        raise ValueError(f"at most {MAX_CUSTOMER_WORDS} words")


class CustomerMessageView(ApiModel):
    """A customer message: connective text from the LLM (placeholders only), template fields from
    code, the fixed legal clause appended by code, and the text the customer receives."""

    lang: Lang
    connective_text: Annotated[str, StringConstraints(min_length=1, max_length=900)]
    fields: TemplateFields
    legal_clause_kind: ClauseKind | None
    rendered_text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]

    @model_validator(mode="after")
    def _render(self) -> Self:
        check_connective_text(self.connective_text)
        rendered = render_customer_message(
            self.connective_text, self.fields.as_mapping(), self.lang, self.legal_clause_kind
        )
        if rendered != self.rendered_text:
            raise ValueError("rendered_text must be llm.messages.render_customer_message output")
        return self


class DecisionEchoView(ApiModel):
    path: Path
    quadrant: Quadrant | None
    lever_ids: list[LeverIdText]


class GenerationInfo(ApiModel):
    """How an LLM output was produced (spec 8.7 validator path, US-901 cached label)."""

    generation_id: Id
    generation_path: GenerationPath
    llm_mode: LlmMode
    served_from: ServedFrom
    model: ShortText  # the served model id, "mock" in LLM_MODE=mock
    attempts: int = Field(ge=0, le=2)
    validator_errors: list[ShortText]
    created_at: AwareDatetime
    latency_ms: int | None = Field(ge=0)

    @model_validator(mode="after")
    def _path(self) -> Self:
        if self.generation_path == "llm" and (self.attempts != 1 or self.validator_errors):
            raise ValueError("llm: validated on the first attempt")
        if self.generation_path == "llm_regenerated" and (
            self.attempts != 2 or not self.validator_errors
        ):
            raise ValueError("llm_regenerated: second attempt after validator errors")
        if self.generation_path == "template" and self.attempts > 0 and not self.validator_errors:
            raise ValueError("template: the LLM was unavailable (0 attempts) or failed validation")
        if self.llm_mode == "mock" and self.served_from == "cached":
            raise ValueError("cached outputs were produced by vLLM on the MI300X, not by the mock")
        return self


class DraftView(ApiModel):
    """The explanation and messages drafted for a decision (US-304, US-305)."""

    generation: GenerationInfo
    tone: Tone
    decision_echo: DecisionEchoView
    explanation: list[ExplanationStatement] = Field(min_length=1)
    messages: list[CustomerMessageView] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _langs(self) -> Self:
        langs = [m.lang for m in self.messages]
        if len(set(langs)) != len(langs):
            raise ValueError("one message per language")
        return self


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
    """A version of the case's decision (F7: the log keeps every version)."""

    decision_id: Id
    version: int = Field(ge=1)
    trigger: RevisionTrigger
    created_at: AwareDatetime
    quadrant: Quadrant | None
    risk: Probability | None
    lever_ids: list[LeverIdText]
    state: DecisionStatus


def required_clause(decision: DecisionView) -> ClauseKind | None:
    """The fixed legal clause a message must end with: the guarantee clause on the guarantee
    path, the withdrawal clause when the decision's levers are restrictive, none otherwise."""
    if decision.path is Path.GUARANTEE:
        return "guarantee"
    base = decision.inputs.base_terms.to_policy()
    if is_restrictive(to_policy_levers(decision.levers, base), base):
        return "withdrawal"
    return None


def _check_draft(draft: DraftView, decision: DecisionView) -> None:
    echo = draft.decision_echo
    if (echo.path, echo.quadrant, sorted(echo.lever_ids)) != (
        decision.path,
        decision.quadrant,
        sorted(decision.lever_ids),
    ):
        raise ValueError("the draft must echo the decision shown (rule 1)")
    kind = required_clause(decision)
    if any(m.legal_clause_kind != kind for m in draft.messages):
        raise ValueError(f"every message must carry the {kind} legal clause")
    if decision.path is Path.GUARANTEE and any(s.label == "SCORE" for s in draft.explanation):
        raise ValueError("the guarantee path is not scored: no SCORE statement")


class CaseCard(ApiModel):
    """GET /api/returns/{return_id}: everything Sara needs to decide one return (US-302)."""

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
        cluster = self.linked_accounts.cluster_id if self.linked_accounts else None
        if cluster != inputs.cluster_id:
            raise ValueError("the cluster shown is the cluster given to the engine")
        kinds = [a.kind for a in self.alternatives]
        if len(set(kinds)) != len(kinds) or sum(a.recommended for a in self.alternatives) != 1:
            raise ValueError("alternatives have distinct kinds and exactly one recommended")
        recommended = next(a for a in self.alternatives if a.recommended)
        if recommended.levers != d.proposed_levers:
            raise ValueError("the recommended alternative carries the engine's proposed levers")
        if (self.draft_status == "ready") != (self.draft is not None):
            raise ValueError("a ready draft is present, a pending one is not")
        if self.draft is not None:
            _check_draft(self.draft, d)
        if self.product_anomaly is not None and (
            self.product_anomaly.variant_id != self.item.variant_id
        ):
            raise ValueError("the anomaly shown is on the returned variant")
        last = self.revisions[-1]
        if (last.decision_id, last.version) != (d.decision_id, d.version):
            raise ValueError("the last revision is the decision shown")
        if [r.version for r in self.revisions] != list(range(1, d.version + 1)):
            raise ValueError("revisions list versions 1..n in order")
        return self


# ---- Decision action (US-303, US-306, spec 19.B) -----------------------------------------------


class MessageChoice(ApiModel):
    """The customer message to send: its language, and optionally the operator's edit of the
    connective text (placeholders only; the API fills the fields and appends the legal clause)."""

    lang: Lang
    text: Annotated[str, StringConstraints(min_length=1, max_length=900)] | None = None

    @model_validator(mode="after")
    def _text(self) -> Self:
        if self.text is not None:
            check_connective_text(self.text)
        return self


class DecisionRequest(ApiModel):
    """POST /api/returns/{return_id}/decision (spec 19.B, plus decision_id and the category).

    approve: confirm the proposal (levers, if sent, must equal it: 409 otherwise); override:
    replace the levers, with a reason and a category, checked by the legal guard (422); reject:
    discard the proposal (no lever applies, the order's terms stay; the return itself is never
    refused), with a reason and a category. Every action sends one message to the outbox."""

    decision_id: Id  # the version the operator saw: 409 stale_decision if a newer one exists
    action: DecisionAction
    levers: list[Lever] | None = None
    message: MessageChoice
    override_reason: Reason | None = None
    override_category: OverrideCategory | None = None
    approver: HumanActor

    @model_validator(mode="after")
    def _action(self) -> Self:
        departs = self.action in ("override", "reject")
        if departs and (_blank(self.override_reason) or self.override_category is None):
            raise ValueError(f"{self.action} needs override_reason and override_category")
        if not departs and (self.override_reason is not None or self.override_category):
            raise ValueError("approve takes no override_reason or override_category")
        if self.action == "override" and self.levers is None:
            raise ValueError("override needs the levers chosen (an empty list: standard terms)")
        if self.action == "reject" and self.levers is not None:
            raise ValueError("reject takes no levers")
        if self.levers is not None and len({x.id for x in self.levers}) != len(self.levers):
            raise ValueError("a lever is given twice")
        return self


class AuditEntry(ApiModel):
    """One row of the append-only audit log (US-604)."""

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
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    rules_version: Version | None
    model_versions: dict[str, Version] | None
    assumptions_version: Version | None
    request_id: Id | None

    @model_validator(mode="after")
    def _actor(self) -> Self:
        if (self.actor == SYSTEM_ACTOR) != (self.actor_role == "system"):
            raise ValueError("the system actor, and only it, has the system role")
        if self.action in HUMAN_ONLY_ACTIONS and self.actor_role == "system":
            raise ValueError(f"{self.action} is taken by a person (rule 5)")
        if self.action in REASON_REQUIRED_ACTIONS and _blank(self.reason):
            raise ValueError(f"{self.action} needs a reason")
        return self


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
    """A message in the simulated outbox (spec 4: no real email)."""

    message_id: Id
    return_id: Id
    decision_id: Id
    customer_id: Id
    lang: Lang
    subject: ShortText
    body: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    legal_clause_kind: ClauseKind | None
    generation_id: Id | None
    status: OutboxStatus
    created_by: HumanActor
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _clause(self) -> Self:
        if self.legal_clause_kind is not None and not self.body.endswith(
            clause(self.legal_clause_kind, self.lang)
        ):
            raise ValueError("the body ends with the fixed legal clause (ADR-06)")
        return self


class DecisionResult(ApiModel):
    decision: DecisionView
    audit_entry: AuditEntry
    outbox_message: OutboxMessage

    @model_validator(mode="after")
    def _linked(self) -> Self:
        if self.decision.state == "proposed":
            raise ValueError("a decision result is an approved, overridden or rejected decision")
        if self.audit_entry.decision_id != self.decision.decision_id:
            raise ValueError("the audit entry is about this decision")
        if self.outbox_message.decision_id != self.decision.decision_id:
            raise ValueError("the message is about this decision")
        if self.outbox_message.legal_clause_kind != required_clause(self.decision):
            raise ValueError("the message carries the legal clause the decision requires")
        return self


# ---- Regeneration, evidence and disputes (US-304, US-310, F7) ----------------------------------


class RegenerateRequest(ApiModel):
    """A new draft in other languages or another tone. No free-text instruction: the prompt
    only takes data (NFR-11). The response is plain JSON, not a stream: the validator checks the
    whole output before it reaches the UI."""

    decision_id: Id
    languages: list[Lang] = Field(default_factory=lambda: list(LANGS), min_length=1, max_length=2)
    tone: Tone = "warm"
    requested_by: HumanActor

    @model_validator(mode="after")
    def _langs(self) -> Self:
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("languages are unique")
        return self


class RegenerateResponse(ApiModel):
    return_id: Id
    decision_id: Id
    draft: DraftView


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
    """A case recomputed after evidence or a dispute: a new decision version (F7)."""

    return_id: Id
    trigger: Literal["evidence_added", "link_disputed"]
    evidence_id: Id | None
    dispute_id: Id | None
    before: CaseSnapshot
    after: CaseSnapshot
    decision: DecisionView  # the new version
    draft: DraftView  # regenerated for the new version
    audit_entry_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def _versions(self) -> Self:
        if (self.trigger == "evidence_added") != (self.evidence_id is not None) or (
            self.trigger == "link_disputed"
        ) != (self.dispute_id is not None):
            raise ValueError("the trigger names its evidence or its dispute")
        if self.after.version != self.before.version + 1:
            raise ValueError("a recompute creates the next version")
        if (self.decision.decision_id, self.decision.version) != (
            self.after.decision_id,
            self.after.version,
        ):
            raise ValueError("decision is the after version")
        if self.after.lever_ids != self.decision.lever_ids:
            raise ValueError("after shows the levers of the new decision")
        _check_draft(self.draft, self.decision)
        return self


class LinkDisputeRequest(ApiModel):
    reason: Reason
    author: HumanActor
    return_id: Id | None = None  # the case being reviewed: its recompute is returned


class LinkDisputeResponse(ApiModel):
    link: AccountLink  # now disputed
    affected_return_ids: list[Id]  # open cases of the cluster, all recomputed
    recompute: RecomputeResponse | None  # the case of return_id, when given

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


class SizeAdvice(ApiModel):
    """US-401: a specific size only with measurement and exchange data; otherwise the size guide
    or a plain statement that no reliable recommendation exists."""

    kind: SizeAdviceKind
    suggested_size: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None
    text: ShortText

    @model_validator(mode="after")
    def _size(self) -> Self:
        if (self.kind == "specific") != (self.suggested_size is not None):
            raise ValueError("only a specific advice suggests a size")
        return self


class CheckoutLine(ApiModel):
    variant_id: Id
    size: Annotated[str, StringConstraints(min_length=1, max_length=16)]
    p_return: Probability  # personalized predictor (spec 8.1); provenance of the response
    size_advice: SizeAdvice | None  # present exactly when p_return >= p_return_threshold


class CouponBlock(ApiModel):
    """Why coupons are off for this customer: a confirmed cluster rule application (US-402)."""

    rule_version: Version
    application_id: Id
    review_at: date
    text: ShortText


class CheckoutEvaluateResponse(ApiModel):
    """Spec 19.B, plus the provenance of the numbers, the source of the terms and the shown
    snapshot: the API stores the terms shown before answering, and /confirm binds them to the
    order (E2E-02: terms shown = snapshot stored)."""

    customer_id: Id
    quadrant: Quadrant | None  # of the decision in force for the customer; null: base terms
    lines: list[CheckoutLine] = Field(min_length=1)
    terms: Terms
    coupon_eligible: bool  # coupons may be used by this customer on this order
    coupon_block: CouponBlock | None
    terms_snapshot_id: Id
    terms_source: Id  # "base" or the id of the decision whose levers are in force
    review_at: date | None  # review date of a restriction in force (US-607)
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
    inspection: InspectionView
    claim: ClaimView | None
    decision: DecisionView
    outbox_message: OutboxMessage | None  # the message with the motivation, when a claim applies

    @model_validator(mode="after")
    def _claim(self) -> Self:
        if self.inspection.state != "confirmed" or self.inspection.confirmed_observations is None:
            raise ValueError("a confirm response holds a confirmed inspection")
        if self.decision.diminished_value_claim != self.claim:
            raise ValueError("the claim is attached to the decision")
        if self.claim is not None:
            observed = {o.item: o.observation for o in self.inspection.confirmed_observations}
            if observed["visible_damage"] != "yes":
                raise ValueError("a claim rests on confirmed visible damage, never on vision alone")
            if self.claim.confirmed_by != self.inspection.confirmed_by:
                raise ValueError("the inspector who confirmed signs the claim")
            if self.outbox_message is None:
                raise ValueError("a claim is communicated with its motivation")
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
    """One scored customer: what the browser simulator needs to move the thresholds (ADR-04)."""

    customer_id: Id
    display_name: ShortText | None
    risk: Probability
    value_eur: Eur
    evidence_quality: EvidenceQuality
    quadrant: Quadrant | None  # at the thresholds of the response; null if insufficient
    cluster_id: Id | None
    returns_12m: int = Field(ge=0)
    welcome_coupons: int = Field(ge=0)


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
            if p.evidence_quality is EvidenceQuality.INSUFFICIENT:
                insufficient += 1
                if p.quadrant is not None:
                    raise ValueError(f"{p.customer_id}: insufficient evidence has no quadrant")
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
        if self.net_benefit.assumptions.version != self.version:
            raise ValueError("the net benefit is computed on this version")
        expected = sensitivity(self.net_benefit.assumptions.as_policy())
        if [s.id for s in self.sensitivity] != list(expected):
            raise ValueError(f"sensitivity scenarios are {list(expected)} in order")
        for scenario in self.sensitivity:
            if _dec(scenario.net_benefit.total.value) != expected[scenario.id].total:
                raise ValueError(f"scenario {scenario.id} differs from policy.simulator")
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
        if (
            self.net_benefit_realized is not None
            and self.net_benefit_realized.assumptions.basis != "realized"
        ):
            raise ValueError("net_benefit_realized has basis realized")
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


class CouponRuleApplication(ApiModel):
    """The rule applied to one cluster: proposed with a decision, confirmed by the operator who
    approves it, always reversible, with a review date."""

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
        return self


class CouponRulesResponse(ApiModel):
    current: CouponRule
    history: list[CouponRule] = Field(min_length=1)  # newest first, current included
    applications: list[CouponRuleApplication]

    @model_validator(mode="after")
    def _current(self) -> Self:
        if self.history[0] != self.current or not self.current.active:
            raise ValueError("the current rule is active and heads the history")
        if sum(r.active for r in self.history) != 1:
            raise ValueError("exactly one rule version is active")
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
        if any(term in self.text.lower() for term in BANNED_EVERYWHERE):
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


@dataclass(frozen=True)
class Endpoint:
    """One endpoint of the contract. Model names are keys of API_MODELS. Every error response
    has the body `error` (ErrorResponse); 422 invalid_request applies to every endpoint with a
    body, a path parameter or a query."""

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
        "Approve, override with a reason, or reject the proposal",
        ("US-303", "US-306", "US-607"),
        "decision_result",
        request="decision_request",
        errors=(403, 404, 409, 422),
    ),
    Endpoint(
        "POST",
        "/api/returns/{return_id}/regenerate",
        "New draft in other languages or another tone",
        ("US-304", "US-305"),
        "regenerate_response",
        request="regenerate_request",
        errors=(404, 409),
    ),
    Endpoint(
        "POST",
        "/api/returns/{return_id}/evidence",
        "Add favourable evidence and recompute the case (before and after)",
        ("US-310",),
        "recompute_response",
        request="evidence_request",
        errors=(404, 409),
    ),
    Endpoint(
        "POST",
        "/api/links/{link_id}/dispute",
        "Dispute a link between accounts and recompute the affected cases",
        ("US-310", "US-203"),
        "link_dispute_response",
        request="link_dispute_request",
        errors=(404, 409),
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
    ),
    Endpoint(
        "POST",
        "/api/inspections/{inspection_id}/confirm",
        "Confirm the observations and, optionally, the compensation",
        ("US-502",),
        "inspection_confirm_response",
        request="inspection_confirm_request",
        errors=(403, 404, 409, 422),
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
    ),
    Endpoint(
        "PATCH",
        "/api/catalog-tasks/{task_id}",
        "Move a catalogue task (in progress, done, cancelled)",
        ("US-606",),
        "catalog_task",
        request="catalog_task_update",
        errors=(403, 404, 409),
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
    ),
    Endpoint(
        "POST",
        "/api/coupon-rules/applications/{application_id}/reverse",
        "Reverse a cluster application (rectification)",
        ("US-402", "US-607"),
        "coupon_rule_application",
        request="coupon_application_reverse",
        errors=(403, 404, 409),
    ),
    Endpoint(
        "POST",
        "/api/agent/ask",
        "Question to the read-only agent, answer streamed as server-sent events",
        ("US-701", "US-702"),
        "agent_stream",
        request="agent_ask_request",
        media_type=SSE,
        errors=(404, 429),
    ),
    Endpoint("GET", "/api/benchmarks", "Benchmarks B1-B4", ("US-902",), "benchmarks"),
    Endpoint("GET", "/api/telemetry", "GPU, LLM and API telemetry", ("US-905",), "telemetry"),
)
