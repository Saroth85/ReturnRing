"""Download ASOS GraphReturns from OSF and check every file against pinned hashes (US-101).

Dataset: McGowan et al. (2023), "A Dataset for Learning Graph Representations to Predict Customer
Returns in Fashion Retail", arXiv:2302.14096, https://osf.io/c793h. Licence: CC BY 4.0. It is not
redistributed in this repository.

The six files are pickles, and loading a pickle runs code, so integrity is a security control
(ADR-10, review findings ASOS-3, ASOS-4, ASOS-12):

- EXPECTED pins the name, size, sha256 and OSF download id of each file. Only these six names are
  ever written, and only as plain file names inside the destination.
- A download is streamed in chunks to a temporary file while it is hashed. It fails closed on any
  size or hash mismatch, and only a matching file is renamed into place.
- A file already present with the pinned hash is not fetched again, so an interrupted run resumes.
- --source-dir takes the files from a local cache: hard link when possible, copy otherwise, and
  the same check before the file is installed.
- verify() compares against EXPECTED. MANIFEST.json is only a log of the last run.

Usage: python -m pipeline.ingest.asos_download [--dest data/raw/asos] [--source-dir DIR]
                                               [--verify-only]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO, NamedTuple, cast

ATTRIBUTION = (
    "ASOS GraphReturns dataset, McGowan et al. (2023), arXiv:2302.14096, "
    "https://osf.io/c793h, licensed CC BY 4.0."
)
OSF_DOWNLOAD_URL = "https://osf.io/download/{osf_id}/"
MANIFEST = "MANIFEST.json"
CHUNK = 1 << 20  # bytes read at a time: no file is ever held in memory
TIMEOUT_S = 60


class Pinned(NamedTuple):
    """What a file must be: its sha256, its size in bytes and its OSF download id."""

    sha256: str
    size: int
    osf_id: str


# ADR-10 "Dataset facts for US-101"; OSF ids from the node listing of 12 Oct 2026.
EXPECTED: Mapping[str, Pinned] = MappingProxyType(
    {
        "event_table_training.p": Pinned(
            "6d61ce2fc645e6720aed243d45174bfc7fc139dbed70b9306798c648c6a7fed6", 32_859_910, "d9tmv"
        ),
        "event_table_testing.p": Pinned(
            "bc0ae494b0d95be651c988da8e83142a9371aedbf28a6264614a2656321602fa", 35_049_502, "v6nxs"
        ),
        "product_nodes_training.p": Pinned(
            "eb731fc46ba6712fb986971d0af235822ebc2317e38877f5627ee6b6f2f8d9e0", 143_204_587, "hu3m6"
        ),
        "product_nodes_testing.p": Pinned(
            "aa0d960208fb159df53a45a395de3c4dd06cff55cc9f43bf164f8843538974ef", 143_221_671, "qh4ec"
        ),
        "customer_nodes_training.p": Pinned(
            "8517d56a4a27d38be28f2b0b5fb0d5281c1c74246ebc666e2bad49e8efccfcae", 188_037_655, "34t69"
        ),
        "customer_nodes_testing.p": Pinned(
            "a927539fe661d037ae6fb78b7966c65a30cf1d2d9e44b3b0f0b415c57fea7d24", 199_798_243, "uwmb6"
        ),
    }
)

Opener = Callable[[str], BinaryIO]


class IntegrityError(Exception):
    """A file whose bytes are not the pinned ones: it is never installed or loaded."""


def pinned_files(expected: Mapping[str, Pinned] | None = None) -> Mapping[str, Pinned]:
    """The pins to check against: EXPECTED unless a test passes its own."""
    return EXPECTED if expected is None else expected


def download_url(osf_id: str) -> str:
    return OSF_DOWNLOAD_URL.format(osf_id=osf_id)


def open_url(url: str) -> BinaryIO:
    """Open an OSF download (https, fixed host; OSF redirects to its storage)."""
    if not url.startswith("https://osf.io/download/"):
        raise ValueError(f"refusing to fetch {url!r}: not an OSF download URL")
    return cast(BinaryIO, urllib.request.urlopen(url, timeout=TIMEOUT_S))  # noqa: S310


def target_path(directory: Path, name: str, expected: Mapping[str, Pinned] | None = None) -> Path:
    """The path of a pinned file inside `directory`. Refuses unknown names, names with path parts
    and paths that resolve outside the directory (ASOS-4)."""
    files = pinned_files(expected)
    if name not in files:
        raise ValueError(f"unknown ASOS file {name!r}: expected one of {sorted(files)}")
    if name in {"", ".", ".."} or any(part in name for part in ("/", "\\", "\0", "..")):
        raise ValueError(f"{name!r} is not a plain file name")
    root = directory.resolve()
    if (root / name).resolve().parent != root:
        raise ValueError(f"{directory / name} resolves outside {directory}")
    return directory / name


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def problem(path: Path, pin: Pinned) -> str | None:
    """Why the file at `path` is not the pinned file, or None if it is."""
    if not path.is_file():
        return "missing"
    size = path.stat().st_size
    if size != pin.size:
        return f"size {size:,} bytes, expected {pin.size:,}"
    digest = sha256_of(path)
    if digest != pin.sha256:
        return f"sha256 {digest}, expected {pin.sha256}"
    return None


def verify(dest: Path, expected: Mapping[str, Pinned] | None = None) -> dict[str, str]:
    """The pinned files that are missing or differ, with the reason; empty when all match."""
    files = pinned_files(expected)
    problems: dict[str, str] = {}
    for name, pin in files.items():
        found = problem(target_path(dest, name, files), pin)
        if found is not None:
            problems[name] = found
    return problems


def _stream_into(stream: BinaryIO, tmp: Path, pin: Pinned, origin: str) -> None:
    """Copy `stream` to `tmp` chunk by chunk while hashing; fail closed on size or hash."""
    digest = hashlib.sha256()
    size = 0
    with tmp.open("wb") as out:
        while chunk := stream.read(CHUNK):
            size += len(chunk)
            if size > pin.size:
                raise IntegrityError(f"{origin}: more than the pinned {pin.size:,} bytes")
            digest.update(chunk)
            out.write(chunk)
    if size != pin.size:
        raise IntegrityError(f"{origin}: {size:,} bytes, expected {pin.size:,}")
    if digest.hexdigest() != pin.sha256:
        raise IntegrityError(f"{origin}: sha256 {digest.hexdigest()}, expected {pin.sha256}")


def _from_cache(source: Path, pin: Pinned, tmp: Path) -> str:
    try:
        os.link(source, tmp)
    except OSError:  # another file system, or links not allowed: copy instead
        with source.open("rb") as stream:
            _stream_into(stream, tmp, pin, str(source))
        return "copied"
    found = problem(tmp, pin)  # hash the linked bytes, not the path checked earlier
    if found is not None:
        raise IntegrityError(f"{source}: {found}")
    return "linked"


def _install(target: Path, fill: Callable[[Path], str]) -> str:
    """Fill a temporary file next to `target`, then rename it into place; clean up on failure."""
    tmp = target.with_name(f".{target.name}.part")
    tmp.unlink(missing_ok=True)
    try:
        action = fill(tmp)
        os.replace(tmp, target)
        return action
    finally:
        tmp.unlink(missing_ok=True)


def _write_log(dest: Path, files: Mapping[str, Pinned], actions: Mapping[str, str]) -> None:
    log = {
        "attribution": ATTRIBUTION,
        "note": "Log of the last run only; files are checked against the hashes pinned in "
        "pipeline/ingest/asos_download.py (ADR-10).",
        "files": {
            name: {**files[name]._asdict(), "action": action} for name, action in actions.items()
        },
    }
    tmp = dest / f".{MANIFEST}.part"
    tmp.write_text(json.dumps(log, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, dest / MANIFEST)


def download(
    dest: Path,
    source_dir: Path | None = None,
    opener: Opener | None = None,
    names: Iterable[str] | None = None,
    expected: Mapping[str, Pinned] | None = None,
) -> dict[str, str]:
    """Install the pinned files into `dest` and return what was done to each one: "present",
    "linked" or "copied" (from source_dir) or "downloaded". Raises IntegrityError, leaving no
    partial file, if any bytes differ from their pin."""
    files = pinned_files(expected)
    fetch = open_url if opener is None else opener
    wanted = list(files if names is None else names)
    targets = {name: target_path(dest, name, files) for name in wanted}  # all names, then I/O
    dest.mkdir(parents=True, exist_ok=True)
    actions: dict[str, str] = {}
    for name, target in targets.items():
        pin = files[name]
        if problem(target, pin) is None:
            actions[name] = "present"
            continue
        source = None if source_dir is None else source_dir / name  # name checked above
        if source is not None and source.is_file():
            actions[name] = _install(target, partial(_from_cache, source, pin))
        else:
            actions[name] = _install(target, partial(_fetch, fetch, pin))
    _write_log(dest, files, actions)
    return actions


def _fetch(fetch: Opener, pin: Pinned, tmp: Path) -> str:
    url = download_url(pin.osf_id)
    with fetch(url) as stream:
        _stream_into(stream, tmp, pin, url)
    return "downloaded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and check ASOS GraphReturns (US-101)")
    parser.add_argument("--dest", type=Path, default=Path("data/raw/asos"))
    parser.add_argument("--source-dir", type=Path, help="local cache to link or copy from")
    parser.add_argument("--verify-only", action="store_true", help="check, do not download")
    args = parser.parse_args(argv)

    if not args.verify_only:
        try:
            actions = download(args.dest, source_dir=args.source_dir)
        except (IntegrityError, OSError, ValueError) as exc:
            print(f"ASOS download failed: {exc}", file=sys.stderr)
            return 1
        for name, action in actions.items():
            print(f"{name}: {action}")
    try:
        problems = verify(args.dest)
    except ValueError as exc:  # a path in dest that resolves outside it
        print(f"ASOS verification failed: {exc}", file=sys.stderr)
        return 1
    if problems:
        for name, reason in problems.items():
            print(f"{name}: {reason}", file=sys.stderr)
        return 1
    print(f"All {len(pinned_files())} ASOS files match the pinned sha256 (ADR-10). {ATTRIBUTION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
