"""Fixed fallback outputs (review finding LLM-12, spec 8.7 "template di riserva").

Used when the model is unavailable, echoes a different decision or fails validation twice. The
customer message is a default selection of phrasebook ids per decision kind (llm/phrasebook.py,
adversarial review of 13 Oct, round 2): code writes the body, the terms, the deadline and the
legal clause around it. The operator explanation is built from the case facts, so every number
in it is grounded by construction. A test proves that every template passes
llm/validator.validate for every kind of engine decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from contracts.levers import format_lever_id
from llm.decision import ExpectedDecision
from llm.phrasebook import PHRASEBOOK, MessageContext, allowed, context_of, context_of_case

# The fallback customer message of each decision kind, as phrasebook ids: code writes the body,
# the terms, the deadline and the legal clause around them (llm/phrasebook.py).
DEFAULT_SELECTION: dict[str, tuple[str, ...]] = {
    "guarantee": ("OPEN_SORRY_ITEM", "CLOSE_PATIENCE"),
    "insufficient": ("OPEN_THANKS", "CONTACT_REPLY", "CLOSE_REGARDS"),
    "Q1": ("OPEN_THANKS", "CLOSE_HAPPY"),
    "Q2": ("OPEN_THANKS", "HELP_SIZE", "CLOSE_REGARDS"),
    "Q3": ("OPEN_THANKS", "CONTACT_REPLY", "CLOSE_VALUED"),
    "Q4": ("OPEN_THANKS", "CONTACT_REPLY", "CLOSE_REGARDS"),
}


def default_selection(ctx: MessageContext) -> list[str]:
    """The fixed selection for the decision kind, without the extras its levers rule out."""
    return [i for i in DEFAULT_SELECTION[ctx.kind] if allowed(PHRASEBOOK[i], ctx)]


def format_value(value: int | float) -> str:
    """A fact value as written in the case JSON (42.1, 0.87, 7)."""
    return format(Decimal(str(value)).normalize(), "f") if isinstance(value, float) else str(value)


def _statement(label: str, fact: Mapping[str, Any] | None, text: str) -> dict[str, Any]:
    return {"label": label, "fact_ids": [fact["id"]] if fact is not None else [], "text": text}


def explanation_from_case(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Operator statements from the case facts only (English, rule 14)."""
    by_metric = {fact["metric"]: fact for fact in case["facts"]}
    statements: list[dict[str, Any]] = []
    if case["path"] == "guarantee":
        statements.append(
            _statement(
                "OBSERVED",
                None,
                "The return reason is a defect or a non-conformity: the case follows the legal "
                "guarantee path, with no risk score and no restrictive levers.",
            )
        )
    elif case["evidence_quality"] == "insufficient":
        statements.append(
            _statement(
                "OBSERVED",
                None,
                "Evidence is insufficient for a risk estimate: standard terms apply, no quadrant "
                "is assigned, and the missing evidence is listed with the case.",
            )
        )
    else:
        risk = by_metric["risk_score"]
        statements.append(
            _statement(
                "SCORE",
                risk,
                f"Risk score {format_value(risk['value'])} from the model: an estimate, not a "
                "cause.",
            )
        )
        for factor in case["risk"]["top_factors"]:
            fact = by_metric[factor["feature"]]
            statements.append(
                _statement(
                    "SCORE",
                    fact,
                    f"Contribution to the score: {fact['metric']} is "
                    f"{format_value(fact['value'])}.",
                )
            )
        value = by_metric["value_eur_12m"]
        statements.append(
            _statement(
                "OBSERVED",
                value,
                f"Customer net margin over the period: {format_value(value['value'])} EUR.",
            )
        )
        if case.get("cluster") and "cluster_size" in by_metric:
            # only a case with a cluster has one (an account alone has its cluster features
            # computed on itself); the size counts this customer too (round 4, finding E3)
            size = by_metric["cluster_size"]
            statements.append(
                _statement(
                    "OBSERVED",
                    size,
                    "Accounts in the cluster of linked accounts, this customer included: "
                    f"{format_value(size['value'])}.",
                )
            )
        statements.append(
            _statement(
                "OBSERVED",
                None,
                f"The rules engine placed the case in {case['quadrant']}; the levers are listed "
                "with the decision.",
            )
        )
    days = by_metric["days_since_delivery"]
    statements.append(
        _statement(
            "OBSERVED",
            days,
            f"Days from delivery to the return notice: {format_value(days['value'])}.",
        )
    )
    return statements


def lever_ids_of(case: Mapping[str, Any]) -> list[str]:
    return [format_lever_id(lever["id"], lever["value"]) for lever in case["levers"]]


def output_from_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """A schema-valid output derived from the case alone (the mock model of LLM_MODE=mock)."""
    return {
        "decision_echo": {
            "path": case["path"],
            "quadrant": case["quadrant"],
            "lever_ids": lever_ids_of(case),
        },
        "operator_explanation": explanation_from_case(case),
        "customer_message": {"sentence_ids": default_selection(context_of_case(case))},
    }


def template_output(case: Mapping[str, Any], expected: ExpectedDecision) -> dict[str, Any]:
    """The fallback output: echo and sentence ids from the engine decision, explanation from
    the case facts."""
    return {
        "decision_echo": {
            "path": expected.path,
            "quadrant": expected.quadrant,
            "lever_ids": list(expected.lever_ids),
        },
        "operator_explanation": explanation_from_case(case),
        "customer_message": {"sentence_ids": default_selection(context_of(expected))},
    }
