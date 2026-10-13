"""Download ASOS GraphReturns from OSF, verify checksums, print a schema report (US-101).

Dataset: McGowan et al. (2023), "A Dataset for Learning Graph Representations to Predict Customer
Returns in Fashion Retail", arXiv:2302.14096. Licence: CC BY 4.0. Not redistributed in this repo.

Usage: python -m pipeline.ingest.asos_download [--dest data/raw/asos] [--verify-only] [--no-report]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OSF_FILES_URL = "https://api.osf.io/v2/nodes/c793h/files/osfstorage/"
ATTRIBUTION = (
    "ASOS GraphReturns dataset, McGowan et al. (2023), arXiv:2302.14096, "
    "https://osf.io/c793h, licensed CC BY 4.0."
)
MANIFEST = "MANIFEST.json"

FetchJson = Callable[[str], dict[str, Any]]
FetchBytes = Callable[[str], bytes]


@dataclass(frozen=True)
class RemoteFile:
    path: str  # path inside the OSF storage, without leading slash
    download_url: str
    size: int | None


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed https host
        data: dict[str, Any] = json.load(resp)
        return data


def _fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=600) as resp:  # noqa: S310
        body: bytes = resp.read()
        return body


def list_files(url: str, fetch_json: FetchJson = _fetch_json) -> Iterator[RemoteFile]:
    """Walk the OSF storage tree, following folders and pagination."""
    next_url: str | None = url
    while next_url:
        page = fetch_json(next_url)
        for item in page.get("data", []):
            attrs = item["attributes"]
            if attrs["kind"] == "folder":
                yield from list_files(
                    item["relationships"]["files"]["links"]["related"]["href"], fetch_json
                )
            else:
                yield RemoteFile(
                    path=attrs["materialized_path"].lstrip("/"),
                    download_url=item["links"]["download"],
                    size=attrs.get("size"),
                )
        next_url = page.get("links", {}).get("next")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_all(
    dest: Path,
    fetch_json: FetchJson = _fetch_json,
    fetch_bytes: FetchBytes = _fetch_bytes,
) -> dict[str, dict[str, Any]]:
    dest.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, Any]] = {}
    for remote in list_files(OSF_FILES_URL, fetch_json):
        target = dest / remote.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch_bytes(remote.download_url))
        manifest[remote.path] = {"sha256": sha256_of(target), "size": target.stat().st_size}
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def verify(dest: Path) -> list[str]:
    """Return the files whose checksum does not match the manifest (empty list = all good)."""
    manifest = json.loads((dest / MANIFEST).read_text())
    bad = []
    for rel, meta in manifest.items():
        target = dest / rel
        if not target.exists() or sha256_of(target) != meta["sha256"]:
            bad.append(rel)
    return bad


def report(dest: Path) -> str:
    """Schema and row count of every tabular file, as plain text."""
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq

    lines = [ATTRIBUTION, ""]
    for path in sorted(dest.rglob("*")):
        if path.suffix == ".parquet":
            meta = pq.read_metadata(path)
            lines.append(f"{path.relative_to(dest)}: {meta.num_rows} rows")
            lines.extend(f"  {f.name}: {f.type}" for f in pq.read_schema(path))
        elif path.suffix in {".csv", ".tsv"}:
            delimiter = "\t" if path.suffix == ".tsv" else ","
            table = pacsv.read_csv(path, parse_options=pacsv.ParseOptions(delimiter=delimiter))
            lines.append(f"{path.relative_to(dest)}: {table.num_rows} rows")
            lines.extend(f"  {f.name}: {f.type}" for f in table.schema)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path("data/raw/asos"))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)

    if not args.verify_only:
        manifest = download_all(args.dest)
        print(f"Downloaded {len(manifest)} files to {args.dest}")
    bad = verify(args.dest)
    if bad:
        print(f"Checksum mismatch: {bad}", file=sys.stderr)
        return 1
    print("Checksums OK")
    if not args.no_report:
        print(report(args.dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
