"""Guards on the compose profiles, images and GPU spike setup (US-001, spec sections 9-11, 13).

Pure YAML and text parsing, so no Docker daemon is needed. The last test renders the real merged
config with `docker compose config` when the Compose CLI is installed (it is on GitHub's
ubuntu-24.04 runners) and is skipped otherwise.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from apps.api.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
COMPOSE_FILES = ["docker-compose.yml", "compose.gpu.yml", "compose.demo.yml", "compose.spike.yml"]
PINNED_IMAGES = ("VLLM_IMAGE", "DGL_IMAGE", "ROCM_BASE_IMAGE")
DIGEST = re.compile(r"^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")


def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((INFRA / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def services(name: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = load(name).get("services", {})
    return result


def env_example() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def bind_sources(service: dict[str, Any]) -> list[Path]:
    """Host paths of bind mounts (relative to infra/, as Compose resolves them)."""
    paths = []
    for volume in service.get("volumes", []):
        source = volume.split(":", 1)[0] if isinstance(volume, str) else volume.get("source", "")
        if source.startswith((".", "/")):
            paths.append((INFRA / source).resolve())
    return paths


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("compose.gpu.yml", {"DEVICE": "gpu", "LLM_MODE": "vllm"}),
        ("compose.demo.yml", {"DEMO_MODE": "cached", "LLM_MODE": "mock", "DEVICE": "cpu"}),
    ],
)
def test_profile_overrides_set_api_switches(override: str, expected: dict[str, str]) -> None:
    env = services(override)["api"]["environment"]
    for key, value in expected.items():
        assert env[key] == value


@pytest.mark.parametrize("override", ["compose.gpu.yml", "compose.demo.yml"])
def test_api_settings_accept_override_values(
    override: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in services(override)["api"]["environment"].items():
        monkeypatch.setenv(key, str(value))
    Settings.from_env()


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_vllm_has_no_published_port(name: str) -> None:
    assert "ports" not in services(name).get("vllm", {}), "vLLM must stay internal (NFR-11)"


def test_only_the_proxy_publishes_on_all_interfaces() -> None:
    for name in COMPOSE_FILES:
        for service_name, service in services(name).items():
            for port in service.get("ports", []):
                if service_name == "proxy":
                    assert str(port) in {"80:80", "443:443"}
                else:
                    assert str(port).startswith("127.0.0.1:"), (name, service_name, port)


def test_no_service_mounts_truth_or_the_whole_repo() -> None:
    truth = ROOT / "data" / "truth"
    for name in COMPOSE_FILES:
        for service_name, service in services(name).items():
            for source in bind_sources(service):
                assert source != ROOT, (name, service_name, "mounts the whole repo")
                assert source != truth and truth not in source.parents, (name, service_name)
                if source in truth.parents:
                    # data/ contains truth/: only the generator job may mount it (rule 12)
                    assert service_name == "pipeline", (name, service_name, source)


def test_spike_scripts_are_mounted_read_only() -> None:
    spike = services("compose.spike.yml")
    for service_name in ("trainer-gbm", "trainer-gnn", "spike-client"):
        assert "../infra/spikes:/app/infra/spikes:ro" in spike[service_name]["volumes"]
    assert spike["spike-client"]["profiles"] == ["spike"]


def test_gpu_images_pinned_by_tag_and_digest() -> None:
    env = env_example()
    for key in PINNED_IMAGES:
        assert DIGEST.match(env[key]), f"{key}={env[key]!r} is not tag@sha256:digest"
        assert ":latest@" not in env[key]


def test_gemma_weights_pinned_to_a_revision() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", env_example()["GEMMA_REVISION"])
    command = services("docker-compose.yml")["vllm"]["command"]
    assert "--revision=${GEMMA_REVISION}" in command
    assert "--tokenizer-revision=${GEMMA_REVISION}" in command


def test_vllm_keeps_weights_and_compile_cache_in_volumes() -> None:
    vllm = services("docker-compose.yml")["vllm"]
    assert "hf-cache:/root/.cache/huggingface" in vllm["volumes"]
    assert "vllm-cache:/root/.cache/vllm" in vllm["volumes"]


def compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    out = subprocess.run(["docker", "compose", "version"], capture_output=True, check=False)
    return out.returncode == 0


def test_no_latest_image_tag_in_compose_files() -> None:
    for name in COMPOSE_FILES:
        for service_name, service in services(name).items():
            image = str(service.get("image", ""))
            if image and not image.startswith("${"):
                assert not image.endswith(":latest") and ":latest@" not in image
                assert ":" in image.split("/")[-1] or "@sha256:" in image, (name, service_name)


def test_amd_xgboost_comes_only_from_the_amd_index_with_a_hash() -> None:
    dockerfile = (INFRA / "docker" / "trainer-gbm.Dockerfile").read_text(encoding="utf-8")
    assert "--extra-index-url" not in dockerfile
    assert '--require-hashes --index-url "$AMD_PYPI_INDEX_URL"' in dockerfile
    amd = [
        line
        for line in (INFRA / "docker" / "requirements-gbm-amd.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(amd) == 1
    assert re.fullmatch(r"amd_xgboost==[\d.]+ --hash=sha256:[0-9a-f]{64}", amd[0])
    assert env_example()["AMD_PYPI_INDEX_URL"].startswith("https://pypi.amd.com/")


def test_gbm_pypi_requirements_pinned_and_without_upstream_xgboost() -> None:
    lines = [
        line.strip()
        for line in (INFRA / "docker" / "requirements-gbm.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines
    for line in lines:
        name, _, version = line.partition("==")
        assert version, f"{line} is not pinned"
        assert name.lower() not in {"xgboost", "xgboost-cpu", "amd_xgboost", "amd-xgboost"}


def test_makefile_targets_use_overrides_and_existing_scripts() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^GPU = .*-f infra/compose\.gpu\.yml", makefile, re.M)
    assert re.search(r"^DEMO = .*-f infra/compose\.demo\.yml", makefile, re.M)
    assert "$$status -eq 5" in makefile, "make test-gpu must accept 'no tests collected'"
    for script in set(re.findall(r"infra/spikes/[\w.]+\.(?:py|sh)", makefile)):
        assert (ROOT / script).is_file(), script


@pytest.mark.skipif(not compose_available(), reason="docker compose not installed")
@pytest.mark.parametrize(
    ("profile", "files", "expected"),
    [
        ("dev", [], {"DEVICE": "cpu", "LLM_MODE": "mock", "DEMO_MODE": "live"}),
        ("gpu", ["compose.gpu.yml"], {"DEVICE": "gpu", "LLM_MODE": "vllm"}),
        ("demo", ["compose.demo.yml"], {"DEMO_MODE": "cached", "LLM_MODE": "mock"}),
    ],
)
def test_rendered_compose_config(
    profile: str, files: list[str], expected: dict[str, str], tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    cmd = ["docker", "compose", "-f", str(INFRA / "docker-compose.yml")]
    for extra in files:
        cmd += ["-f", str(INFRA / extra)]
    cmd += ["--env-file", str(env_file), "--profile", profile, "config", "--format", "json"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    rendered = json.loads(out.stdout)["services"]
    for key, value in expected.items():
        assert rendered["api"]["environment"][key] == value
    assert "ports" not in rendered.get("vllm", {})
    for service in rendered.values():
        for volume in service.get("volumes", []):
            assert not volume.get("source", "").endswith("/data/truth")
