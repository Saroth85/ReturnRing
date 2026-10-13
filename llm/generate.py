"""Generate a validated explanation and customer message for a decided case (spec 8.7, F1 step 5).

1. The case must match the engine decision, otherwise GenerationBlocked (a code bug, BLOCK).
2. The model answers; the validator checks the answer.
3. REGENERATE errors: one more answer, with the error codes as feedback.
4. TEMPLATE errors (decision echo), a second failure or an unavailable model: the fixed template.
The result says which path was taken (model, regenerated, template) for the audit log and KPIs.
The model only ever sees a copy of the case, so it cannot change what the validator checks.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from llm.client import LlmClient, LlmUnavailable
from llm.decision import ExpectedDecision
from llm.templates import template_output
from llm.validator import Action, LlmOutput, ValidationIssue, check_case, validate

MAX_MODEL_CALLS = 2


class GenerationPath(StrEnum):
    MODEL = "model"
    REGENERATED = "regenerated"
    TEMPLATE = "template"


class GenerationBlocked(RuntimeError):
    """The case does not match the decision, or a template fails validation: a code bug."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        super().__init__("; ".join(f"{i.code}: {i.detail}" for i in issues))
        self.issues = issues


@dataclass(frozen=True)
class Generation:
    output: LlmOutput
    path: GenerationPath
    model_calls: int
    issues: tuple[ValidationIssue, ...]  # every issue found on the way, for the audit log
    fallback_reason: str | None = None  # "decision_echo", "invalid_twice" or "unavailable"


def _template(
    case: Mapping[str, Any],
    expected: ExpectedDecision,
    calls: int,
    issues: list[ValidationIssue],
    reason: str,
) -> Generation:
    result = validate(template_output(case, expected), expected, case)
    if result.output is None:
        raise GenerationBlocked(result.errors)
    return Generation(result.output, GenerationPath.TEMPLATE, calls, tuple(issues), reason)


def generate(case: Mapping[str, Any], expected: ExpectedDecision, client: LlmClient) -> Generation:
    blocking = check_case(case, expected)
    if blocking:
        raise GenerationBlocked(tuple(blocking))
    issues: list[ValidationIssue] = []
    retry_codes: tuple[str, ...] = ()
    for call in range(1, MAX_MODEL_CALLS + 1):
        try:
            completion = client.complete(copy.deepcopy(dict(case)), retry_codes=retry_codes)
        except LlmUnavailable:
            return _template(case, expected, call - 1, issues, "unavailable")
        result = validate(completion.content, expected, case)
        if result.output is not None:
            path = GenerationPath.MODEL if call == 1 else GenerationPath.REGENERATED
            return Generation(result.output, path, call, tuple(issues))
        issues.extend(result.errors)
        if result.action is Action.BLOCK:
            raise GenerationBlocked(result.errors)
        if result.action is Action.TEMPLATE:
            return _template(case, expected, call, issues, "decision_echo")
        retry_codes = tuple(result.codes)
    return _template(case, expected, MAX_MODEL_CALLS, issues, "invalid_twice")
