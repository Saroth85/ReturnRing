"""Regression probes of the closing verification of b4ee1e4, round 6 (findings W1 to W7).

Every correct statement and answer the verifier quoted is in the must-accept corpus
(tests/test_llm_corpus.py), and so are the correct statements that break a rule of the prompt
(W3, the number words of W6): those regenerate with the words in the feedback, never the
template. This file holds the structural safety net (a risk or value level read from the words
is never a decision echo), the narrower readings of levels (W2, W3), flag and caution (W5), the
grounding words of W6 and the agent's comparison pairs, ordinals and lever values (W1, W4), each
with the catches that must stay, and the measurement behind the customer wording of W7.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from typing import Any

import pytest

from llm import phrasebook
from llm.agent import lever_values
from llm.client import MockClient
from llm.generate import GenerationPath, generate
from llm.messages import template_fields
from llm.phrasebook import allowed_sentences, context_of, mandatory_body, selection_problems
from llm.templates import template_output
from llm.text import LANGS, banned_terms, grounding_problems, level_mentions, word_count
from llm.validator import Action, Code, retry_feedback
from tests.llm_factories import SCENARIOS, make
from tests.test_llm_corpus import (
    COUPONS,
    DAYS,
    MARGIN,
    QUESTIONS,
    RETURNS,
    RISK,
    SIZE,
    ask,
    check,
    golden,
)
from tests.test_llm_review3 import (
    REFUND,
    SWEEP_BASES,
    assert_rejected,
    decided,
    unordered_selections,
)

# ---- W2, W3: a level read from the words regenerates, an explicit label is a decision echo -----

# A level of the risk score or the customer value that is not the engine's: (case, metrics, text,
# the words the feedback names).
CONTRADICTIONS = [
    ("giulia", (), "The risk score is high.", "risk score: high"),
    ("giulia", (), "The risk score is higher than the threshold.", "risk score: higher"),
    ("marco", RISK, "The risk score (0.87) is low.", "risk score: low"),
    ("marco", (), "The customer is highly profitable.", "customer value: profitable"),
    ("marco", (), "Customer net margin is substantial.", "customer value: substantial"),
    ("rossi", (), "The shared address pushes the risk score into the high range.", "high"),
    ("guarantee", (), "The risk score is high.", "risk score: high"),
    ("insufficient", (), "The risk score is low.", "risk score: low"),
]


@pytest.mark.parametrize("name, metrics, text, words", CONTRADICTIONS)
def test_w2_w3_a_level_read_from_the_words_regenerates_with_its_words(
    name: str, metrics: tuple[str, ...], text: str, words: str
) -> None:
    built = make(name) if name in ("guarantee", "insufficient") else golden(name)
    result = check(built, text, metrics, "HYPOTHESIS")
    assert result.codes == ["TERMS_TOPIC"], result.errors
    assert result.action is Action.REGENERATE
    assert any(words in item for item in retry_feedback(result.errors)), result.errors


def test_w2_generate_regenerates_a_level_with_its_words_in_the_feedback() -> None:
    built = golden("giulia")
    out = template_output(built.case, built.expected)
    out["operator_explanation"].append(
        {"label": "HYPOTHESIS", "fact_ids": [], "text": "The risk score is high."}
    )
    mock = MockClient([out])
    result = generate(built.case, built.expected, mock)
    assert result.path is GenerationPath.REGENERATED and result.model_calls == 2
    index = len(out["operator_explanation"]) - 1
    assert mock.calls[1] == (f"TERMS_TOPIC in operator_explanation[{index}]: risk score: high",)


@pytest.mark.parametrize(
    "name, text",
    [
        ("giulia", "The case sits in Q4."),
        ("giulia", "The case sits in QIV."),
        ("giulia", "The case is in the fourth quadrant."),
        ("marco", "The rules engine placed the case in quadrant 1."),
        ("elena", "The case is in Q3."),
    ],
)
def test_w2_an_explicit_quadrant_other_than_the_engines_is_still_a_decision_echo(
    name: str, text: str
) -> None:
    result = check(golden(name), text)
    assert Code.DECISION_ECHO.value in result.codes
    assert result.action is Action.TEMPLATE


@pytest.mark.parametrize(
    "text, mentions",
    [
        # comparatives and direction verbs (W2)
        ("The shared card pushes the risk score higher.", []),
        ("The risk score is higher because of the shared card.", []),
        ("The shared card leads to a higher risk score.", []),
        ("The return timing lowers the risk score a little.", []),
        ("It raises the risk score by a considerable step.", []),
        ("The shared card lifts the risk score by a small margin.", []),
        ("This is a higher-risk customer.", [("axis", "risk", "high", "higher")]),
        ("The risk score is higher than the threshold.", [("score", "risk", "high", "higher")]),
        ("The risk score is lower than average.", [("score", "risk", "low", "lower")]),
        ("It pushes the risk score into the high range.", [("score", "risk", "high", "high")]),
        ("The cards make the risk score low.", [("score", "risk", "low", "low")]),
        ("The risk score is a little high.", [("score", "risk", "high", "high")]),
        # negations before the level word in its stretch, far from, nowhere near (W3)
        ("The risk score is not in the high range.", [("score", "risk", "?", "not high")]),
        (
            "The address does not push the risk score into the high range.",
            [("score", "risk", "?", "not high")],
        ),
        ("The risk score isn't that low.", [("score", "risk", "?", "isn t low")]),
        ("The risk score is far from high.", [("score", "risk", "?", "far from high")]),
        (
            "The risk score, far from the high range, is 0.18.",
            [("score", "risk", "?", "far from high")],
        ),
        ("The risk score is nowhere near high.", [("score", "risk", "?", "nowhere near high")]),
        ("The customer is not, at 0.18, high risk.", [("axis", "risk", "high", "high")]),
        (
            "The risk score is high (0.87): an estimate, not a cause.",
            [("score", "risk", "high", "high")],
        ),
        ("The risk score is moderate.", [("score", "risk", "?", "moderate")]),
        ("The risk score is near the threshold.", [("score", "risk", "?", "threshold")]),
        ("The customer is not reliable.", [("axis", "risk", "?", "not reliable")]),
    ],
)
def test_w2_w3_level_readings(text: str, mentions: list[tuple[str, str, str, str]]) -> None:
    assert level_mentions(text) == mentions


# ---- W5: flag and caution only about a person or the case ------------------------------------

W5_REJECTED = [
    "The case was flagged for review.",
    "The customer has been flagged.",
    "Flag it for a manual check.",
    "Flag the linked accounts.",
    "This is a red flag.",
    "Proceed with caution.",
    "Treat the linked accounts with caution.",
    "The customer should be handled cautiously.",
]


@pytest.mark.parametrize("text", W5_REJECTED)
def test_w5_flag_and_caution_about_a_person_or_the_case_are_topics(text: str) -> None:
    assert_rejected(check(golden("marco"), text, (), "HYPOTHESIS"), "TERMS_TOPIC")


@pytest.mark.parametrize(
    "name, metrics, text",
    [
        ("marco", COUPONS, "The model flags cluster_welcome_coupons (7) as the top contributor."),
        ("giulia", RETURNS, "The model also flags returns_90d (4) in the score."),
        ("giulia", (), "The estimate should be read with caution."),
        ("marco", RISK, "Interpret the risk score (0.87) with caution, as an estimate."),
    ],
)
def test_w5_the_model_flags_a_factor_and_the_score_is_read_with_caution(
    name: str, metrics: tuple[str, ...], text: str
) -> None:
    result = check(golden(name), text, metrics, "SCORE" if metrics else "HYPOTHESIS")
    assert result.ok, result.errors


# ---- W6: contribution verbs, conjunctions, short, coupon counts, the withdrawal period ---------


@pytest.mark.parametrize(
    "name, metrics, text",
    [
        ("marco", DAYS, "days_since_delivery (6) plays a small part in the score."),
        ("marco", COUPONS, "cluster_welcome_coupons (7) dominates the score."),
        ("rossi", RISK, "The risk score is low (0.22) despite the shared address."),
        ("rossi", RISK, "The risk score is low (0.22) although the accounts share an address."),
        ("giulia", RISK, "The risk score of 0.18 may change with more returns."),
        ("elena", DAYS, "The return notice came 6 days after delivery; the sleeves are short."),
        ("marco", COUPONS, "7 coupons were redeemed by the linked accounts."),
        ("missing", (), "The request came within the withdrawal period."),
        ("rossi", (), "The return belongs to the Rossi family cluster F-ROSSI."),
    ],
)
def test_w6_plain_words_pass(name: str, metrics: tuple[str, ...], text: str) -> None:
    result = check(golden(name), text, metrics, "SCORE" if metrics == RISK else "OBSERVED")
    assert result.ok, result.errors


@pytest.mark.parametrize(
    "name, metrics, text, code",
    [
        ("marco", MARGIN, "Customer net margin: 42.1 EUR short.", "UNGROUNDED_NUMBER"),
        ("marco", MARGIN, "Customer net margin is short by 42.1 EUR.", "UNGROUNDED_NUMBER"),
        ("marco", MARGIN, "Customer net margin: 42.1 EUR even.", "UNGROUNDED_NUMBER"),
        ("marco", MARGIN, "Customer net margin: 42.1 EUR, give or take.", "UNGROUNDED_NUMBER"),
        ("marco", SIZE, "The 7 linked accounts used 7 coupons.", "TERMS_TOPIC"),
        ("marco", COUPONS, "The 7 coupons remain available to the cluster.", "TERMS_TOPIC"),
        ("marco", COUPONS, "The coupon terms changed after 7 redemptions.", "TERMS_TOPIC"),
        ("giulia", (), "The withdrawal window is open.", "TERMS_TOPIC"),
        ("rossi", (), "The linked accounts belong to the Rossi family.", "BANNED_TERM"),
        ("rossi", (), "The accounts belong to one person in the cluster.", "BANNED_TERM"),
    ],
)
def test_w6_catches_stay(name: str, metrics: tuple[str, ...], text: str, code: str) -> None:
    assert_rejected(check(golden(name), text, metrics, "OBSERVED"), code)


def test_w6_short_is_a_sign_only_next_to_a_number() -> None:
    cited = [("value_eur_12m", Decimal("42.1"))]
    assert "short" in grounding_problems("Margin: 42.1 EUR short.", cited)
    assert grounding_problems("Margin: 42.1 EUR, a short history.", cited) == []
    assert banned_terms("The return belongs to the Rossi family cluster.") == []


# ---- W1, W4: the agent's comparison pairs, ordinals and lever values -------------------------

AGENT_CATCHES = [
    ("RET-MARCO", "rossi", "R-MARCO has 3 vs 7 linked accounts."),
    ("RET-MARCO", "rossi", "F-ROSSI has a return rate of 0.3."),
    ("RET-MARCO", "rossi", "The cluster (F-ROSSI) has 7 linked accounts."),
    ("RET-MARCO", "rossi", "F-ROSSI has 3 linked accounts and 3 distinct cards, over 9 days."),
    ("RET-MARCO", "rossi", "R-MARCO has 2 vs 3 linked accounts."),
    ("RET-MARCO", "rossi", "R-MARCO has 7 linked accounts instead of 2."),
    ("RET-MARCO", "rossi", "R-MARCO has 7 linked accounts (F-ROSSI: 4)."),
    ("RET-MARCO", "rossi", "R-MARCO vs F-ROSSI: 3 vs 7 linked accounts."),
    ("RET-MARCO", "rossi", "R-MARCO ha 3 account collegati contro 7."),
    ("RET-MARCO", "rossi", "R-MARCO: 7 linked accounts vs 3; 3 distinct cards vs 2."),
    (
        "RET-MARCO",
        "rossi",
        "Compared with F-ROSSI, R-MARCO has a cluster return rate of 0.18 vs 0.92.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO has 7 linked accounts (F-ROSSI: 3) and 3 distinct cards (F-ROSSI: 2).",
    ),
    (
        "RET-MARCO",
        "rossi",
        "This case's cluster has 3 vs 7 linked accounts compared with the Rossi family.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "The case cluster R-MARCO differs from the Rossi family (F-ROSSI) in size (3 vs 7 linked "
        "accounts).",
    ),
    ("RET-MARCO", "summary", "The second factor is cluster_welcome_coupons = 7."),
    ("RET-MARCO", "summary", "The third factor is days_since_delivery = 6."),
    ("RET-MARCO", "summary", "The second factor has value 6."),
    ("RET-MARCO", "summary", "The case has two score factors."),
    ("RET-MARCO", "summary", "RET-MARCO has a 30 day return window lever."),
    ("RET-GIULIA", "summary", "RET-GIULIA is in Q1, with a 14 day return window lever."),
    ("RET-MARCO", "link", "The second link L-101 has weight 0.98."),
]


@pytest.mark.parametrize("case_id, kind, final", AGENT_CATCHES)
def test_w1_w4_wrong_pairs_ordinals_and_levers_fall_back(
    case_id: str, kind: str, final: str
) -> None:
    for question in QUESTIONS[kind]:
        assert ask(case_id, kind, question, final).source == "fallback", final


def test_w4_lever_values_are_the_numbers_of_id_value_lever_strings() -> None:
    case: dict[str, Any] = {
        "case_id": "RET-1",
        "lever_ids": ["return_window_days:14", "instant_refund:false", "x:1.5", "y:", "z:1e3"],
    }
    assert lever_values(case) == [("return_window_days", Decimal(14)), ("x", Decimal("1.5"))]
    assert lever_values({"case_id": "RET-1", "lever_ids": None}) == []
    assert lever_values(["return_window_days:14"]) == []


# ---- W7: the restrictive {terms} sentence, measured --------------------------------------------

W7_WORDING = {
    "it": (
        "I tuoi prossimi ordini avranno queste modifiche alle condizioni di reso, mostrate prima "
        "dell'acquisto: {terms}."
    ),
    "en": (
        "Your next orders will have these changes to the return terms, shown before each "
        "purchase: {terms}."
    ),
}


def longest_messages() -> dict[str, int]:
    """The longest message before the clause per language, over the sweep of test T5."""
    best = dict.fromkeys(LANGS, 0)
    seen: set[Any] = set()
    for base, name, refund in itertools.product(SWEEP_BASES, sorted(SCENARIOS), (None, REFUND)):
        decision, expected = decided(name, base, refund)
        ctx = context_of(expected)
        if ctx in seen:
            continue
        seen.add(ctx)
        coherent = [ids for ids in unordered_selections(ctx) if not selection_problems(ids, ctx)]
        for lang in LANGS:
            filled = " ".join(mandatory_body(ctx, lang))
            for field, value in template_fields(decision, base, lang, refund).items():
                filled = filled.replace("{" + field + "}", value)
            words = {s.id: word_count(s.text(lang)) for s in allowed_sentences(ctx)}
            for ids in coherent:
                count = word_count(filled) + sum(words[i] for i in ids)
                best[lang] = max(best[lang], count)
    return best


def test_w7_the_suggested_wording_would_break_the_120_word_limit_in_italian(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W7 suggested "these changes to the return terms" ("queste modifiche alle condizioni di
    reso"). The longest Italian message would have 121 words before the clause, over the 120 of
    spec 8.7, so the sentence keeps "these return terms" ("queste condizioni di reso")."""
    assert longest_messages() == {"it": 119, "en": 116}
    monkeypatch.setitem(phrasebook._TERMS, "restrictive", W7_WORDING)
    assert longest_messages() == {"it": 121, "en": 119}
