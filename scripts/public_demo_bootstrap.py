"""Package or launch the small synthetic public demo; no model secret enters its archive.

Runtime state is temporary: each service start restores the same read-only demo, including
the three previously validated Gemma drafts. The live agent still uses Token Factory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT = "returnring.public-demo.v1"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024


def safe_path(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(
        name
        and not path.is_absolute()
        and ".." not in path.parts
        and "truth" not in path.parts
        and "\\" not in name
        and ":" not in name
        and path.as_posix() == name
    )


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(serving: Path, database: Path, out: Path) -> dict[str, Any]:
    manifest = json.loads((serving / "manifest.json").read_text("utf-8"))
    for name, entry in manifest["tables"].items():
        if entry["provenance"]["nature"] != "synthetic":
            raise ValueError(f"public demo requires synthetic tables: {name}")
    if not all(safe_path(name) for name in manifest["files"]):
        raise ValueError("unsafe serving manifest")
    paths = [PurePosixPath(name) for name in manifest["files"]]
    for path in paths:
        source = serving.joinpath(*path.parts)
        if (
            not source.resolve().is_relative_to(serving.resolve())
            or any(
                serving.joinpath(*path.parts[:i]).is_symlink()
                for i in range(1, len(path.parts) + 1)
            )
            or not source.is_file()
        ):
            raise ValueError("unsafe serving source")
        entry = manifest["files"][path.as_posix()]
        if source.stat().st_size != entry["bytes"] or digest(source) != entry["sha256"]:
            raise ValueError("serving source size or hash mismatch")
    files = {f"serving/{p.as_posix()}": serving.joinpath(*p.parts) for p in paths}
    files["serving/manifest.json"] = serving / "manifest.json"
    meta: dict[str, Any] = {
        "format": FORMAT,
        "nature": "synthetic",
        "seed": manifest["seed"],
        "customers": manifest["tables"]["store/customers"]["rows"],
        "purpose": "Small illustrative online demo; separate from MI300X training results",
        "reset_on_restart": True,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    # SQLite's backup API takes a consistent copy; no WAL or source database is copied blindly.
    with tempfile.TemporaryDirectory() as temp:
        backup = Path(temp) / "demo.sqlite"
        with (
            closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(backup)) as dst,
        ):
            src.backup(dst)
            dst.commit()
        files["demo.sqlite"] = backup
        meta["files"] = {
            name: {"bytes": path.stat().st_size, "sha256": digest(path)}
            for name, path in sorted(files.items())
        }
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, file_path in sorted(files.items()):
                archive.write(file_path, name)
            archive.writestr("PACKAGE.json", json.dumps(meta, indent=2) + "\n")
    return {"path": str(out), "bytes": out.stat().st_size, "sha256": digest(out), **meta}


def restore(archive_path: Path, expected_sha256: str, destination: Path) -> dict[str, Any]:
    if archive_path.stat().st_size > MAX_ARCHIVE or digest(archive_path) != expected_sha256:
        raise ValueError("demo archive size or SHA-256 mismatch")
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > 1000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise ValueError("demo archive exceeds expansion limits")
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive path")
        for entry in entries:
            if (
                not safe_path(entry.filename)
                or stat.S_ISLNK(entry.external_attr >> 16)
                or not (
                    entry.filename in {"PACKAGE.json", "demo.sqlite"}
                    or entry.filename.startswith("serving/")
                )
            ):
                raise ValueError("unsafe demo archive path")
        if "PACKAGE.json" not in names or archive.getinfo("PACKAGE.json").file_size > 512_000:
            raise ValueError("missing or oversized package manifest")
        meta: dict[str, Any] = json.loads(archive.read("PACKAGE.json"))
        if meta.get("format") != FORMAT or meta.get("nature") != "synthetic":
            raise ValueError("not a synthetic public-demo package")
        if set(names) != {"PACKAGE.json", *meta["files"]}:
            raise ValueError("package files do not match manifest")
        if not {"demo.sqlite", "serving/manifest.json"} <= set(names):
            raise ValueError("missing demo database or serving snapshot")
        # Validate every byte before exposing any package file to the application.
        for name, info in meta["files"].items():
            if archive.getinfo(name).file_size != info["bytes"]:
                raise ValueError("package file size mismatch")
            if hashlib.sha256(archive.read(name)).hexdigest() != info["sha256"]:
                raise ValueError("package file hash mismatch")
        archive.extractall(destination)
    with closing(
        sqlite3.connect((destination / "demo.sqlite").as_uri() + "?mode=ro", uri=True)
    ) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("demo database is corrupt")
    return meta


def launch() -> None:
    import uvicorn

    if not os.environ.get("RADEON_API_KEY"):
        raise ValueError("set RADEON_API_KEY in the hosting service's secret variables")
    expected = os.environ.get("DEMO_PACKAGE_SHA256", "")
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("DEMO_PACKAGE_SHA256 must be a lowercase SHA-256 digest")
    work = Path(tempfile.mkdtemp(prefix="returnring-public-demo-"))
    local = os.environ.get("DEMO_PACKAGE_FILE")
    archive_path = Path(local).resolve() if local else work / "download.zip"
    if not local:
        url = os.environ.get("DEMO_PACKAGE_URL", "")
        if not url.startswith("https://"):
            raise ValueError("set the HTTPS DEMO_PACKAGE_URL of the pinned synthetic archive")
        request = urllib.request.Request(url, headers={"User-Agent": "ReturnRing-public-demo/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            if not response.url.startswith("https://"):
                raise ValueError("demo download redirected away from HTTPS")
            with archive_path.open("wb") as target:
                remaining = MAX_ARCHIVE + 1
                while chunk := response.read(min(1024 * 1024, remaining)):
                    target.write(chunk)
                    remaining -= len(chunk)
                    if remaining <= 0:
                        raise ValueError("demo archive download exceeds size limit")
    data = work / "runtime"
    data.mkdir()
    meta = restore(archive_path, expected, data)
    os.environ["SERVING_DIR"] = str(data / "serving")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{data / 'demo.sqlite'}"
    for name, value in {
        "DEVICE": "cpu",
        "LLM_MODE": "tokenfactory",
        "DEMO_MODE": "live",
        "PUBLIC_READ_ONLY": "true",
        "DEMO_CLOCK": "on",
        "LLM_CONCURRENCY": "1",
        "VLLM_TIMEOUT_S": "90",
        "SEED": str(meta["seed"]),
    }.items():
        os.environ.setdefault(name, value)
    print(f"Synthetic public demo restored: {meta['customers']} customers; reset on restart")
    uvicorn.run(
        "apps.public_demo:create_public_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=1,
        proxy_headers=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    pack = commands.add_parser("pack")
    pack.add_argument("--serving", type=Path, required=True)
    pack.add_argument("--database", type=Path, required=True)
    pack.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "pack":
        result = package(args.serving.resolve(), args.database.resolve(), args.out.resolve())
        print(json.dumps({k: v for k, v in result.items() if k != "files"}, indent=2))
    else:
        launch()


if __name__ == "__main__":
    main()
