"""The README a judge follows (US-904, spec 17 checklist; DEP-01).

The quick start is at most three commands that exist; every make target and every relative link
of the README exists; the sections of US-904 are there; the ASOS attribution, Claude Code as a
development tool and the licence table of every runtime dependency are present; and the numbers
table gives an origin, a period and a nature for each figure (rule 11).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_infra import _pins, makefile_targets

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
NATURES = {"real", "synthetic", "simulated"}


def section(title: str) -> str:
    """The text of the `## title...` section, up to the next level-2 heading."""
    match = re.search(rf"^## {re.escape(title)}.*?$(.*?)(?=^## |\Z)", README, re.M | re.S)
    assert match is not None, f"no section {title!r}"
    return match.group(1)


def code_blocks(text: str) -> list[list[str]]:
    blocks = re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.S)
    return [
        [line.split("#", 1)[0].strip() for line in b.splitlines() if line.strip()] for b in blocks
    ]


def test_the_quick_start_is_three_commands_that_exist() -> None:
    """US-904: start in at most 3 commands, CPU and mock, from a fresh clone."""
    commands = code_blocks(section("Quick start"))[0]
    assert commands == ["cp .env.example .env", "make demo-data", "make dev-up"]
    assert (ROOT / ".env.example").is_file()
    targets = makefile_targets()
    assert "demo-data" in targets and "dev-up" in targets
    assert "http://localhost:8080" in section("Quick start")
    assert "LLM_MODE=mock" in section("Quick start") and "DEVICE=cpu" in section("Quick start")


def test_every_make_target_named_in_the_readme_exists() -> None:
    # In inline code (`make reset`) and at the start of a line of a code block.
    named = set(re.findall(r"(?:`|^)make ([a-z][a-z0-9-]*)", README, re.M))
    assert named >= {"demo-data", "dev-up", "reset", "demo-walk", "demo-export", "demo-local"}
    missing = named - set(makefile_targets())
    assert not missing, f"make targets in the README but not in the Makefile: {missing}"


def test_every_relative_link_exists() -> None:
    links = re.findall(r"\]\(([^)]+)\)", README)
    assert links
    for link in links:
        if link.startswith(("http://", "https://", "mailto:")):
            continue
        path = link.split("#", 1)[0]
        assert (ROOT / path).exists(), link
    for path in re.findall(
        r"`((?:docs|infra|policy|llm|scripts|apps|ml|pipeline)/[\w./-]+)`", README
    ):
        assert (ROOT / path).exists(), path


def test_the_sections_of_us_904() -> None:
    for title in (
        "Quick start",
        "The cached demo",
        "The GPU session",
        "Architecture",
        "How the risk is computed",
        "What Gemma does",
        "The legal guard",
        "AMD benchmarks",
        "Licences",
        "Limits",
        "Team and hackathon",
    ):
        assert section(title).strip(), title
    assert "infra/spikes/README.md" in section("The GPU session")
    assert "docs/risk-model.md" in section("How the risk is computed")
    for rule_file in ("llm/validator.py", "llm/phrasebook.py", "llm/legal_clause.py"):
        assert rule_file in section("What Gemma does"), rule_file
    assert "policy/legal_guard.py" in section("The legal guard")
    benchmarks = section("AMD benchmarks")
    assert benchmarks.count("pending") >= 4 and "benchmarks-cpu.md" in benchmarks
    limits = section("Limits")
    for needle in ("simulated", "FAIR-02", "6.78", "OOD", "ADR-14", "pending", "professional"):
        assert needle in limits, needle


def test_licences_attribution_and_development_tool() -> None:
    """US-002 (licence report in the README), spec 17: ASOS CC BY 4.0 attribution, Claude Code
    declared as a development tool, ADR-12 for the images."""
    licences = section("Licences")
    for needle in ("CC BY 4.0", "arXiv:2302.14096", "https://osf.io/c793h", "McGowan", "ADR-12"):
        assert needle in licences, needle
    table = dict(re.findall(r"^\| ([a-z0-9-]+) \| [\d.]+ \| (.+?) \|$", licences, re.M))
    base = set(_pins(ROOT / "requirements" / "base.txt"))
    assert set(table) == base, f"runtime dependencies without a row: {base - set(table)}"
    for name, licence in table.items():
        assert "GPL" not in licence, name
    assert "Claude Code" in section("Team and hackathon")
    assert "no hosted model is called by the product at runtime" in README


def test_every_figure_of_the_results_table_has_origin_period_and_nature() -> None:
    """Rule 11: a figure without its origin, period and nature is not shown."""
    rows = [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in section("How the risk is computed").splitlines()
        if line.startswith("| ") and not line.startswith("| ---")
    ]
    header, body = rows[0], rows[1:]
    assert header == ["Figure", "Value", "Origin", "Period", "Nature"]
    assert len(body) >= 4
    for row in body:
        assert all(row), row
        assert row[4] in NATURES, row
    natures = {row[4] for row in body}
    assert {"real", "synthetic"} <= natures


def test_linked_accounts_never_same_person() -> None:
    """Rule 11 wording: "same person" appears only to say it is never claimed."""
    for match in re.finditer(r"same person", README, re.I):
        before = README[max(0, match.start() - 8) : match.start()]
        assert "never" in before, README[max(0, match.start() - 40) : match.end()]
