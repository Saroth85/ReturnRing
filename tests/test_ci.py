"""The CI workflow (US-002, US-003, US-004; NFR-14, LIC-01, SEC-03): the Python job, the UI job
(Node 22), the GraphSAGE job with PyTorch on the CPU and gitleaks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "apps" / "ui"


def jobs() -> dict[str, dict[str, Any]]:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    result: dict[str, dict[str, Any]] = workflow["jobs"]
    return result


def runs(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job["steps"] if "run" in step]


def uses(job: dict[str, Any]) -> list[str]:
    return [str(step["uses"]) for step in job["steps"] if "uses" in step]


def test_the_python_job_runs_lint_tests_coverage_licences_and_contract_checks() -> None:
    commands = runs(jobs()["test"])
    assert "pip install -r requirements/dev.txt" in commands
    assert "make lint" in commands and "make licenses" in commands
    suite = next(c for c in commands if c.startswith("python -m pytest"))
    assert '-m "not gpu"' in suite and "--cov=policy" in suite and "--cov=llm" in suite
    assert "--cov-branch" in suite
    assert any("--fail-under=100" in c and '"policy/*,llm/*"' in c for c in commands)
    assert "python -m scripts.export_api_schemas --check" in commands
    assert "python -m scripts.build_api_examples --check" in commands


UI_SCRIPTS = ("lint", "typecheck", "check:types-gen", "check:licenses", "build")


def test_the_ui_job_runs_every_check_on_node_22() -> None:
    job = jobs()["ui"]
    assert job["defaults"]["run"]["working-directory"] == "apps/ui"
    node = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/setup-node"))
    assert node["with"]["node-version"] == "22"
    assert node["with"]["cache-dependency-path"] == "apps/ui/package-lock.json"
    commands = runs(job)
    assert commands[0].startswith("npm ci")
    for script in UI_SCRIPTS:
        assert f"npm run {script}" in commands, script
    assert "npm test" in commands  # the unit tests (vitest)
    assert commands[-1] == "npm run build"


@pytest.mark.skipif(
    not (UI / "package.json").exists(),
    reason="apps/ui is the placeholder page; the phase 5 UI brings package.json",
)
def test_the_ui_job_calls_scripts_the_ui_defines() -> None:
    scripts = json.loads((UI / "package.json").read_text(encoding="utf-8"))["scripts"]
    for script in (*UI_SCRIPTS, "test"):
        assert script in scripts, script
    assert (UI / "package-lock.json").is_file()


def test_the_graphsage_job_installs_cpu_pytorch() -> None:
    commands = runs(jobs()["gnn-cpu"])
    assert "pip install -r requirements/dev.txt -r infra/requirements-gnn-cpu.txt" in commands
    assert "python -m pytest -m torch -q" in commands and "mypy" in commands
    cpu = (ROOT / "infra" / "requirements-gnn-cpu.txt").read_text(encoding="utf-8")
    # The PyTorch of the 16 Oct MI300X session (2.8.0+rocm6.4 there), CPU build here.
    assert "torch==2.8.0+cpu" in cpu


def test_gitleaks_scans_the_whole_history() -> None:
    job = jobs()["secrets"]
    assert any(u.startswith("gitleaks/gitleaks-action@") for u in uses(job))
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["fetch-depth"] == 0
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored  # only .env.example is versioned (US-004)


def test_every_job_pins_its_actions_and_runner() -> None:
    for name, job in jobs().items():
        assert job["runs-on"] == "ubuntu-24.04", name
        for action in uses(job):
            assert "@v" in action, (name, action)
