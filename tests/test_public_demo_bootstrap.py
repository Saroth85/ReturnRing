"""Public artifact boundaries, using tiny synthetic fixtures and a real SQLite WAL."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import warnings
import zipfile
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from scripts import public_demo_bootstrap as demo


def _manifest(serving: Path) -> dict[str, Any]:
    table = serving / "store" / "customers.parquet"
    table.parent.mkdir(parents=True)
    table.write_bytes(b"tiny synthetic table fixture")
    manifest = {
        "format": "returnring.serving.v1",
        "seed": 7,
        "as_of": "2026-10-01T00:00:00+00:00",
        "tables": {"store/customers": {"rows": 2, "provenance": {"nature": "synthetic"}}},
        "files": {
            "store/customers.parquet": {
                "bytes": table.stat().st_size,
                "sha256": demo.digest(table),
            }
        },
    }
    _write_manifest(serving, manifest)
    return manifest


def _write_manifest(serving: Path, manifest: dict[str, Any]) -> None:
    (serving / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    serving = tmp_path / "source-serving"
    manifest = _manifest(serving)
    database = tmp_path / "source.sqlite"
    with closing(sqlite3.connect(database)) as db:
        db.execute("CREATE TABLE drafts (provider TEXT, body TEXT)")
        db.execute("INSERT INTO drafts VALUES (?, ?)", ("tokenfactory", "Synthetic draft"))
        db.commit()
    return serving, database, manifest


@pytest.fixture
def archive(source: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> Path:
    serving, database, _ = source
    out = tmp_path / "demo.zip"
    demo.package(serving, database, out)
    return out


def _rewrite(
    archive: Path,
    out: Path,
    *,
    metadata: Callable[[dict[str, Any]], None] | None = None,
    extra: Sequence[tuple[str | zipfile.ZipInfo, bytes]] = (),
    omit: Sequence[str] = (),
) -> Path:
    with zipfile.ZipFile(archive) as original:
        members = [(entry.filename, original.read(entry)) for entry in original.infolist()]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as rewritten:
        for name, data in members:
            if name in omit:
                continue
            if name == "PACKAGE.json" and metadata is not None:
                meta = json.loads(data)
                metadata(meta)
                data = json.dumps(meta).encode()
            rewritten.writestr(name, data)
        for name, data in extra:
            rewritten.writestr(name, data)
    return out


def _restore(archive: Path, destination: Path) -> dict[str, Any]:
    return demo.restore(archive, demo.digest(archive), destination)


def test_pack_and_restore_include_committed_wal_rows_and_no_sidecars(
    source: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    serving, database, _ = source
    out = tmp_path / "wal-demo.zip"
    with closing(sqlite3.connect(database)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO drafts VALUES (?, ?)", ("mock", "Committed in WAL"))
        writer.commit()
        assert Path(str(database) + "-wal").stat().st_size > 0
        result = demo.package(serving, database, out)
        # The source remains open with its WAL during the snapshot and its restore.
        destination = tmp_path / "restored"
        meta = _restore(out, destination)
        with closing(sqlite3.connect(destination / "demo.sqlite")) as restored:
            assert restored.execute("SELECT * FROM drafts ORDER BY rowid").fetchall() == [
                ("tokenfactory", "Synthetic draft"),
                ("mock", "Committed in WAL"),
            ]
            assert restored.execute("PRAGMA quick_check").fetchone() == ("ok",)
        with zipfile.ZipFile(out) as zipped:
            assert set(zipped.namelist()) == {
                "PACKAGE.json",
                "demo.sqlite",
                "serving/manifest.json",
                "serving/store/customers.parquet",
            }
        assert result["sha256"] == demo.digest(out)
        assert meta["nature"] == "synthetic" and meta["reset_on_restart"]
        assert meta["seed"] == 7 and meta["customers"] == 2


@pytest.mark.parametrize("nature", ["real", "simulated", "unknown"])
def test_pack_refuses_any_non_synthetic_table(
    source: tuple[Path, Path, dict[str, Any]], tmp_path: Path, nature: str
) -> None:
    serving, database, manifest = source
    manifest["tables"]["store/other"] = {"rows": 1, "provenance": {"nature": nature}}
    _write_manifest(serving, manifest)
    with pytest.raises(ValueError, match="synthetic"):
        demo.package(serving, database, tmp_path / "rejected.zip")


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/outside.txt",
        "store/../outside.txt",
        "store//customers.parquet",
        "store/./customers.parquet",
        r"store\customers.parquet",
        "C:/outside.txt",
        r"C:\outside.txt",
        "store/customers.parquet:stream",
        "truth/labels.parquet",
    ],
)
def test_pack_refuses_unsafe_or_noncanonical_manifest_paths(
    source: tuple[Path, Path, dict[str, Any]], tmp_path: Path, name: str
) -> None:
    serving, database, manifest = source
    manifest["files"] = {name: {"bytes": 0, "sha256": "0" * 64}}
    _write_manifest(serving, manifest)
    with pytest.raises(ValueError):
        demo.package(serving, database, tmp_path / "rejected.zip")


def test_pack_refuses_a_source_symlink_even_with_matching_hash(
    source: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    serving, database, manifest = source
    table = serving / "store" / "customers.parquet"
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(table.read_bytes())
    table.unlink()
    try:
        table.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    _write_manifest(serving, manifest)
    with pytest.raises(ValueError):
        demo.package(serving, database, tmp_path / "rejected.zip")


@pytest.mark.parametrize("field", ["bytes", "sha256"])
def test_pack_refuses_a_snapshot_changed_since_its_manifest(
    source: tuple[Path, Path, dict[str, Any]], tmp_path: Path, field: str
) -> None:
    serving, database, manifest = source
    expected = manifest["files"]["store/customers.parquet"]
    expected[field] = expected[field] + 1 if field == "bytes" else "0" * 64
    _write_manifest(serving, manifest)
    with pytest.raises(ValueError):
        demo.package(serving, database, tmp_path / "rejected.zip")


def test_restore_checks_outer_digest_before_creating_destination(
    archive: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="SHA-256"):
        demo.restore(archive, "0" * 64, destination)
    assert not destination.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/outside.txt",
        "serving/../outside.txt",
        "serving//alias.txt",
        "serving/./alias.txt",
        r"serving\alias.txt",
        "C:/outside.txt",
        "serving/file:stream",
        "serving/truth/labels.parquet",
        "unexpected.env",
    ],
)
def test_restore_rejects_unsafe_archive_members_without_extracting(
    archive: Path, tmp_path: Path, name: str
) -> None:
    # Preserve the raw name a ZIP produced on Linux could contain. Windows may normalize it
    # again when reading; that must still be rejected before extraction by the member allowlist.
    entry = zipfile.ZipInfo("placeholder")
    entry.filename = name
    altered = _rewrite(archive, tmp_path / "unsafe.zip", extra=[(entry, b"rejected")])
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="unsafe|files do not match"):
        _restore(altered, destination)
    assert not destination.exists()


def test_restore_rejects_zip_symlink_without_extracting(archive: Path, tmp_path: Path) -> None:
    entry = zipfile.ZipInfo("serving/link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    altered = _rewrite(archive, tmp_path / "symlink.zip", extra=[(entry, b"../outside")])
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="unsafe"):
        _restore(altered, destination)
    assert not destination.exists()


def test_restore_rejects_duplicate_members(archive: Path, tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        altered = _rewrite(archive, tmp_path / "duplicate.zip", extra=[("demo.sqlite", b"second")])
    with pytest.raises(ValueError, match="duplicate"):
        _restore(altered, tmp_path / "restored")


def test_restore_rejects_unlisted_serving_files(archive: Path, tmp_path: Path) -> None:
    altered = _rewrite(
        archive, tmp_path / "unlisted.zip", extra=[("serving/unlisted.txt", b"unexpected")]
    )
    with pytest.raises(ValueError, match="files do not match"):
        _restore(altered, tmp_path / "restored")


@pytest.mark.parametrize("field", ["format", "nature"])
def test_restore_requires_public_synthetic_package_metadata(
    archive: Path, tmp_path: Path, field: str
) -> None:
    altered = _rewrite(
        archive,
        tmp_path / "wrong-meta.zip",
        metadata=lambda meta: meta.update({field: "other"}),
    )
    with pytest.raises(ValueError, match="synthetic public-demo"):
        _restore(altered, tmp_path / "restored")


@pytest.mark.parametrize("field", ["bytes", "sha256"])
def test_restore_checks_per_file_size_and_hash_before_extracting(
    archive: Path, tmp_path: Path, field: str
) -> None:
    def change(meta: dict[str, Any]) -> None:
        info = meta["files"]["serving/store/customers.parquet"]
        info[field] = info[field] + 1 if field == "bytes" else "0" * 64

    altered = _rewrite(archive, tmp_path / "wrong-content.zip", metadata=change)
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        _restore(altered, destination)
    assert not destination.exists()


@pytest.mark.parametrize("name", ["PACKAGE.json", "demo.sqlite", "serving/manifest.json"])
def test_restore_refuses_missing_required_members(archive: Path, tmp_path: Path, name: str) -> None:
    altered = _rewrite(archive, tmp_path / "missing.zip", omit=[name])
    with pytest.raises(ValueError, match="missing|files do not match"):
        _restore(altered, tmp_path / "restored")


@pytest.mark.parametrize("limit", ["MAX_ARCHIVE", "MAX_EXPANDED"])
def test_restore_enforces_compressed_and_expanded_size_limits(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setattr(demo, limit, 1)
    with pytest.raises(ValueError, match="size|expansion"):
        _restore(archive, tmp_path / "restored")


def test_restore_detects_corrupt_sqlite_even_when_package_hashes_match(
    archive: Path, tmp_path: Path
) -> None:
    content = b"this is not a SQLite database"

    def change(meta: dict[str, Any]) -> None:
        meta["files"]["demo.sqlite"] = {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    altered = _rewrite(
        archive,
        tmp_path / "corrupt-db.zip",
        metadata=change,
        omit=["demo.sqlite"],
        extra=[("demo.sqlite", content)],
    )
    with pytest.raises((sqlite3.DatabaseError, ValueError)):
        _restore(altered, tmp_path / "restored")
