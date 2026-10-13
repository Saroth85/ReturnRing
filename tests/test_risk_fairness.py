"""FAIR-02 and FAIR-03 (US-204, US-605) on hand-built scores, and the scores/request_scores
contract (ADR-13 amendment 4). Every value is a made-up test fixture (nature: synthetic)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from contracts.io import validate_table
from contracts.parquet import SCHEMAS
from ml.risk.fairness import GAP_LIMIT_PP, fair02, fair03, segment_labels

T = datetime(2026, 10, 1, tzinfo=UTC)


def scores(rows: list[tuple[str, float, str | None]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["customer_id", "risk", "quadrant"])


def test_fair02_rates_gaps_and_warnings() -> None:
    # Ten non-abusive customers and one abusive; threshold 0.5.
    s = scores(
        [
            ("N1", 0.9, "Q4"),  # new, flagged and restricted
            ("N2", 0.1, "Q2"),
            ("O1", 0.1, "Q1"),
            ("O2", 0.1, "Q2"),
            ("O3", 0.6, None),  # flagged, insufficient evidence: no quadrant
            ("O4", 0.1, "Q2"),
            ("O5", 0.1, "Q2"),
            ("O6", 0.1, "Q2"),
            ("O7", 0.1, "Q1"),
            ("O8", 0.1, "Q2"),
            ("X1", 0.9, "Q4"),  # abusive: never a false positive
        ]
    )
    segments = pd.DataFrame(
        {"New customers": ["new"] * 2 + ["old"] * 8 + ["new"]}, index=s["customer_id"]
    )
    abusive = pd.Series([0] * 10 + [1], index=s["customer_id"])
    everyone = pd.Series(True, index=s["customer_id"])
    report = fair02(s, segments, abusive, everyone, 0.5, (10.0, 20.0), "fixture")
    assert (report.non_abusers, report.flagged, report.restrictive) == (10, 2, 1)
    assert report.fpr == 0.2 and report.fpr_restrictive == 0.1
    rows = {r.segment: r for r in report.rows}
    assert (rows["new"].non_abusers, rows["new"].flagged, rows["new"].restrictive) == (2, 1, 1)
    assert (rows["old"].fpr, rows["old"].fpr_restrictive) == (0.125, 0.0)
    (dim,) = report.dimensions
    # Spec 12 (review F8): the gap is between segments, the largest rate minus the smallest.
    assert dim.gap_pp == pytest.approx(50.0 - 12.5)
    assert dim.gap_restrictive_pp == pytest.approx(50.0)
    assert (dim.highest, dim.lowest) == ("new", "old")
    assert dim.smallest_segment == 2 and not dim.passed
    assert not report.passed and report.failures == ["New customers"]
    assert report.as_dict()["failures"] == ["New customers"]
    assert GAP_LIMIT_PP == 5.0


def test_f8_fair02_gap_is_between_segments_not_against_the_population() -> None:
    """F8: segment C at 4/59, five other segments at 0 and a large segment at 30/5,000. Against
    the pooled rate the gap of C is under 6.3 points; between segments it is 6.78 points, above
    the 5-point limit of spec 12, so FAIR-02 fails (never a 'warning')."""
    ids = (
        [f"C{i}" for i in range(59)] + [f"A{i}" for i in range(38)] + [f"J{i}" for i in range(5000)]
    )
    flagged = {f"C{i}" for i in range(4)} | {f"J{i}" for i in range(30)}
    s = scores([(c, 0.9 if c in flagged else 0.1, "Q4" if c in flagged else "Q2") for c in ids])
    category = ["C"] * 59 + ["A"] * 38 + ["J"] * 5000
    segments = pd.DataFrame({"Main category": category}, index=s["customer_id"])
    abusive = pd.Series(0, index=s["customer_id"])
    everyone = pd.Series(True, index=s["customer_id"])
    report = fair02(s, segments, abusive, everyone, 0.5, (0.0, 0.0), "fixture")
    (dim,) = report.dimensions
    assert 100 * (4 / 59 - report.fpr) < 6.3
    assert dim.gap_pp == pytest.approx(100 * 4 / 59)
    assert dim.gap_restrictive_pp == pytest.approx(100 * 4 / 59)
    assert (dim.highest, dim.lowest, dim.smallest_segment) == ("C", "A", 38)
    assert not dim.passed and not report.passed


def test_fair02_counts_only_the_population_and_handles_no_threshold() -> None:
    s = scores([("A", 0.9, "Q3"), ("B", 0.9, "Q3"), ("C", 0.1, "Q1")])
    segments = pd.DataFrame({"Tenure": ["x", "x", "x"]}, index=s["customer_id"])
    abusive = pd.Series([0, 0, 0], index=s["customer_id"])
    population = pd.Series([True, False, True], index=s["customer_id"])
    report = fair02(s, segments, abusive, population, None, (0.0, 0.0), "fixture")
    assert report.non_abusers == 2 and report.flagged == 0 and report.restrictive == 1
    (dim,) = report.dimensions
    assert dim.gap_pp == 0.0 and dim.passed and report.passed  # one segment: no gap


def test_fair03_counts_families_restricted_because_of_the_cluster() -> None:
    s = scores([("F1", 0.9, "Q3"), ("F2", 0.9, "Q4"), ("F3", 0.1, "Q2"), ("N1", 0.9, "Q4")])
    alone = pd.Series({"F1": "Q1", "F2": "Q4", "F3": "Q2", "N1": "Q2"})
    behaviour = pd.Series(
        {"F1": "shared_benign", "F2": "shared_benign", "F3": "shared_benign", "N1": "normal"}
    )
    everyone = pd.Series(True, index=s["customer_id"])
    check = fair03(s, alone, behaviour, everyone, "fixture")
    # F1 is in Q3 only with its cluster; F2 is in Q4 with or without it; N1 is not a family.
    assert (check.families, check.restrictive, check.because_of_cluster) == (3, 2, 1)
    assert not check.passed
    assert fair03(s, s.set_index("customer_id")["quadrant"], behaviour, everyone, "x").passed


def test_f2_fair03_output_holds_counts_never_customer_ids() -> None:
    """F2 (rule 12): the FAIR-03 output lands in artifacts; it names no customer, since a
    customer listed there would carry its ground-truth behaviour (shared_benign) with it."""
    s = scores([("F1", 0.9, "Q3"), ("F2", 0.1, "Q2")])
    alone = pd.Series({"F1": "Q1", "F2": "Q2"})
    behaviour = pd.Series({"F1": "shared_benign", "F2": "shared_benign"})
    out = fair03(s, alone, behaviour, pd.Series(True, index=s["customer_id"]), "x").as_dict()
    assert out["because_of_cluster"] == 1
    assert "F1" not in json.dumps(out) and "customers" not in out


def test_segment_labels() -> None:
    frame = pd.DataFrame(
        {
            "customer_id": ["A", "B", "C", "D"],
            "tenure_days": [10.0, 200.0, 500.0, 1000.0],
            "cluster_size": [1.0, 3.0, 1.0, 2.0],
            "n_observations": [0.0, 3.0, 4.0, 12.0],
            "mean_line_price_eur": [np.nan, 10.0, 20.0, 30.0],
        }
    )
    behaviour = pd.Series({"A": "normal", "B": "shared_benign", "C": "size", "D": "wardrober"})
    categories = pd.Series({"B": "Jeans", "C": "Jeans", "D": "Shoes"})
    population = pd.Series(True, index=["A", "B", "C", "D"])
    seg, cuts = segment_labels(frame, behaviour, categories, population, min_observations=4)
    assert seg.loc["A", "New customers"] == "opened under 90 days ago"
    assert seg.loc["B", "Linked accounts"] == "in a group of linked accounts"
    assert seg.loc["B", "Families (ground truth)"] == "family member"
    assert seg.loc["B", "Low-data groups"] == "fewer than 4 settled lines"
    assert seg.loc["C", "Low-data groups"] == "4 or more settled lines"
    assert seg["Tenure"].tolist() == [
        "under 90 days",
        "90 days to 1 year",
        "1 to 2 years",
        "2 years or more",
    ]
    assert cuts == pytest.approx((10 + 10 / 1.5, 20 + 10 / 3))
    assert seg.loc["A", "Price band (mean line price)"] == "no line ordered"
    assert seg.loc["A", "Main category"] == "no line ordered"
    assert seg.loc["D", "Main category"] == "Shoes"


def request_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "return_id": "RET-1",
        "customer_id": "C-1",
        "decided_at": datetime(2026, 9, 20, tzinfo=UTC),
        "path": "withdrawal",
        "context_as_of": datetime(2026, 9, 1, tzinfo=UTC),
        "risk": 0.42,
        "risk_threshold": 0.5,
        "evidence_quality": "sufficient",
        "value_eur": Decimal("12.50"),
        "value_threshold_eur": Decimal("100.00"),
        "quadrant": "Q2",
        "shap_top3": "[]",
        "model_version": "risk-request",
        "as_of": T,
    }
    row.update(changes)
    return row


def check(row: dict[str, Any]) -> list[str]:
    schema = SCHEMAS["scores/request_scores"]
    return validate_table("scores/request_scores", pa.Table.from_pylist([row], schema=schema))


def test_request_scores_contract_accepts_a_scored_and_a_guarantee_row() -> None:
    assert check(request_row()) == []
    guarantee = request_row(
        path="guarantee",
        context_as_of=None,
        risk=None,
        risk_threshold=None,
        evidence_quality=None,
        value_eur=None,
        value_threshold_eur=None,
        quadrant=None,
        shap_top3=None,
        model_version=None,  # ADR-13 amendment 5 (review F20): no version, not scored
    )
    assert check(guarantee) == []


@pytest.mark.parametrize(
    ("changes", "column"),
    [
        ({"path": "guarantee", "quadrant": None}, "risk"),  # a guarantee row is never scored
        ({"risk": None}, "risk"),  # a withdrawal row always is
        ({"evidence_quality": "insufficient"}, "quadrant"),  # no quadrant without evidence
        ({"context_as_of": datetime(2026, 9, 20, tzinfo=UTC)}, "context_as_of"),  # MOD-08
        ({"decided_at": datetime(2026, 10, 2, tzinfo=UTC)}, "decided_at"),  # after as_of
        ({"risk": 1.5}, "risk"),
        ({"path": "other"}, "path"),
    ],
)
def test_request_scores_contract_refuses(changes: dict[str, Any], column: str) -> None:
    found = check(request_row(**changes))
    assert any(column in e for e in found), found


def test_scores_contract_refuses_a_quadrant_without_sufficient_evidence() -> None:
    row = {
        "customer_id": "C-1",
        "risk": 0.2,
        "risk_threshold": 0.5,
        "evidence_quality": "insufficient",
        "value_eur": Decimal("1.00"),
        "value_threshold_eur": Decimal("100.00"),
        "quadrant": "Q2",
        "shap_top3": None,
        "model_version": "risk-customer",
        "as_of": T,
    }
    table = pa.Table.from_pylist([row], schema=SCHEMAS["scores/scores"])
    assert any("quadrant" in e for e in validate_table("scores/scores", table))
