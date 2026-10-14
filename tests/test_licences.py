"""The licence gate (CLAUDE.md rule 15) also covers what the GPU images add from PyPI."""

from __future__ import annotations

from pathlib import Path

from scripts import check_licenses

ROOT = Path(__file__).resolve().parents[1]


def test_image_packages_follow_the_runtime_rule_and_test_tools_the_dev_rule() -> None:
    found = {
        "numpy": "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
        "hypothesis": "MPL-2.0",
        "somelib": "MPL-2.0",
        "gpl-thing": "GNU General Public License v3 (GPLv3)",
        "python-dateutil": "Apache Software License; BSD License",
    }
    names = ["numpy", "hypothesis", "somelib", "gpl_thing", "python-dateutil", "absent"]
    failures = check_licenses.image_failures("img", names, found, ["hypothesis", "pytest"])
    assert failures == [
        "img somelib: MPL-2.0",  # MPL only for a test tool that is also a dev tool
        "img gpl_thing: GNU General Public License v3 (GPLv3)",
        "img absent: not installed here, licence not checked",
    ]


def test_every_image_requirement_file_is_checked() -> None:
    files = sorted(p.name for p in (ROOT / "infra" / "docker").glob("requirements-*.txt"))
    # requirements-gbm-amd.txt: amd_xgboost from the AMD index with its hash (checked by hand).
    assert sorted(check_licenses.IMAGE_FILES) == [
        f for f in files if f != "requirements-gbm-amd.txt"
    ]
    names = check_licenses.requirement_names(ROOT / "infra" / "docker" / "requirements-gnn.txt")
    assert {"numpy", "pandas", "hypothesis"} <= set(names)
