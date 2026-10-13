"""Deterministic checks on Gemma's output before it reaches the UI (docs/spec.md 8.7).

Failure means: regenerate once, then fall back to a template. The model never changes the decision.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_CUSTOMER_WORDS = 120

BANNED_FOR_CUSTOMER = (
    "frode",
    "truff",
    "abus",
    "sospett",
    "fraud",
    "scam",
    "abuse",
    "suspic",
    "suspect",
)
BANNED_EVERYWHERE = ("stessa persona", "same person")

_NUMBER_RE = re.compile(r"(?<![\w{])\d+(?:[.,]\d+)?")
_PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")


class DecisionEcho(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: Literal["withdrawal", "guarantee"]
    quadrant: Literal["Q1", "Q2", "Q3", "Q4"] | None
    lever_ids: list[str]


class CustomerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    it: str = Field(max_length=900)
    en: str = Field(max_length=900)


class LlmOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_echo: DecisionEcho
    operator_explanation: str = Field(max_length=900)
    customer_message: CustomerMessage


@dataclass(frozen=True)
class ExpectedDecision:
    path: str
    quadrant: str | None
    lever_ids: list[str]


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    output: LlmOutput | None = None


def _normalize(num: str) -> str:
    try:
        return format(Decimal(num.replace(",", ".")).normalize(), "f")
    except InvalidOperation:  # pragma: no cover - regex guarantees a number
        return num


def numbers_in(obj: Any) -> set[str]:
    """Every number that appears in a case, in normalized form."""
    found: set[str] = set()
    if isinstance(obj, bool):
        return found
    if isinstance(obj, int | float | Decimal):
        found.add(_normalize(str(obj)))
    elif isinstance(obj, str):
        found.update(_normalize(n) for n in _NUMBER_RE.findall(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found |= numbers_in(k) | numbers_in(v)
    elif isinstance(obj, list | tuple):
        for v in obj:
            found |= numbers_in(v)
    return found


def validate(raw: str | dict[str, Any], expected: ExpectedDecision, case: Any) -> ValidationResult:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        out = LlmOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        return ValidationResult(ok=False, errors=[f"SCHEMA: {exc.__class__.__name__}"])

    errors: list[str] = []
    echo = out.decision_echo
    if (
        echo.path != expected.path
        or echo.quadrant != expected.quadrant
        or sorted(echo.lever_ids) != sorted(expected.lever_ids)
    ):
        errors.append("DECISION_ECHO: output does not match the engine decision")

    for lang, text in (("it", out.customer_message.it), ("en", out.customer_message.en)):
        lower = text.lower()
        if any(term in lower for term in BANNED_FOR_CUSTOMER + BANNED_EVERYWHERE):
            errors.append(f"BANNED_TERM: customer message ({lang})")
        if re.search(r"\d", _PLACEHOLDER_RE.sub("", text)):
            errors.append(f"LITERAL_NUMBER: use placeholders in the customer message ({lang})")
        if len(text.split()) > MAX_CUSTOMER_WORDS:
            errors.append(f"TOO_LONG: customer message ({lang})")

    explanation = out.operator_explanation
    if any(term in explanation.lower() for term in BANNED_EVERYWHERE):
        errors.append("BANNED_TERM: operator explanation")
    allowed = numbers_in(case)
    unknown = {_normalize(n) for n in _NUMBER_RE.findall(explanation)} - allowed
    if unknown:
        errors.append(f"UNGROUNDED_NUMBER: {sorted(unknown)}")

    return ValidationResult(ok=not errors, errors=errors, output=out)
