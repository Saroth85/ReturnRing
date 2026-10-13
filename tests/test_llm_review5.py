"""Regression probes of the adversarial re-review of 5d27259, round 5 (findings V1 to V18).

This is the closing round of the LLM layer. Operator statements and agent answers are free text
read by a person who also sees the engine decision, so the checks are high precision (ADR-14
decision 11): the over-blocked examples of V3, V4, V5, V11 to V14 and V16 to V18 are in the
must-accept corpus (tests/test_llm_corpus.py), and this file holds the catches that were kept or
added (V1, V2, V10, V15), each one with its probes, the profile guard (V6), the retry feedback
(V17) and the customer wording (V7, V8, V9). Every new catch also leaves the whole corpus passing.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from llm.agent import VllmAgent
from llm.case import CaseError, build_case
from llm.client import MockClient
from llm.decision import serialize_decision
from llm.generate import GenerationPath, generate
from llm.messages import template_fields, term_phrases
from llm.phrasebook import (
    SIZE_EXCHANGE_NOW,
    context_of,
    mandatory_body,
    phrase_problems,
    render_selection,
)
from llm.prompts import retry_message
from llm.templates import default_selection, template_output
from llm.text import LANGS, Lang, banned_terms, grounding_problems, word_count
from llm.validator import ACTIONS, Code, ValidationIssue, retry_feedback
from policy.engine import decide
from policy.types import Decision, ReturnTerms
from tests.factories import BASE, THRESHOLDS, TODAY
from tests.llm_factories import SCENARIOS, make
from tests.test_llm_agent import FakeReader, ScriptedChat, answer, calls, tool_call
from tests.test_llm_review3 import GOLDEN, REFUND, SWEEP_BASES, ask_vllm, assert_rejected, check

# ---- V1: an identifier never opens a grounding bypass -----------------------------------------

V1_PROBES = [
    "Customer net margin: 42.1 RET-1 k.",
    "42.1 R-MARCO k EUR.",
    "42.1 Q4 M.",
    "42.1 Q4 B.",
    "42.1 Q4 x.",
    "42.1 Q4 times.",
    "42.1 value_eur_12m k.",
    "42.1 cluster_size M EUR.",
    "about R-MARCO 42.1 EUR.",
    "about value_eur_12m 42.1 EUR.",
    "close to value_eur_12m 42.1 EUR.",
    "Q4 (42.1).",
    "R-MARCO – 42.1 EUR.",
    "Customer net margin (42.1) value_eur_12m mil.",
]


@pytest.mark.parametrize("text", V1_PROBES)
def test_v1_an_identifier_does_not_ground_a_scale_comparator_or_sign(text: str) -> None:
    assert_rejected(check(GOLDEN, text, ("value_eur_12m",)), "UNGROUNDED_NUMBER")


@pytest.mark.parametrize(
    "name, text, metric",
    [
        ("Q4_negative_margin", "Customer net margin: Q4 – 42.1 EUR.", "value_eur_12m"),
        (
            "Q4_cluster",
            "The score is driven by cluster_welcome_coupons (7).",
            "cluster_welcome_coupons",
        ),
        ("Q4_cluster", "cluster_welcome_coupons (7) drives the score.", "cluster_welcome_coupons"),
        ("Q4_cluster", "cluster_welcome_coupons 7 drives the score.", "cluster_welcome_coupons"),
        ("Q4_cluster", "Linked accounts: cluster_size 7.", "cluster_size"),
        ("Q4_cluster", "Customer net margin: value_eur_12m 42.1 EUR.", "value_eur_12m"),
    ],
)
def test_v1_guard_labels_and_signs_after_an_identifier(name: str, text: str, metric: str) -> None:
    result = check(make(name), text, (metric,), "SCORE" if "score" in text else "OBSERVED")
    assert result.ok, result.errors


def test_v1_the_falsifying_examples_are_replayed() -> None:
    from tests import test_llm as validator_tests

    prop = validator_tests.test_llm03_every_number_after_the_validator_is_a_cited_fact
    inner = prop.hypothesis.inner_test  # type: ignore[attr-defined]
    for tokens in (["Q4", "– 42.1"], ["Q4", "(42.1)"], ["R-MARCO", "– 42.1", "EUR"]):
        inner(tokens=tokens, fact_ids=["F2"], label="OBSERVED")


# ---- V2: control verbs with the accounts as object, whatever the subject -----------------------

V2_STATEMENTS = [
    "They control the linked accounts.",
    "The family runs the linked accounts.",
    "The household manages all linked accounts.",
    "The linked accounts are run by them.",
    "The linked accounts are managed by them.",
    "Whoever runs the linked accounts used 7 welcome coupons.",
    "Marco controls the linked accounts.",
    "The 7 linked accounts are operated by one family.",
    "The customer c0ntr0ls the linked accounts.",
]
V2_ITALIAN = [
    "La famiglia gestisce gli account collegati.",
    "Loro controllano gli account collegati.",
    "Gli account collegati sono gestiti da loro.",
    "Chi gestisce gli account collegati ha usato 7 coupon.",
    "Marco gestisce gli account collegati.",
    "Gli account collegati sono gestiti da Marco.",
]
V2_ANSWERS = [
    "They control the 7 linked accounts of cluster R-MARCO.",
    "The household manages the 7 linked accounts of cluster R-MARCO.",
    "The 7 linked accounts of cluster R-MARCO are run by them.",
    "The family runs the 7 linked accounts of cluster R-MARCO.",
]


@pytest.mark.parametrize("text", V2_STATEMENTS)
def test_v2_control_of_the_accounts_is_a_banned_term(text: str) -> None:
    assert_rejected(check(GOLDEN, text, label="HYPOTHESIS"), "BANNED_TERM")


@pytest.mark.parametrize("text", V2_ITALIAN)
def test_v2_italian_control_of_the_accounts_is_found(text: str) -> None:
    assert banned_terms(text)


@pytest.mark.parametrize("final", V2_ANSWERS)
def test_v2_control_in_an_agent_answer_falls_back(final: str) -> None:
    assert ask_vllm(final) == "fallback"


@pytest.mark.parametrize(
    "text",
    [
        "The linked accounts operate from a shared address.",
        "Orders from the linked accounts ran in rapid succession.",
        "The linked accounts are controlled by a shared device.",
        "The linked accounts are managed by the same card on file.",
        "Gli account collegati sono controllati da un dispositivo condiviso.",
        "The Rossi family has 3 linked accounts.",
        "The customer has no linked accounts.",
        "Il cliente non ha account collegati.",
    ],
)
def test_v2_guard_intransitive_uses_identifiers_and_negations_pass(text: str) -> None:
    assert banned_terms(text) == []


# ---- V10: same person as a predicate; duplicate accounts, clones, multi-accounting --------------

V10_STATEMENTS = [
    "The person is the same across the linked accounts.",
    "The account holder is the same across the cluster.",
    "The linked accounts are duplicate accounts.",
    "The cluster shows multi-accounting.",
    "The linked accounts are clones of one another.",
    "The user behind the accounts is the same.",
    "Il titolare è lo stesso per tutti gli account collegati.",
    "The linked accounts are duplicates of each other.",
]
V10_ANSWERS = [
    "Link L-101: C-MARCO-1 and C-MARCO-2 share a device and a card (4 shared events, weight "
    "0.98), so the person is the same.",
    "The user is the same on C-MARCO-1 and C-MARCO-2.",
    "The account holder is the same for C-MARCO-1 and C-MARCO-2 (link L-101, weight 0.98).",
    "The 7 linked accounts of R-MARCO are duplicate accounts.",
    "R-MARCO shows multi-accounting: 7 linked accounts with 2 distinct cards.",
    "Marco controls the 7 linked accounts of R-MARCO.",
]


@pytest.mark.parametrize("text", V10_STATEMENTS)
def test_v10_same_person_predicates_and_duplicate_accounts_are_banned_terms(text: str) -> None:
    assert_rejected(check(GOLDEN, text, label="HYPOTHESIS"), "BANNED_TERM")


@pytest.mark.parametrize("final", V10_ANSWERS)
def test_v10_same_person_in_an_agent_answer_falls_back(final: str) -> None:
    assert ask_vllm(final, "Which evidence supports the link?") == "fallback"


@pytest.mark.parametrize(
    "text",
    [
        "The customer's address is the same as that of the linked accounts.",
        "The shipping address is the same for the linked accounts.",
        "The customer device is the same as the device of a linked account.",
        "The return reason is the same as in the previous return.",
    ],
)
def test_v10_guard_the_same_identifier_is_not_the_same_person(text: str) -> None:
    assert banned_terms(text) == []


# ---- V15: decision talk and verdicts in operator statements --------------------------------------

V15_PROBES = [
    ("giulia", "Flag this customer for review."),
    ("giulia", "Monitor this customer closely."),
    ("giulia", "This customer warrants a manual review."),
    ("giulia", "Treat this customer with caution."),
    ("giulia", "The customer deserves closer scrutiny."),
    ("marco", "No further action is required."),
    ("marco", "This case can be closed without changes."),
    ("marco", "The behaviour is predatory."),
    ("marco", "The linked accounts are throwaway accounts."),
    ("marco", "The linked accounts look like burner accounts."),
    ("marco", "The accounts were created to farm welcome coupons."),
    ("marco", "This looks like account farming."),
    ("marco", "The cluster is gaming the welcome coupons."),
]


@pytest.mark.parametrize("name, text", V15_PROBES)
def test_v15_decisions_and_verdicts_are_rejected(name: str, text: str) -> None:
    assert_rejected(check(make(name), text, label="HYPOTHESIS"), "TERMS_TOPIC", "BANNED_TERM")


@pytest.mark.parametrize(
    "text",
    [
        "The link predates the order.",
        "The customer bought a game for a friend.",
        "The coat has a flagship fabric.",
    ],
)
def test_v15_guard_words_that_share_a_stem_pass(text: str) -> None:
    result = check(make("marco"), text)
    assert result.ok, result.errors


# ---- V14: missing evidence must pass the statement checks --------------------------------------


@pytest.mark.parametrize(
    "item", ["fewer than 3 orders observed", "fewer than three orders", "orders below the 4 needed"]
)
def test_v14_a_missing_evidence_item_with_a_number_is_a_case_error(item: str) -> None:
    built = make("insufficient")
    request = replace(SCENARIOS["insufficient"].request, return_id="RET-1", order_terms=BASE)
    with pytest.raises(CaseError, match="statement checks"):
        build_case(
            built.decision,
            request,
            BASE,
            policy_text="Returns within 30 days.",
            scores=SCENARIOS["insufficient"].scores,
            missing_evidence=[item],
        )


def test_v14_restating_the_missing_evidence_keeps_the_model_path() -> None:
    built = make("insufficient")
    out = template_output(built.case, built.expected)
    items = "; ".join(built.case["missing_evidence"])
    out["operator_explanation"].append(
        {"label": "OBSERVED", "fact_ids": [], "text": f"Missing evidence: {items}."}
    )
    result = generate(built.case, built.expected, MockClient([out]))
    assert result.path is GenerationPath.MODEL, result.issues


# ---- V16, V17: grounding after a parenthesis and after "used by" -------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Customer net margin (42.1) lakh.",
        "Customer net margin (42.1) times the median.",
        "Customer net margin (42.1) per order.",
        "Customer net margin: 42.1 EUR, less 6.",
        "The margin shrank by 42.1 EUR.",
    ],
)
def test_v16_only_contribution_verbs_follow_a_closed_parenthesis(text: str) -> None:
    assert grounding_problems(text, [("value_eur_12m", Decimal("42.1"))])


@pytest.mark.parametrize(
    "text",
    [
        "The cluster grew by 7 linked accounts.",
        "The cluster is short by 7 linked accounts.",
        "Linked accounts: 7 or more.",
    ],
)
def test_v17_by_before_a_count_is_allowed_only_after_a_participle_of_use(text: str) -> None:
    assert grounding_problems(text, [("cluster_size", Decimal("7"))])


@pytest.mark.parametrize(
    "text",
    [
        "The welcome coupons used by 7 linked accounts.",
        "The card is shared by 7 different accounts.",
        "The address is shared by 7 distinct linked accounts.",
    ],
)
def test_v17_guard_used_by_a_count_is_grounded(text: str) -> None:
    assert grounding_problems(text, [("cluster_size", Decimal("7"))]) == []


def issue(code: Code, where: str, detail: str = "") -> ValidationIssue:
    return ValidationIssue(code, ACTIONS[code], where, detail)


def test_v17_the_retry_feedback_names_the_matched_words() -> None:
    issues = [
        issue(Code.TERMS_TOPIC, "e[2]", "kept"),
        issue(Code.BANNED_TERM, "e[3]", "behind the linked accounts"),
        issue(Code.SCRIPT, "e[4]", "\u0410"),
        issue(Code.SCHEMA, "output"),
        issue(Code.SCHEMA, "output"),
    ]
    feedback = retry_feedback(issues)
    assert feedback == (
        "TERMS_TOPIC in e[2]: kept",
        "BANNED_TERM in e[3]: behind the linked accounts",
        "SCRIPT in e[4]: U+0410",
        "SCHEMA in output",
    )
    message = retry_message(feedback)
    assert "TERMS_TOPIC in e[2]: kept; BANNED_TERM" in message
    assert "does not repeat them" in message


def test_v17_generate_sends_the_matched_words_on_the_second_call() -> None:
    out = template_output(GOLDEN.case, GOLDEN.expected)
    out["operator_explanation"].append(
        {"label": "OBSERVED", "fact_ids": [], "text": "The cluster keeps its welcome coupons."}
    )
    mock = MockClient([out])
    result = generate(GOLDEN.case, GOLDEN.expected, mock)
    assert result.path is GenerationPath.REGENERATED
    index = len(out["operator_explanation"]) - 1
    assert mock.calls[1] == (
        f"TERMS_TOPIC in operator_explanation[{index}]: keeps its welcome coupons",
    )


# ---- V7, V8, V9: the customer message on every base and order snapshot --------------------------

KEEPS = {
    "en": "This return keeps the terms of your order.",
    "it": "Questo reso mantiene le condizioni del tuo ordine.",
}
KEEPS_EXCHANGE = {
    "en": "This return keeps the terms of your order, and the size exchange is free.",
    "it": "Questo reso mantiene le condizioni del tuo ordine, e il cambio taglia è gratuito.",
}
EXCHANGE = {"en": "free size exchange", "it": "cambio taglia gratuito"}
NOTICES = {
    "en": ("These terms take into account", "take into account your previous returns"),
    "it": ("Queste condizioni tengono conto", "tengono conto dei tuoi resi precedenti"),
}
RESTRICTIVE = sorted(n for n in SCENARIOS if n.startswith(("Q3", "Q4")))
Q4_SNAPSHOT = ReturnTerms(14, False, False, True, False)
Q1_SNAPSHOT = ReturnTerms(60, True, True, True, True)


def decided_on(
    name: str, base: ReturnTerms, order_terms: ReturnTerms, refund: Decimal | None = None
) -> tuple[Decision, Any]:
    """The engine decision of a scenario on a merchant base, for an order bought under
    `order_terms` (the base, a former Q4 snapshot or a former Q1 snapshot)."""
    scenario = SCENARIOS[name]
    request = replace(scenario.request, order_terms=order_terms)
    decision = decide(request, scenario.scores, base, THRESHOLDS, TODAY)
    return decision, serialize_decision(decision, base, refund)[1]


def sweep() -> Any:
    for base, name, refund in itertools.product(SWEEP_BASES, RESTRICTIVE, (None, REFUND)):
        for order_terms in (base, Q4_SNAPSHOT, Q1_SNAPSHOT):
            yield base, name, refund, order_terms


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


@pytest.mark.parametrize("lang", LANGS)
def test_v7_the_current_return_sentence_states_the_free_size_exchange(lang: Lang) -> None:
    """Whenever the current return gets the free size exchange its order lacked, the message
    says so in the current-return sentence; "keeps the terms of your order" never stands alone
    then. The current terms differ from the order snapshot only by that exchange."""
    differs = 0
    for base, name, refund, order_terms in sweep():
        decision, expected = decided_on(name, base, order_terms, refund)
        body = mandatory_body(context_of(expected), lang)
        current = decision.current_return_terms
        if current != order_terms:
            differs += 1
            assert current == replace(order_terms, free_size_exchange=True)
            if "free_size_exchange:true" in expected.lever_ids:
                assert body[1] == KEEPS_EXCHANGE[lang], (base, name, order_terms)
        if "free_size_exchange:true" in expected.lever_ids:
            assert current.free_size_exchange and body[1] == KEEPS_EXCHANGE[lang]
        else:
            assert body[1] == KEEPS[lang]
    assert differs > 0


@pytest.mark.parametrize("lang", LANGS)
def test_v8_the_free_size_exchange_is_never_a_next_order_term_of_a_restriction(
    lang: Lang,
) -> None:
    """In Q3 and Q4 the free size exchange is never in {terms}, never shares a sentence with the
    past-returns notice and never comes right before it; the restrictive sentence has no
    "new"."""
    for base, name, refund, order_terms in sweep():
        decision, expected = decided_on(name, base, order_terms, refund)
        fields = template_fields(decision, base, lang, refund)
        assert EXCHANGE[lang] not in fields.get("terms", "")
        final = render_selection(default_selection(context_of(expected)), decision, base, lang)
        parts = sentences(final.split("\n\n")[0])
        for index, sentence in enumerate(parts):
            if any(notice in sentence for notice in NOTICES[lang]):
                assert EXCHANGE[lang] not in sentence
                before = parts[index - 1]
                assert EXCHANGE[lang] not in before or before == KEEPS_EXCHANGE[lang]
        assert not re.search(r"\b(?:new|nuove)\b", final), final


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize(
    "name, base",
    [
        ("Q4_cluster", ReturnTerms(14, False, False, False, False)),
        ("Q3", ReturnTerms(30, True, True, False, True)),
        ("Q3", ReturnTerms(30, True, False, False, True)),
        ("Q4_cluster", ReturnTerms(30, True, True, False, True)),
    ],
)
def test_v8_the_bases_of_the_review(name: str, base: ReturnTerms, lang: Lang) -> None:
    decision, expected = decided_on(name, base, base)
    final = render_selection(default_selection(context_of(expected)), decision, base, lang)
    assert KEEPS_EXCHANGE[lang] in final
    phrases = term_phrases(decision.levers.to_contract(base), lang, base)
    assert EXCHANGE[lang] not in phrases
    if name == "Q4_cluster" and not base.coupons_enabled:
        assert phrases == [] and "terms" not in expected.placeholders  # V8(b): the notice alone


def test_v8_the_cluster_coupon_block_has_a_phrase_only_when_coupons_were_enabled() -> None:
    lever = [{"id": "cluster_coupons_disabled", "value": True, "scope": "cluster"}]
    no_coupons = ReturnTerms(30, True, True, True, False)
    assert term_phrases(lever, "en", BASE) == ["discount codes not available"]  # type: ignore[arg-type]
    assert term_phrases(lever, "en", no_coupons) == []  # type: ignore[arg-type]


def test_v9_the_body_never_compares_with_the_order_and_stays_within_120_words() -> None:
    """Over every base, restrictive scenario, refund variant and order snapshot (the base, a
    former Q4 snapshot, a former Q1 snapshot): the message has no "new" terms and at most 120
    words before the clause."""
    best = 0
    for base, name, refund, order_terms in sweep():
        decision, expected = decided_on(name, base, order_terms, refund)
        for lang in LANGS:
            final = render_selection(
                default_selection(context_of(expected)), decision, base, lang, refund
            )
            message = final.split("\n\n")[0]
            assert not re.search(r"\b(?:new|nuove)\b", message)
            best = max(best, word_count(message))
    assert best <= 120


def test_v7_the_exchange_clause_is_code_text_like_a_field() -> None:
    for lang in LANGS:
        assert phrase_problems(KEEPS_EXCHANGE[lang], lang) == []
        assert SIZE_EXCHANGE_NOW[lang] in KEEPS_EXCHANGE[lang]


# ---- V12, V13: the agent binds a "vs" pair and names factors by their feature ------------------


def ask_rossi(final: str) -> str:
    chat = ScriptedChat(
        [
            calls(tool_call("get_case")),
            calls(tool_call("get_cluster", {"cluster_id": "R-MARCO"})),
            calls(tool_call("get_cluster", {"cluster_id": "F-ROSSI"})),
            answer(final),
        ]
    )
    question = "What makes this case different from the Rossi family?"
    return VllmAgent(chat, FakeReader()).ask("RET-1", question).source


@pytest.mark.parametrize(
    "final, source",
    [
        ("Compared with the Rossi family, R-MARCO has more linked accounts (7 vs 3).", "model"),
        ("Compared with the Rossi family, R-MARCO has more linked accounts (3 vs 7).", "fallback"),
        ("Compared with the Rossi family (F-ROSSI), R-MARCO has 3 linked accounts.", "fallback"),
        (
            "This case's cluster has more linked accounts than the Rossi family (3 vs 7).",
            "fallback",
        ),
        ("R-MARCO has fewer distinct cards (3 vs 2).", "fallback"),
        ("R-MARCO has fewer distinct cards (2 vs 3).", "model"),
        ("R-MARCO has 7 linked accounts, meno 3.", "fallback"),
        ("Case RET-1: 7 vs 3 linked accounts.", "model"),
    ],
)
def test_v12_a_vs_pair_binds_the_clause_record_and_the_other_one(final: str, source: str) -> None:
    assert ask_rossi(final) == source


def ask_summary(final: str) -> str:
    chat = ScriptedChat(
        [calls(tool_call("get_case")), calls(tool_call("get_score_factors")), answer(final)]
    )
    return VllmAgent(chat, FakeReader()).ask("RET-1", "What is this case about?").source


@pytest.mark.parametrize(
    "final, source",
    [
        ("Top factor: cluster_welcome_coupons = 7.", "model"),
        ("Top factor: cluster_welcome_coupons = 9.", "fallback"),
        ("cluster_welcome_coupons (7) drives the score.", "model"),
        ("cluster_welcome_coupons: (7).", "fallback"),
        # a "vs" pair with no other factor read is grounded on the factor in scope
        ("cluster_welcome_coupons: 7 vs 7.", "model"),
        ("cluster_welcome_coupons: 7 vs 3.", "fallback"),
    ],
)
def test_v13_a_factor_is_named_by_its_feature(final: str, source: str) -> None:
    assert ask_summary(final) == source
