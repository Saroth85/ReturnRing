"""The US-204 results file (ml/risk/report.py) on hand-built metrics: what it calls a regression,
how it labels the request unit and how it reports FAIR-02 (review F8, F9, F12). Every value is a
made-up test fixture (nature: synthetic)."""

from __future__ import annotations

from typing import Any

from ml.risk.report import REQUEST_METRIC_HEADER, _deltas, _fair02_section, _metric_cells


def metrics(**changes: Any) -> dict[str, Any]:
    m: dict[str, Any] = {
        "n": 1000,
        "positives": 50,
        "auprc": 0.9,
        "precision_at": {"50": 0.9, "100": 0.8},
        "threshold": 0.2,
        "flagged": 50,
        "precision": 0.9,
        "recall": 0.9,
        "fpr": 0.005,
        "fpr_high_value": 0.001,
        "high_value_negatives": 300,
        "high_value_false_positives": 1,
        "ece": 0.005,
        "brier": 0.01,
    }
    return {**m, **changes}


def doc_with(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "ablation": [
            {"set": "A1", "status": "run", "test": before},
            {"set": "A2", "status": "run", "test": after},
        ]
    }


def test_f9_every_reported_metric_that_gets_worse_is_a_regression() -> None:
    """F9 (rule 13): precision, P@50, P@100, ECE and Brier count as well as AUPRC, recall, FPR
    and FPR high-value."""
    worse = metrics(
        auprc=0.89,
        precision_at={"50": 0.88, "100": 0.7},
        precision=0.8,
        recall=0.85,
        fpr=0.006,
        fpr_high_value=0.002,
        ece=0.006,
        brier=0.011,
    )
    (row,) = _deltas(doc_with(metrics(), worse), "test")
    listed = row[-1].split(", ")
    assert listed == [
        "AUPRC",
        "P@50",
        "P@100",
        "precision",
        "recall",
        "FPR",
        "FPR high-value",
        "ECE",
        "Brier",
    ]
    (same,) = _deltas(doc_with(metrics(), metrics()), "test")
    assert same[-1] == "none"
    (better,) = _deltas(doc_with(metrics(), metrics(precision=0.95, ece=0.004)), "test")
    assert better[-1] == "none"


def test_f9_a_missing_precision_is_not_a_regression_of_zero() -> None:
    (row,) = _deltas(doc_with(metrics(precision=None), metrics(precision=0.5)), "test")
    assert "precision" not in row[-1]


def test_f12_request_rows_count_requests_not_customers() -> None:
    """F12: the high-value FPR of the request unit counts requests of high-value non-abusive
    customers; the header says so."""
    header = " ".join(REQUEST_METRIC_HEADER)
    assert "FP requests / requests of high-value non-abusive customers" in header
    cells = _metric_cells(metrics(), 0.2)
    assert len(cells) == len(REQUEST_METRIC_HEADER)


def test_f8_fair02_is_reported_per_dimension_with_sizes_and_fails_above_5_points() -> None:
    doc = {
        "eval_end": "2026-10-01T00:00:00+00:00",
        "fair02": {
            "population": "held-out test customers",
            "non_abusers": 120,
            "fpr": 0.01,
            "fpr_restrictive": 0.01,
            "price_cuts": [10.0, 20.0],
            "gap_limit_pp": 5.0,
            "failures": ["Main category"],
            "passed": False,
            "rows": [
                {
                    "dimension": "Main category",
                    "segment": "C",
                    "non_abusers": 59,
                    "flagged": 4,
                    "fpr": 4 / 59,
                    "restrictive": 3,
                    "fpr_restrictive": 3 / 59,
                },
                {
                    "dimension": "Main category",
                    "segment": "A",
                    "non_abusers": 61,
                    "flagged": 0,
                    "fpr": 0.0,
                    "restrictive": 0,
                    "fpr_restrictive": 0.0,
                },
            ],
            "dimensions": [
                {
                    "dimension": "Main category",
                    "segments": 2,
                    "smallest_segment": 59,
                    "highest": "C",
                    "lowest": "A",
                    "gap_pp": 100 * 4 / 59,
                    "highest_restrictive": "C",
                    "lowest_restrictive": "A",
                    "gap_restrictive_pp": 100 * 3 / 59,
                    "passed": False,
                }
            ],
        },
    }
    text = "\n".join(_fair02_section(doc))
    assert "6.78" in text and "5.08" in text
    assert "**fail**" in text and "warning" not in text
    assert "59" in text and "61" in text  # segment sizes next to the rates
    assert "largest segment rate minus the smallest" in text
