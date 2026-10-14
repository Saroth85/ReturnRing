"""Licence gate (CLAUDE.md rule 15).

Runtime dependencies (requirements/base.txt) must be permissive. Development tools
(requirements/dev.txt) may also be MPL-2.0 (Hypothesis): they are never distributed.

The packages the GPU images add from PyPI (infra/docker/requirements-*.txt: trainer-graphsage,
trainer-gbm, bench) follow the runtime rule, except the test tools that are also development tools
(pytest and Hypothesis run the GPU tests inside trainer-graphsage). Their licences are read from
this environment, where requirements/dev.txt installs every one of them; a package missing here is
a failure, not a skip. amd_xgboost (requirements-gbm-amd.txt, Apache-2.0) comes from the AMD index
with a pinned hash and is checked when that file changes, by hand.
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
IMAGE_FILES = ("requirements-gnn.txt", "requirements-gbm.txt", "requirements-bench.txt")


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
