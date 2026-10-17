"""Licence gate (CLAUDE.md rule 15).

Runtime dependencies (requirements/base.txt) must be permissive. Development tools
(requirements/dev.txt) may also be MPL-2.0 (Hypothesis): they are never distributed.

No image requirement file is left: the GPU images and their infra/docker/requirements-*.txt were
removed on 17 Oct 2026, since no recorded run used them (the 16 Oct MI300X training ran in a
notebook without Docker, docs/amd-training-session-2026-10-16.md). IMAGE_FILES is therefore
empty. A file added there must be named in IMAGE_FILES (tests/test_licences.py checks it) and is
checked by the same rule (image_failures): its packages follow the runtime rule, except the test
tools that are also development tools; their licences are read from this environment, and a
package missing here is a failure, not a skip.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

PERMISSIVE = ("MIT", "BSD", "Apache", "ISC", "PSF", "Python Software Foundation", "PostgreSQL")
DEV_EXTRA = ("MPL", "Mozilla Public License")
FORBIDDEN = ("GPL",)  # also matches AGPL and LGPL
# pip-licenses never lists itself; its licence is MIT (checked on PyPI).
KNOWN = {"pip-licenses": "MIT"}
IMAGE_FILES: tuple[str, ...] = ()


def requirement_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.append(re.split(r"[=<>!~\[ ]", line, maxsplit=1)[0])
    return names


def licences(names: list[str]) -> dict[str, str]:
    out = subprocess.run(
        ["pip-licenses", "--format=json", "--packages", *names],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {_key(row["Name"]): row["License"] for row in json.loads(out)}


def _key(name: str) -> str:
    return name.lower().replace("_", "-")


def is_allowed(licence: str, extra: tuple[str, ...] = ()) -> bool:
    if any(bad in licence for bad in FORBIDDEN):
        return False
    return any(ok in licence for ok in PERMISSIVE + extra)


def image_failures(
    image: str, names: Iterable[str], found: Mapping[str, str], dev_tools: Iterable[str]
) -> list[str]:
    """The packages of an image file whose licence is not allowed, or not known here."""
    dev = {_key(n) for n in dev_tools}
    failures = []
    for name in names:
        lic = found.get(_key(name), KNOWN.get(_key(name)))
        if lic is None:
            failures.append(f"{image} {name}: not installed here, licence not checked")
        elif not is_allowed(lic, DEV_EXTRA if _key(name) in dev else ()):
            failures.append(f"{image} {name}: {lic}")
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    runtime = requirement_names(root / "requirements/base.txt")
    dev = requirement_names(root / "requirements/dev.txt")
    images = {
        name: requirement_names(root / "infra" / "docker" / name)
        for name in IMAGE_FILES
        if (root / "infra" / "docker" / name).exists()
    }
    every = sorted({*runtime, *dev, *(n for names in images.values() for n in names)})
    found = licences(every)
    failures = []
    for name in runtime:
        lic = found.get(_key(name), "NOT INSTALLED")
        if not is_allowed(lic):
            failures.append(f"runtime {name}: {lic}")
    for name in dev:
        dev_lic = found.get(_key(name), KNOWN.get(_key(name)))
        if dev_lic is None:
            print(f"warning: dev tool {name} not installed, licence not checked")
        elif not is_allowed(dev_lic, DEV_EXTRA):
            failures.append(f"dev {name}: {dev_lic}")
    for image, names in images.items():
        failures += image_failures(image, names, found, dev)
    for line in failures:
        print(f"licence not allowed: {line}", file=sys.stderr)
    print("licences OK" if not failures else f"{len(failures)} licence problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
