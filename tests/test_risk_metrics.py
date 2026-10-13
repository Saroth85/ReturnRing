"""Metrics of the risk models (US-204, spec 8.4 and 8.6; MOD-04, MOD-06): hand-computed cases.

Every value here is a made-up test fixture (nature: synthetic)."""

from __future__ import annotations

import numpy as np
import pytest

from ml.risk.metrics import (
    average_precision,
    evaluate,
    false_positive_rate,
    high_value_mask,
    precision_at_k,
    precision_recall_at,
    threshold_for_precision,
)


def test_average_precision_by_hand() -> None:
    # Ranked: 0.9 (1), 0.8 (0), 0.7 (1), 0.1 (0). Precision at each positive: 1/1 and 2/3.
    y = [1, 0, 1, 0]
    p = [0.9, 0.8, 0.7, 0.1]
    assert average_precision(y, p) == pytest.approx((1.0 + 2 / 3) / 2)


def test_average_precision_treats_equal_scores_as_one_step() -> None:
    # Both rows at 0.5: one step, recall 1, precision 1/2.
    assert average_precision([1, 0], [0.5, 0.5]) == pytest.approx(0.5)


def test_average_precision_without_positives_is_none() -> None:
    assert average_precision([0, 0], [0.1, 0.2]) is None


def test_precision_at_k_without_ties() -> None:
    y = [1, 0, 1, 1, 0]
    p = [0.9, 0.8, 0.7, 0.2, 0.1]
    assert precision_at_k(y, p, 1) == 1.0
    assert precision_at_k(y, p, 2) == 0.5
    assert precision_at_k(y, p, 3) == pytest.approx(2 / 3)


def test_precision_at_k_shares_out_the_tie_at_the_boundary() -> None:
    # One clear positive on top, then four tied rows with one positive; k = 3 takes 2 of the 4
    # tied rows: expected positives 1 + 2 * 1/4.
    y = [1, 0, 1, 0, 0]
    p = [0.9, 0.5, 0.5, 0.5, 0.5]
    assert precision_at_k(y, p, 3) == pytest.approx((1 + 2 * 0.25) / 3)
    # The answer does not depend on the order of the tied rows.
    assert precision_at_k([1, 1, 0, 0, 0], p, 3) == pytest.approx((1 + 2 * 0.25) / 3)


def test_precision_at_k_rejects_k_out_of_range() -> None:
    with pytest.raises(ValueError):
        precision_at_k([1, 0], [0.5, 0.4], 3)
    with pytest.raises(ValueError):
        precision_at_k([1, 0], [0.5, 0.4], 0)


def test_precision_recall_at_threshold() -> None:
    y = [1, 0, 1, 0]
    p = [0.9, 0.6, 0.4, 0.1]
    assert precision_recall_at(y, p, 0.5) == (0.5, 0.5, 2)
    assert precision_recall_at(y, p, 0.95) == (0.0, 0.0, 0)


def test_threshold_is_the_lowest_score_reaching_the_precision_target() -> None:
    # Cumulative precision from the top: 1, 1, 2/3, 3/4, 3/5. Target 0.75: lowest t is 0.6.
    y = [1, 1, 0, 1, 0]
    p = [0.9, 0.8, 0.7, 0.6, 0.5]
    assert threshold_for_precision(y, p, 0.75) == 0.6
    assert threshold_for_precision(y, p, 0.6) == 0.5
    assert threshold_for_precision(y, p, 1.0) == 0.8


def test_threshold_respects_ties() -> None:
    # The two rows at 0.7 are flagged together: precision at 0.7 is 2/3, not 1/2 or 1.
    y = [1, 1, 0, 0]
    p = [0.9, 0.7, 0.7, 0.1]
    assert threshold_for_precision(y, p, 0.8) == 0.9
    assert threshold_for_precision(y, p, 0.6) == 0.7


def test_threshold_none_when_the_target_is_never_reached() -> None:
    assert threshold_for_precision([0, 1], [0.9, 0.1], 0.8) is None


def test_high_value_is_the_top_share_of_non_abusers() -> None:
    # Non-abusers' values 10..100; the 0.7 quantile is 73: the top three (80, 90, 100).
    y = [0] * 10 + [1]
    value = [float(v) for v in range(10, 101, 10)] + [1000.0]
    mask = high_value_mask(value, y)
    assert mask.tolist() == [False] * 7 + [True] * 3 + [False]


def test_false_positive_rate_overall_and_in_a_subset() -> None:
    y = [0, 0, 0, 0, 1]
    p = [0.9, 0.2, 0.8, 0.1, 0.9]
    assert false_positive_rate(y, p, 0.5) == (2, 4, 0.5)
    subset = np.array([True, True, False, False, True])
    assert false_positive_rate(y, p, 0.5, subset) == (1, 2, 0.5)
    assert false_positive_rate([1], [0.9], 0.5) == (0, 0, None)


def test_evaluate_ranks_on_the_raw_score_and_flags_on_the_calibrated_risk() -> None:
    y = np.array([1, 0, 1, 0, 0, 0])
    raw = np.array([0.95, 0.9, 0.85, 0.3, 0.2, 0.1])
    calibrated = np.array([0.8, 0.8, 0.8, 0.1, 0.1, 0.1])  # isotonic merged the top three
    value = np.array([0.0, 100.0, 0.0, 10.0, 20.0, 30.0])
    m = evaluate(y, calibrated, 0.5, value, rank=raw, top_k=(1, 2))
    assert m.auprc == pytest.approx((1 + 2 / 3) / 2)  # from the raw ranking
    assert m.precision_at == {1: 1.0, 2: 0.5}
    assert (m.flagged, m.precision, m.recall) == (3, pytest.approx(2 / 3), 1.0)
    assert m.fpr == pytest.approx(1 / 4)
    # Non-abusers' values 100, 10, 20, 30: the 0.7 quantile is 51, so only the 100 row counts.
    assert (m.high_value_false_positives, m.high_value_negatives) == (1, 1)
    assert m.fpr_high_value == 1.0


def test_evaluate_with_an_explicit_high_value_mask_and_no_threshold() -> None:
    y = np.array([1, 0, 0])
    p = np.array([0.6, 0.4, 0.2])
    m = evaluate(y, p, None, np.zeros(3), high_value=np.array([True, True, False]), top_k=(1, 5))
    assert m.flagged == 0 and m.precision is None and m.recall == 0.0
    assert m.high_value_negatives == 1  # an abuser never counts as a high-value non-abuser
    assert m.precision_at == {1: 1.0, 5: None}
    assert m.ece >= 0.0
