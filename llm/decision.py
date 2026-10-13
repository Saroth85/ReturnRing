"""One serializer from the engine Decision to what the LLM layer sees and checks (review LLM-7).

`serialize_decision` returns, from a single pass over `Levers.to_contract`:
- the lever objects of llm_case (ADR-13);
- the ExpectedDecision the validator compares the decision echo with, plus the placeholders the
  customer message may and must use.
The echo is therefore compared with exactly what the engine decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from contracts.levers import LeverObject, format_lever_id
from llm.messages import Placeholder, available_placeholders, requires_terms
from policy.types import Decision, ReturnTerms

TemplateKey = str  # "guarantee", "insufficient", "Q1", "Q2", "Q3" or "Q4"


@dataclass(frozen=True)
class ExpectedDecision:
    path: str
    evidence_quality: str
    quadrant: str | None
    lever_ids: tuple[str, ...]
    placeholders: frozenset[Placeholder]
    requires_terms: bool

    @property
    def template_key(self) -> TemplateKey:
        if self.path == "guarantee":
            return "guarantee"
        if self.evidence_quality == "insufficient" or self.quadrant is None:
            return "insufficient"
        return self.quadrant


def serialize_decision(
    decision: Decision, base: ReturnTerms, refund_eur: Decimal | None = None
) -> tuple[list[LeverObject], ExpectedDecision]:
    levers = decision.levers.to_contract(base)
    expected = ExpectedDecision(
        path=decision.path.value,
        evidence_quality=decision.evidence_quality.value,
        quadrant=decision.quadrant.value if decision.quadrant is not None else None,
        lever_ids=tuple(format_lever_id(x["id"], x["value"]) for x in levers),
        placeholders=available_placeholders(decision.path.value, levers, refund_eur is not None),
        requires_terms=requires_terms(decision, base),
    )
    return levers, expected
