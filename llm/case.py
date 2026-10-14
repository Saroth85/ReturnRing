"""Build the llm_case sent to Gemma from an engine decision (contracts/json/llm_case.schema.json,
ADR-14, docs/spec.md 8.7 and 19.B).

The case is already decided: path, quadrant and levers come from the engine through the one
serializer of llm/decision.py. Every number the operator explanation may use is a code-built fact
{id, entity, metric, value, period, nature} about the customer, the return, the order or the
cluster; the levers are not facts, because a statement does not talk about them (ADR-14, round 3).
Scores never reach a guarantee case (rule 4) or a case
with insufficient evidence (US-307: neither safe nor suspicious). The customer's free text is data:
it travels between <customer_text> delimiters, escaped so it cannot close them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from contracts.parquet import IDENTIFIER_KINDS, is_forbidden_feature
from llm.decision import serialize_decision
from llm.text import (
    LANGS,
    PRONOUN_NUMBERS,
    Lang,
    banned_terms,
    grounding_problems,
    number_words,
    operator_topics,
    other_numeric_characters,
    roman_numerals,
)
from policy.types import CaseScores, Decision, EvidenceQuality, Path, ReturnRequest, ReturnTerms

Nature = Literal["real", "synthetic", "simulated"]
# No "policy" entity: operator statements do not talk about levers or terms, so the levers are not
# facts (round 3, finding T7; ADR-14).
Entity = Literal["customer", "return", "order", "cluster"]
NATURES: tuple[Nature, ...] = ("real", "synthetic", "simulated")
ENTITIES: tuple[Entity, ...] = ("customer", "return", "order", "cluster")

MAX_FACTORS = 3
MAX_MISSING_EVIDENCE = 10
MAX_POLICY_CHARS = 4000
MAX_CUSTOMER_CHARS = 2000
MAX_ID_CHARS = 64
MAX_ITEM_CHARS = 200
_METRIC_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_TAG_RE = re.compile(r"customer_text", re.IGNORECASE)
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn"})


class CaseError(ValueError):
    """Inputs that cannot form a valid llm_case: a bug in the caller."""


# Score features measured on the cluster of linked accounts (pipeline.graph.clusters; a test keeps
# them equal). An account without linked accounts has them computed on itself (cluster_size 1):
# their facts are then about the customer, never about a cluster (rule 11, round 4 of the API
# review, finding E3).
CLUSTER_FEATURES: frozenset[str] = frozenset(
    {
        "cluster_size",
        "shared_strong_ids",
        "welcome_coupons",
        "n_distinct_cards",
        "creation_spread_days",
        "welcome_coupons_per_account",
        "cluster_return_rate",
        "strong_id_share",
    }
)


def factor_entity(feature: str, clustered: bool) -> Entity:
    """The entity of the fact of a score factor: the cluster for a cluster feature of a customer
    in a cluster, the customer otherwise."""
    return "cluster" if clustered and feature in CLUSTER_FEATURES else "customer"


@dataclass(frozen=True)
class ScoreFactor:
    """One of the top-3 contributions to the risk score (SHAP, spec 8.4): feature, its value and
    the phrase from the fixed feature -> phrase table."""

    feature: str
    value: int | float | Decimal
    phrase: str
    entity: Entity = "customer"


@dataclass(frozen=True)
class ClusterSummary:
    id: str
    size: int
    shared: tuple[str, ...]  # identifier kinds shared inside the cluster


def _number(value: int | float | Decimal, places: int = 2) -> int | float:
    """A JSON number rounded half-up to `places` decimals; integers stay integers."""
    if isinstance(value, bool):
        raise CaseError(f"Facts are numbers, got {value!r}")
    if isinstance(value, int):
        return value
    exact = Decimal(str(value))
    if not exact.is_finite():
        raise CaseError(f"Facts are finite numbers, got {value!r}")
    rounded = exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return int(rounded) if rounded == rounded.to_integral_value() else float(rounded)


def wrap_customer_text(text: str) -> str:
    """The customer's text as data: NFKC, no control or format characters, at most
    MAX_CUSTOMER_CHARS characters, "&", "<" and ">" escaped and the tag name broken, between
    <customer_text> delimiters."""
    clean = "".join(
        ch if ch in "\n\t" or unicodedata.category(ch) not in _CONTROL_CATEGORIES else " "
        for ch in unicodedata.normalize("NFKC", text)
    )[:MAX_CUSTOMER_CHARS]
    escaped = clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<customer_text>{_TAG_RE.sub('customer text', escaped)}</customer_text>"


def missing_evidence_problems(item: str) -> list[str]:
    """Why a missing-evidence item could not be restated in an operator statement, as the prompt
    asks ("say what is missing"): a number in digits, words or Roman numerals (no fact grounds
    it), a topic of the statement check or a banned term (round 5, finding V14). Empty when the
    item can be copied into a statement as it is."""
    problems = grounding_problems(item, [])
    problems += other_numeric_characters(item) + roman_numerals(item)
    problems += number_words(item, "en", skip=PRONOUN_NUMBERS)
    return problems + operator_topics(item) + banned_terms(item)


def _text(value: str, what: str, limit: int) -> str:
    """A non-empty string of at most `limit` characters (the limits of llm_case.schema.json)."""
    if not 1 <= len(value) <= limit:
        raise CaseError(f"{what} must have 1 to {limit} characters, got {len(value)}")
    return value


class _Facts:
    def __init__(self, nature: Nature) -> None:
        self.items: list[dict[str, Any]] = []
        self._nature = nature

    def add(self, entity: Entity, metric: str, value: int | float | Decimal, period: str) -> None:
        """Add a fact; a metric already present (a score factor named like a fixed fact, such
        as days_since_delivery) is kept once, with its first value."""
        if _METRIC_RE.fullmatch(metric) is None:
            raise CaseError(f"Metric names are snake_case identifiers, got {metric!r}")
        if any(item["metric"] == metric for item in self.items):
            return
        self.items.append(
            {
                "id": f"F{len(self.items) + 1}",
                "entity": entity,
                "metric": metric,
                "value": _number(value),
                "period": period,
                "nature": self._nature,
            }
        )


def build_case(
    decision: Decision,
    request: ReturnRequest,
    base: ReturnTerms,
    *,
    policy_text: str,
    scores: CaseScores | None = None,
    factors: Sequence[ScoreFactor] = (),
    cluster: ClusterSummary | None = None,
    missing_evidence: Sequence[str] = (),
    customer_text: str | None = None,
    refund_eur: Decimal | None = None,
    nature: Nature = "synthetic",
    as_of: date | None = None,
    languages: Sequence[Lang] = LANGS,
    risk_as_of: date | None = None,
) -> dict[str, Any]:
    """The llm_case dict for a decided return. `as_of` (default: the decision day) is the period
    of the value, factor and cluster facts (the customer snapshot); `risk_as_of` (default:
    `as_of`) that of the risk score, which is computed at the request, after that snapshot (rule
    11). Scores, factors and cluster are left out of guarantee cases and of cases with
    insufficient evidence."""
    if decision.return_id != request.return_id:
        raise CaseError("Decision and request are about different returns")
    if len(policy_text) > MAX_POLICY_CHARS:
        raise CaseError(f"Policy text longer than {MAX_POLICY_CHARS} characters")
    if nature not in NATURES:
        raise CaseError(f"Unknown nature {nature!r}")
    if any(lang not in LANGS for lang in languages):
        raise CaseError(f"Languages must be among {LANGS}, got {list(languages)}")
    day = (as_of or decision.decided_on).isoformat()
    risk_day = risk_as_of.isoformat() if risk_as_of is not None else day
    levers, expected = serialize_decision(decision, base, refund_eur)
    scored = (
        decision.path is Path.WITHDRAWAL and decision.evidence_quality is EvidenceQuality.SUFFICIENT
    )
    insufficient = decision.evidence_quality is EvidenceQuality.INSUFFICIENT
    if insufficient and not missing_evidence:
        raise CaseError("US-307: insufficient evidence needs the list of missing evidence")
    facts = _Facts(nature)
    case: dict[str, Any] = {
        "case_id": _text(request.return_id, "case_id", MAX_ID_CHARS),
        "languages": list(dict.fromkeys(languages)),
        "path": expected.path,
        "evidence_quality": expected.evidence_quality,
    }
    if missing_evidence:
        if len(missing_evidence) > MAX_MISSING_EVIDENCE:
            raise CaseError(f"At most {MAX_MISSING_EVIDENCE} missing evidence items")
        for item in missing_evidence:
            problems = missing_evidence_problems(_text(item, "missing evidence", MAX_ITEM_CHARS))
            if problems:
                raise CaseError(f"Missing evidence {item!r} fails the statement checks: {problems}")
        case["missing_evidence"] = list(missing_evidence)
    case["quadrant"] = expected.quadrant
    case["levers"] = levers
    case["placeholders"] = {
        "available": [p for p in ("terms", "amount", "deadline") if p in expected.placeholders],
        "required": ["terms"] if expected.requires_terms else [],
    }
    case["facts"] = facts.items
    if scored:
        if scores is None:
            raise CaseError("A scored withdrawal case needs its scores")
        if len(factors) > MAX_FACTORS:
            raise CaseError(f"At most {MAX_FACTORS} score factors")
        facts.add("customer", "risk_score", scores.risk, f"as of {risk_day}")
        facts.add("customer", "value_eur_12m", scores.value_eur, f"12 months to {day}")
        for factor in factors:
            if is_forbidden_feature(factor.feature):
                raise CaseError(f"Forbidden feature {factor.feature!r} (rule 6)")
            if factor.entity == "cluster" and cluster is None:
                raise CaseError(
                    f"{factor.feature} is a fact of a cluster, and the case has none: an account "
                    "without linked accounts has customer facts"
                )
            facts.add(factor.entity, factor.feature, factor.value, f"as of {day}")
        if cluster is not None:
            if cluster.id != scores.cluster_id:
                raise CaseError("Cluster summary and scores name different clusters")
            if cluster.size < 2:
                raise CaseError(f"A cluster links at least 2 accounts, got {cluster.size}")
            facts.add("cluster", "cluster_size", cluster.size, f"as of {day}")
    days = (request.notice_on - request.delivered_on).days
    facts.add("return", "days_since_delivery", days, f"{request.delivered_on}/{request.notice_on}")
    if scored and scores is not None:
        case["risk"] = {
            "score": _number(scores.risk),
            "top_factors": [
                {
                    "feature": f.feature,
                    "value": _number(f.value),
                    "phrase": _text(f.phrase, "factor phrase", MAX_ITEM_CHARS),
                }
                for f in factors
            ],
        }
        case["value_eur"] = _number(scores.value_eur)
        if cluster is not None:
            unknown = [k for k in cluster.shared if k not in IDENTIFIER_KINDS]
            if unknown:
                raise CaseError(f"Unknown identifier kinds {unknown}")
            case["cluster"] = {
                "id": cluster.id,
                "size": cluster.size,
                "shared": list(cluster.shared),
            }
    case["policy_text"] = policy_text
    if customer_text:
        case["customer_text"] = wrap_customer_text(customer_text)
    return case
