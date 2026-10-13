"""Atomic file writes, shared by every writer of the repository (review findings I2, P3).

A file is written under a temporary name next to its target, .<name>.part, then renamed over the
target, so a reader never sees a half-written file. The temporary file is always created anew,
after removing whatever was at its name, with O_CREAT | O_EXCL | O_NOFOLLOW: a symlink planted at
that name is never followed, so no other file is ever written. The rename replaces the target
itself, never what a symlink at the target points to. On any failure nothing is left at the
temporary name.

Used by contracts.io.write_table (store, truth and raw tables), the ASOS calibration and profile
report (pipeline/ingest/asos_profile.py) and the ASOS downloader (pipeline/ingest/asos_download.py).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

# A new file only: never an existing one, never through a symlink.
NEW_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)


def temp_path(target: Path) -> Path:
    """The temporary name of `target`: a hidden .part file in the same folder, so the rename
    stays on one file system."""
    return target.with_name(f".{target.name}.part")


def create_new(path: Path, mode: int = 0o644) -> int:
    """A file descriptor of a new file at `path`, after removing whatever was there."""
    path.unlink(missing_ok=True)
    return os.open(path, NEW_FILE_FLAGS, mode)


def install[T](target: Path, fill: Callable[[Path], T]) -> T:
    """Call fill(tmp) to create the temporary file of `target` (with create_new, or a hard
    link), then rename it over `target`. Returns what fill returns; leaves nothing at the
    temporary name, whether it succeeds or fails."""
    tmp = temp_path(target)
    tmp.unlink(missing_ok=True)
    try:
        result = fill(tmp)
        os.replace(tmp, target)
        return result
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write(target: Path, write: Callable[[BinaryIO], object]) -> Path:
    """Write `target` atomically: write(out) fills a new temporary file, which then replaces
    `target`. The folder must exist."""

    def fill(tmp: Path) -> None:
        with os.fdopen(create_new(tmp), "wb") as out:
            write(out)

    install(target, fill)
    return target
