"""US-101 on the real ASOS GraphReturns files (origin: OSF c793h; nature: real; CC BY 4.0).

Runs only when ASOS_DIR points to a directory holding the six original pickles; skipped in CI,
where the dataset is not available (it is not redistributed). Example:

    ASOS_DIR=data/raw/asos python -m pytest tests/test_asos_real.py -m asos
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest

from contracts.calibration import check_calibration
from contracts.io import read_table
from pipeline.ingest import asos_convert as ac
from pipeline.ingest import asos_download as dl
from pipeline.ingest import asos_profile as ap

ASOS_DIR = os.environ.get("ASOS_DIR", "")
ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.asos,
    pytest.mark.skipif(not ASOS_DIR, reason="set ASOS_DIR to the six ASOS GraphReturns pickles"),
]

# ADR-10 dataset facts (origin: ASOS GraphReturns, OSF c793h; nature: real).
ROWS = {
    "raw/asos_events": {"train": 1_369_133, "test": 1_460_366},
    "raw/asos_customers": {"train": 777_001, "test": 825_598},
    "raw/asos_variants": {"train": 411_495, "test": 411_544},
}


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> Path:
    assert dl.verify(Path(ASOS_DIR)) == {}
    root = tmp_path_factory.mktemp("asos_data")
    assert ac.convert(Path(ASOS_DIR), root) == ROWS
    return root


def test_raw_tables_follow_the_contracts(converted: Path) -> None:
    for contract, rows in ROWS.items():
        table, provenance = read_table(contract, converted)
        assert table.num_rows == sum(rows.values())
        assert provenance.nature == "real"


def test_profile_reproduces_the_facts_of_adr10(converted: Path, tmp_path: Path) -> None:
    doc, text = ap.profile(converted, tmp_path / "calibration.json", tmp_path / "profile.txt")
    schema = json.loads((ROOT / "contracts/json/calibration.schema.json").read_text())
    jsonschema.validate(doc, schema)
    assert check_calibration(doc) == []
    overall = doc["overall"]
    assert overall["n_events"] == 1_369_133
    assert overall["event_label_rate"] == round(757_227 / 1_369_133, 6)
    assert 0.30 < overall["product_sales_weighted_return_rate"] < 0.32  # ADR-10 review: 0.310
    coverage = doc["join_coverage"]
    assert 0.94 < coverage["events_with_customer"] < 0.96  # ADR-10: about 95%
    assert 0.64 < coverage["events_with_variant"] < 0.66  # ADR-10: about 65% in train
    assert "ADR-10" in text
    committed = json.loads((ROOT / "pipeline/calibration/asos_calibration.json").read_text())
    assert committed == doc  # the committed file is what the profiler writes from the real data
