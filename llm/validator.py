"""Deterministic checks on Gemma's output before it reaches the UI (docs/spec.md 8.7, ADR-14).

Every error carries a code and the action of the 8.7 table:
- REGENERATE: ask the model once more, then use the template;
- TEMPLATE: use the fixed template at once (decision echo mismatch);
- BLOCK: a bug in the code (the case does not match the engine decision); nothing is shown.
The model never changes the decision (CLAUDE.md rule 1). Numbers in the operator explanation are
grounded only on code-built facts the statement cites; customer text, policy text and identifiers
never feed grounding. A statement that names a quadrant, a lever value or a restriction other than
the engine's is a decision echo mismatch, and a statement with a letter of another script is
regenerated (adversarial review of 13 Oct, rounds 1 and 2). The model writes no customer prose:
it picks phrasebook sentence ids, which must be known, unique, in their slot and allowed for the
decision (llm/phrasebook.py, round 2, finding R1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from contracts.levers import LEVERS, LeverError, format_lever_id
from llm.decision import ExpectedDecision
from llm.phrasebook import (
    MAX_SENTENCES,
    MIN_SENTENCES,
    PHRASEBOOK,
    context_of,
    selection_problems,
)
from llm.text import (
    PRONOUN_NUMBERS,
    QUADRANT_AXES,
    banned_terms,
    fold,
    foreign_letters,
    is_dash,
    language_of,
    number_modifiers,
    number_words,
    numbers_in_text,
    other_numeric_characters,
    quadrant_axes,
    quadrant_labels,
    quadrant_positions,
    restriction_words,
    strip_tokens,
    word_set,
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
    UNKNOWN_FACT = "UNKNOWN_FACT"
    MISSING_FACT = "MISSING_FACT"
    LABEL_MISMATCH = "LABEL_MISMATCH"
    UNGROUNDED_NUMBER = "UNGROUNDED_NUMBER"
    BANNED_TERM = "BANNED_TERM"
    SCRIPT = "SCRIPT"
    SENTENCE_ID = "SENTENCE_ID"
    WRONG_LANGUAGE = "WRONG_LANGUAGE"


# The 8.7 table: echo (also a quadrant, lever value or restriction named in the explanation) ->
# template; numbers, banned terms, letters of other scripts, phrasebook ids and language ->
# regenerate, then template; the legal clause and any mismatch between case and decision ->
# blocking error.
ACTIONS: dict[Code, Action] = {
    Code.SCHEMA: Action.REGENERATE,
    Code.CASE_INVALID: Action.BLOCK,
    Code.DECISION_ECHO: Action.TEMPLATE,
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


# ---- Levers named in the operator explanation (adversarial review of 13 Oct, finding 3; round 2,
# finding R2) -------------------------------------------------------------------------------------

# Lever names: the catalogue id as words ("instant refund"), its camelCase form ("instantRefund")
# and a few aliases, in English and Italian. A name followed by a value ("instant_refund:false",
# "instant refund is off", "return window of 30 days") or preceded by one ("no instant refund",
# "the customer keeps instant refund") is a claim about the decision. A negation inverts the value
# ("coupons are not disabled" claims coupons_enabled:true); a value that cannot be read, or that
# is contradicted right after ("false? No, true"), is the unknown value "?", never the engine's.
_LEVER_ALIASES: dict[str, tuple[str, ...]] = {
    "return_window_days": ("return window", "finestra di reso", "finestra reso"),
    "free_return_shipping": ("return shipping", "spedizione del reso", "spedizione reso"),
    "instant_refund": ("instant refunds", "rimborso istantaneo", "rimborsi istantanei"),
    "free_size_exchange": ("size exchange", "size exchanges", "cambio taglia", "cambi taglia"),
    "coupons_enabled": (
        "coupons",
        "coupon",
        "discount codes",
        "discount code",
        "codici sconto",
        "codice sconto",
    ),
    "priority_size_exchange": ("priority exchange", "cambio taglia prioritario"),
    "transparency_notice": ("avviso di trasparenza",),
    "size_advice": ("consiglio di taglia", "consiglio taglia"),
}
# Names whose values are the opposite of the lever id ("cluster coupons disabled" is true when the
# lever cluster_coupons_disabled is true, "cluster coupons enabled" when it is false).
_INVERTED_ALIASES: dict[str, tuple[str, ...]] = {
    "cluster_coupons_disabled": ("cluster coupons", "coupon del cluster"),
}
_LEVER_NAMES: tuple[tuple[tuple[str, ...], str, bool], ...] = tuple(
    sorted(
        [
            (tuple(name.split()), lever.id, False)
            for lever in LEVERS
            for name in (lever.id.replace("_", " "), *_LEVER_ALIASES.get(lever.id, ()))
        ]
        + [
            (tuple(name.split()), lever_id, True)
            for lever_id, names in _INVERTED_ALIASES.items()
            for name in names
        ],
        key=lambda item: -len(item[0]),
    )
)
_INTEGER_LEVERS = frozenset(lever.id for lever in LEVERS if lever.value_type == "integer")
UNKNOWN = "?"
_TRUE_WORDS = word_set(
    "true yes on enabled enable active available allowed granted kept vero attivo attiva attivi "
    "attive abilitato abilitata abilitati acceso accesa disponibile disponibili"
)
_FALSE_WORDS = word_set(
    "false off disabled disable inactive unavailable removed blocked withdrawn revoked denied "
    "suspended falso disattivato disattivata disattivati disabilitato disabilitata spento spenta "
    "rimosso rimossa tolto tolta"
)
_NEGATIONS = word_set("not no never non")
# Words right after a value that contradict it ("false? No, true").
_CONTRADICTIONS = word_set("true false yes no not never vero falso")
_TRUE_BEFORE = word_set("enabled granted keeps keep kept retains retain gets get regains")
_FALSE_BEFORE = word_set(
    "no without disabled removed revoked blocked withdrawn pays pay paid senza nessun nessuna"
)
_ARTICLES = word_set("the a an any its their for il lo la i gli le del della dello per")
# The window changed in words ("shortened", "extended"): a claim whose value cannot be read.
_CHANGE_WORDS = word_set(
    "shortened reduced shorter cut extended longer lengthened increased raised decreased lowered "
    "ridotta ridotto accorciata accorciato estesa esteso allungata allungato aumentata aumentato"
)
_FILLERS = word_set(
    "is are was were be been being has have had set to now still at of the a an equal equals "
    "changed turned switched kept stay stays remain remains lever levers code codes can could "
    "will would shall should must may might do does did ca wo e resta restano rimane rimangono"
)
_KEEP_WORDS = word_set("kept")
_FILLER_MARKS = frozenset(":=>→⇒()[]\"'`")
_LEVER_TOKEN_RE = re.compile(r"[a-z]+|[0-9]+|[^\sa-z0-9]")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_NOT_RE = re.compile(r"n['’]t\b")
_CANNOT_RE = re.compile(r"\bcannot\b")


def _lever_tokens(text: str) -> list[str]:
    folded = fold(_CAMEL_RE.sub(" ", text), " ")
    folded = _CANNOT_RE.sub("can not", _NOT_RE.sub(" not", folded))
    folded = "".join(" " if ch == "_" or is_dash(ch) else ch for ch in folded)
    return _LEVER_TOKEN_RE.findall(folded)


def _name_at(tokens: Sequence[str], index: int) -> tuple[tuple[str, ...], str, bool] | None:
    return next(
        (item for item in _LEVER_NAMES if tuple(tokens[index : index + len(item[0])]) == item[0]),
        None,
    )


def _skip_fillers(tokens: Sequence[str], index: int) -> tuple[int, bool]:
    kept = False
    while index < len(tokens) and (tokens[index] in _FILLERS or tokens[index] in _FILLER_MARKS):
        kept = kept or tokens[index] in _KEEP_WORDS
        index += 1
    return index, kept


def _contradicted(tokens: Sequence[str], index: int) -> bool:
    """A yes/no word right after a value, punctuation aside, that does not start a negated lever
    name ("off, no instant refund")."""
    while index < len(tokens) and not tokens[index].isalnum():
        index += 1
    if index >= len(tokens) or tokens[index] not in _CONTRADICTIONS:
        return False
    return _name_at(tokens, index + 1) is None


def _value_after(tokens: Sequence[str], start: int, lever_id: str) -> str | None:
    index, kept = _skip_fillers(tokens, start)
    token = tokens[index] if index < len(tokens) else ""
    after = index + 1  # where a contradiction would start
    if token in _NEGATIONS:
        index, _ = _skip_fillers(tokens, after)
        word = tokens[index] if index < len(tokens) else ""
        if lever_id in _INTEGER_LEVERS:
            return UNKNOWN
        if word in _TRUE_WORDS:
            value, after = "false", index + 1
        elif word in _FALSE_WORDS:
            value, after = "true", index + 1
        elif not word.isalnum():  # "instant refund: no."
            value, after = "false", index
        else:
            return UNKNOWN
    elif lever_id in _INTEGER_LEVERS:
        if not token.isdigit():
            return UNKNOWN if token in _CHANGE_WORDS else None
        value = token
    elif token in _TRUE_WORDS:
        value = "true"
    elif token in _FALSE_WORDS:
        value = "false"
    elif kept:  # "instant refund is kept"
        value, after = "true", index
    else:
        return None
    return UNKNOWN if _contradicted(tokens, after) else value


def _value_before(tokens: Sequence[str], start: int, lever_id: str) -> str | None:
    index = start - 1
    while index >= 0 and tokens[index] in _ARTICLES:
        index -= 1
    token = tokens[index] if index >= 0 else ""
    if lever_id in _INTEGER_LEVERS:
        return None
    if token in _TRUE_BEFORE:
        return "true"
    return "false" if token in _FALSE_BEFORE else None


_INVERT = {"true": "false", "false": "true"}


def lever_claims(text: str) -> list[str]:
    """Lever values a text states, as "id:value" strings, for example "instant_refund:false";
    "?" when the value cannot be read. A name whose id ends in "enabled" or "disabled" states
    the value true by itself."""
    tokens = _lever_tokens(text)
    claims: list[str] = []
    index = 0
    while index < len(tokens):
        found = _name_at(tokens, index)
        if found is None:
            index += 1
            continue
        name, lever_id, inverted = found
        value = _value_after(tokens, index + len(name), lever_id)
        if value is None:
            value = _value_before(tokens, index, lever_id)
        if value is None and name[-1] in ("enabled", "disabled"):
            value = "true"
        if value is not None:
            claims.append(f"{lever_id}:{_INVERT.get(value, value) if inverted else value}")
        index += len(name)
    return claims


# ---- Checks ----------------------------------------------------------------------------------


def _check_echo(echo: DecisionEcho, expected: ExpectedDecision) -> list[ValidationIssue]:
    if (
        echo.path != expected.path
        or echo.quadrant != expected.quadrant
        or sorted(echo.lever_ids) != sorted(expected.lever_ids)
    ):
        return [_issue(Code.DECISION_ECHO, "decision_echo", "does not match the engine decision")]
    return []


def _check_mentions(
    where: str, text: str, entity_ids: Sequence[str], expected: ExpectedDecision
) -> list[ValidationIssue]:
    """A quadrant, by label or by its risk and value levels, and a lever value named in a
    statement must be the engine's. Without restrictive levers (guarantee, insufficient
    evidence, Q1, Q2), a restriction or a place in the matrix may not be named either."""
    named = strip_tokens(text, entity_ids)
    other = [label for label in quadrant_labels(named) if label != expected.quadrant]
    axes = QUADRANT_AXES.get(expected.quadrant or "", frozenset())
    other += [axis for axis in quadrant_axes(named) if axis not in axes]
    if not expected.restrictive:
        other += restriction_words(named) + quadrant_positions(named)
    other += [claim for claim in lever_claims(text) if claim not in expected.lever_ids]
    if other:
        return [_issue(Code.DECISION_ECHO, where, f"not the engine decision: {', '.join(other)}")]
    return []


def _check_statement(
    index: int,
    statement: Statement,
    facts: Mapping[str, _Fact],
    identifiers: Sequence[str],
    entity_ids: Sequence[str],
    expected: ExpectedDecision,
) -> list[ValidationIssue]:
    where = f"operator_explanation[{index}]"
    errors = _check_mentions(where, statement.text, entity_ids, expected)
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
    ungrounded += number_modifiers(text)
    if ungrounded:
        errors.append(_issue(Code.UNGROUNDED_NUMBER, where, ", ".join(ungrounded)))
    banned = banned_terms(statement.text)
    if banned:
        errors.append(_issue(Code.BANNED_TERM, where, ", ".join(banned)))
    foreign = foreign_letters(strip_tokens(statement.text, entity_ids))
    if foreign:
        errors.append(_issue(Code.SCRIPT, where, "".join(foreign)))
    return errors


def _check_explanation(
    statements: Sequence[Statement], case: Mapping[str, Any], expected: ExpectedDecision
) -> list[ValidationIssue]:
    facts = _facts(case)
    identifiers = _identifiers(case, expected)
    entity_ids = _entity_ids(case)
    errors: list[ValidationIssue] = []
    for index, statement in enumerate(statements):
        errors.extend(_check_statement(index, statement, facts, identifiers, entity_ids, expected))
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
    "lever_claims",
    "validate",
]
