"""Top 3 contributions in fixed phrases (US-204, spec 8.4; MOD-05; US-302): the phrase table, the
choice of the factors and their JSON. Every value is a made-up test fixture (nature: synthetic)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from contracts.parquet import is_forbidden_feature
from llm.text import (
    banned_terms,
    decision_wording,
    foreign_letters,
    quadrant_axes,
    quadrant_labels,
    restriction_words,
)
from ml.risk.explain import (
    ALONE_LABELS,
    DIRECTIONS,
    LABELS,
    NOT_A_CAUSE,
    TOP,
    Factor,
    Lang,
    check_factors,
    direction_of,
    factors_json,
    phrase,
    top_factors,
)
from ml.risk.features import all_feature_names


def every_phrase() -> list[str]:
    langs: tuple[Lang, ...] = ("en", "it")
    features = [*LABELS, "graphsage_0"]
    return [
        phrase(f, c, lang, alone)
        for f in features
        for c in (1.0, -1.0, 0.0)
        for lang in langs
        for alone in (False, True)
    ]


def test_every_feature_has_a_phrase_in_both_languages() -> None:
    assert set(LABELS) == set(all_feature_names())
    for feature, (en, it) in LABELS.items():
        assert en and it and en != it, feature
        assert not is_forbidden_feature(feature)


def test_every_phrase_says_it_is_a_contribution_not_a_cause() -> None:
    assert phrase("n_welcome_coupons_used", 0.4, "en") == (
        "Welcome coupons used: raises the score (a contribution to the score, not a cause)"
    )
    assert phrase("n_welcome_coupons_used", -0.4, "it") == (
        "Coupon di benvenuto usati: abbassa il punteggio (un contributo al punteggio, non una "
        "causa)"
    )
    for text in every_phrase():
        assert NOT_A_CAUSE[0] in text or NOT_A_CAUSE[1] in text
        assert len(text) <= 200  # llm_case top_factors.phrase maxLength


def test_phrases_hold_no_number_no_verdict_and_no_decision() -> None:
    """The LLM may quote a phrase: it holds no number to anchor, no banned or decision word, no
    quadrant and no restriction (llm.text checks of spec 8.7)."""
    for text in every_phrase():
        assert not any(ch.isdigit() for ch in text), text
        assert banned_terms(text) == [], text
        assert decision_wording(text) == [], text
        assert quadrant_labels(text) == [] and quadrant_axes(text) == [], text
        assert restriction_words(text) == [], text
        assert foreign_letters(text) == [], text


def test_top_factors_rank_by_absolute_contribution_among_known_values() -> None:
    features = ("size_share", "changed_mind_share", "other_reason_share", "coupon_order_share")
    values = np.array([[0.1, np.nan, 0.3, 0.4], [1.0, 2.0, 3.0, 4.0]])
    contributions = np.array(
        [
            [0.5, 9.0, -0.7, 0.1, 5.0],  # b is missing: never a factor; the bias (5.0) neither
            [0.0, 0.2, -0.2, 0.0, 1.0],  # tie of |0.2|: feature order; then a zero
        ]
    )
    rows = top_factors(features, values, contributions)
    assert [f.feature for f in rows[0]] == [
        "other_reason_share",
        "size_share",
        "coupon_order_share",
    ]
    assert [f.contribution for f in rows[0]] == [-0.7, 0.5, 0.1]
    assert [f.feature for f in rows[1]] == [
        "changed_mind_share",
        "other_reason_share",
        "size_share",
    ]
    assert rows[1][2].as_dict()["direction"] == "unchanged"


def test_top_factors_refuse_bad_shapes_and_forbidden_features() -> None:
    with pytest.raises(ValueError):
        top_factors(("a",), np.zeros((1, 1)), np.zeros((1, 1)))
    with pytest.raises(ValueError, match="rule 6"):
        top_factors(("shipping_country",), np.zeros((1, 1)), np.zeros((1, 2)))
    with pytest.raises(ValueError, match="phrase"):
        top_factors(("free_text_feature",), np.zeros((1, 1)), np.zeros((1, 2)))


def test_embedding_components_share_one_label() -> None:
    rows = top_factors(("graphsage_0", "n_orders"), np.ones((1, 2)), np.array([[0.3, 0.1, 0.0]]))
    assert rows[0][0].as_dict()["phrase_en"].startswith("Position in the graph of accounts")


def test_factors_json_is_canonical() -> None:
    factors = [Factor("n_returns", 3.0, 0.25), Factor("tenure_days", 12.5, -0.125)]
    data = json.loads(factors_json(factors))
    assert [d["feature"] for d in data] == ["n_returns", "tenure_days"]
    assert data[0]["value"] == 3.0 and data[0]["contribution"] == 0.25
    assert data[0]["direction"] == "raises" and data[1]["direction"] == "lowers"
    assert data[1]["phrase_it"].startswith("Giorni dall'apertura dell'account: abbassa")
    assert factors_json(factors) == factors_json(list(factors))


def test_f18_directions_use_the_api_vocabulary() -> None:
    """F18: one vocabulary for the direction of a contribution, the API's ScoreContribution words
    (raises, lowers) plus unchanged for a zero contribution."""
    assert set(DIRECTIONS) == {"raises", "lowers", "unchanged"}
    assert [direction_of(x) for x in (0.2, -0.2, 0.0)] == ["raises", "lowers", "unchanged"]
    for value, word in ((0.2, "raises"), (-0.2, "lowers"), (0.0, "unchanged")):
        assert Factor("n_returns", 1.0, value).as_dict()["direction"] == word


def test_check_factors_counts_complete_rows() -> None:
    full = [Factor(f, 1.0, 0.1) for f in ("n_orders", "n_lines", "n_returns")]
    check = check_factors([full, full[:2]])
    assert (check.rows, check.with_three, check.forbidden) == (2, 1, ())
    assert not check.passed and check_factors([full]).passed
    assert check_factors([]).passed is False
    assert TOP == 3


def test_an_account_alone_is_never_described_with_linked_accounts() -> None:
    """Rule 11: cluster features of an account without a linked account are its own."""
    features = ("cluster_size", "cluster_return_rate", "n_returns")
    values = np.array([[1.0, 0.9, 5.0], [4.0, 0.9, 5.0]])
    contributions = np.array([[0.1, 2.0, 0.5, 0.0], [0.1, 2.0, 0.5, 0.0]])
    alone, linked = top_factors(features, values, contributions)
    assert (
        alone[0]
        .as_dict()["phrase_en"]
        .startswith(
            "Share of the delivered order lines of this account returned (it has no linked account)"
        )
    )
    assert (
        linked[0]
        .as_dict()["phrase_en"]
        .startswith("Share of the delivered order lines of the linked accounts returned")
    )
    assert (
        alone[2]
        .as_dict()["phrase_it"]
        .startswith("Dimensione del gruppo di account collegati (questo account non ne ha)")
    )
    assert set(ALONE_LABELS) == {
        "cluster_size",
        "shared_strong_ids",
        "welcome_coupons",
        "n_distinct_cards",
        "creation_spread_days",
        "welcome_coupons_per_account",
        "cluster_return_rate",
        "strong_id_share",
    }
