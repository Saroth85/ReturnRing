"""A0, the returnguard-like rule baseline (US-204, MOD-04): thresholds fitted by a fixed procedure,
missing values never fire. Every value is a made-up test fixture (nature: synthetic)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.risk.rules import QUANTILES, Rule, RuleBaseline, fit_rule


def test_a_rule_never_fires_on_a_missing_value() -> None:
    rule = Rule("return_rate_to_date", ">=", 0.5)
    assert rule.fires([0.4, 0.5, np.nan, 0.9]).tolist() == [False, True, False, True]
    assert Rule("median_days_to_return", "<=", 3.0).fires([2.0, 3.0, 4.0, np.nan]).tolist() == [
        True,
        True,
        False,
        False,
    ]


def test_fit_rule_takes_the_quantile_with_the_best_f1() -> None:
    # Positives have rates 0.8..0.95, negatives 0.0..0.75: the best rule splits at 0.8.
    rates = np.round(np.arange(0.0, 1.0, 0.05), 2)
    y = (rates >= 0.8).astype(int)
    rule = fit_rule("return_rate_to_date", rates, y)
    assert rule.direction == ">="
    assert rule.threshold == pytest.approx(float(np.quantile(rates, 0.8)))
    assert rule.fires(rates).tolist() == (rates >= 0.8).tolist()


def test_fit_rule_for_the_return_window_looks_at_short_delays() -> None:
    days = np.arange(1.0, 21.0)
    y = (days <= 4).astype(int)
    rule = fit_rule("median_days_to_return", days, y)
    assert rule.direction == "<="
    assert rule.fires(days).tolist() == (days <= 4).tolist()


def test_fit_rule_needs_a_known_value() -> None:
    with pytest.raises(ValueError):
        fit_rule("mean_line_price_eur", [np.nan, np.nan], [0, 1])


def test_the_quantile_grid_is_fixed() -> None:
    assert QUANTILES[0] == 0.05 and QUANTILES[-1] == 0.95 and len(QUANTILES) == 19


def test_the_baseline_scores_the_share_of_rules_that_fire_and_round_trips() -> None:
    frame = pd.DataFrame(
        {
            "return_rate_to_date": [0.9, 0.9, 0.1, np.nan],
            "median_days_to_return": [2.0, 9.0, 2.0, np.nan],
            "mean_line_price_eur": [80.0, 80.0, 10.0, 90.0],
        }
    )
    baseline = RuleBaseline(
        (
            Rule("return_rate_to_date", ">=", 0.5),
            Rule("median_days_to_return", "<=", 5.0),
            Rule("mean_line_price_eur", ">=", 50.0),
        )
    )
    assert baseline.fired(frame).tolist() == [3, 2, 1, 1]
    assert baseline.score(frame).tolist() == pytest.approx([1.0, 2 / 3, 1 / 3, 1 / 3])
    again = RuleBaseline.from_dict(baseline.to_dict())
    assert again == baseline
    with pytest.raises(ValueError):
        RuleBaseline.from_dict({"format": "other", "rules": []})
