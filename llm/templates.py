"""Fixed fallback texts (review finding LLM-12, spec 8.7 "template di riserva").

Used when the model is unavailable, echoes a different decision or fails validation twice. The
customer texts are connective text only: terms and deadlines come from the {terms} and {deadline}
placeholders, and the legal clause is appended by llm/messages.render. The operator explanation is
built from the case facts, so every number in it is grounded by construction. A test proves that
every template passes llm/validator.validate for every kind of engine decision.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from decimal import Decimal
from typing import Any

from contracts.levers import format_lever_id
from llm.decision import ExpectedDecision
from llm.text import LANGS, Lang

_OPENING: dict[Lang, str] = {
    "it": "Ciao, grazie per averci scritto.",
    "en": "Hello, thank you for writing to us.",
}
_REGISTERED_WITH_DEADLINE: dict[Lang, str] = {
    "it": "Abbiamo registrato la tua richiesta di reso e il rimborso è previsto per il {deadline}.",
    "en": "We have registered your return request and your refund is due by {deadline}.",
}
_REGISTERED: dict[Lang, str] = {
    "it": "Abbiamo registrato la tua richiesta di reso.",
    "en": "We have registered your return request.",
}
_GUARANTEE_OPENING: dict[Lang, str] = {
    "it": "Ciao, ci dispiace per il problema con il prodotto che hai ricevuto.",
    "en": "Hello, we are sorry about the problem with the item you received.",
}
_GUARANTEE_REGISTERED: dict[Lang, str] = {
    "it": "Abbiamo registrato la tua segnalazione e ti scriveremo presto con i prossimi passi.",
    "en": "We have registered your report and we will write to you soon with the next steps.",
}
_TERMS: dict[str, dict[Lang, str]] = {
    "Q1": {
        "it": "Per i tuoi prossimi ordini ti offriamo condizioni di reso migliori: {terms}.",
        "en": "For your next orders we are glad to offer you better return terms: {terms}.",
    },
    "restrictive": {
        "it": (
            "I tuoi prossimi ordini avranno nuove condizioni di reso, mostrate prima di ogni "
            "acquisto: {terms}."
        ),
        "en": "Your next orders will have new return terms, shown before each purchase: {terms}.",
    },
    "other": {
        "it": "Per i tuoi prossimi ordini le condizioni di reso sono queste: {terms}.",
        "en": "For your next orders the return terms are these: {terms}.",
    },
}
# The transparency notice of spec 8.6 (Q3, Q4): neutral, never a judgement.
_NOTICE: dict[Lang, str] = {
    "it": "Queste condizioni tengono conto dei resi dei tuoi ordini precedenti.",
    "en": "These terms take into account the returns on your previous orders.",
}
_CLOSING: dict[str, dict[Lang, str]] = {
    "Q1": {
        "it": "Siamo felici di averti tra i nostri clienti.",
        "en": "We are happy to have you with us.",
    },
    "Q2": {
        "it": "Se ti serve un'altra taglia, rispondi pure a questo messaggio.",
        "en": "If you need a different size, just reply to this message.",
    },
    "Q3": {
        "it": "Teniamo molto a te: per qualsiasi domanda rispondi pure a questo messaggio.",
        "en": "You matter to us: for any questions, just reply to this message.",
    },
    "Q4": {
        "it": "Per qualsiasi domanda rispondi pure a questo messaggio.",
        "en": "For any questions, just reply to this message.",
    },
    "insufficient": {
        "it": "Le condizioni di reso dei tuoi ordini restano quelle di sempre.",
        "en": "The return terms of your orders stay the same as always.",
    },
    "guarantee": {
        "it": "Grazie per la pazienza e per averci avvisato.",
        "en": "Thank you for your patience and for letting us know.",
    },
}


def customer_template(
    key: str, placeholders: Collection[str], lever_ids: Collection[str]
) -> dict[Lang, str]:
    """The fallback customer message for a template key (guarantee, insufficient, Q1..Q4)."""
    messages: dict[Lang, str] = {}
    for lang in LANGS:
        if key == "guarantee":
            parts = [_GUARANTEE_OPENING[lang], _GUARANTEE_REGISTERED[lang]]
        else:
            registered = _REGISTERED_WITH_DEADLINE if "deadline" in placeholders else _REGISTERED
            parts = [_OPENING[lang], registered[lang]]
        if "terms" in placeholders:
            style = "Q1" if key == "Q1" else "restrictive" if key in ("Q3", "Q4") else "other"
            parts.append(_TERMS[style][lang])
        if "transparency_notice:true" in lever_ids:
            parts.append(_NOTICE[lang])
        parts.append(_CLOSING[key][lang])
        messages[lang] = " ".join(parts)
    return messages


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
                "guarantee path, without a risk score or restrictive levers.",
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
        if "cluster_size" in by_metric:
            size = by_metric["cluster_size"]
            statements.append(
                _statement(
                    "OBSERVED",
                    size,
                    f"Linked accounts in the cluster: {format_value(size['value'])}.",
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
    if case["path"] == "guarantee":
        key = "guarantee"
    elif case["evidence_quality"] == "insufficient":
        key = "insufficient"
    else:
        key = str(case["quadrant"])
    lever_ids = lever_ids_of(case)
    return {
        "decision_echo": {
            "path": case["path"],
            "quadrant": case["quadrant"],
            "lever_ids": lever_ids,
        },
        "operator_explanation": explanation_from_case(case),
        "customer_message": customer_template(key, case["placeholders"]["available"], lever_ids),
    }


def template_output(case: Mapping[str, Any], expected: ExpectedDecision) -> dict[str, Any]:
    """The fallback output: echo and placeholders from the engine decision, explanation from
    the case facts."""
    return {
        "decision_echo": {
            "path": expected.path,
            "quadrant": expected.quadrant,
            "lever_ids": list(expected.lever_ids),
        },
        "operator_explanation": explanation_from_case(case),
        "customer_message": customer_template(
            expected.template_key, expected.placeholders, expected.lever_ids
        ),
    }
