"""Deterministic checks on Gemma's output before it reaches the UI (docs/spec.md 8.7, ADR-14).

Every error carries a code and the action of the 8.7 table:
- REGENERATE: ask the model once more, then use the template;
- TEMPLATE: use the fixed template at once (decision echo mismatch);
- BLOCK: a bug in the code (the case does not match the engine decision); nothing is shown.
The model never changes the decision (CLAUDE.md rule 1). Numbers in the operator explanation are
grounded only on code-built facts the statement cites, with the unit of the fact or none
around them; customer text, policy text and identifiers never feed grounding. A statement that
names a quadrant other than the engine's, or a level of the risk score or of the customer value
(net margin, "profitable") that is not the engine's, is a decision echo mismatch. A statement
that talks about levers, their state or scope (enabled, available, future orders), terms,
refunds, restrictions, decisions or the risk and value levels of the quadrant at all, also
negated or as a middle level, is regenerated (TERMS_TOPIC): the interface shows the engine
decision and its levers from code, so no lever value is read from words. A character outside the
allowlist is regenerated too (adversarial review of 13 Oct, rounds 1 to 4). The model writes no
customer prose: it picks phrasebook sentence ids, which must be known, unique, in their slot,
allowed for the decision, coherent and not too many (two extras in Q3 and Q4): llm/phrasebook.py,
round 2, finding R1; round 3, finding T4; round 4, finding U2.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from contracts.levers import LeverError, format_lever_id
from llm.decision import ExpectedDecision
from llm.phrasebook import (
    MAX_SENTENCES,
    MIN_SENTENCES,
    PHRASEBOOK,
    context_of,
    selection_problems,
)
from llm.text import (
    IDENTIFIER_PLACEHOLDER,
    PRONOUN_NUMBERS,
    QUADRANT_AXES,
    banned_terms,
    foreign_characters,
    grounding_problems,
    language_of,
    number_words,
    operator_topics,
    other_numeric_characters,
    quadrant_labels,
    quadrant_levels,
    roman_numerals,
    strip_tokens,
)

MAX_STATEMENTS = 8
MAX_FACTS_PER_STATEMENT = 4
MAX_STATEMENT_CHARS = 300
MAX_LEVER_IDS = 9
# Facts that are model outputs: an OBSERVED statement may not cite them.
SCORE_METRICS = frozenset({"risk_score"})


class Action(StrEnum):
    REGENERATE = "REGENERATE"
    TEMPLATE = "TEMPLATE"
    BLOCK = "BLOCK"


_ACTION_RANK = {Action.REGENERATE: 0, Action.TEMPLATE: 1, Action.BLOCK: 2}


class Code(StrEnum):
    SCHEMA = "SCHEMA"
    CASE_INVALID = "CASE_INVALID"
    DECISION_ECHO = "DECISION_ECHO"
    TERMS_TOPIC = "TERMS_TOPIC"
    UNKNOWN_FACT = "UNKNOWN_FACT"
    MISSING_FACT = "MISSING_FACT"
    LABEL_MISMATCH = "LABEL_MISMATCH"
    UNGROUNDED_NUMBER = "UNGROUNDED_NUMBER"
    BANNED_TERM = "BANNED_TERM"
    SCRIPT = "SCRIPT"
    SENTENCE_ID = "SENTENCE_ID"
    WRONG_LANGUAGE = "WRONG_LANGUAGE"


# The 8.7 table: echo (also another quadrant or risk score level named in the explanation) ->
# template; levers, terms and levels named in the explanation, numbers, banned terms, characters
# outside the allowlist, phrasebook ids and language -> regenerate, then template; the legal
# clause and any mismatch between case and decision -> blocking error.
ACTIONS: dict[Code, Action] = {
    Code.SCHEMA: Action.REGENERATE,
    Code.CASE_INVALID: Action.BLOCK,
    Code.DECISION_ECHO: Action.TEMPLATE,
    Code.TERMS_TOPIC: Action.REGENERATE,
    Code.UNKNOWN_FACT: Action.REGENERATE,
    Code.MISSING_FACT: Action.REGENERATE,
    Code.LABEL_MISMATCH: Action.REGENERATE,
    Code.UNGROUNDED_NUMBER: Action.REGENERATE,
    Code.BANNED_TERM: Action.REGENERATE,
    Code.SCRIPT: Action.REGENERATE,
    Code.SENTENCE_ID: Action.REGENERATE,
    Code.WRONG_LANGUAGE: Action.REGENERATE,
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


def _known_sentence(sentence_id: str) -> str:
    if sentence_id not in PHRASEBOOK:
        raise ValueError(f"unknown sentence id {sentence_id!r}")
    return sentence_id


SentenceId = Annotated[str, AfterValidator(_known_sentence)]  # the enum of the JSON schema


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
    """Phrasebook ids only: code writes the message (llm/phrasebook.py)."""

    model_config = ConfigDict(extra="forbid")
    sentence_ids: list[SentenceId] = Field(min_length=MIN_SENTENCES, max_length=MAX_SENTENCES)


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


def _entity_ids(case: Mapping[str, Any]) -> list[str]:
    """The case and cluster ids: removed before quadrants are read ("RET-Q1" is not Q1)."""
    ids = [str(case["case_id"])]
    cluster = case.get("cluster")
    if isinstance(cluster, Mapping):
        ids.append(str(cluster["id"]))
    return ids


def _identifiers(case: Mapping[str, Any], expected: ExpectedDecision) -> list[str]:
    """Identifiers a statement may name; they are removed before grounding, never allowed. Only
    the engine's quadrant is one: any other quadrant is a decision echo mismatch."""
    ids = _entity_ids(case)
    if expected.quadrant is not None:
        ids.append(expected.quadrant)
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


_MEASURES = {"risk": "risk score", "value": "customer value"}


def _check_decision(
    where: str, text: str, entity_ids: Sequence[str], expected: ExpectedDecision
) -> list[ValidationIssue]:
    """A quadrant named by its label, and a level of the risk score or of the customer value
    in other words (net margin, profitable), must be the engine's (adversarial review of 13 Oct,
    finding 3; round 3, finding T7; round 4, finding U5)."""
    named = strip_tokens(text, entity_ids)
    other = [label for label in quadrant_labels(named) if label != expected.quadrant]
    axes = QUADRANT_AXES.get(expected.quadrant or "", frozenset())
    other += [
        f"{_MEASURES[axis]} {level}"
        for kind, axis, level in quadrant_levels(named)
        if kind == "score" and level != "?" and f"{axis}:{level}" not in axes
    ]
    if other:
        return [_issue(Code.DECISION_ECHO, where, f"not the engine decision: {', '.join(other)}")]
    return []


def _check_topics(
    where: str, text: str, identifiers: Sequence[str], expected: ExpectedDecision
) -> list[ValidationIssue]:
    """Levers, terms, refunds, restrictions, decisions and the risk and value levels of the
    quadrant are not topics of an operator statement, whatever their value (round 3, findings
    T7 and T8; round 4, findings U5 and U6): metric names are removed first, a level of unknown
    side (negated, middle) counts, and "no quadrant" is an observation only when the engine
    assigned none."""
    plain = strip_tokens(text, identifiers)
    found = operator_topics(plain, no_quadrant=expected.quadrant is None)
    found += [
        f"{_MEASURES[axis]} {lv}"
        for kind, axis, lv in quadrant_levels(plain)
        if (kind, lv) == ("score", "?")
    ]
    if found:
        return [_issue(Code.TERMS_TOPIC, where, ", ".join(found))]
    return []


def _check_statement(
    index: int,
    statement: Statement,
    facts: Mapping[str, _Fact],
    identifiers: Sequence[str],
    entity_ids: Sequence[str],
    metrics: Sequence[str],
    expected: ExpectedDecision,
) -> list[ValidationIssue]:
    where = f"operator_explanation[{index}]"
    errors = _check_decision(where, statement.text, entity_ids, expected)
    errors += _check_topics(where, statement.text, [*entity_ids, *metrics], expected)
    unknown = [fid for fid in statement.fact_ids if fid not in facts]
    if unknown:
        errors.append(_issue(Code.UNKNOWN_FACT, where, ", ".join(unknown)))
    cited = [facts[fid] for fid in statement.fact_ids if fid in facts]
    if statement.label == "SCORE" and not cited:
        errors.append(_issue(Code.MISSING_FACT, where, "a SCORE statement cites a fact"))
    if statement.label == "OBSERVED" and any(f.metric in SCORE_METRICS for f in cited):
        errors.append(_issue(Code.LABEL_MISMATCH, where, "a model score is not an observation"))
    text = strip_tokens(statement.text, identifiers, f" {IDENTIFIER_PLACEHOLDER} ")
    ungrounded = grounding_problems(text, [(f.metric, f.value) for f in cited])
    ungrounded += other_numeric_characters(text)
    ungrounded += number_words(text, "en", skip=PRONOUN_NUMBERS)
    ungrounded += roman_numerals(text)
    if ungrounded:
        errors.append(_issue(Code.UNGROUNDED_NUMBER, where, ", ".join(ungrounded)))
    banned = banned_terms(strip_tokens(statement.text, entity_ids))
    if banned:
        errors.append(_issue(Code.BANNED_TERM, where, ", ".join(banned)))
    foreign = foreign_characters(statement.text)
    if foreign:
        errors.append(_issue(Code.SCRIPT, where, "".join(foreign)))
    return errors


def _check_explanation(
    statements: Sequence[Statement], case: Mapping[str, Any], expected: ExpectedDecision
) -> list[ValidationIssue]:
    facts = _facts(case)
    identifiers = _identifiers(case, expected)
    entity_ids = _entity_ids(case)
    metrics = [fact.metric for fact in facts.values()]
    errors: list[ValidationIssue] = []
    for index, statement in enumerate(statements):
        errors.extend(
            _check_statement(index, statement, facts, identifiers, entity_ids, metrics, expected)
        )
    if language_of(" ".join(s.text for s in statements)) == "it":
        errors.append(_issue(Code.WRONG_LANGUAGE, "operator_explanation", "English expected"))
    return errors


def _check_sentences(ids: Sequence[str], expected: ExpectedDecision) -> list[ValidationIssue]:
    problems = selection_problems(ids, context_of(expected))
    if problems:
        return [_issue(Code.SENTENCE_ID, "customer_message.sentence_ids", "; ".join(problems))]
    return []


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
    errors += _check_explanation(out.operator_explanation, case, expected)
    errors += _check_sentences(out.customer_message.sentence_ids, expected)
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
