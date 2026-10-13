"""US-101 command line: python -m pipeline.ingest (download -> verify -> convert -> profile).

Runs on the small replica pickles of tests/asos_fixtures.py (nature: synthetic), with the pinned
hashes pointed at them.
"""

from __future__ import annotations

import json
import pickle
import runpy
import sys
from pathlib import Path

import jsonschema
import pytest

from contracts.calibration import check_calibration
from contracts.io import read_table
from pipeline.ingest import asos_download as dl
from pipeline.ingest import cli
from tests.asos_fixtures import pin_of, write_replica

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "contracts/json/calibration.schema.json").read_text())


def args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--dest",
        str(tmp_path / "data/raw/asos"),
        "--data-root",
        str(tmp_path / "data"),
        "--calibration-out",
        str(tmp_path / "calibration.json"),
        "--report-out",
        str(tmp_path / "data/raw/asos_profile.txt"),
        *extra,
    ]


def test_full_run_from_a_local_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = write_replica(tmp_path / "cache")
    monkeypatch.setattr(dl, "EXPECTED", pins)
    assert cli.main(args(tmp_path, "--source-dir", str(tmp_path / "cache"))) == 0
    out = capsys.readouterr().out
    for step in ("download", "verify", "convert", "profile"):
        assert f"[{step}]" in out
    assert "peak resident memory" in out
    events, provenance = read_table("raw/asos_events", tmp_path / "data")
    assert events.num_rows == 8 and provenance.nature == "real"
    doc = json.loads((tmp_path / "calibration.json").read_text())
    jsonschema.validate(doc, SCHEMA)
    assert check_calibration(doc) == []
    assert doc["overall"]["n_events"] == 5
    assert "ADR-10" in (tmp_path / "data/raw/asos_profile.txt").read_text()


def test_skip_download_uses_the_files_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = write_replica(tmp_path / "data/raw/asos")
    monkeypatch.setattr(dl, "EXPECTED", pins)

    def no_network(url: str) -> None:
        raise AssertionError("no download expected")

    monkeypatch.setattr(dl, "open_url", no_network)
    assert cli.main(args(tmp_path, "--skip-download")) == 0
    assert "[download] skipped" in capsys.readouterr().out


def test_a_failed_verification_stops_before_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = write_replica(tmp_path / "data/raw/asos")
    monkeypatch.setattr(dl, "EXPECTED", pins)
    (tmp_path / "data/raw/asos/event_table_testing.p").write_bytes(b"tampered")
    assert cli.main(args(tmp_path, "--skip-download")) == 1
    err = capsys.readouterr().err
    assert "event_table_testing.p" in err
    assert not (tmp_path / "data/raw/asos_events.parquet").exists()
    assert not (tmp_path / "calibration.json").exists()


def test_a_failed_download_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = write_replica(tmp_path / "cache")
    monkeypatch.setattr(dl, "EXPECTED", pins)
    (tmp_path / "cache/customer_nodes_testing.p").write_bytes(b"tampered")
    assert cli.main(args(tmp_path, "--source-dir", str(tmp_path / "cache"))) == 1
    assert "customer_nodes_testing.p" in capsys.readouterr().err


def test_module_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["python -m pipeline.ingest", "--help"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("pipeline.ingest", run_name="__main__")
    assert stop.value.code == 0


def test_a_file_that_is_not_a_frame_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pinned bytes that hold something else than a DataFrame: exit 1, no calibration."""
    dest = tmp_path / "data/raw/asos"
    pins = write_replica(dest)
    data = pickle.dumps(slice(0, 1), protocol=4)
    (dest / "customer_nodes_testing.p").write_bytes(data)
    pins["customer_nodes_testing.p"] = pin_of(data)
    monkeypatch.setattr(dl, "EXPECTED", pins)
    assert cli.main(args(tmp_path, "--skip-download")) == 1
    assert "not a pandas DataFrame" in capsys.readouterr().err
    assert not (tmp_path / "calibration.json").exists()


def test_an_escaping_symlink_stops_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "data/raw/asos"
    dest.mkdir(parents=True)
    (dest / "event_table_training.p").symlink_to(tmp_path / "outside.p")
    assert cli.main(args(tmp_path, "--skip-download")) == 1
    assert "[verify] failed" in capsys.readouterr().err


def test_i3_a_calibration_path_that_cannot_be_written_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An OSError in convert or profile is a clean exit 1 with a message, and no .tmp is left
    (review I3). Here the calibration path is an existing directory."""
    pins = write_replica(tmp_path / "data/raw/asos")
    monkeypatch.setattr(dl, "EXPECTED", pins)
    (tmp_path / "calibration.json").mkdir()
    assert cli.main(args(tmp_path, "--skip-download")) == 1
    err = capsys.readouterr().err
    assert "[convert/profile] failed" in err and "calibration.json" in err
    assert not list(tmp_path.rglob("*.tmp")) and not list(tmp_path.rglob("*.part"))


def test_p3_a_raw_table_path_that_cannot_be_written_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review P3: the convert step writes through contracts.io.write_table. A directory in the
    way of raw/asos_events.parquet is a clean exit 1, and no temporary file is left in raw/."""
    pins = write_replica(tmp_path / "data/raw/asos")
    monkeypatch.setattr(dl, "EXPECTED", pins)
    (tmp_path / "data/raw/asos_events.parquet").mkdir()
    assert cli.main(args(tmp_path, "--skip-download")) == 1
    assert "[convert/profile] failed" in capsys.readouterr().err
    assert not list(tmp_path.rglob("*.tmp")) and not list(tmp_path.rglob("*.part"))


def test_i3_a_data_root_that_is_a_file_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = write_replica(tmp_path / "data/raw/asos")
    monkeypatch.setattr(dl, "EXPECTED", pins)
    root = tmp_path / "not-a-folder"
    root.write_text("a file")
    argv = args(tmp_path, "--skip-download")
    argv[argv.index("--data-root") + 1] = str(root)
    assert cli.main(argv) == 1
    assert "[convert/profile] failed" in capsys.readouterr().err
