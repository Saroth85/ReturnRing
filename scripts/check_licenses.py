"""Licence gate (CLAUDE.md rule 15).

Runtime dependencies (requirements/base.txt) must be permissive. Development tools
(requirements/dev.txt) may also be MPL-2.0 (Hypothesis): they are never distributed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

PERMISSIVE = ("MIT", "BSD", "Apache", "ISC", "PSF", "Python Software Foundation", "PostgreSQL")
DEV_EXTRA = ("MPL", "Mozilla Public License")
FORBIDDEN = ("GPL",)  # also matches AGPL and LGPL
# pip-licenses never lists itself; its licence is MIT (checked on PyPI).
KNOWN = {"pip-licenses": "MIT"}


def requirement_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-r"):
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
    return {row["Name"].lower(): row["License"] for row in json.loads(out)}


def is_allowed(licence: str, extra: tuple[str, ...] = ()) -> bool:
    if any(bad in licence for bad in FORBIDDEN):
        return False
    return any(ok in licence for ok in PERMISSIVE + extra)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    runtime = requirement_names(root / "requirements/base.txt")
    dev = requirement_names(root / "requirements/dev.txt")
    found = licences(runtime + dev)
    failures = []
    for name in runtime:
        lic = found.get(name.lower(), "NOT INSTALLED")
        if not is_allowed(lic):
            failures.append(f"runtime {name}: {lic}")
    for name in dev:
        dev_lic = found.get(name.lower(), KNOWN.get(name.lower()))
        if dev_lic is None:
            print(f"warning: dev tool {name} not installed, licence not checked")
        elif not is_allowed(dev_lic, DEV_EXTRA):
            failures.append(f"dev {name}: {dev_lic}")
    for line in failures:
        print(f"licence not allowed: {line}", file=sys.stderr)
    print("licences OK" if not failures else f"{len(failures)} licence problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
