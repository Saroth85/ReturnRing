"""UNIT-02: the residual of spec 8.2 equals a hand computation on 5 customers (US-206).

Every number here is a made-up test value (nature: synthetic).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from pipeline.features.residual import RESIDUAL_COLUMNS, residual, residuals

# Customer: (outcomes r_l, reference probabilities p_l, R by hand, sum p(1-p) by hand, z by hand)
HAND = {
    # R = 2 - (0.2 + 0.5 + 0.1) = 1.2; V = 0.16 + 0.25 + 0.09 = 0.5; z = 1.2 / sqrt(0.5)
    "C-A": ([1, 0, 1], [0.2, 0.5, 0.1], 1.2, 0.5, 1.697056274847714),
    # R = 0 - 1.0 = -1.0; V = 0.25 + 0.25 = 0.5; z = -1 / sqrt(0.5)
    "C-B": ([0, 0], [0.5, 0.5], -1.0, 0.5, -1.4142135623730951),
    # R = 1 - 0.9 = 0.1; V = 0.09; z = 0.1 / 0.3
    "C-C": ([1], [0.9], 0.1, 0.09, 0.3333333333333333),
    # R = 4 - 1 = 3; V = 4 * 0.1875 = 0.75; z = 3 / sqrt(0.75)
    "C-D": ([1, 1, 1, 1], [0.25, 0.25, 0.25, 0.25], 3.0, 0.75, 3.4641016151377544),
    # No observed line: R = 0, V = 0, z undefined (null, never inf)
    "C-E": ([], [], 0.0, 0.0, None),
}


@pytest.mark.parametrize("customer", sorted(HAND))
def test_unit02_residual_matches_the_hand_computation(customer: str) -> None:
    r, p, value, variance, z = HAND[customer]
    got = residual(r, p)
    assert got.n == len(r)
    assert got.returned == sum(r)
    assert got.residual == pytest.approx(value, abs=1e-12)
    assert got.variance == pytest.approx(variance, abs=1e-12)
    if z is None:
        assert got.z is None
    else:
        assert got.z == pytest.approx(z, abs=1e-12)


def test_unit02_per_customer_table_matches_the_hand_computation() -> None:
    rows = [
        {"customer_id": c, "returned": ri, "p": pi}
        for c, (r, p, *_) in HAND.items()
        for ri, pi in zip(r, p, strict=True)
    ]
    out = residuals(pd.DataFrame(rows), customers=sorted(HAND)).set_index("customer_id")
    assert list(out.reset_index().columns) == ["customer_id", *RESIDUAL_COLUMNS]
    for customer, (r, p, value, variance, z) in HAND.items():
        row = out.loc[customer]
        assert row["n_observations"] == len(r)
        assert row["observed_returns"] == sum(r)
        assert row["expected_returns"] == pytest.approx(sum(p), abs=1e-12)
        assert row["residual"] == pytest.approx(value, abs=1e-12)
        assert row["residual_variance"] == pytest.approx(variance, abs=1e-12)
        if z is None:
            assert math.isnan(row["residual_z"])
        else:
            assert row["residual_z"] == pytest.approx(z, abs=1e-12)


def test_zero_variance_gives_no_z_and_never_inf() -> None:
    """p exactly 0 or 1 has zero variance: z is missing, not +/-inf (contracts reject inf)."""
    got = residual([0, 1], [0.0, 1.0])
    assert got.residual == 0.0 and got.variance == 0.0 and got.z is None
    got = residual([1], [0.0])
    assert got.residual == 1.0 and got.z is None
    table = residuals(pd.DataFrame({"customer_id": ["C"], "returned": [1], "p": [0.0]}))
    assert table["residual_z"].isna().all()
    assert all(math.isfinite(v) for v in table["residual"])


def test_returning_what_everybody_keeps_is_high_and_what_everybody_returns_is_near_zero() -> None:
    """Spec 8.2: returning items that almost everybody keeps gives a high z; returning items
    known to come back gives z near zero."""
    keeps = residual([1, 1, 1], [0.1, 0.1, 0.1])
    problematic = residual([1, 1, 1], [0.9, 0.9, 0.9])
    assert keeps.z is not None and problematic.z is not None
    assert keeps.z > 3 and abs(problematic.z) < 1


@pytest.mark.parametrize(
    ("r", "p", "message"),
    [
        ([1], [0.5, 0.5], "one probability per line"),
        ([1], [1.5], r"\[0, 1\]"),
        ([1], [float("nan")], r"\[0, 1\]"),
        ([2], [0.5], "0 or 1"),
    ],
)
def test_invalid_inputs_are_refused(r: list[int], p: list[float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        residual(r, p)


def test_table_refuses_probabilities_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        residuals(pd.DataFrame({"customer_id": ["C"], "returned": [1], "p": [-0.1]}))
