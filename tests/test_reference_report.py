"""The offline evaluation report (ml.reference.report) and rule 12 for pipeline.features.

The stores are the generator at scale 0.05, seed 11, from the illustrative example calibration:
every number is synthetic and none is a result.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from ml.reference import report
from pipeline.generator.calibration import load_calibration
from pipeline.generator.core import generate

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "contracts" / "examples" / "calibration.json"


def test_f13_the_evidence_sentence_is_read_from_the_data() -> None:
    """F13: whether the evidence was built with the account graph is read from
    n_observations_linked, never written as fixed text."""
    without = pd.DataFrame({"n_observations_linked": pd.array([None, None], dtype="Int64")})
    with_graph = pd.DataFrame({"n_observations_linked": pd.array([3, None], dtype="Int64")})
    text = report.linked_evidence_sentence([without])
    assert "null in every row" in text and "own settled lines" in text
    text = report.linked_evidence_sentence([without, with_graph])
    assert "1 of 4 rows" in text and "never counted" in text
    assert "was not available" not in text


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("report-data")
    cal = load_calibration(EXAMPLE)
    generate(cal, out, seed=11, scale=0.05, scenario="main")
    generate(cal, out, seed=11, scale=0.05, scenario="ood")
    return out


def test_report_covers_every_story_with_provenance_and_no_wall_clock(
    data: Path, tmp_path: Path
) -> None:
    results = tmp_path / "results.md"
    assert report.main(["--data", str(data), "--results", str(results)]) == 0
    text = results.read_text(encoding="utf-8")
    for heading in (
        "## A. Reference model",
        "## B. Residual with two references",
        "## C. Customer value",
        "## D. Evidence quality",
        "## E. Customer features by behaviour",
    ):
        assert heading in text, heading
    assert "Nature: synthetic" in text or "nature: synthetic" in text
    assert "Out-of-distribution" in text and "OOD" in text
    assert "C-GIULIA" in text and "never a measure of accuracy" in text
    assert "| 0 |" in text or "Mismatches" in text
    # Only the decision times of the split appear as instants: no wall-clock date was written.
    instants = set(re.findall(r"20\d\d-\d\d-\d\d \d\d:\d\dZ", text))
    assert instants <= {
        "2026-07-01 00:00Z",
        "2026-08-01 00:00Z",
        "2026-09-01 00:00Z",
        "2026-10-01 00:00Z",
    }


def test_running_services_code_never_reads_the_ground_truth() -> None:
    """Rule 12: pipeline.features is used by services and jobs alike; it never names truth/.
    Only the offline evaluation (ml.reference.report) and the risk labels of the offline risk
    job (ml.risk.labels, US-204) read truth labels."""
    literal = re.compile(r"[\"']truth")  # a string literal naming truth/ or a truth table
    for path in (ROOT / "pipeline" / "features").glob("*.py"):
        assert not literal.search(path.read_text(encoding="utf-8")), path
    readers = [
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "ml").rglob("*.py")
        if literal.search(p.read_text(encoding="utf-8"))
    ]
    assert sorted(readers) == ["ml/reference/report.py", "ml/risk/labels.py"]
