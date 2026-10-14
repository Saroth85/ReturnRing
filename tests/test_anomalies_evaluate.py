"""The offline evaluation of US-208 (MOD-10, MOD-12; E2E-04 and US-606 data side): scoring by
hand, and the report on a small generated store (the generator at scale 0.05, seed 11, from the
illustrative example calibration: every number is synthetic and none is a result)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from pipeline.anomalies import evaluate
from pipeline.generator.calibration import load_calibration
from pipeline.generator.core import generate

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "contracts" / "examples" / "calibration.json"
UTC_WEEK = pd.Timestamp("2026-03-02", tz="UTC")


def truth() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "variant_id": ["D1", "D2", "N1"],
            "base_return_rate": [0.3, 0.3, 0.3],
            "defective": [True, True, False],
            "defect_reason": ["defective", "not_as_described", None],
            "defect_start": [UTC_WEEK, UTC_WEEK, pd.NaT],
            "defect_end": [UTC_WEEK + pd.Timedelta(weeks=4), pd.NaT, pd.NaT],
        }
    )


def cells(rows: list[tuple[str, str, int, str]]) -> pd.DataFrame:
    """(variant, reason, week offset from UTC_WEEK, as_of) rows."""
    frame = pd.DataFrame(rows, columns=["variant_id", "reason", "offset", "as_of"])
    frame["week"] = UTC_WEEK + pd.to_timedelta(frame.pop("offset") * 7, unit="D")
    frame["as_of"] = pd.to_datetime(frame["as_of"], utc=True)
    return evaluate.label_cells(frame, truth())


def test_a_match_needs_the_defect_reason_and_the_window() -> None:
    labelled = cells(
        [
            ("D1", "defective", 0, "2026-03-16"),  # in the window
            ("D1", "defective", -1, "2026-03-16"),  # the week before the start
            ("D1", "defective", 4, "2026-04-06"),  # the week the defect ends
            ("D1", "size", 1, "2026-03-16"),  # another reason
            ("D2", "not_as_described", 30, "2026-10-01"),  # never fixed
            ("N1", "size", 0, "2026-03-16"),
        ]
    )
    assert labelled["match"].tolist() == [True, False, False, False, True, False]
    assert labelled["defective"].tolist() == [True, True, True, True, True, False]


def test_scores_count_variants_and_distinct_cells() -> None:
    labelled = cells(
        [
            ("D1", "defective", 0, "2026-03-09"),
            ("D1", "defective", 0, "2026-03-16"),  # the same cell in a later run
            ("D1", "size", 1, "2026-03-16"),
            ("D2", "size", 1, "2026-03-16"),  # a defective variant, wrong reason
            ("N1", "size", 0, "2026-03-16"),
        ]
    )
    flags = pd.Series(True, index=labelled.index)
    s = evaluate.score(labelled, flags, defective=2)
    assert (s.flagged, s.true, s.any_defect, s.cells, s.true_cells) == (3, 1, 2, 4, 1)
    assert s.precision == pytest.approx(1 / 3)
    assert s.precision_any_defect == pytest.approx(2 / 3)
    assert s.recall == pytest.approx(1 / 2)
    assert s.cell_precision == pytest.approx(1 / 4)
    none = evaluate.score(labelled, ~flags, defective=2)
    assert none.precision is None and none.recall == 0.0
    days = evaluate.first_flags(labelled, flags, truth())
    assert days.to_dict() == {"D1": 7.0}  # first matching run on 9 March, start on 2 March


def test_f11_catalogue_tasks_are_counted_per_variant_and_reason() -> None:
    """F11: catalogue.catalogue_task opens one task per (variant, reason). A defective variant
    flagged for its defect and for another reason is one true variant (MOD-10) but two tasks,
    one of them false; task precision counts it so."""
    labelled = cells(
        [
            ("D1", "defective", 0, "2026-03-09"),  # the defect: a true task
            ("D1", "defective", 1, "2026-03-16"),  # the same task in a later run and week
            ("D1", "other", 1, "2026-03-16"),  # the same variant, another reason: a false task
            ("N1", "size", 0, "2026-03-16"),  # a false task on a sound variant
        ]
    )
    s = evaluate.score(labelled, pd.Series(True, index=labelled.index), defective=2)
    assert (s.flagged, s.true) == (2, 1)  # variants: D1 true, N1 false
    assert (s.tasks, s.true_tasks) == (3, 1)
    assert s.precision == pytest.approx(1 / 2)
    assert s.task_precision == pytest.approx(1 / 3)


def test_f11_the_weekly_table_names_what_it_counts() -> None:
    header = evaluate.WEEKLY_HEADER
    assert "Variants flagged in any run" in header
    assert "Catalogue tasks (variant, reason)" in header and "Task precision" in header
    assert "Catalogue tasks (variants)" not in header


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("anomaly-data")
    cal = load_calibration(EXAMPLE)
    generate(cal, out, seed=11, scale=0.05, scenario="main")
    generate(cal, out, seed=11, scale=0.05, scenario="ood")
    return out


def test_report_covers_the_stories_with_provenance_and_no_wall_clock(
    data: Path, tmp_path: Path
) -> None:
    results = tmp_path / "results.md"
    assert evaluate.main(["--data", str(data), "--results", str(results)]) == 0
    text = results.read_text(encoding="utf-8")
    for heading in (
        "## A. Volume",
        "## B. MOD-10 on the main store",
        "## C. MOD-12: the out-of-distribution store",
        "## D. Flagged cells that match no defect window",
        "## E. Golden case: Elena and the jacket V-JKT-DEF",
        "## F. Follow-up after a change (US-606)",
        "## G. Known limits, measured",
        "## H. Decision-time availability (MOD-08)",
    ):
        assert heading in text, heading
    assert "nature synthetic" in text and "Nature: synthetic" in text
    assert "1. Spec 8.9 read literally" in text and "the product rule" in text
    assert "| Path (policy.engine.choose_path) | guarantee |" in text
    assert "restrictive levers none" in text and "approval required no" in text
    assert "| Customer columns in features/product_anomalies | none |" in text
    assert "never a measure of accuracy" in text
    assert "| NO |" not in text  # MOD-08: identical rows at every checked decision time
    # Only decision times appear as instants (Mondays 00:00 UTC and the extraction instant),
    # besides the instant Elena's return was known, a fact of the store.
    scanned = "\n".join(x for x in text.splitlines() if not x.startswith("| Her return"))
    for instant in set(re.findall(r"20\d\d-\d\d-\d\d \d\d:\d\dZ", scanned)):
        t = pd.Timestamp(instant.replace("Z", ""))
        assert t.hour == 0 and t.minute == 0, instant
        assert t.weekday() == 0 or t == pd.Timestamp("2026-10-01"), instant
    again = tmp_path / "again.md"
    assert evaluate.main(["--data", str(data), "--results", str(again)]) == 0
    assert again.read_text(encoding="utf-8") == text  # the same store gives the same file
