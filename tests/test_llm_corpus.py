"""The must-accept corpus of the LLM layer (round 5 of the adversarial review, ADR-14 decision 11).

Operator statements and agent answers are free text read by a person who also sees the engine
decision, so their checks must be high precision: a check that rejects correct Gemma output on
the golden cases is a worse defect than a contrived paraphrase that slips through. This file is
the guard. It holds correct operator statements (OBSERVED, SCORE, HYPOTHESIS; English, as the
prompt of spec 19.D asks) on the golden cases of spec 16 (Giulia, Marco's ring of 7 linked
accounts, the Rossi family with and without a shared card, Elena on the guarantee path, a case
with insufficient evidence), written the way an instruction-following model writes them, and
correct agent answers to the three US-702 questions and to "what is this case about", in English
and Italian, plain and bulleted. Every over-blocked example of the review (V3, V4, V5, V11, V12,
V13, V14, V16, V17, V18) and of the closing verification (W1, W2, W4, W5, W6) is here. Every
statement must validate, every answer must keep the model path, and so must the mock agent's own
answers and every template. Correct statements that break a rule of the prompt (W3: a negated
level; a number in words) regenerate with the words in the feedback, never the template. A new
check is added only if this whole corpus still passes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from llm.agent import (
    CaseRecord,
    ClusterRecord,
    FactorRecord,
    LinkRecord,
    MockAgent,
    OrderRecord,
    PolicyRecord,
    VllmAgent,
)
from llm.case import ScoreFactor, missing_evidence_problems
from llm.templates import template_output
from llm.text import banned_terms, grounding_problems, operator_topics
from llm.validator import Action, ValidationResult, retry_feedback, validate
from policy.engine import NAVIGLI_BASE_TERMS
from tests.llm_factories import GOLDEN_SCENARIOS, SCENARIOS, Built, make
from tests.test_llm_agent import ScriptedChat, answer, calls, tool_call

# ---- Operator statements on the golden cases ---------------------------------------------------

CUSTOMER_TEXT = {
    "giulia": "La M mi sta larga, tengo la S.",
    "marco": "Non mi piace più.",
    "elena": "La cucitura è storta e le maniche sono più corte della guida alle misure.",
}


def golden(name: str) -> Built:
    return make(name, customer_text=CUSTOMER_TEXT.get(name, "Non mi sta bene."))


def check(built: Built, text: str, metrics: tuple[str, ...] = (), label: str = "OBSERVED") -> Any:
    """The template output plus one statement that cites the facts of these metrics."""
    ids = {f["metric"]: f["id"] for f in built.case["facts"]}
    out = template_output(built.case, built.expected)
    out["operator_explanation"].append(
        {"label": label, "fact_ids": [ids[m] for m in metrics], "text": text}
    )
    result: ValidationResult = validate(out, built.expected, built.case)
    return result


RISK = ("risk_score",)
MARGIN = ("value_eur_12m",)
DAYS = ("days_since_delivery",)
SIZE = ("cluster_size",)
COUPONS = ("cluster_welcome_coupons",)
RETURNS = ("returns_90d",)
SIZE_RETURNS = ("size_returns",)
CARDS = ("n_distinct_cards",)

# (case, label, metrics of the cited facts, text)
Statement = tuple[str, str, tuple[str, ...], str]
STATEMENTS: list[Statement] = [
    # ---- Giulia: Q1, 4 recent returns, 3 of them for size, no linked accounts
    ("giulia", "SCORE", RISK, "Risk score 0.18 from the model: an estimate, not a cause."),
    ("giulia", "SCORE", RISK, "The risk score is low (0.18): an estimate, not a cause."),
    ("giulia", "SCORE", RETURNS, "returns_90d: 4 recent returns contribute to the risk score."),
    (
        "giulia",
        "SCORE",
        RETURNS,
        "The highest contribution to the risk score comes from returns_90d: 4.",
    ),
    ("giulia", "SCORE", RETURNS, "returns_90d is a significant contributor to the risk score: 4."),
    ("giulia", "SCORE", RETURNS, "The 4 recent returns are a substantial part of the risk score."),
    ("giulia", "SCORE", RETURNS, "returns_90d (4) has a big effect on the risk score."),
    (
        "giulia",
        "SCORE",
        SIZE_RETURNS,
        "size_returns: 3, a considerable contribution to the risk score.",
    ),
    ("giulia", "SCORE", SIZE_RETURNS, "Size returns (3) contribute to the score."),
    ("giulia", "SCORE", RETURNS, "Recent returns (4) push the score up slightly."),
    ("giulia", "SCORE", RETURNS, "returns_90d (4) contributes to the score."),
    ("giulia", "SCORE", SIZE_RETURNS, "The model weighs 3 returns for size in the score."),
    ("giulia", "OBSERVED", (), "The customer says the M was too large and kept the S."),
    ("giulia", "OBSERVED", (), "The customer is keeping the S."),
    ("giulia", "OBSERVED", (), "The customer kept the other size."),
    ("giulia", "OBSERVED", (), "The only return was for size, with the other size kept."),
    ("giulia", "OBSERVED", (), "The customer's reason says the dress is too tight."),
    ("giulia", "OBSERVED", DAYS, "The return notice came 6 days after delivery."),
    ("giulia", "OBSERVED", MARGIN, "Customer net margin over the period: 350 EUR."),
    (
        "giulia",
        "OBSERVED",
        RETURNS + SIZE_RETURNS,
        "The customer has 4 recent returns, 3 of them for size.",
    ),
    ("giulia", "OBSERVED", (), "No linked accounts are on file for this customer."),
    ("giulia", "OBSERVED", (), "The return is on the withdrawal path."),
    (
        "giulia",
        "HYPOTHESIS",
        (),
        "The customer may order several sizes and keep the one that fits, to be verified.",
    ),
    (
        "giulia",
        "HYPOTHESIS",
        SIZE_RETURNS,
        "The 3 returns for size may point to a fit problem with the item.",
    ),
    # ---- Marco: Q4, R-MARCO with 7 linked accounts, 7 welcome coupons, 6 days
    ("marco", "SCORE", RISK, "Risk score 0.87 from the model: an estimate, not a cause."),
    ("marco", "SCORE", RISK, "The risk score is high (0.87): an estimate, not a cause."),
    ("marco", "SCORE", RISK, "The model gives a high risk score (0.87), not a verdict."),
    ("marco", "SCORE", RISK, "The risk score of 0.87 is high, not a certainty."),
    ("marco", "SCORE", RISK, "The risk score (0.87) is marginally above the threshold."),
    (
        "marco",
        "SCORE",
        RISK,
        "The risk score is high even though the customer has never returned a defective item.",
    ),
    ("marco", "SCORE", COUPONS, "cluster_welcome_coupons (7) drives the score."),
    ("marco", "SCORE", COUPONS, "The score is driven by cluster_welcome_coupons (7)."),
    (
        "marco",
        "SCORE",
        COUPONS,
        "The welcome coupons used by 7 linked accounts are the main driver of the score.",
    ),
    (
        "marco",
        "SCORE",
        COUPONS,
        "7 linked accounts each used the welcome coupon, the largest contribution to the score.",
    ),
    ("marco", "SCORE", DAYS, "days_since_delivery: 6, a minor contribution to the risk score."),
    ("marco", "SCORE", DAYS, "days_since_delivery (6) has a small effect on the risk score."),
    ("marco", "SCORE", DAYS, "days_since_delivery (6) is a modest contributor to the risk score."),
    ("marco", "SCORE", DAYS, "The return timing (6 days) contributes little to the risk score."),
    (
        "marco",
        "SCORE",
        DAYS,
        "The timing of the return (6 days after delivery) contributes less to the score.",
    ),
    (
        "marco",
        "SCORE",
        COUPONS,
        "The main signal behind the risk score is the welcome coupon use of the linked accounts.",
    ),
    ("marco", "OBSERVED", SIZE, "The customer's cluster has 7 linked accounts."),
    ("marco", "OBSERVED", SIZE, "For this customer, 7 linked accounts share a device."),
    (
        "marco",
        "OBSERVED",
        SIZE,
        "The customer is new; 7 linked accounts were created in quick succession.",
    ),
    (
        "marco",
        "OBSERVED",
        SIZE,
        "The customer returned the item; 7 linked accounts are in the cluster.",
    ),
    (
        "marco",
        "OBSERVED",
        SIZE,
        "The customer's cluster R-MARCO has 7 linked accounts that share a device, an address "
        "and a card.",
    ),
    ("marco", "OBSERVED", SIZE, "The customer is in a cluster of 7 linked accounts."),
    ("marco", "OBSERVED", SIZE, "The customer belongs to a cluster of 7 linked accounts."),
    (
        "marco",
        "OBSERVED",
        SIZE,
        "The 7 linked accounts share a device, a shipping address and a card.",
    ),
    ("marco", "OBSERVED", SIZE, "Linked accounts in the cluster: 7."),
    ("marco", "OBSERVED", MARGIN, "Customer net margin over the period: 42.1 EUR."),
    (
        "marco",
        "OBSERVED",
        MARGIN,
        "Customer net margin over the period: 42.1 EUR, below the median.",
    ),
    ("marco", "OBSERVED", (), "Orders from the linked accounts were shipped to the same address."),
    ("marco", "OBSERVED", (), "Later orders from the linked accounts used the same card."),
    ("marco", "OBSERVED", SIZE, "The 7 linked accounts each used the welcome offer."),
    ("marco", "OBSERVED", (), "The linked accounts differ in name but share a card."),
    ("marco", "OBSERVED", DAYS, "The return notice came 6 days after delivery."),
    ("marco", "HYPOTHESIS", (), "The customer's order history resembles the linked accounts."),
    ("marco", "HYPOTHESIS", (), "A person should review the linked accounts."),
    (
        "marco",
        "HYPOTHESIS",
        SIZE,
        "The coupon use across the 7 linked accounts needs verification by an operator.",
    ),
    (
        "marco",
        "HYPOTHESIS",
        (),
        "The shared device may be a household device; the links need verification.",
    ),
    (
        "marco",
        "HYPOTHESIS",
        COUPONS,
        "The 7 welcome coupons may have been redeemed in quick succession, to be verified.",
    ),
    # ---- The Rossi family: Q1, F-ROSSI with 3 linked accounts and a shared address
    ("rossi", "SCORE", RISK, "Despite the shared address, the risk score remains at 0.22."),
    ("rossi", "SCORE", RISK, "Risk score 0.22 from the model: an estimate, not a cause."),
    ("rossi", "SCORE", SIZE, "cluster_size (3) contributes to the score."),
    ("rossi", "SCORE", CARDS, "n_distinct_cards: 3, which lowers the score."),
    ("rossi", "OBSERVED", (), "Each linked account pays with its own card."),
    (
        "rossi",
        "OBSERVED",
        SIZE + CARDS,
        "The 3 linked accounts share an address but use 3 distinct cards.",
    ),
    ("rossi", "OBSERVED", (), "The linked accounts were created more than a year apart."),
    ("rossi", "OBSERVED", RETURNS, "The customer has 2 recent returns."),
    ("rossi", "OBSERVED", MARGIN, "Customer net margin over the period: 260 EUR."),
    ("rossi", "OBSERVED", SIZE, "The 3 linked accounts share one address."),
    (
        "rossi",
        "HYPOTHESIS",
        (),
        "The shared address is consistent with a family living together, to be verified.",
    ),
    # ---- The Rossi family with a shared card: Q1, F-ROSSI-CARD
    (
        "rossi_card",
        "OBSERVED",
        CARDS + SIZE,
        "2 distinct payment cards across the 3 linked accounts.",
    ),
    ("rossi_card", "OBSERVED", (), "The linked accounts share an address and a card."),
    ("rossi_card", "OBSERVED", SIZE, "cluster_size: 3."),
    ("rossi_card", "SCORE", RISK, "Risk score 0.34 from the model: an estimate, not a cause."),
    (
        "rossi_card",
        "SCORE",
        CARDS,
        "The shared card raises the score; the 2 distinct cards lower it.",
    ),
    ("rossi_card", "HYPOTHESIS", (), "The shared card may be a family card, to be verified."),
    # ---- Elena: a defect, on the guarantee path
    (
        "elena",
        "OBSERVED",
        (),
        "The return reason is a defect: the case follows the guarantee path.",
    ),
    ("elena", "OBSERVED", (), "The customer reports a crooked seam."),
    (
        "elena",
        "OBSERVED",
        (),
        "The customer says the sleeves are shorter than the size guide states.",
    ),
    ("elena", "OBSERVED", DAYS, "The return notice came 6 days after delivery."),
    ("elena", "OBSERVED", (), "No photo of the item is available."),
    ("elena", "OBSERVED", (), "The item has gone back to the warehouse."),
    (
        "elena",
        "HYPOTHESIS",
        (),
        "The defect may come from production, to be verified with the catalogue team.",
    ),
    # ---- Insufficient evidence
    (
        "missing",
        "OBSERVED",
        (),
        "Missing evidence: too few orders observed; no identifier on file.",
    ),
    ("missing", "OBSERVED", (), "Not enough order history is available."),
    ("missing", "OBSERVED", (), "No identifier is available for this customer."),
    ("missing", "OBSERVED", (), "The return is on the withdrawal path."),
    ("missing", "OBSERVED", (), "Return requests on the withdrawal path."),
    ("missing", "OBSERVED", (), "The return reason remains unclear."),
    ("missing", "OBSERVED", (), "The item no longer fits, according to the customer."),
    ("missing", "OBSERVED", (), "The customer paid for the item with a card."),
    ("missing", "OBSERVED", (), "The item was bought during a special offer."),
    (
        "missing",
        "OBSERVED",
        (),
        "Evidence is insufficient for a risk estimate: no quadrant is assigned.",
    ),
    ("missing", "OBSERVED", DAYS, "The return notice came 6 days after delivery."),
    ("missing", "OBSERVED", (), "No risk score is available because evidence is insufficient."),
    # ---- A second batch, written apart from the review probes (round 5)
    ("giulia", "SCORE", RISK, "The model estimates a risk score of 0.18."),
    ("giulia", "SCORE", RETURNS, "returns_90d = 4 raises the score."),
    ("giulia", "SCORE", SIZE_RETURNS, "The 3 returns for size contribute to the score."),
    ("giulia", "OBSERVED", RETURNS, "The customer made 4 returns in the period."),
    (
        "giulia",
        "OBSERVED",
        (),
        "The customer's message says the M is too large and she keeps the S.",
    ),
    ("giulia", "OBSERVED", (), "The customer wrote in Italian that the size M is too large."),
    ("giulia", "OBSERVED", MARGIN, "Net margin of the customer: 350 EUR."),
    (
        "giulia",
        "HYPOTHESIS",
        (),
        "Size returns may reflect the sizing of this brand, to be checked.",
    ),
    ("giulia", "OBSERVED", (), "There are no linked accounts for this customer."),
    ("giulia", "OBSERVED", (), "No cluster is associated with this customer."),
    ("marco", "SCORE", COUPONS, "The top factor is cluster_welcome_coupons: 7."),
    (
        "marco",
        "SCORE",
        COUPONS,
        "cluster_welcome_coupons (7) is the largest contribution to the score.",
    ),
    ("marco", "SCORE", RISK, "The model gives a risk score of 0.87, an estimate, not a cause."),
    ("marco", "OBSERVED", SIZE, "The cluster R-MARCO has 7 linked accounts."),
    ("marco", "OBSERVED", SIZE, "7 linked accounts share a device, an address and a card."),
    ("marco", "OBSERVED", COUPONS, "Each of the 7 linked accounts used a welcome coupon."),
    ("marco", "OBSERVED", (), "The linked accounts were opened within a short span."),
    ("marco", "OBSERVED", DAYS, "The item was returned 6 days after delivery."),
    ("marco", "OBSERVED", DAYS, "The return notice arrived 6 days after delivery."),
    ("marco", "OBSERVED", MARGIN, "The customer's net margin is 42.1 EUR."),
    ("marco", "OBSERVED", (), "The customer says they no longer like the item."),
    ("marco", "HYPOTHESIS", (), "The shared device could belong to a household, to be verified."),
    (
        "marco",
        "HYPOTHESIS",
        (),
        "The links between the accounts should be verified by an operator.",
    ),
    (
        "marco",
        "HYPOTHESIS",
        SIZE,
        "The 7 linked accounts may be related; the evidence needs review.",
    ),
    ("marco", "HYPOTHESIS", (), "The welcome coupon pattern needs to be confirmed by a person."),
    (
        "marco",
        "HYPOTHESIS",
        (),
        "The address link may be a shared building rather than a shared home.",
    ),
    ("marco", "OBSERVED", (), "One link between the accounts was disputed by an operator."),
    ("marco", "SCORE", DAYS, "days_since_delivery = 6 has little weight in the score."),
    ("rossi", "OBSERVED", SIZE, "The cluster F-ROSSI has 3 linked accounts that share an address."),
    ("rossi", "OBSERVED", CARDS, "The linked accounts use 3 distinct cards."),
    ("rossi", "SCORE", CARDS, "n_distinct_cards (3) lowers the score."),
    ("rossi", "SCORE", RISK, "Risk score: 0.22."),
    ("rossi", "HYPOTHESIS", (), "A shared address is common for a family."),
    ("rossi_card", "OBSERVED", CARDS, "The linked accounts use 2 distinct cards."),
    (
        "rossi_card",
        "HYPOTHESIS",
        (),
        "The shared card may be used by family members, to be verified.",
    ),
    ("rossi_card", "SCORE", RISK, "The risk score is 0.34."),
    ("elena", "OBSERVED", (), "The customer reports a defect: the seam is crooked."),
    ("elena", "OBSERVED", (), "The return follows the guarantee path, so there is no risk score."),
    (
        "elena",
        "OBSERVED",
        (),
        "The sleeves are shorter than stated in the size guide, according to the customer.",
    ),
    ("elena", "HYPOTHESIS", (), "The defect may affect other items of the same batch."),
    ("elena", "HYPOTHESIS", (), "Other customers may have reported the same variant issue."),
    ("elena", "OBSERVED", DAYS, "The customer reported the problem 6 days after delivery."),
    (
        "missing",
        "OBSERVED",
        (),
        "Evidence is insufficient: too few orders observed and no identifier on file.",
    ),
    ("missing", "OBSERVED", (), "There is not enough history to estimate a risk score."),
    ("missing", "OBSERVED", (), "No quadrant was assigned because evidence is insufficient."),
    ("missing", "HYPOTHESIS", (), "More orders would be needed for a reliable estimate."),
    ("missing", "OBSERVED", (), "The customer says the fabric differs from the photo."),
    # ---- The test scenarios of the review (V1, V4, V5)
    ("Q1", "SCORE", RISK, "The risk score is low (0.1): an estimate, not a cause."),
    ("Q1", "SCORE", RISK, "The risk score (0.1) is marginally below the threshold."),
    ("Q2", "SCORE", RISK, "The risk score is low (0.2): an estimate, not a cause."),
    ("Q3", "SCORE", RISK, "The risk score is high (0.9): an estimate, not a cause."),
    ("Q4", "SCORE", RISK, "The risk score is high (0.87): an estimate, not a cause."),
    ("Q4_cluster", "SCORE", RISK, "The risk score is high (0.87): an estimate, not a cause."),
    ("Q4_negative_margin", "OBSERVED", MARGIN, "Customer net margin: Q4 – 42.1 EUR."),
    ("Q4_cluster", "OBSERVED", SIZE, "The 7 linked accounts each used the welcome offer."),
    ("guarantee", "OBSERVED", (), "The customer reports a crooked seam."),
    # ---- Round 6 (W2): the direction of a factor is the job of a SCORE statement
    ("rossi_card", "SCORE", CARDS, "The shared card pushes the risk score higher."),
    ("rossi_card", "SCORE", CARDS, "The risk score is higher because of the shared card."),
    ("giulia", "SCORE", RETURNS, "The 4 recent returns push the risk score higher."),
    ("marco", "SCORE", DAYS, "The return timing (6 days) lowers the risk score a little."),
    ("marco", "SCORE", DAYS, "days_since_delivery (6) makes the risk score slightly lower."),
    ("rossi", "SCORE", RETURNS, "returns_90d (2) pushes the risk score higher."),
    ("rossi_card", "SCORE", CARDS, "n_distinct_cards (2) makes the risk score higher."),
    ("marco", "SCORE", DAYS, "days_since_delivery (6) pulls the risk score lower."),
    # ---- Round 6 (W5): the model flags a factor, the score is read with caution
    (
        "marco",
        "SCORE",
        COUPONS,
        "The model flags cluster_welcome_coupons (7) as the main contribution to the score.",
    ),
    (
        "marco",
        "SCORE",
        RISK,
        "Read the risk score (0.87) with caution: it is an estimate, not a cause.",
    ),
    ("giulia", "SCORE", RISK, "The risk score of 0.18 should be interpreted with caution."),
    # ---- Round 6 (W6): contribution verbs, conjunctions, coupon counts, the withdrawal period
    ("marco", "SCORE", DAYS, "days_since_delivery (6) plays a minor role in the score."),
    (
        "marco",
        "SCORE",
        COUPONS + DAYS,
        "cluster_welcome_coupons (7) outweighs days_since_delivery (6) in the score.",
    ),
    (
        "marco",
        "HYPOTHESIS",
        COUPONS,
        "The 7 welcome coupons may have been redeemed within a short time; the order log should "
        "confirm it.",
    ),
    (
        "marco",
        "OBSERVED",
        COUPONS + SIZE,
        "The welcome coupon use stands out: 7 coupons across 7 linked accounts.",
    ),
    ("giulia", "OBSERVED", (), "The return request falls within the withdrawal period."),
    (
        "rossi",
        "SCORE",
        RISK,
        "The risk score is low (0.22) even though the linked accounts share an address.",
    ),
    ("rossi", "OBSERVED", (), "The case belongs to the Rossi family cluster."),
    # Final check of round 6: an adverb before the contribution verb, and comes/accounts for.
    ("marco", "SCORE", DAYS, "days_since_delivery (6) slightly lowers the risk score."),
    ("marco", "SCORE", COUPONS, "cluster_welcome_coupons (7) strongly pushes the risk score up."),
    ("giulia", "SCORE", RETURNS, "returns_90d (4) also raises the risk score."),
    ("giulia", "SCORE", RETURNS, "returns_90d (4) accounts for most of the risk score."),
    (
        "marco",
        "SCORE",
        RISK + COUPONS,
        "The risk score (0.87) comes mostly from cluster_welcome_coupons (7).",
    ),
]
STATEMENT_IDS = [f"{name}: {text}" for name, _, _, text in STATEMENTS]


@pytest.mark.parametrize(("name", "label", "metrics", "text"), STATEMENTS, ids=STATEMENT_IDS)
def test_correct_statements_on_the_golden_cases_validate(
    name: str, label: str, metrics: tuple[str, ...], text: str
) -> None:
    result = check(golden(name), text, metrics, label)
    assert result.ok, result.errors


@pytest.mark.parametrize("name", sorted(GOLDEN_SCENARIOS))
def test_the_corpus_of_each_golden_case_validates_as_whole_explanations(name: str) -> None:
    """The statements of a case, eight at a time, as one explanation (the language check reads
    them together)."""
    built = golden(name)
    ids = {f["metric"]: f["id"] for f in built.case["facts"]}
    mine = [s for s in STATEMENTS if s[0] == name]
    assert mine
    for start in range(0, len(mine), 8):
        out = template_output(built.case, built.expected)
        out["operator_explanation"] = [
            {"label": label, "fact_ids": [ids[m] for m in metrics], "text": text}
            for _, label, metrics, text in mine[start : start + 8]
        ]
        result = validate(out, built.expected, built.case)
        assert result.ok, result.errors


def test_the_corpus_is_large_enough_and_covers_every_golden_case_and_label() -> None:
    golden_statements = [s for s in STATEMENTS if s[0] in GOLDEN_SCENARIOS]
    assert len(golden_statements) >= 60
    assert {s[0] for s in golden_statements} == set(GOLDEN_SCENARIOS)
    assert {s[1] for s in golden_statements} == {"OBSERVED", "SCORE", "HYPOTHESIS"}
    assert len(set(STATEMENT_IDS)) == len(STATEMENT_IDS)


# Correct statements that break a rule of the prompt (round 6, finding W3, and the number words
# of W6): a level of the score in words, negated ("not in the high range", "far from a high
# value", "nowhere near high"), or a number in words. They agree with the engine, so they never
# go to the template at once: they are regenerated, and the retry feedback names the words.
# (case, label, metrics of the cited facts, text, a word the feedback names)
REGENERATED: list[tuple[str, str, tuple[str, ...], str, str]] = [
    ("giulia", "SCORE", RISK, "The risk score of 0.18 is not in the high range.", "high"),
    ("giulia", "SCORE", RISK, "The risk score is 0.18, far from the high-risk range.", "high"),
    (
        "rossi",
        "SCORE",
        CARDS,
        "The shared address does not push the risk score into the high range.",
        "high",
    ),
    ("marco", "OBSERVED", MARGIN, "Customer net margin: 42.1 EUR, far from a high value.", "high"),
    ("giulia", "SCORE", RISK, "The risk score (0.18) is nowhere near high.", "high"),
    ("giulia", "SCORE", RISK, "At 0.18, the risk score is far below high.", "high"),
    (
        "elena",
        "OBSERVED",
        (),
        "The customer describes two problems: a crooked seam and short sleeves.",
        "two",
    ),
]


@pytest.mark.parametrize(
    ("name", "label", "metrics", "text", "word"),
    REGENERATED,
    ids=[f"{name}: {text}" for name, _, _, text, _ in REGENERATED],
)
def test_correct_statements_that_break_a_prompt_rule_regenerate_never_the_template(
    name: str, label: str, metrics: tuple[str, ...], text: str, word: str
) -> None:
    result = check(golden(name), text, metrics, label)
    assert result.action is Action.REGENERATE, result.errors
    assert "DECISION_ECHO" not in result.codes
    assert any(word in item for item in retry_feedback(result.errors)), result.errors


@pytest.mark.parametrize("name", sorted(GOLDEN_SCENARIOS) + sorted(SCENARIOS))
def test_every_template_passes_the_model_checks(name: str) -> None:
    built = golden(name)
    result = validate(template_output(built.case, built.expected), built.expected, built.case)
    assert result.ok, result.errors


# ---- Factor phrases and missing evidence a model may copy (V4, V14) ---------------------------

# The phrase of every score factor of the test cases, and the factor labels and number-free
# phrases of the API's feature table (ml/risk/explain.py and the API case cards on the API
# branch), with their own fact: a model may copy them into a statement.
API_FACTOR_PHRASES: list[tuple[str, float, str]] = [
    ("return_rate_to_date", 0.4, "Share of the delivered order lines returned so far"),
    ("median_days_to_return", 6, "Median days from delivery to the return request"),
    ("mean_line_price_eur", 59.9, "Mean price of the items ordered"),
    ("n_orders", 11, "Orders placed"),
    ("n_lines", 14, "Order lines placed"),
    ("n_delivered_lines", 14, "Order lines delivered"),
    ("n_observations", 11, "Order lines whose return window has closed"),
    ("n_returns", 4, "Return requests on the withdrawal path"),
    (
        "return_rate",
        0.4,
        "Share of the order lines with a closed return window that were returned",
    ),
    ("size_share", 0.8, "Share of returns for size"),
    ("changed_mind_share", 0.8, "Share of returns because the customer changed their mind"),
    ("not_as_expected_share", 0.2, "Share of returns because the item was not as expected"),
    ("other_reason_share", 0.1, "Share of returns for another reason"),
    ("tenure_days", 326, "Days since the account was opened"),
    ("n_welcome_coupons_used", 1, "Welcome coupons used"),
    ("n_promo_coupons_used", 2, "Promotional and referral codes used"),
    ("coupon_order_share", 0.5, "Share of orders with a coupon"),
    ("weekend_order_share", 0.3, "Share of orders placed at the weekend"),
    ("cluster_size", 7, "Accounts in the group of linked accounts"),
    ("shared_strong_ids", 3, "Card, e-mail and device values shared among the linked accounts"),
    ("welcome_coupons", 7, "Welcome coupons used by the linked accounts"),
    ("n_distinct_cards", 2, "Distinct payment cards of the linked accounts"),
    (
        "creation_spread_days",
        9,
        "Days between the first and the last opening among the linked accounts",
    ),
    ("welcome_coupons_per_account", 1.0, "Welcome coupons used per linked account"),
    (
        "cluster_return_rate",
        0.92,
        "Share of the delivered order lines of the linked accounts returned",
    ),
    (
        "strong_id_share",
        0.8,
        "Share of card, e-mail and device values among the values the linked accounts share",
    ),
    ("expected_returns", 2.1, "Returns expected from the items bought, by the product reference"),
    ("residual", 1.9, "Returns beyond those the items bought would predict"),
    ("residual_z", 1.9, "Standardised return residual against the product reference"),
    ("days_since_delivery", 6, "Days from delivery to this return request"),
    ("item_price_eur", 59.9, "Price of the item returned"),
    ("variant_return_rate", 0.3, "Return rate of this item among other customers"),
    ("size_share", 1.0, "The only return was for size, with the other size kept"),
    ("residual_z", -0.3, "Returns below what the products predict"),
    ("tenure_days", 326, "Customer for almost a year"),
    ("residual_z", 0.4, "Returns close to what the products predict"),
    ("cluster_size", 1, "No linked accounts"),
    ("residual_z", 1.4, "Returns above what the products predict"),
    ("changed_mind_share", 1.0, "The only return was for change of mind"),
    ("return_value_share", 0.54, "The returned items are the most expensive ones"),
    ("strong_id_share", 0.0, "No identifier shared beyond address and device"),
    ("cluster_size", 1, "No linked accounts after the dispute"),
    ("residual_z", 0.6, "Returns slightly above what the products predict"),
    ("strong_id_share", 0.5, "A payment card is shared with a linked account"),
]
TEST_FACTORS: list[ScoreFactor] = [
    factor
    for scenario in [*SCENARIOS.values(), *GOLDEN_SCENARIOS.values()]
    for factor in scenario.factors
]


@pytest.mark.parametrize(
    ("feature", "value", "phrase"),
    API_FACTOR_PHRASES + [(f.feature, float(f.value), f.phrase) for f in TEST_FACTORS],
)
def test_every_factor_phrase_passes_the_statement_checks_with_its_own_fact(
    feature: str, value: float, phrase: str
) -> None:
    cited = [(feature, Decimal(str(value)))]
    assert grounding_problems(phrase, cited) == []
    assert operator_topics(phrase) == []
    assert banned_terms(phrase) == []


@pytest.mark.parametrize(
    "item",
    [
        *GOLDEN_SCENARIOS["missing"].missing,
        "too few order lines observed for this customer",
        "only one identifier kind on file: email",
        "no device, address or card on file",
        "not enough order history",
    ],
)
def test_missing_evidence_items_pass_the_statement_checks(item: str) -> None:
    assert missing_evidence_problems(item) == []
    built = golden("missing")
    assert check(built, f"Missing evidence: {item}.").ok


# ---- Agent answers on the golden cases ---------------------------------------------------------

AS_OF = date(2026, 10, 14)
Q4_LEVERS = (
    "return_window_days:14",
    "free_return_shipping:false",
    "instant_refund:false",
    "coupons_enabled:false",
    "cluster_coupons_disabled:true",
    "transparency_notice:true",
)


def _link(link_id: str, a: str, b: str, kinds: tuple[str, ...], n: int, weight: float) -> Any:
    return LinkRecord(link_id, a, b, kinds, date(2026, 3, 2), date(2026, 9, 28), n, weight)


CLUSTERS = {
    "R-MARCO": ClusterRecord(
        "R-MARCO",
        7,
        (
            _link("L-101", "C-MARCO-1", "C-MARCO-2", ("device", "card"), 4, 0.98),
            replace(_link("L-102", "C-MARCO-1", "C-MARCO-3", ("address",), 2, 0.6), disputed=True),
        ),
        n_distinct_cards=2,
        creation_spread_days=9.0,
        welcome_coupons_per_account=1.0,
        cluster_return_rate=0.92,
        strong_id_share=0.8,
        nature="synthetic",
        as_of=AS_OF,
    ),
    "F-ROSSI": ClusterRecord(
        "F-ROSSI",
        3,
        (
            _link("L-201", "C-ROSSI-1", "C-ROSSI-2", ("address",), 12, 0.4),
            _link("L-202", "C-ROSSI-2", "C-ROSSI-3", ("address",), 10, 0.4),
        ),
        n_distinct_cards=3,
        creation_spread_days=2900.0,
        welcome_coupons_per_account=0.33,
        cluster_return_rate=0.18,
        strong_id_share=0.3,
        nature="synthetic",
        as_of=AS_OF,
    ),
    "F-ROSSI-CARD": ClusterRecord(
        "F-ROSSI-CARD",
        3,
        (
            _link("L-301", "C-RCARD-1", "C-RCARD-2", ("address", "card"), 8, 0.7),
            _link("L-302", "C-RCARD-2", "C-RCARD-3", ("address",), 6, 0.4),
        ),
        n_distinct_cards=2,
        creation_spread_days=557.0,
        welcome_coupons_per_account=0.33,
        cluster_return_rate=0.2,
        strong_id_share=0.5,
        nature="synthetic",
        as_of=AS_OF,
    ),
}


def _case(name: str, quadrant: str | None, levers: tuple[str, ...], cluster: str | None) -> Any:
    path = "guarantee" if name == "ELENA" else "withdrawal"
    return CaseRecord(
        f"RET-{name}",
        f"ORD-{name}",
        path,
        "sufficient",
        quadrant,
        levers,
        cluster,
        "synthetic",
        AS_OF,
    )


CASES = {
    "RET-MARCO": _case("MARCO", "Q4", Q4_LEVERS, "R-MARCO"),
    "RET-GIULIA": _case("GIULIA", "Q1", ("return_window_days:60",), None),
    "RET-ROSSI": _case("ROSSI", "Q1", ("return_window_days:60",), "F-ROSSI"),
    "RET-RCARD": _case("RCARD", "Q1", ("return_window_days:60",), "F-ROSSI-CARD"),
    "RET-ELENA": _case("ELENA", None, (), None),
}
FACTORS = {
    "RET-MARCO": (
        FactorRecord(
            "cluster_welcome_coupons", 7, "7 linked accounts each used the welcome coupon"
        ),
        FactorRecord("days_since_delivery", 6, "returned 6 days after delivery"),
    ),
    "RET-GIULIA": (
        FactorRecord("returns_90d", 4, "4 recent returns"),
        FactorRecord("size_returns", 3, "3 returns for size"),
    ),
    "RET-ROSSI": (
        FactorRecord("returns_90d", 2, "2 recent returns"),
        FactorRecord("n_distinct_cards", 3, "3 distinct payment cards across the linked accounts"),
    ),
    "RET-RCARD": (
        FactorRecord("n_distinct_cards", 2, "2 distinct payment cards across the linked accounts"),
    ),
}


class GoldenReader:
    """The golden cases of spec 16 as the read-only data access of the agent."""

    def get_case(self, case_id: str) -> CaseRecord | None:
        return CASES.get(case_id)

    def get_orders(self, case_id: str) -> tuple[OrderRecord, ...]:
        order = CASES[case_id].order_id
        return (OrderRecord(order, date(2026, 9, 20), "TS-1", "base", NAVIGLI_BASE_TERMS),)

    def get_cluster(self, cluster_id: str) -> ClusterRecord | None:
        return CLUSTERS.get(cluster_id)

    def get_score_factors(self, case_id: str) -> tuple[FactorRecord, ...]:
        return FACTORS.get(case_id, ())

    def get_policy_text(self, case_id: str) -> PolicyRecord | None:
        return PolicyRecord("navigli-2026-10", "Returns within 30 days.")


LINK_QUESTIONS = ("Which evidence supports the link?", "Quali evidenze sostengono il collegamento?")
ROSSI_QUESTIONS = (
    "What makes this case different from the Rossi family?",
    "Cosa rende questo caso diverso dalla famiglia Rossi?",
)
TERMS_QUESTIONS = (
    "Which terms applied when the order was bought?",
    "Quali condizioni valevano quando l'ordine è stato acquistato?",
)
SUMMARY_QUESTIONS = ("What is this case about?", "Di cosa tratta questo caso?")
QUESTIONS = {
    "link": LINK_QUESTIONS,
    "rossi": ROSSI_QUESTIONS,
    "terms": TERMS_QUESTIONS,
    "summary": SUMMARY_QUESTIONS,
}


def tools_of(kind: str, case_id: str) -> list[tuple[str, str | None]]:
    cluster = CASES[case_id].cluster_id
    if kind == "terms":
        return [("get_case", None), ("get_orders", None), ("get_policy_text", None)]
    if kind == "summary":
        return [("get_case", None), ("get_score_factors", None)]
    tools: list[tuple[str, str | None]] = [("get_case", None)]
    if cluster is not None:
        tools.append(("get_cluster", cluster))
    if kind == "rossi" and cluster is not None:
        tools.append(("get_cluster", "F-ROSSI"))
    return tools


def ask(case_id: str, kind: str, question: str, final: str) -> Any:
    script: list[Any] = [
        calls(tool_call(name, {"cluster_id": arg} if arg else None))
        for name, arg in tools_of(kind, case_id)
    ]
    chat = ScriptedChat([*script, answer(final)])
    return VllmAgent(chat, GoldenReader()).ask(case_id, question)


# (case, kind of question, answer)
Answer = tuple[str, str, str]
ANSWERS: list[Answer] = [
    # ---- Which evidence supports the link?
    ("RET-MARCO", "link", "The customer's cluster R-MARCO has 7 linked accounts."),
    ("RET-MARCO", "link", "This customer sits in R-MARCO: 7 linked accounts, 2 distinct cards."),
    (
        "RET-MARCO",
        "link",
        "For this customer, the 7 linked accounts of R-MARCO share a device and a card.",
    ),
    ("RET-MARCO", "link", "The customer belongs to R-MARCO, a cluster of 7 linked accounts."),
    ("RET-MARCO", "link", "The customer returned the item; R-MARCO has 7 linked accounts."),
    (
        "RET-MARCO",
        "link",
        "Link L-101: C-MARCO-1 and C-MARCO-2 share a device and a card (4 shared events, weight "
        "0.98). Link L-102: C-MARCO-1 and C-MARCO-3 share an address (2 shared events, weight "
        "0.6), disputed by an operator.",
    ),
    (
        "RET-MARCO",
        "link",
        "R-MARCO has 7 linked accounts.\n"
        "- L-101: device and card, 4 shared events, weight 0.98\n"
        "- L-102: address, 2 shared events, weight 0.6, disputed by an operator",
    ),
    (
        "RET-MARCO",
        "link",
        "Il cluster R-MARCO ha 7 account collegati; i suoi legami sono L-101 (dispositivo e "
        "carta, peso 0.98) e L-102 (indirizzo, peso 0.6, contestato).",
    ),
    (
        "RET-MARCO",
        "link",
        "Evidenze del collegamento per R-MARCO:\n"
        "- L-101: C-MARCO-1 e C-MARCO-2 condividono dispositivo e carta, 4 eventi condivisi, "
        "peso 0.98\n"
        "- L-102: C-MARCO-1 e C-MARCO-3 condividono un indirizzo, 2 eventi condivisi, peso 0.6, "
        "contestato da un operatore",
    ),
    ("RET-MARCO", "link", "Link L-101 has weight (0.98) and 4 shared events."),
    ("RET-GIULIA", "link", "The customer has no linked accounts, so there is no link evidence."),
    (
        "RET-GIULIA",
        "link",
        "Il cliente non ha account collegati: non ci sono evidenze di collegamento.",
    ),
    (
        "RET-ROSSI",
        "link",
        "F-ROSSI has 3 linked accounts. Link L-201 joins C-ROSSI-1 and C-ROSSI-2 through a shared "
        "address (12 shared events, weight 0.4).",
    ),
    (
        "RET-ROSSI",
        "link",
        "Il cluster F-ROSSI ha 3 account collegati e le sue evidenze sono due indirizzi condivisi: "
        "L-201 e L-202, con peso 0.4.",
    ),
    # ---- What makes this case different from the Rossi family?
    (
        "RET-MARCO",
        "rossi",
        "Compared with the Rossi family (F-ROSSI), R-MARCO has more linked accounts (7 vs 3) and "
        "fewer distinct cards (2 vs 3).",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Rispetto alla famiglia Rossi (F-ROSSI), il cluster R-MARCO ha più account collegati (7 "
        "contro 3) e meno carte distinte (2 contro 3).",
    ),
    (
        "RET-MARCO",
        "rossi",
        "This case's cluster has more linked accounts than the Rossi family (7 vs 3).",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Compared with the Rossi family, this case's cluster has more linked accounts (7 vs 3), "
        "fewer distinct cards (2 vs 3) and a higher return rate (0.92 vs 0.18).",
    ),
    (
        "RET-MARCO",
        "rossi",
        "The main factor behind the difference is the number of linked accounts: 7 in R-MARCO, 3 "
        "in F-ROSSI.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Differenze tra R-MARCO e la famiglia Rossi (F-ROSSI):\n"
        "- account collegati: 7 contro 3\n"
        "- carte distinte: 2 contro 3",
    ),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO vs F-ROSSI:\n- 7 vs 3 linked accounts\n- 2 vs 3 distinct cards",
    ),
    (
        "RET-ELENA",
        "rossi",
        "The customer has no linked accounts, so there is nothing to compare with the Rossi "
        "family.",
    ),
    (
        "RET-GIULIA",
        "rossi",
        "Questo caso non ha account collegati, quindi non c'è un cluster da confrontare con la "
        "famiglia Rossi.",
    ),
    (
        "RET-RCARD",
        "rossi",
        "F-ROSSI-CARD has the same number of linked accounts (3) but fewer distinct cards (2 vs "
        "3).",
    ),
    (
        "RET-RCARD",
        "rossi",
        "F-ROSSI-CARD and F-ROSSI both have 3 linked accounts; F-ROSSI-CARD has 2 distinct cards, "
        "F-ROSSI has 3.",
    ),
    # ---- Which terms applied when the order was bought?
    (
        "RET-MARCO",
        "terms",
        "Order ORD-MARCO was bought with terms snapshot TS-1: return window 30 days, free return "
        "shipping, instant refund, free size exchange, coupons enabled.",
    ),
    (
        "RET-GIULIA",
        "terms",
        "L'ordine ORD-GIULIA è stato acquistato con lo snapshot TS-1: reso entro 30 giorni, "
        "spedizione del reso gratuita, rimborso istantaneo, cambio taglia gratuito.",
    ),
    (
        "RET-ROSSI",
        "terms",
        "Order ORD-ROSSI (terms snapshot TS-1):\n- return window: 30 days\n- free return "
        "shipping: yes\n- instant refund: yes\n- coupons: enabled",
    ),
    (
        "RET-ELENA",
        "terms",
        "The order ORD-ELENA was bought on 2026-09-20 under snapshot TS-1, the merchant base "
        "terms: a return window of 30 days and free return shipping.",
    ),
    # ---- What is this case about?
    ("RET-MARCO", "summary", "Top factor: cluster_welcome_coupons = 7."),
    # Final check of round 6: Italian "primo" ranks the first record of the tool order.
    (
        "RET-GIULIA",
        "summary",
        "RET-GIULIA è un caso Q1 sul percorso di recesso. Il primo fattore del punteggio è "
        "returns_90d = 4, il secondo è size_returns = 3.",
    ),
    ("RET-MARCO", "summary", "Il primo fattore è cluster_welcome_coupons = 7."),
    ("RET-MARCO", "link", "Il primo legame, L-101, ha peso 0.98 e 4 eventi condivisi."),
    ("RET-MARCO", "summary", "The main factor is cluster_welcome_coupons = 7."),
    (
        "RET-MARCO",
        "summary",
        "Case RET-MARCO is a Q4 case on the withdrawal path, with sufficient evidence. The score "
        "factors are cluster_welcome_coupons (7) and days_since_delivery (6).",
    ),
    (
        "RET-MARCO",
        "summary",
        "Case RET-MARCO:\n- path: withdrawal\n- quadrant: Q4\n- top factor: "
        "cluster_welcome_coupons = 7 (7 linked accounts each used the welcome coupon)",
    ),
    (
        "RET-ROSSI",
        "summary",
        "Case RET-ROSSI: withdrawal path, Q1, sufficient evidence. Top score factor: returns_90d "
        "= 2.",
    ),
    (
        "RET-GIULIA",
        "summary",
        "Il caso RET-GIULIA è sul percorso di recesso, in Q1, con evidenze sufficienti. Fattori "
        "principali: returns_90d = 4 e size_returns = 3.",
    ),
    (
        "RET-ELENA",
        "summary",
        "Case RET-ELENA follows the guarantee path: there is no quadrant and no score factor.",
    ),
    (
        "RET-ELENA",
        "summary",
        "This is a guarantee case: the return reason is a defect, so no risk score was computed.",
    ),
    # ---- A second batch, written apart from the review probes (round 5)
    (
        "RET-MARCO",
        "link",
        "The link evidence for R-MARCO: L-101 joins C-MARCO-1 and C-MARCO-2 through a shared "
        "device and card, with 4 shared events and weight 0.98; L-102 joins C-MARCO-1 and "
        "C-MARCO-3 through a shared address, with 2 shared events and weight 0.6, and it is "
        "disputed.",
    ),
    (
        "RET-MARCO",
        "link",
        "The strongest link is L-101, with weight 0.98 and 4 shared events between C-MARCO-1 "
        "and C-MARCO-2.",
    ),
    (
        "RET-MARCO",
        "link",
        "- L-101: weight 0.98, 4 shared events, device and card\n- L-102: weight 0.6, 2 shared"
        " events, address, disputed by an operator",
    ),
    (
        "RET-MARCO",
        "link",
        "Il legame più forte è L-101 (peso 0.98, 4 eventi condivisi): C-MARCO-1 e C-MARCO-2 "
        "condividono dispositivo e carta.",
    ),
    ("RET-MARCO", "link", "L-102 is disputed by an operator; its weight is 0.6."),
    (
        "RET-MARCO",
        "link",
        "The cluster has 7 linked accounts and 2 distinct cards; the links were first shared "
        "on 2026-03-02.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO has 7 linked accounts and 2 distinct cards, while F-ROSSI has 3 linked "
        "accounts and 3 distinct cards.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "The accounts of R-MARCO were created within 9 days, those of F-ROSSI over 2900 days.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Unlike the Rossi family, R-MARCO has a cluster return rate of 0.92 and 1 welcome "
        "coupon per account.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "The Rossi family (F-ROSSI) has 3 linked accounts with 3 distinct cards; R-MARCO has "
        "7 linked accounts with 2.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "- Linked accounts: 7 in R-MARCO, 3 in F-ROSSI\n- Distinct cards: 2 in R-MARCO, 3 in "
        "F-ROSSI\n- Return rate: 0.92 in R-MARCO, 0.18 in F-ROSSI",
    ),
    ("RET-MARCO", "rossi", "Il cluster R-MARCO ha 7 account collegati, la famiglia Rossi 3."),
    (
        "RET-MARCO",
        "terms",
        "The order ORD-MARCO was bought under terms snapshot TS-1 (source: base): return "
        "window of 30 days, free return shipping, instant refund, free size exchange and "
        "coupons enabled.",
    ),
    (
        "RET-MARCO",
        "terms",
        "At purchase, order ORD-MARCO had a 30 days return window and free return shipping "
        "(snapshot TS-1).",
    ),
    (
        "RET-ROSSI",
        "summary",
        "Case RET-ROSSI is in Q1 on the withdrawal path. The main score factors are "
        "returns_90d (2) and n_distinct_cards (3).",
    ),
    (
        "RET-GIULIA",
        "summary",
        "This case is a Q1 return on the withdrawal path; the top factors are returns_90d = 4"
        " and size_returns = 3.",
    ),
    (
        "RET-ELENA",
        "summary",
        "Il caso RET-ELENA segue il percorso di garanzia: non ha quadrante né fattori di "
        "punteggio.",
    ),
    (
        "RET-GIULIA",
        "rossi",
        "This case has no linked accounts, so it cannot be compared with the Rossi family cluster.",
    ),
    ("RET-ELENA", "link", "No linked accounts are on file for this case."),
    # ---- Round 6 (W1): "a vs b" with the records of the clause, in English and Italian
    ("RET-MARCO", "rossi", "R-MARCO has 7 vs 3 linked accounts."),
    ("RET-MARCO", "rossi", "R-MARCO has a cluster return rate of 0.92 vs 0.18."),
    (
        "RET-MARCO",
        "rossi",
        "Compared with F-ROSSI, R-MARCO has a cluster return rate of 0.92 vs 0.18.",
    ),
    ("RET-MARCO", "rossi", "R-MARCO ha 2 carte distinte contro 3."),
    (
        "RET-MARCO",
        "rossi",
        "Il cluster R-MARCO ha 7 account collegati contro 3, 2 carte distinte contro 3 e un tasso "
        "di reso di 0.92 contro 0.18.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Rispetto a F-ROSSI, R-MARCO ha un tasso di reso del cluster di 0.92 contro 0.18 e una "
        "quota di identificativi forti di 0.8 contro 0.3.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO has 7 linked accounts (F-ROSSI: 3) and 2 distinct cards (F-ROSSI: 3).",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Key differences: R-MARCO's accounts were created within 9 days (F-ROSSI: 2900 days), and "
        "its return rate is 0.92 (F-ROSSI: 0.18).",
    ),
    ("RET-MARCO", "rossi", "R-MARCO has 7 linked accounts instead of 3."),
    (
        "RET-MARCO",
        "rossi",
        "This case's cluster has 7 vs 3 linked accounts compared with the Rossi family.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Compared with the Rossi family, this case's cluster has 7 vs 3 linked accounts and 2 vs "
        "3 distinct cards.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO has 7 vs 3 linked accounts and 2 vs 3 distinct cards compared with F-ROSSI.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "The case cluster R-MARCO differs from the Rossi family (F-ROSSI) in size (7 vs 3 linked "
        "accounts), cards (2 vs 3) and return rate (0.92 vs 0.18).",
    ),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO vs F-ROSSI: 7 vs 3 linked accounts, 2 vs 3 distinct cards, return rate 0.92 vs "
        "0.18.",
    ),
    ("RET-MARCO", "rossi", "R-MARCO: 7 linked accounts vs 3; 2 distinct cards vs 3."),
    (
        "RET-RCARD",
        "rossi",
        "Compared with F-ROSSI, F-ROSSI-CARD also shares a card (L-301), and its strong "
        "identifier share is 0.5 against 0.3.",
    ),
    # ---- Round 6 (W4): ordinals that rank the factors, a lever value from get_case
    (
        "RET-MARCO",
        "summary",
        "RET-MARCO is a Q4 return on the withdrawal path. The first factor is "
        "cluster_welcome_coupons = 7, the second days_since_delivery = 6.",
    ),
    (
        "RET-MARCO",
        "summary",
        "Case RET-MARCO (Q4, withdrawal path). The top score factor is cluster_welcome_coupons, "
        "value 7: 7 linked accounts each used the welcome coupon. The second is "
        "days_since_delivery, value 6.",
    ),
    (
        "RET-MARCO",
        "summary",
        "RET-MARCO: Q4, levers return_window_days:14, instant_refund:false, coupons_enabled:false,"
        " transparency_notice:true.",
    ),
    ("RET-GIULIA", "summary", "RET-GIULIA is in Q1, with a 60 day return window lever."),
    # ---- Round 6 (W6): a case belongs to a cluster
    (
        "RET-ROSSI",
        "summary",
        "RET-ROSSI belongs to the Rossi family cluster F-ROSSI and is in Q1; the top score factor "
        "is returns_90d = 2.",
    ),
    # ---- Round 6: variants written apart from the verifier's probes
    ("RET-MARCO", "rossi", "R-MARCO has 7 linked accounts rather than 3."),
    ("RET-MARCO", "rossi", "R-MARCO ha 7 account collegati invece di 3."),
    ("RET-MARCO", "rossi", "R-MARCO ha 7 account collegati anziché 3."),
    (
        "RET-MARCO",
        "rossi",
        "Il cluster del caso ha 7 account collegati contro 3 della famiglia Rossi.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Compared with the Rossi family this case's cluster has 7 vs 3 linked accounts.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "Rispetto alla famiglia Rossi, R-MARCO ha 7 account collegati contro 3 e 2 carte distinte "
        "contro 3.",
    ),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO and F-ROSSI: 7 vs 3 linked accounts, 0.92 vs 0.18 return rate.",
    ),
    ("RET-MARCO", "rossi", "R-MARCO has 7 linked accounts compared with 3 in F-ROSSI."),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO has 2 distinct cards (F-ROSSI: 3) and a return rate of 0.92 (F-ROSSI: 0.18).",
    ),
    ("RET-MARCO", "rossi", "Linked accounts: 7 vs 3. Distinct cards: 2 vs 3."),
    (
        "RET-MARCO",
        "rossi",
        "R-MARCO's accounts were created within 9 days versus 2900 days for F-ROSSI.",
    ),
    ("RET-MARCO", "rossi", "The Rossi family has 3 linked accounts against 7 in R-MARCO."),
    ("RET-MARCO", "rossi", "The cluster (R-MARCO) has 7 linked accounts, the Rossi family 3."),
    ("RET-MARCO", "summary", "Il secondo fattore è days_since_delivery = 6."),
    ("RET-MARCO", "summary", "The case has the lever return_window_days:14: a 14 day window."),
    ("RET-MARCO", "link", "The second link, L-102, has weight 0.6 and is disputed."),
]
ANSWER_CASES = [
    (case_id, kind, question, final)
    for case_id, kind, final in ANSWERS
    for question in QUESTIONS[kind]
]


@pytest.mark.parametrize(("case_id", "kind", "question", "final"), ANSWER_CASES)
def test_correct_agent_answers_on_the_golden_cases_keep_the_model_path(
    case_id: str, kind: str, question: str, final: str
) -> None:
    result = ask(case_id, kind, question, final)
    assert result.source == "model", final


def test_the_answer_corpus_is_large_enough_and_covers_every_question() -> None:
    assert len(ANSWERS) >= 30
    assert {kind for _, kind, _ in ANSWERS} == set(QUESTIONS)
    assert any("\n- " in final for _, _, final in ANSWERS)
    assert len({final for _, _, final in ANSWERS}) == len(ANSWERS)


@pytest.mark.parametrize("case_id", sorted(CASES))
@pytest.mark.parametrize("kind", sorted(QUESTIONS))
def test_the_mock_agent_answers_pass_the_model_checks(case_id: str, kind: str) -> None:
    for question in QUESTIONS[kind]:
        mock = MockAgent(GoldenReader()).ask(case_id, question)
        assert mock.source == "mock"
        result = ask(case_id, kind, question, mock.text)
        assert result.source == "model", mock.text
