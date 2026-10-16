"""A shared-model reply must be rejected before the API sees an impossible score label."""

import pytest

from llm.client import MockClient
from llm.generate import generate
from llm.templates import output_from_case
from llm.validator import Action, Code, validate
from tests.llm_factories import make


@pytest.mark.parametrize("name", ["guarantee", "insufficient", "guarantee_insufficient"])
def test_unscored_cases_reject_score_statements_and_regenerate(name: str) -> None:
    built = make(name)
    wrong = output_from_case(built.case)
    wrong["operator_explanation"] = [
        {
            "label": "SCORE",
            "fact_ids": ["F1"],
            "text": "The delivery date contributes to the score",
        }
    ]
    checked = validate(wrong, built.expected, built.case)
    assert Code.LABEL_MISMATCH in {error.code for error in checked.errors}
    assert checked.action is Action.REGENERATE
    result = generate(built.case, built.expected, MockClient([wrong]))
    assert result.path == "regenerated"
    assert all(statement.label != "SCORE" for statement in result.output.operator_explanation)
