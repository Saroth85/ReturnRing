"""US-101 without network: OSF listing, download, manifest, verification, report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.ingest import asos_download as dl

CSV = b"customer_id,return_rate\nc1,0.5\nc2,0.25\n"


def fake_json(url: str) -> dict[str, Any]:
    if url == dl.OSF_FILES_URL:
        return {
            "data": [
                {
                    "attributes": {"kind": "folder", "name": "train"},
                    "relationships": {"files": {"links": {"related": {"href": "folder-url"}}}},
                }
            ],
            "links": {"next": None},
        }
    return {
        "data": [
            {
                "attributes": {
                    "kind": "file",
                    "materialized_path": "/train/customers.csv",
                    "size": len(CSV),
                },
                "links": {"download": "download-url"},
            }
        ],
        "links": {},
    }


def fake_bytes(url: str) -> bytes:
    assert url == "download-url"
    return CSV


def test_download_manifest_verify_and_report(tmp_path: Path) -> None:
    manifest = dl.download_all(tmp_path, fake_json, fake_bytes)
    assert list(manifest) == ["train/customers.csv"]
    assert dl.verify(tmp_path) == []
    text = dl.report(tmp_path)
    assert "CC BY 4.0" in text and "train/customers.csv: 2 rows" in text


def test_tampered_file_is_detected(tmp_path: Path) -> None:
    dl.download_all(tmp_path, fake_json, fake_bytes)
    (tmp_path / "train/customers.csv").write_bytes(b"tampered")
    assert dl.verify(tmp_path) == ["train/customers.csv"]


def test_main_verify_only(tmp_path: Path, capsys: object) -> None:
    dl.download_all(tmp_path, fake_json, fake_bytes)
    assert dl.main(["--dest", str(tmp_path), "--verify-only"]) == 0
    (tmp_path / "train/customers.csv").write_bytes(b"tampered")
    assert dl.main(["--dest", str(tmp_path), "--verify-only", "--no-report"]) == 1
