"""The licence gate (CLAUDE.md rule 15): requirements/base.txt and dev.txt, and the rule an image
requirement file would follow if one were added (none is left since 17 Oct 2026)."""

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
    """A requirements file added under infra/docker/ must be named in IMAGE_FILES, so the gate
    reads it. None is left: the GPU images that had them were removed on 17 Oct 2026."""
    files = sorted(p.name for p in (ROOT / "infra" / "docker").glob("requirements-*.txt"))
    assert sorted(check_licenses.IMAGE_FILES) == files == []


def test_requirement_names_skip_includes_and_comments() -> None:
    names = check_licenses.requirement_names(ROOT / "requirements" / "dev.txt")
    assert {"pytest", "hypothesis", "pip-licenses"} <= set(names)
    assert "fastapi" not in names  # from base.txt through -r, which the gate reads on its own
    assert all(name and not name.startswith(("-", "#")) for name in names)
