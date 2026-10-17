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
    """The text of a Markdown section, including its nested subsections."""
    match = re.search(rf"^(#{1,6}) {re.escape(title)}(?:\s.*)?$", README, re.M)
    assert match is not None, f"no section {title!r}"
    level = len(match.group(1))
    next_heading = re.search(rf"^#{{1,{level}}} ", README[match.end() :], re.M)
    end = match.end() + next_heading.start() if next_heading else len(README)
    return README[match.end() : end]


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
        "Demo modes",
        "Cached demo",
        "The GPU session",
        "Architecture at a glance",
        "How the risk is computed",
        "AMD benchmarks",
        "Licences",
        "Limits, declared",
        "Team and hackathon",
    ):
        assert section(title).strip(), title
    assert "infra/spikes/README.md" in section("The GPU session")
    assert "docs/risk-model.md" in section("How the risk is computed")
    assert "policy/legal_guard.py" in section("Key guarantees")
    benchmarks = section("AMD benchmarks")
    assert benchmarks.count("pending") >= 4 and "benchmarks-cpu.md" in benchmarks
    limits = section("Limits, declared")
    for needle in ("simulated", "FAIR-02", "6.78", "OOD", "pending", "professional"):
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
    assert "Runtime Python dependencies" in licences
    assert "licence gate" in licences.lower()
    assert "CC BY 4.0" in licences


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


def test_the_resets_come_before_the_walk_and_the_cached_stack_can_stop() -> None:
    quick = section("Quick start")
    assert "`make reset` (dev) or `make demo-reset` (cached)" in quick
    assert "before `make demo-walk`" in quick
    cached = code_blocks(section("The cached demo"))[0]
    assert cached == ["make demo-import", "make demo-local", "make demo-reset", "make demo-down"]


def test_the_cached_demo_says_where_the_package_comes_from_and_what_it_claims() -> None:
    """Local cached replay distinguishes verified MI300X output from rehearsal provenance."""
    from apps.api.schemas import CACHED_LABEL, REHEARSAL_LABEL

    cached = _flat(section("The cached demo"))
    assert "local mode" in cached and "not the live Railway demo" in cached
    assert "manifest verifies" in cached
    assert "read-only access is controlled separately by `PUBLIC_READ_ONLY`" in cached
    assert "Python 3.8 or later" in cached
    assert f'"{CACHED_LABEL}"' in cached and f'"{REHEARSAL_LABEL}"' in cached
    assert "DEMO_GPU" in cached and "REHEARSAL=1" in cached and "GEMMA_MODEL" in cached
    live = _flat(section("Live demo"))
    assert "`DEMO_MODE=live`" in live and "AMD Token Factory" in live
    assert "retain their recorded provenance" in live
    assert "DEMO_MODE=cached" in section("Live AMD inference")


def _base_images() -> list[str]:
    images = []
    for dockerfile in ("apps/api/Dockerfile", "apps/ui/Dockerfile"):
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
    assert "pinned by tag and digest" in licences and "pinned by version tag only" in licences
    assert "PSF License" in licences and "Debian packages" in licences
    compose = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    for image in re.findall(r"^\s+image: (\S+@sha256:\S+)$", compose, re.M):
        assert image.split(":")[0].split("/")[-1] in licences.lower(), image


def test_basic_auth_protects_the_data_not_the_admin_page() -> None:
    gpu = _flat(section("The GPU session"))
    assert "basic auth on the telemetry data and the writes" in gpu
    assert "admin page, the telemetry" not in README


def test_data_workflow_supplies_required_arguments_and_exports_last() -> None:
    workflow = (ROOT / "data" / "README.md").read_text(encoding="utf-8")
    for command in (
        "python -m pipeline.features --store data --out data",
        "python -m pipeline.anomalies --store data --out data",
        "python -m ml.risk --data data --out artifacts --scores data",
        "python -m pipeline.serving --data data --artifacts artifacts --out serving",
    ):
        assert command in workflow
    assert workflow.index("python -m ml.risk") < workflow.index("python -m pipeline.serving")


def test_ui_documentation_uses_the_unprefixed_health_endpoint() -> None:
    ui_readme = (ROOT / "apps" / "ui" / "README.md").read_text(encoding="utf-8")
    assert "`/healthz`" in ui_readme
    assert "`/api/healthz`" not in ui_readme
    assert "`/api/readyz`" not in ui_readme
