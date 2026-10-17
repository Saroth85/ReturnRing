"""The README a judge follows (US-904, spec 17 checklist; DEP-01).

The quick start is at most three commands that exist; every make target and every relative link
of the README exists; the sections of US-904 are there, the live demo among them; the reset of
each stack comes before the walk, and the optional cached mode says how a CPU package is made,
started and stopped; the ASOS attribution, the development tools (Claude Code, GitHub Copilot)
and the licence table of every runtime dependency are present; and the numbers table gives an
origin, a period and a nature for each figure (rule 11).
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
        "Live demo",
        "Quick start",
        "Architecture",
        "How the risk is computed",
        "What Gemma does",
        "The legal guard",
        "AMD",
        "Licences",
        "Limits",
        "Team and hackathon",
    ):
        assert section(title).strip(), title
    assert "docs/railway-demo.md" in section("Live demo")
    assert "docs/risk-model.md" in section("How the risk is computed")
    for rule_file in ("llm/validator.py", "llm/phrasebook.py", "llm/legal_clause.py"):
        assert rule_file in section("What Gemma does"), rule_file
    assert "policy/legal_guard.py" in section("The legal guard")
    amd = _flat(section("AMD"))
    for needle in (
        "ADR-18",
        "docs/amd-training-session-2026-10-16.md",
        "docs/results/amd-session-2026-10-16.json",
        "No CPU/GPU speed comparison",
        "benchmarks-cpu.md",
        "synthetic",
    ):
        assert needle in amd, needle
    assert "pending" not in amd  # B1-B4 were retired (ADR-18), not postponed
    limits = section("Limits")
    for needle in (
        "simulated",
        "FAIR-02",
        "6.78",
        "OOD",
        "ADR-14",
        "OSF",
        "not deployed",
        "professional",
    ):
        assert needle in limits, needle


def test_licences_attribution_and_development_tool() -> None:
    """US-002 (licence report in the README), spec 17: ASOS CC BY 4.0 attribution, the
    development tools declared (Claude Code, GitHub Copilot), ADR-12 for the images."""
    licences = section("Licences")
    for needle in ("CC BY 4.0", "arXiv:2302.14096", "https://osf.io/c793h", "McGowan", "ADR-12"):
        assert needle in licences, needle
    table = dict(re.findall(r"^\| ([a-z0-9-]+) \| [\d.]+ \| (.+?) \|$", licences, re.M))
    runtime = set(_pins(ROOT / "requirements" / "base.txt"))
    runtime |= set(_pins(ROOT / "requirements" / "public-demo.txt"))  # the live demo's service
    assert set(table) == runtime, f"runtime dependencies without a row: {runtime - set(table)}"
    for name, licence in table.items():
        assert "GPL" not in licence, name
    team = _flat(section("Team and hackathon"))
    assert "Claude Code" in team and "GitHub Copilot" in team
    assert "neither is called by the product at runtime" in team
    # Since ADR-18 the product does call a hosted model (Gemma on AMD Token Factory).
    assert "no hosted model is called by the product at runtime" not in README


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


# ---- Review of 14 Oct 2026 ("A judge following the README") -------------------------------------


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_memory_figure_is_a_dated_range_measured_once() -> None:
    """The quoted peak did not reproduce (rules 11, 13): a range, its date, how and how often."""
    quick = _flat(section("Quick start"))
    assert "1.81 GB" not in README and "1.57 GB" not in README
    for needle in ("about 1.6 GB", "up to about 2.2 GB", "14 Oct 2026", "measured once"):
        assert needle in quick, needle
    assert "not in a container" in quick
    compose = _flat((ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "about 1.6 GB" in compose and "up to about 2.2 GB" in compose
    assert "1.81 GB" not in compose


def _cached_mode() -> str:
    """The `DEMO_MODE=cached` bullet of the optional modes, flattened."""
    match = re.search(r"^- \*\*`DEMO_MODE=cached`\*\*(.*?)(?=^- |^$)", README, re.M | re.S)
    assert match is not None, "no DEMO_MODE=cached bullet"
    return _flat(match.group(1))


def test_the_resets_come_before_the_walk_and_the_cached_stack_can_stop() -> None:
    """US-903: each stack's reset before the walk. The cached mode (US-901, optional) packs a CPU
    rehearsal only, so the README gives the switch that packs one, the host python3 the packaging
    needs, how it starts and stops, and the rehearsal label; no MI300X package exists, so the
    MI300X label is never claimed (rules 11 and 13; the live-demo test below checks it)."""
    from apps.api.schemas import REHEARSAL_LABEL

    quick = section("Quick start")
    assert "`make reset` (dev) or `make demo-reset` (cached)" in quick
    assert "before `make demo-walk`" in quick
    cached = _cached_mode()
    for needle in (
        "`make demo-export ALLOW_EMPTY_CACHE=1`",
        "`REHEARSAL=1`",
        "Python 3.8 or later",
        "`make demo-import`",
        "`make demo-local`",
        "`make demo-reset` before `make demo-walk`",
        "`make demo-down`",
        f'"{REHEARSAL_LABEL}"',
        "no package made on an MI300X exists",
    ):
        assert needle in cached, needle


def test_the_live_demo_says_what_it_runs_and_claims_no_mi300x_output() -> None:
    """The public demo is live on the CPU with Gemma on Token Factory (ADR-18); no package of
    outputs precomputed on the MI300X exists, so its label is never claimed (rules 11 and 13)."""
    from apps.api.schemas import CACHED_LABEL

    live = _flat(section("Live demo"))
    for needle in ("`DEMO_MODE=live`", "`LLM_MODE=tokenfactory`", "`DEVICE=cpu`", "synthetic"):
        assert needle in live, needle
    assert CACHED_LABEL not in README
    assert "releases/download" not in README and "curl -LO" not in README


def _base_images() -> list[str]:
    images = []
    for dockerfile in (
        "apps/api/Dockerfile",
        "apps/ui/Dockerfile",
        "infra/docker/public-demo.Dockerfile",
    ):
        text = (ROOT / dockerfile).read_text(encoding="utf-8")
        images += re.findall(r"^FROM (\S+)", text, re.M)
    return images


def test_the_image_licences_say_how_each_image_is_pinned() -> None:
    """Not every image is pulled by digest: the claim names which are, and lists the base image
    of every image the project builds, Python included (PSF, Debian packages)."""
    licences = _flat(section("Licences"))
    assert "pulled unmodified by digest" not in README
    images = _base_images()
    assert any(image.startswith("python:") for image in images)
    for image in images:
        if "@sha256:" not in image:
            assert f"`{image}`" in licences, image  # named among those pinned by tag only
    assert "pinned by tag and digest" in licences and "by version tag only" in licences
    assert "PSF License" in licences and "Debian packages" in licences
    compose = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    for image in re.findall(r"^\s+image: (\S+@sha256:\S+)$", compose, re.M):
        assert image.split(":")[0].split("/")[-1] in licences.lower(), image


def test_basic_auth_protects_the_data_not_the_admin_page() -> None:
    live = _flat(section("Live demo")).lower()
    assert "basic auth on the telemetry data" in live
    assert "admin page, the telemetry" not in README
