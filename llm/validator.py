"""Deterministic checks on Gemma's output before it reaches the UI (docs/spec.md 8.7, ADR-14).

Every error carries a code and the action of the 8.7 table:
- REGENERATE: ask the model once more, then use the template;
- TEMPLATE: use the fixed template at once (decision echo mismatch);
- BLOCK: a bug in the code (the case does not match the engine decision); nothing is shown.
The model never changes the decision (CLAUDE.md rule 1). Numbers in the operator explanation are
grounded only on code-built facts the statement cites; customer text, policy text and identifiers
never feed grounding.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from contracts.levers import LeverError, format_lever_id
from llm.decision import ExpectedDecision
from llm.messages import placeholder_problems, strip_placeholders
from llm.text import (
    LANGS,
    PRONOUN_NUMBERS,
    Lang,
    banned_terms,
    language_of,
    legal_wording,
    literal_numbers,
    literal_units,
    number_words,
    numbers_in_text,
    other_numeric_characters,
    strip_tokens,
    word_count,
)

MAX_CUSTOMER_WORDS = 120
MAX_STATEMENTS = 8
MAX_FACTS_PER_STATEMENT = 4
MAX_STATEMENT_CHARS = 300
MAX_MESSAGE_CHARS = 900
MAX_LEVER_IDS = 9
# Facts that are model outputs: an OBSERVED statement may not cite them.
SCORE_METRICS = frozenset({"risk_score"})
_QUADRANT_LABELS = ("Q1", "Q2", "Q3", "Q4")


class Action(StrEnum):
    REGENERATE = "REGENERATE"
    TEMPLATE = "TEMPLATE"
    BLOCK = "BLOCK"


_ACTION_RANK = {Action.REGENERATE: 0, Action.TEMPLATE: 1, Action.BLOCK: 2}


class Code(StrEnum):
    SCHEMA = "SCHEMA"
    CASE_INVALID = "CASE_INVALID"
    DECISION_ECHO = "DECISION_ECHO"
    UNKNOWN_FACT = "UNKNOWN_FACT"
    MISSING_FACT = "MISSING_FACT"
    LABEL_MISMATCH = "LABEL_MISMATCH"
    UNGROUNDED_NUMBER = "UNGROUNDED_NUMBER"
    BANNED_TERM = "BANNED_TERM"
    LITERAL_NUMBER = "LITERAL_NUMBER"
    LITERAL_UNIT = "LITERAL_UNIT"
    LEGAL_WORDING = "LEGAL_WORDING"
    PLACEHOLDER_SYNTAX = "PLACEHOLDER_SYNTAX"
    PLACEHOLDER_UNAVAILABLE = "PLACEHOLDER_UNAVAILABLE"
    TERMS_MISSING = "TERMS_MISSING"
    WRONG_LANGUAGE = "WRONG_LANGUAGE"
    TOO_LONG = "TOO_LONG"


# The 8.7 table: echo -> template; numbers, banned terms, language and length -> regenerate, then
# template; the legal clause and any mismatch between case and decision -> blocking error.
ACTIONS: dict[Code, Action] = {
    Code.SCHEMA: Action.REGENERATE,
    Code.CASE_INVALID: Action.BLOCK,
    Code.DECISION_ECHO: Action.TEMPLATE,
    Code.UNKNOWN_FACT: Action.REGENERATE,
    Code.MISSING_FACT: Action.REGENERATE,
    Code.LABEL_MISMATCH: Action.REGENERATE,
    Code.UNGROUNDED_NUMBER: Action.REGENERATE,
    Code.BANNED_TERM: Action.REGENERATE,
    Code.LITERAL_NUMBER: Action.REGENERATE,
    Code.LITERAL_UNIT: Action.REGENERATE,
    Code.LEGAL_WORDING: Action.REGENERATE,
    Code.PLACEHOLDER_SYNTAX: Action.REGENERATE,
    Code.PLACEHOLDER_UNAVAILABLE: Action.REGENERATE,
    Code.TERMS_MISSING: Action.REGENERATE,
    Code.WRONG_LANGUAGE: Action.REGENERATE,
    Code.TOO_LONG: Action.REGENERATE,
}


@dataclass(frozen=True)
class ValidationIssue:
    code: Code
    action: Action
    where: str
    detail: str = ""


def _issue(code: Code, where: str, detail: str = "") -> ValidationIssue:
    return ValidationIssue(code, ACTIONS[code], where, detail)


# ---- Output model: mirrors contracts/json/llm_output.schema.json (a test keeps them equal) -----

FactId = Annotated[str, StringConstraints(min_length=1, max_length=4)]
LeverId = Annotated[str, StringConstraints(min_length=1, max_length=40)]


class DecisionEcho(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Literal["withdrawal", "guarantee"]
    quadrant: Literal["Q1", "Q2", "Q3", "Q4"] | None
    lever_ids: list[LeverId] = Field(max_length=MAX_LEVER_IDS)


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Literal["OBSERVED", "SCORE", "HYPOTHESIS"]
    fact_ids: list[FactId] = Field(max_length=MAX_FACTS_PER_STATEMENT)
    text: str = Field(min_length=1, max_length=MAX_STATEMENT_CHARS)


class CustomerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    it: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    en: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class LlmOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_echo: DecisionEcho
    operator_explanation: list[Statement] = Field(min_length=1, max_length=MAX_STATEMENTS)
    customer_message: CustomerMessage


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[ValidationIssue, ...]
    output: LlmOutput | None  # None whenever there is an error

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def action(self) -> Action | None:
        """The strongest action among the errors, or None when the output is valid."""
        if not self.errors:
            return None
        return max((e.action for e in self.errors), key=_ACTION_RANK.__getitem__)

    @property
    def codes(self) -> list[str]:
        return sorted({e.code.value for e in self.errors})


# ---- Case: built by code from the engine decision ---------------------------------------------


@dataclass(frozen=True)
class _Fact:
    metric: str
    value: Decimal


def _facts(case: Mapping[str, Any]) -> dict[str, _Fact]:
    facts: dict[str, _Fact] = {}
    for fact in case["facts"]:
        value = fact["value"]
        if isinstance(value, bool) or not isinstance(value, int | float) or fact["id"] in facts:
            raise ValueError(f"fact {fact['id']!r} is not a unique numeric fact")
        facts[str(fact["id"])] = _Fact(str(fact["metric"]), Decimal(str(value)))
    return facts


def check_case(case: Mapping[str, Any], expected: ExpectedDecision) -> list[ValidationIssue]:
    """The case must say exactly what the engine decided; otherwise the code has a bug (BLOCK)."""
    try:
        _facts(case)
        lever_ids = tuple(format_lever_id(x["id"], x["value"]) for x in case["levers"])
        placeholders = case["placeholders"]
        same = (
            case["path"] == expected.path
            and case["evidence_quality"] == expected.evidence_quality
            and case["quadrant"] == expected.quadrant
            and lever_ids == expected.lever_ids
            and set(placeholders["available"]) == expected.placeholders
            and ("terms" in placeholders["required"]) == expected.requires_terms
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, LeverError) as exc:
        return [_issue(Code.CASE_INVALID, "case", f"{type(exc).__name__}: {exc}")]
    if not same:
        return [_issue(Code.CASE_INVALID, "case", "case does not match the engine decision")]
    return []


def _identifiers(case: Mapping[str, Any]) -> list[str]:
    """Identifiers a statement may name; they are removed before grounding, never allowed."""
    ids = [str(case["case_id"]), *_QUADRANT_LABELS]
    cluster = case.get("cluster")
    if isinstance(cluster, Mapping):
        ids.append(str(cluster["id"]))
    ids.extend(str(fact["metric"]) for fact in case["facts"])
    return ids


# ---- Checks ----------------------------------------------------------------------------------


def _check_echo(echo: DecisionEcho, expected: ExpectedDecision) -> list[ValidationIssue]:
    if (
        echo.path != expected.path
        or echo.quadrant != expected.quadrant
        or sorted(echo.lever_ids) != sorted(expected.lever_ids)
    ):
        return [_issue(Code.DECISION_ECHO, "decision_echo", "does not match the engine decision")]
    return []


def _check_statement(
    index: int, statement: Statement, facts: Mapping[str, _Fact], identifiers: Sequence[str]
) -> list[ValidationIssue]:
    where = f"operator_explanation[{index}]"
    errors: list[ValidationIssue] = []
    unknown = [fid for fid in statement.fact_ids if fid not in facts]
    if unknown:
        errors.append(_issue(Code.UNKNOWN_FACT, where, ", ".join(unknown)))
    cited = [facts[fid] for fid in statement.fact_ids if fid in facts]
    if statement.label == "SCORE" and not cited:
        errors.append(_issue(Code.MISSING_FACT, where, "a SCORE statement cites a fact"))
    if statement.label == "OBSERVED" and any(f.metric in SCORE_METRICS for f in cited):
        errors.append(_issue(Code.LABEL_MISMATCH, where, "a model score is not an observation"))
    allowed = {f.value for f in cited}
    text = strip_tokens(statement.text, identifiers)
    ungrounded = [str(n) for n in numbers_in_text(text) if n not in allowed]
    ungrounded += other_numeric_characters(text)
    ungrounded += number_words(text, "en", skip=PRONOUN_NUMBERS)
    if ungrounded:
        errors.append(_issue(Code.UNGROUNDED_NUMBER, where, ", ".join(ungrounded)))
    banned = banned_terms(statement.text)
    if banned:
        errors.append(_issue(Code.BANNED_TERM, where, ", ".join(banned)))
    return errors


def _check_explanation(
    statements: Sequence[Statement], case: Mapping[str, Any]
) -> list[ValidationIssue]:
    facts = _facts(case)
    identifiers = _identifiers(case)
    errors: list[ValidationIssue] = []
    for index, statement in enumerate(statements):
        errors.extend(_check_statement(index, statement, facts, identifiers))
    if language_of(" ".join(s.text for s in statements)) == "it":
        errors.append(_issue(Code.WRONG_LANGUAGE, "operator_explanation", "English expected"))
    return errors


def _check_customer(text: str, lang: Lang, expected: ExpectedDecision) -> list[ValidationIssue]:
    where = f"customer_message.{lang}"
    errors = [
        _issue(Code(p.code), where, p.detail)
        for p in placeholder_problems(text, expected.placeholders, expected.requires_terms)
    ]
    body = strip_placeholders(text)
    checks = (
        (Code.LITERAL_NUMBER, literal_numbers(body, lang)),
        (Code.LITERAL_UNIT, literal_units(body)),
        (Code.LEGAL_WORDING, legal_wording(body)),
        (Code.BANNED_TERM, banned_terms(body)),
    )
    errors.extend(_issue(code, where, ", ".join(found)) for code, found in checks if found)
    detected = language_of(body)
    if detected != lang:
        errors.append(_issue(Code.WRONG_LANGUAGE, where, f"detected {detected or 'unknown'}"))
    if word_count(text) > MAX_CUSTOMER_WORDS:
        errors.append(_issue(Code.TOO_LONG, where, f"more than {MAX_CUSTOMER_WORDS} words"))
    return errors


def validate(
    raw: str | Mapping[str, Any], expected: ExpectedDecision, case: Mapping[str, Any]
) -> ValidationResult:
    """Check a model output against the engine decision and the case it was given."""
    blocking = check_case(case, expected)
    if blocking:
        return ValidationResult(tuple(blocking), None)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        out = LlmOutput.model_validate(data)
    except ValueError as exc:  # invalid JSON or pydantic ValidationError
        return ValidationResult((_issue(Code.SCHEMA, "output", type(exc).__name__),), None)

    errors = _check_echo(out.decision_echo, expected)
    errors += _check_explanation(out.operator_explanation, case)
    messages = {"it": out.customer_message.it, "en": out.customer_message.en}
    for lang in LANGS:
        errors += _check_customer(messages[lang], lang, expected)
    return ValidationResult(tuple(errors), None if errors else out)


__all__ = [
    "ACTIONS",
    "Action",
    "Code",
    "ExpectedDecision",
    "LlmOutput",
    "Statement",
    "ValidationIssue",
    "ValidationResult",
    "check_case",
    "validate",
]
