"""Guards on the compose profiles, services, images, proxy and GPU spike setup (US-001, US-003,
US-901, US-903, US-905; spec sections 9-11, 13).

Pure YAML and text parsing, so no Docker daemon is needed. The rendered-config tests run the real
merge with `docker compose config` when the Compose CLI is installed (it is on GitHub's
ubuntu-24.04 runners) and are skipped otherwise.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from apps.api.settings import DEFAULT_LLM_CONCURRENCY, DEFAULT_VLLM_TIMEOUT_S, Settings

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
COMPOSE_FILES = [
    "docker-compose.yml",
    "compose.gpu.yml",
    "compose.demo.yml",
    "compose.public.yml",
    "compose.spike.yml",
]
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
    return [source for source, _, _ in bind_mounts(service)]


def bind_mounts(service: dict[str, Any]) -> list[tuple[Path, str, bool]]:
    """(host path, container path, read-only) of each bind mount, short or long syntax."""
    mounts = []
    for volume in service.get("volumes", []):
        if isinstance(volume, str):
            parts = volume.split(":")
            source, target = parts[0], parts[1] if len(parts) > 1 else ""
            read_only = len(parts) > 2 and "ro" in parts[2].split(",")
        else:
            source, target = volume.get("source", ""), volume.get("target", "")
            read_only = bool(volume.get("read_only", False))
        if source.startswith((".", "/")):
            mounts.append(((INFRA / source).resolve(), target, read_only))
    return mounts


# Offline jobs may read the ground truth (rule 12 as rewritten in ADR-13); only the pipeline job
# writes data/. Every other service (postgres, reset, api, ui, proxy, vllm, telemetry,
# telemetry-sampler, batch-generate, spike-client) never sees data/truth.
JOB_SERVICES = frozenset({"pipeline", "trainer-gbm", "trainer-gnn"})
DATA_WRITERS = frozenset({"pipeline"})
# Running services of the product (spec 9): long-lived or the reset, never an offline job. None
# mounts data/ or artifacts/ (rule 12, review F2).
RUNNING_SERVICES = frozenset(
    {"postgres", "reset", "api", "ui", "proxy", "vllm", "telemetry", "telemetry-sampler"}
)
# Offline jobs and tools: started on purpose (make targets), never by `up` of a product profile.
OFFLINE_SERVICES = JOB_SERVICES | {"trainer-graphsage", "batch-generate", "spike-client"}
# Jobs that write host folders run as the owner of the checkout (review P5).
HOST_WRITERS = frozenset({"pipeline", "batch-generate"})
API_IMAGE_SERVICES = ("reset", "api", "pipeline", "batch-generate", "telemetry-sampler")


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


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (None, ("cpu", "mock", "live", False)),
        ("compose.gpu.yml", ("gpu", "vllm", "live", False)),
        ("compose.demo.yml", ("cpu", "mock", "cached", True)),  # spec 11: public read-only
    ],
)
def test_api_settings_accept_override_values(
    override: str | None, expected: tuple[str, str, str, bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment each profile gives the API (the base merged with the override, then
    interpolated against .env.example as Compose does) is read by apps.api.settings: the
    switches of ADR-15 section 15 included (DEMO_CLOCK on, LLM_CONCURRENCY and VLLM_TIMEOUT_S
    empty: the API's defaults)."""
    env = dict(compose_service("api")["environment"])
    if override is not None:
        env |= services(override)["api"]["environment"]
    example = env_example()
    for key in API_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, _resolve(str(value), example))
    settings = Settings.from_env()
    got = (settings.device, settings.llm_mode, settings.demo_mode, settings.public_read_only)
    assert got == expected
    assert settings.demo_clock is True
    assert (settings.llm_concurrency, settings.vllm_timeout_s) == (
        DEFAULT_LLM_CONCURRENCY,
        DEFAULT_VLLM_TIMEOUT_S,
    )


@pytest.mark.parametrize("name", COMPOSE_FILES)
def test_vllm_has_no_published_port(name: str) -> None:
    assert "ports" not in services(name).get("vllm", {}), "vLLM must stay internal (NFR-11)"


def test_only_the_public_proxy_publishes_on_all_interfaces() -> None:
    """Docker-published ports bypass ufw: everything is on loopback except the proxy of a public
    server (compose.public.yml), on 80 and 443 only."""
    for name in COMPOSE_FILES:
        for service_name, service in services(name).items():
            for port in service.get("ports", []):
                if name == "compose.public.yml" and service_name == "proxy":
                    assert str(port) in {"80:80", "443:443"}
                else:
                    assert str(port).startswith("127.0.0.1:"), (name, service_name, port)
    assert services("docker-compose.yml")["proxy"]["ports"] == ["127.0.0.1:8080:80"]
    assert services("docker-compose.yml")["api"]["ports"] == ["127.0.0.1:8000:8000"]
    assert "ports" not in services("docker-compose.yml")["ui"]  # only through the proxy


def test_every_service_is_running_or_offline() -> None:
    """A new service must be classified, so the rule 12 and F2 checks cover it."""
    for name in COMPOSE_FILES:
        for service_name in services(name):
            assert service_name in RUNNING_SERVICES | OFFLINE_SERVICES, (name, service_name)
    for service_name in OFFLINE_SERVICES - {"spike-client"}:
        profiles = set(services("docker-compose.yml")[service_name]["profiles"])
        if service_name in {"pipeline", "batch-generate"}:
            # Never started by `up` of a product profile: make demo-data, make batch-generate.
            assert not profiles & {"dev", "gpu", "demo"}, service_name


def mount_parts(source: Path, root: Path = ROOT) -> tuple[str, ...]:
    """The parts of a mount source inside the repository, or all its parts outside it: the
    rule 12 check must not depend on where the repository is cloned (review F7)."""
    try:
        return source.relative_to(root).parts
    except ValueError:
        return source.parts


def test_f7_the_truth_check_reads_the_path_inside_the_repository() -> None:
    clone = Path("/srv/review/truth/ReturnRing")  # a clone under a folder named truth
    assert "truth" not in mount_parts(clone / "artifacts", clone)
    assert "truth" in mount_parts(clone / "data" / "truth", clone)
    assert "truth" in mount_parts(Path("/mnt/truth/x"), clone)


def test_no_service_mounts_truth_or_the_whole_repo() -> None:
    """Rule 12: data/truth (main and OOD truth, docs/synthetic-store.md) reaches offline jobs
    only. A non-job service mounts no ancestor of data/truth and no path with a 'truth' part
    inside the repository."""
    truth = ROOT / "data" / "truth"
    for name in COMPOSE_FILES:
        for service_name, service in services(name).items():
            for source, target, read_only in bind_mounts(service):
                assert source != ROOT, (name, service_name, "mounts the whole repo")
                sees_truth = source == truth or truth in source.parents or source in truth.parents
                if service_name not in JOB_SERVICES:
                    assert not sees_truth, (name, service_name, source)
                    assert "truth" not in mount_parts(source), (name, service_name, source)
                    assert "truth" not in Path(target).parts, (name, service_name, target)
                elif sees_truth and service_name not in DATA_WRITERS:
                    assert read_only, (name, service_name, "training jobs read data/ only")


def test_f2_no_running_service_mounts_the_offline_evaluation_outputs() -> None:
    """Review F2: the API reads its models from the serving snapshot and the telemetry goes
    through a named volume, so no running service mounts artifacts/ (artifacts/eval holds the
    offline evaluation outputs)."""
    artifacts = ROOT / "artifacts"
    for name in COMPOSE_FILES:
        for service_name, service in services(name).items():
            if service_name not in RUNNING_SERVICES:
                continue
            for source, _, _ in bind_mounts(service):
                assert source != artifacts and artifacts not in source.parents, (
                    name,
                    service_name,
                    source,
                )


def test_running_services_never_see_data() -> None:
    data = ROOT / "data"
    for name in COMPOSE_FILES:
        for service_name in RUNNING_SERVICES:
            for source in bind_sources(services(name).get(service_name, {})):
                assert source != data and data not in source.parents, (name, service_name)


def test_training_jobs_read_the_data_folder_read_only() -> None:
    compose = services("docker-compose.yml")
    for service_name in ("trainer-gbm", "trainer-gnn"):
        mounts = bind_mounts(compose[service_name])
        assert ((ROOT / "data").resolve(), "/app/data", True) in mounts, service_name


def test_pipeline_service_runs_the_generator_on_the_real_calibration() -> None:
    pipeline = services("docker-compose.yml")["pipeline"]
    command = " ".join(pipeline["command"])
    assert "print(" not in command
    for scenario in ("main", "ood"):
        assert f"--scenario {scenario}" in command
    assert command.count("python -m pipeline.generator") == 2
    assert "--calibration pipeline/calibration/asos_calibration.json" in command
    assert (ROOT / "pipeline/calibration/asos_calibration.json").is_file()
    assert ((ROOT / "data").resolve(), "/app/data", False) in bind_mounts(pipeline)


def pipeline_job_steps() -> list[str]:
    """The commands of the pipeline job, in order (the `sh -c` chain joined by &&)."""
    command = services("docker-compose.yml")["pipeline"]["command"]
    assert command[:2] == ["sh", "-c"] and len(command) == 3
    return [" ".join(step.split()) for step in command[2].split("&&")]


def test_the_pipeline_job_runs_the_chain_of_make_pipeline() -> None:
    """US-904, H29: `make demo-data` (the job) and `make pipeline` (the host) run the same steps
    in the same order: the store, the graph, the features, the anomalies, the risk models and
    their scores, then the serving snapshot the API reads. Only two differences: the job takes
    SEED and SCALE from Compose, and the risk step writes no results file (--no-results), so a
    container never rewrites docs/results."""
    targets = makefile_targets()
    values = {
        "$(CALIBRATION)": "pipeline/calibration/asos_calibration.json",
        "$(SEED)": "${SEED:-42}",
        "$(SCALE)": "${SCALE:-1.0}",
    }
    expected = []
    for target in targets["pipeline"][0].split():
        for line in targets[target][1]:
            for make_var, compose_var in values.items():
                line = line.replace(make_var, compose_var)
            expected.append(line.replace("--results docs/results/us-204-risk.md", "--no-results"))
    assert pipeline_job_steps() == expected
    assert (
        expected[-1] == "python -m pipeline.serving --data data --artifacts artifacts --out serving"
    )
    pipeline = services("docker-compose.yml")["pipeline"]
    mounts = {target: (source, ro) for source, target, ro in bind_mounts(pipeline)}
    assert mounts == {
        "/app/data": ((ROOT / "data").resolve(), False),
        "/app/artifacts": ((ROOT / "artifacts").resolve(), False),
        "/app/serving": ((ROOT / "serving").resolve(), False),
    }
    assert pipeline["profiles"] == ["jobs"]  # never started by `up`
    assert pipeline["build"]["dockerfile"] == "apps/api/Dockerfile"


def test_the_api_image_installs_what_the_pipeline_job_and_the_api_import() -> None:
    """The pipeline job, the reset, the batch and the sampler run in the API image, which
    installs requirements/base.txt only."""
    dockerfile = (ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    for package in ("policy", "llm", "contracts", "pipeline", "ml", "apps"):
        assert f"COPY {package} {package}" in dockerfile, package
    assert "requirements/base.txt" in dockerfile
    needed = third_party_imports(
        "pipeline.generator.__main__",
        "pipeline.graph.__main__",
        "pipeline.features.__main__",
        "pipeline.anomalies.__main__",
        "ml.risk.__main__",
        "pipeline.serving.__main__",
        "apps.api.main",
        "apps.api.reset",
        "apps.api.batch_generate",
        "apps.api.services.other.telemetry",
    )
    base = _pins(ROOT / "requirements" / "base.txt")
    transitive = {"pydantic_core": "pydantic", "starlette": "fastapi"}
    for module in sorted(needed):
        distribution = transitive.get(module, MODULE_DISTRIBUTION.get(module, module))
        assert distribution.replace("_", "-") in base, f"{module} is not in requirements/base.txt"


def test_p5_the_pipeline_job_writes_data_as_the_host_user() -> None:
    """Review P5: the image runs as user app (uid 1000), but data/ belongs to whoever cloned the
    repository (root on the droplet, infra/spikes/README.md). The job runs as the host user:
    the Makefile passes `id -u` and `id -g`, .env.example documents both, uid 1000 otherwise."""
    pipeline = services("docker-compose.yml")["pipeline"]
    assert pipeline["user"] == "${HOST_UID:-1000}:${HOST_GID:-1000}"
    env = env_example()
    assert env["HOST_UID"].isdigit() and env["HOST_GID"].isdigit()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^export HOST_UID \?= \$\(shell id -u\)$", makefile, re.M)
    assert re.search(r"^export HOST_GID \?= \$\(shell id -g\)$", makefile, re.M)
    # The MI300X batch writes serving/llm_cache/ in the same checkout, as the same user.
    assert services("docker-compose.yml")["batch-generate"]["user"] == pipeline["user"]
    # The other services keep the image's own user.
    for service_name, service in services("docker-compose.yml").items():
        if service_name not in HOST_WRITERS:
            assert "user" not in service, service_name


def makefile_targets() -> dict[str, tuple[str, list[str]]]:
    """Each Makefile target: (prerequisites, recipe lines)."""
    targets: dict[str, tuple[str, list[str]]] = {}
    current = None
    for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+):(?!=)(.*)$", line)
        if match:
            current = match.group(1)
            targets[current] = (match.group(2).strip(), [])
        elif line.startswith("\t") and current is not None:
            targets[current][1].append(line.strip())
        elif line and not line.startswith("#"):
            current = None
    return targets


def test_makefile_data_targets() -> None:
    """F1: the ingest, its checks and the generator are one make command each."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    targets = makefile_targets()
    assert re.search(
        r"^CALIBRATION \?= pipeline/calibration/asos_calibration\.json$", makefile, re.M
    )
    asos = " ".join(targets["asos"][1])
    assert "python -m pipeline.ingest --dest data/raw/asos" in asos
    assert "$(SOURCE_DIR)" in asos and "--source-dir" in asos
    verify = " ".join(targets["asos-verify"][1])
    assert "python -m pipeline.ingest.asos_download --dest data/raw/asos --verify-only" in verify
    test_asos = " ".join(targets["test-asos"][1])
    assert "ASOS_DIR=data/raw/asos" in test_asos and "-m asos" in test_asos
    for target, scenario in (("generate", "main"), ("generate-ood", "ood")):
        recipe = " ".join(targets[target][1])
        assert "python -m pipeline.generator --calibration $(CALIBRATION)" in recipe, target
        assert f"--scenario {scenario}" in recipe, target
        assert "--seed $(SEED)" in recipe and "--out data" in recipe, target
    phony = re.search(r"^\.PHONY:(.*)$", makefile.replace("\\\n", " "), re.M)
    assert phony is not None
    for target in ("asos", "asos-verify", "test-asos", "generate", "generate-ood", "pipeline"):
        assert re.search(rf"(^|\s){re.escape(target)}(\s|$)", phony.group(1)), target


def test_f16_make_pipeline_runs_phase_2_in_order() -> None:
    """Review F16: `make pipeline` builds the store, the graph, the features, the anomalies and
    the risk models with their scores, in that order; graph and anomalies need no features; the
    features never pass --clusters (review F14); the offline reports have targets of their own."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    targets = makefile_targets()
    assert targets["pipeline"][0].split() == [
        "generate",
        "generate-ood",
        "graph",
        "features",
        "anomalies",
        "risk",
        "serving",
    ]
    expected = {
        "graph": [
            "python -m pipeline.graph --store data",
            "python -m pipeline.graph --store data/ood",
        ],
        "features": [
            "python -m pipeline.features --store data --out data",
            "python -m pipeline.features --store data/ood --out data/ood --source data",
        ],
        "anomalies": [
            "python -m pipeline.anomalies --store data --out data",
            "python -m pipeline.anomalies --store data/ood --out data/ood",
        ],
        "risk": [
            "python -m ml.risk --data data --out artifacts --scores data "
            "--results docs/results/us-204-risk.md"
        ],
        "serving": ["python -m pipeline.serving --data data --artifacts artifacts --out serving"],
    }
    for target, recipe in expected.items():
        assert targets[target][1] == recipe, target
    assert "--clusters" not in " ".join(targets["features"][1])
    results = " ".join(targets["results"][1])
    for job in ("pipeline.graph.evaluate", "ml.reference.report", "pipeline.anomalies.evaluate"):
        assert f"python -m {job} --data data" in results, job
    assert "artifacts/eval/graph/evaluation.json" in results
    assert "python -m ml.scoring --data data" in " ".join(targets["score"][1])
    phony = re.search(r"^\.PHONY:(.*)$", makefile.replace("\\\n", " "), re.M)
    assert phony is not None
    for target in (
        "graph",
        "features",
        "anomalies",
        "risk",
        "score",
        "results",
        "propensity",
        "serving",
    ):
        assert re.search(rf"(^|\s){re.escape(target)}(\s|$)", phony.group(1)), target


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


def test_vllm_runs_offline() -> None:
    # Rule 2: vLLM never calls huggingface.co at runtime; only weights.sh downloads.
    assert services("docker-compose.yml")["vllm"]["environment"]["HF_HUB_OFFLINE"] == "1"
    assert "HF_HUB_OFFLINE=0" in (INFRA / "spikes" / "weights.sh").read_text(encoding="utf-8")


def test_dgl_smoke_check_fails_without_a_gpu() -> None:
    command = services("docker-compose.yml")["trainer-gnn"]["command"]
    assert "raise SystemExit(0 if torch.cuda.is_available() else 1)" in command[-1]


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


def test_the_graphsage_trainer_never_sees_the_ground_truth() -> None:
    """US-202 and A4 (ml/gnn) need the ASOS tables, the stores and their features, never the
    ground truth: trainer-graphsage mounts data/ subfolders read-only and no ancestor of
    data/truth, so rule 12 holds by construction (it is not one of the JOB_SERVICES)."""
    service = services("docker-compose.yml")["trainer-graphsage"]
    assert "trainer-graphsage" not in JOB_SERVICES
    data, truth = ROOT / "data", ROOT / "data" / "truth"
    mounts = bind_mounts(service)
    for name in ("raw", "store", "features", "ood"):
        assert ((data / name).resolve(), f"/app/data/{name}", True) in mounts, name
    for source, _, read_only in mounts:
        assert source != truth and truth not in source.parents and source not in truth.parents
        if source == data or data in source.parents:
            assert read_only, source
    assert ((ROOT / "artifacts").resolve(), "/app/artifacts", False) in mounts
    assert service["build"]["dockerfile"] == "infra/docker/trainer-gnn.Dockerfile"
    assert service["build"]["args"]["DGL_IMAGE"] == "${DGL_IMAGE}"
    gpu = services("compose.gpu.yml")["trainer-graphsage"]
    assert gpu["build"]["args"]["DGL_IMAGE"].startswith("${DGL_IMAGE:?")
    assert gpu["environment"]["DEVICE"] == "gpu"


# What the pinned rocm/dgl image (DGL_IMAGE in .env.example) ships in /opt/venv, from its layer
# metadata: torch 2.8.0 (ROCm 7.0), amd-dgl 2.4.0, scipy 1.16.2, and numpy 1.26.4, which amd-dgl
# pins (Requires-Dist numpy==1.26.4) over the numpy 2.3.3 the PyTorch layer had. numpy and pandas
# are therefore installed on top (the ingest and propensity code need numpy 2); torch, DGL and
# scipy (built for numpy >= 1.25.2, < 2.6) stay as shipped.
IMAGE_STACK = frozenset({"torch", "dgl", "scipy"})
MODULE_DISTRIBUTION = {"sklearn": "scikit-learn", "xgboost": "xgboost-cpu"}


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith(("-r", "--")):
            name, _, version = line.partition("==")
            pins[name.lower().replace("_", "-")] = version
    return pins


def third_party_imports(*modules: str) -> set[str]:
    """Top-level third-party modules imported, at any depth, by the project modules reachable
    from `modules` (every import statement, lazy ones included)."""
    import ast
    import sys

    def path_of(name: str) -> Path | None:
        base = ROOT.joinpath(*name.split("."))
        for candidate in (base / "__init__.py", base.with_suffix(".py")):
            if candidate.exists():
                return candidate
        return None

    local = {p.name for p in ROOT.iterdir() if (p / "__init__.py").exists()} | {"tests"}
    seen: set[str] = set()
    found: set[str] = set()
    todo = list(modules)
    while todo:
        name = todo.pop()
        path = path_of(name)
        if name in seen or path is None:
            continue
        seen.add(name)
        package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                parts = package.split(".")
                base = (
                    ".".join(parts[: len(parts) - node.level + 1] + [node.module or ""]).strip(".")
                    if node.level
                    else node.module or ""
                )
                names = [base, *(f"{base}.{a.name}" for a in node.names)]
            for imported in filter(None, names):
                top = imported.split(".")[0]
                if top in local:
                    todo.append(imported)
                elif top not in sys.stdlib_module_names and top != "__future__":
                    found.add(top)
    return found


def test_the_graphsage_image_adds_what_the_jobs_import_and_keeps_the_rocm_stack() -> None:
    dockerfile = (INFRA / "docker" / "trainer-gnn.Dockerfile").read_text(encoding="utf-8")
    assert "FROM ${DGL_IMAGE}" in dockerfile and "--extra-index-url" not in dockerfile
    assert "--no-deps --index-url https://pypi.org/simple" in dockerfile
    assert "import ml.gnn.asos, ml.gnn.embeddings" in dockerfile  # fails the build, not the run
    assert "COPY bench bench" in dockerfile  # B2 runs here (make bench-b2)
    assert "numpy.__version__ == '2.5.4'" in dockerfile and "SAGEConv" in dockerfile
    pins = _pins(INFRA / "docker" / "requirements-gnn.txt")
    assert pins and all(pins.values()), "every package is pinned"
    assert not set(pins) & IMAGE_STACK, "the rocm/dgl image ships these, built together for ROCm"
    assert not set(pins) & {"xgboost", "amd-xgboost"}
    base = _pins(ROOT / "requirements" / "base.txt") | _pins(ROOT / "requirements" / "dev.txt")
    for name in set(pins) & set(base):
        assert pins[name] == base[name], name
    assert pins["numpy"] == "2.5.4" and pins["pandas"] == "3.0.6"
    # Everything the jobs, B2 and the tests of `make gnn-test-gpu` import is either shipped by
    # the image or pinned here.
    needed = third_party_imports(
        "ml.gnn.__main__",
        "ml.gnn.asos",
        "ml.gnn.embeddings",
        "ml.gnn.report",
        "bench.b2_gnn",
        "tests.conftest",
        "tests.test_gnn_model",
        "tests.test_gnn_jobs",
    )
    for module in sorted(needed - IMAGE_STACK):
        distribution = MODULE_DISTRIBUTION.get(module, module)
        assert distribution in pins, f"{module} is imported but not in requirements-gnn.txt"
    cpu = (INFRA / "requirements-gnn-cpu.txt").read_text(encoding="utf-8")
    assert "torch==2.8.0+cpu" in cpu  # the PyTorch of the rocm/dgl image, CPU build


def test_makefile_gnn_targets() -> None:
    targets = makefile_targets()
    assert targets["gnn-asos"][1] == [
        "python -m ml.gnn asos --raw data/raw --out artifacts --sample $(GNN_SAMPLE)"
    ]
    assert " ".join(targets["embeddings"][1]).count("python -m ml.gnn embed") == 2
    assert "--encoder artifacts/gnn/models/customer_encoder" in " ".join(targets["embeddings"][1])
    assert targets["gnn-results"][1] == [
        "python -m ml.gnn report --artifacts artifacts --doc docs/results/us-202-graphsage.md"
    ]
    assert "--device gpu" in " ".join(targets["gnn-asos-gpu"][1])
    assert "--device cpu" in " ".join(targets["gnn-asos-cpu-container"][1])
    for target in ("gnn-asos-gpu", "gnn-asos-cpu-container", "embeddings-gpu"):
        # Alone on the GPU and on the CPU, as B1 and B2 (review G10).
        assert targets[target][1][0] == "@$(VLLM_DOWN)", target
    assert "embeddings" not in targets["pipeline"][0].split()  # make pipeline needs no torch


def test_makefile_targets_use_overrides_and_existing_scripts() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^GPU = .*-f infra/compose\.gpu\.yml", makefile, re.M)
    assert re.search(r"^DEMO = .*-f infra/compose\.demo\.yml", makefile, re.M)
    assert re.search(r"^PUBLIC = -f infra/compose\.public\.yml$", makefile, re.M)
    assert "$$status -eq 5" in makefile, "make test-gpu must accept 'no tests collected'"
    for script in set(re.findall(r"infra/spikes/[\w.]+\.(?:py|sh)", makefile)):
        assert (ROOT / script).is_file(), script


# ---- The product stack: reset, api, ui, proxy, telemetry, MI300X batch, cached demo ----------

API_ENV = {
    "DATABASE_URL",
    "DEVICE",
    "LLM_MODE",
    "DEMO_MODE",
    "SEED",
    "SERVING_DIR",
    "BENCHMARKS_DIR",
    "VLLM_BASE_URL",
    "VLLM_API_KEY",
    "GEMMA_MODEL",
    "PUBLIC_READ_ONLY",
    "DEMO_CLOCK",
    "LLM_CONCURRENCY",
    "VLLM_TIMEOUT_S",
}


def compose_service(name: str) -> dict[str, Any]:
    return services("docker-compose.yml")[name]


def test_the_api_reads_the_serving_snapshot_and_the_benchmarks_read_only() -> None:
    """ADR-16: the API loads serving/ (models included) and GET /api/benchmarks reads
    bench/results; both read-only, never data/ or artifacts/ (rule 12, review F2)."""
    api = compose_service("api")
    assert set(api["environment"]) == API_ENV
    assert api["environment"]["SERVING_DIR"] == "/app/serving"
    assert api["environment"]["BENCHMARKS_DIR"] == "/app/bench/results"
    assert api["environment"]["GEMMA_MODEL"] == "${GEMMA_MODEL}"  # H30: the model vLLM serves
    assert sorted(bind_mounts(api)) == [
        ((ROOT / "bench" / "results").resolve(), "/app/bench/results", True),
        ((ROOT / "serving").resolve(), "/app/serving", True),
    ]
    assert "/serving/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_api_runs_one_worker_and_waits_for_the_reset() -> None:
    """The snapshot lives in memory (about 1.7 GB at full scale): one uvicorn worker. The API
    starts after the reset has exited 0, and is healthy only once /readyz says so."""
    dockerfile = (ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    cmd = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))
    assert "uvicorn" in cmd and "--workers" not in cmd
    api = compose_service("api")
    assert "command" not in api
    assert api["depends_on"]["reset"]["condition"] == "service_completed_successfully"
    assert api["depends_on"]["postgres"]["condition"] == "service_healthy"
    check = api["healthcheck"]
    assert check["start_period"] == "90s" and "/readyz" in check["test"][-1]


class _Readyz(BaseHTTPRequestHandler):
    body: ClassVar[bytes] = b"{}"

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: object) -> None:
        pass


def _readyz(status: str, **checks: str) -> bytes:
    states = {"database": "ok", "scores": "ok", "models": "ok", "vllm": "disabled"} | checks
    items = [{"component": k, "status": v, "detail": None} for k, v in states.items()]
    return json.dumps({"status": status, "checks": items}).encode()


@pytest.mark.parametrize(
    ("body", "healthy"),
    [
        (_readyz("ready"), True),
        (_readyz("degraded", vllm="unavailable"), True),  # templates still serve (NFR-09)
        (_readyz("degraded", scores="unavailable", models="unavailable"), False),  # H37
        (_readyz("not_ready", database="unavailable"), False),  # empty: no reset yet
    ],
)
def test_the_api_healthcheck_reads_the_readiness_body(body: bytes, healthy: bool) -> None:
    """/readyz answers 200 with its status in the body: the healthcheck reads the body."""
    code = compose_service("api")["healthcheck"]["test"][-1]
    _Readyz.body = body
    server = HTTPServer(("127.0.0.1", 0), _Readyz)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/readyz"
        script = code.replace("http://127.0.0.1:8000/readyz", url)
        assert script != code
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        done = subprocess.run([sys.executable, "-c", script], env=env, check=False, timeout=30)
    finally:
        server.shutdown()
    assert (done.returncode == 0) is healthy


def test_api_env_names_are_read_by_the_api() -> None:
    """Every variable compose gives the API image is read by the code under the same name
    (apps.api.settings, and BENCHMARKS_DIR in the benchmarks service): a variable passed but
    never read is a setting the deployment believes it changes."""
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "apps" / "api").rglob("*.py")
    )
    unread = {name for name in API_ENV if f'"{name}"' not in source}
    assert not unread, f"passed but never read: {unread}"


def _resolve(value: str, env: dict[str, str]) -> str:
    """Compose interpolation of ${NAME} and ${NAME:-default} against .env.example."""
    return re.sub(
        r"\$\{(\w+)(?::-([^}]*))?\}",
        lambda m: env.get(m.group(1)) or (m.group(2) or ""),
        value,
    )


def test_api_settings_accept_the_base_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    env = env_example()
    for key, value in compose_service("api")["environment"].items():
        monkeypatch.setenv(key, _resolve(str(value), env))
    settings = Settings.from_env()
    assert settings.serving_dir == Path("/app/serving")
    assert settings.database_url is not None
    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_the_reset_runs_once_on_the_snapshot_before_the_api() -> None:
    """US-903: `python -m apps.api.reset` with the API's own settings, the snapshot read-only,
    after postgres is healthy. In the gpu profile it drafts through vLLM, so it waits for it."""
    reset = compose_service("reset")
    assert reset["command"] == ["python", "-m", "apps.api.reset"]
    assert reset["environment"] == compose_service("api")["environment"]
    assert reset["profiles"] == ["dev", "gpu", "demo"]
    assert reset["restart"] == "no"
    assert bind_mounts(reset) == [((ROOT / "serving").resolve(), "/app/serving", True)]
    assert reset["depends_on"]["postgres"]["condition"] == "service_healthy"
    gpu = services("compose.gpu.yml")["reset"]
    assert gpu["environment"] == services("compose.gpu.yml")["api"]["environment"]
    assert gpu["depends_on"]["vllm"]["condition"] == "service_healthy"
    for name in API_IMAGE_SERVICES:
        service = compose_service(name)
        assert service["build"] == {"context": "..", "dockerfile": "apps/api/Dockerfile"}, name
        assert service["image"] == "returnring-api:local", name


def test_the_mi300x_batch_writes_only_the_llm_cache_of_the_snapshot() -> None:
    """ADR-03, ADR-16 section 4: python -m apps.api.batch_generate through the local vLLM (rule
    2: VLLM_BASE_URL is the internal service), GEMMA_MODEL passed (H30)."""
    batch = compose_service("batch-generate")
    assert batch["command"] == ["python", "-m", "apps.api.batch_generate"]
    env = batch["environment"]
    assert (env["LLM_MODE"], env["DEVICE"], env["DEMO_MODE"]) == ("vllm", "gpu", "live")
    assert env["GEMMA_MODEL"] == "${GEMMA_MODEL}" and env["VLLM_BASE_URL"] == "${VLLM_BASE_URL}"
    assert env_example()["VLLM_BASE_URL"] == "http://vllm:8001/v1"
    assert bind_mounts(batch) == [((ROOT / "serving").resolve(), "/app/serving", False)]
    assert batch["depends_on"] == {"vllm": {"condition": "service_healthy"}}
    assert batch["profiles"] == ["gpu-jobs"]


def test_the_telemetry_sampler_stores_the_amd_smi_readings() -> None:
    """US-905: the ROCm container writes amd-smi JSON every 5 s into a named volume; the sampler
    (API image) reads it read-only and writes telemetry_samples."""
    writer, sampler = compose_service("telemetry"), compose_service("telemetry-sampler")
    command = writer["command"][-1]
    assert "amd-smi metric --json > /telemetry/amd-smi.json.tmp" in command
    assert "mv /telemetry/amd-smi.json.tmp /telemetry/amd-smi.json" in command
    assert "rm -f /telemetry/amd-smi.json" in command and "sleep 5" in command
    assert writer["volumes"] == ["telemetry:/telemetry"]
    assert sampler["volumes"] == ["telemetry:/telemetry:ro"]
    assert sampler["command"] == [
        "python",
        "-m",
        "apps.api.services.other.telemetry",
        "--source-file",
        "/telemetry/amd-smi.json",
    ]
    assert set(sampler["environment"]) == {"DATABASE_URL", "DEVICE"}
    assert sampler["profiles"] == writer["profiles"] == ["gpu"]
    assert sampler["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert sampler["restart"] == "unless-stopped"
    assert "telemetry" in load("docker-compose.yml")["volumes"]


def test_the_ui_is_built_from_apps_ui_and_served_through_the_proxy() -> None:
    ui = compose_service("ui")
    assert ui["build"]["context"] == "../apps/ui"
    assert ui["build"]["args"] == {"VITE_READ_ONLY": "${PUBLIC_READ_ONLY:-false}"}
    demo = services("compose.demo.yml")["ui"]
    assert demo["build"]["args"] == {"VITE_READ_ONLY": "${PUBLIC_READ_ONLY:-true}"}
    assert ui["profiles"] == ["dev", "gpu", "demo"]


UI = ROOT / "apps" / "ui"


@pytest.mark.skipif(
    not (UI / "package.json").exists(),
    reason="apps/ui is the placeholder page; the phase 5 UI brings package.json and its image",
)
def test_the_ui_image_is_multi_stage_with_spa_fallback_and_gzip() -> None:
    dockerfile = (UI / "Dockerfile").read_text(encoding="utf-8")
    stages = re.findall(r"^FROM (\S+)", dockerfile, re.M)
    assert stages[0].startswith("node:22") and stages[-1].startswith("nginx:"), stages
    assert "npm ci" in dockerfile and "npm run build" in dockerfile
    assert "COPY --from=build /app/dist /usr/share/nginx/html" in dockerfile
    nginx = (UI / "nginx.conf").read_text(encoding="utf-8")
    assert "try_files $uri $uri/ /index.html;" in nginx and "gzip on;" in nginx
    # The public read-only build: compose passes VITE_READ_ONLY as a build argument.
    assert re.search(r"^ARG VITE_READ_ONLY", dockerfile, re.M), "ARG VITE_READ_ONLY missing"


CADDYFILE = INFRA / "Caddyfile"


def test_the_proxy_routes_the_api_and_the_ui_on_one_origin() -> None:
    proxy = compose_service("proxy")
    assert proxy["profiles"] == ["dev", "gpu", "demo"]
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in proxy["volumes"]
    assert proxy["depends_on"]["api"]["condition"] == "service_healthy"
    assert proxy["depends_on"]["ui"]["condition"] == "service_started"
    env = proxy["environment"]
    assert (env["SITE_ADDRESS"], env["ADMIN_AUTH"]) == (":80", "off")  # laptop: loopback only
    text = CADDYFILE.read_text(encoding="utf-8")
    for path in ("/api/*", "/healthz", "/readyz"):
        assert re.search(rf"handle {re.escape(path)} {{\s*reverse_proxy api:8000\s*}}", text), path
    assert re.search(r"handle {\s*reverse_proxy ui:80\s*}", text)
    assert "{$SITE_ADDRESS} {" in text and "import admin_auth_{$ADMIN_AUTH}" in text
    assert re.search(r"request_body /api/\* {\s*max_size 10MB\s*}", text)  # H24
    site = text.split("{$SITE_ADDRESS} {", 1)[1]
    assert not re.search(r"^\s*encode\b", site, re.M)  # the agent's SSE stays unbuffered


def test_a_public_server_puts_basic_auth_on_admin_telemetry_and_writes() -> None:
    """Spec 11: HTTPS on PUBLIC_HOST, public read-only views, basic auth on the telemetry data,
    and on every write unless PUBLIC_READ_ONLY=true (then the API refuses them). The admin
    page's client route /system is not protected (a single-page UI serves it from any path, and
    a reload would ask judges for a password): its data endpoint is. The values are required: a
    public server without a host or a password hash does not start."""
    text = CADDYFILE.read_text(encoding="utf-8")
    off = text.split("(admin_auth_off) {", 1)[1].split("}", 1)[0]
    assert off.strip() == ""
    on = text.split("(admin_auth_on) {", 1)[1].split("\n}\n", 1)[0]
    assert "@admin path /api/telemetry /api/telemetry/*" in on
    assert not re.search(r"^\s*@\w+ path .*/system", text, re.M)
    assert on.count(f"{ADMIN_USER} {{$ADMIN_PASSWORD_HASH}}") == 2
    assert "basic_auth @admin {" in on and "basic_auth @write {" in on
    write = on.split("@write {", 1)[1].split("\n\t}", 1)[0]
    assert "path /api/*" in write and "not method GET HEAD OPTIONS" in write
    assert 'expression `!("{$PUBLIC_READ_ONLY}" in ["true", "on", "1"])`' in write
    public = services("compose.public.yml")["proxy"]
    assert public["ports"] == ["80:80", "443:443"]
    env = public["environment"]
    assert env["ADMIN_AUTH"] == "on"
    assert env["SITE_ADDRESS"].startswith("${PUBLIC_HOST:?")
    assert env["ADMIN_PASSWORD_HASH"].startswith("${ADMIN_PASSWORD_HASH:?")
    example = env_example()
    assert example["ADMIN_PASSWORD_HASH"] == ""  # no secret in git
    # Empty, so the :? guard stops a public start that names no host; the example is a comment.
    assert example["PUBLIC_HOST"] == ""
    assert "203-0-113-10.sslip.io" in (ROOT / ".env.example").read_text(encoding="utf-8")
    base = compose_service("proxy")["environment"]["PUBLIC_READ_ONLY"]
    assert base == "${PUBLIC_READ_ONLY:-false}"
    demo = services("compose.demo.yml")["proxy"]["environment"]["PUBLIC_READ_ONLY"]
    assert demo == "${PUBLIC_READ_ONLY:-true}"


def test_cpu_images_pinned_by_tag_and_digest() -> None:
    """postgres and caddy are pinned like the GPU images; make admin-password hashes with the
    proxy's own image."""
    for name in ("postgres", "proxy"):
        image = compose_service(name)["image"]
        assert DIGEST.match(image), (name, image)
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    caddy = re.search(r"^CADDY_IMAGE = (\S+)$", makefile, re.M)
    assert caddy is not None and caddy.group(1) == compose_service("proxy")["image"]
    recipe = " ".join(makefile_targets()["admin-password"][1])
    assert "$(CADDY_IMAGE) caddy hash-password" in recipe


def test_the_admin_password_hash_is_cheap_enough_for_a_small_public_server() -> None:
    """A wrong password of the admin user costs one bcrypt comparison on the public droplet:
    the hash is made at cost 10 (about 70 ms measured with Caddy 2.11.7 on 4 shared vCPUs), not
    Caddy's default 14 (16 times that)."""
    recipe = " ".join(makefile_targets()["admin-password"][1])
    assert "caddy hash-password --algorithm bcrypt --bcrypt-cost 10" in recipe
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ADMIN_PASSWORD_HASH='$2a$10$...'" in example and "$2a$14$" not in example


ADMIN_USER = "admin"
ADMIN_BASIC = "Basic " + base64.b64encode(f"{ADMIN_USER}:".encode()).decode()  # "Basic YWRtaW46"


def test_basic_credentials_of_another_user_get_a_401_before_bcrypt() -> None:
    """Caddy compares the password of an unknown user with its fixed bcrypt cost-14 fake hash
    (about 1.1 s of CPU, measured): any user name would cost the public droplet that per
    request. Basic credentials that do not start with the admin user's get a 401 first, with
    the challenge basic_auth sends, so a wrong user name costs no hash at all. The admin user is
    fixed (its base64 prefix is in the Caddyfile); ADMIN_USER is gone from the environment."""
    assert ADMIN_BASIC == "Basic YWRtaW46"  # 6 bytes: "admin:" encodes to whole base64 groups
    text = CADDYFILE.read_text(encoding="utf-8")
    head = text.split("(admin_auth_off) {", 1)[0]
    assert re.search(r"^{\n\torder respond before basic_auth\n}$", head, re.M)
    on = text.split("(admin_auth_on) {", 1)[1].split("\n}\n", 1)[0]
    matcher = re.search(r"^\t@other_user expression `(.+)`$", on, re.M)
    assert matcher is not None
    assert matcher.group(1) == (
        '{http.request.header.Authorization}.matches("(?i)^basic ") && '
        f'!{{http.request.header.Authorization}}.startsWith("{ADMIN_BASIC}")'
    )
    assert '\theader @other_user WWW-Authenticate `Basic realm="restricted"`' in on
    refuse = on.index("\trespond @other_user 401")
    assert refuse < on.index("basic_auth @admin") and refuse < on.index("basic_auth @write")
    assert "ADMIN_USER" not in text
    assert "ADMIN_USER" not in compose_service("proxy")["environment"]
    assert "ADMIN_USER" not in services("compose.public.yml")["proxy"]["environment"]
    assert "ADMIN_USER" not in env_example()


@pytest.mark.skipif(shutil.which("caddy") is None, reason="caddy not installed")
def test_caddy_refuses_another_user_before_basic_auth() -> None:
    """The Caddyfile as Caddy reads it on a public server: the 401 route comes before both
    basic-auth routes (respond is ordered before basic_auth)."""
    env = {
        **os.environ,
        "SITE_ADDRESS": ":8080",
        "ADMIN_AUTH": "on",
        "ADMIN_PASSWORD_HASH": "$2a$10$" + "x" * 53,
        "PUBLIC_READ_ONLY": "false",
    }
    done = subprocess.run(
        ["caddy", "adapt", "--config", str(CADDYFILE), "--adapter", "caddyfile"],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    routes = json.loads(done.stdout)["apps"]["http"]["servers"]["srv0"]["routes"]
    handlers = [[h["handler"] for h in route.get("handle", [])] for route in routes]
    refuse = handlers.index(["static_response"])
    assert routes[refuse]["handle"][0]["status_code"] == 401
    assert ADMIN_BASIC in routes[refuse]["match"][0]["expression"]["expr"]
    auth = [n for n, h in enumerate(handlers) if h == ["authentication"]]
    assert len(auth) == 2 and refuse < min(auth)


def test_the_long_running_services_restart_after_a_crash_or_a_reboot() -> None:
    """A crash or a reboot of the public server must not take the demo down until someone logs
    in: on the GPU server vLLM and the amd-smi writer restart too (a sampler without its writer
    would read the file a reboot left behind). The reset stays one-shot: a daemon restart keeps
    the database (pgdata persists) instead of resetting it, and the API reconnects lazily."""
    compose = services("docker-compose.yml")
    for name in ("postgres", "api", "ui", "proxy", "telemetry-sampler", "telemetry", "vllm"):
        assert compose[name]["restart"] == "unless-stopped", name
    assert set(RUNNING_SERVICES) - {"reset"} == {
        name for name, service in compose.items() if service.get("restart") == "unless-stopped"
    }
    assert compose["reset"]["restart"] == "no"
    for name in OFFLINE_SERVICES - {"spike-client"}:
        assert "restart" not in compose[name], name


def test_postgres_is_healthy_only_once_it_listens_on_tcp() -> None:
    """First-start race: the image's init-only server listens on the Unix socket only, so a
    pg_isready over TCP is not ready until the real server is (the reset also retries)."""
    check = compose_service("postgres")["healthcheck"]["test"]
    assert check == [
        "CMD-SHELL",
        "pg_isready -h 127.0.0.1 -p 5432 -U ${POSTGRES_USER} -d ${POSTGRES_DB}",
    ]


def test_the_cached_demo_serves_the_demo_package_with_the_gpu_off() -> None:
    """US-901: DEMO_MODE=cached, no vLLM, the API and the reset read demo-cache/ (the package of
    make demo-export) at the same container paths, so the mounts of docker-compose.yml are
    replaced; public views are read-only unless .env says otherwise. The API also reads the
    package's MANIFEST.json next to the snapshot, as in demo-cache/, for its label (rule 11)."""
    demo = services("compose.demo.yml")
    for name in ("api", "reset"):
        env = demo[name]["environment"]
        assert (env["DEMO_MODE"], env["LLM_MODE"], env["DEVICE"]) == ("cached", "mock", "cpu")
        assert env["PUBLIC_READ_ONLY"] == "${PUBLIC_READ_ONLY:-true}"
    package = (ROOT / "demo-cache").resolve()
    assert sorted(bind_mounts(demo["api"])) == [
        (package / "MANIFEST.json", "/app/MANIFEST.json", True),
        (package / "bench" / "results", "/app/bench/results", True),
        (package / "serving", "/app/serving", True),
    ]
    assert bind_mounts(demo["reset"]) == [(package / "serving", "/app/serving", True)]
    assert "vllm" not in demo and "telemetry" not in demo
    for name in ("vllm", "telemetry", "telemetry-sampler", "trainer-gbm", "trainer-gnn"):
        assert "demo" not in compose_service(name)["profiles"], name  # GPU containers off
    assert "demo-cache/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_docker_build_context_leaves_out_data_and_secrets() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for entry in ("data", "artifacts", "serving", "demo-cache", "bench-work", ".env", ".git"):
        assert entry in ignored, entry
    assert "!.env.example" in ignored


def test_makefile_deploy_targets() -> None:
    """US-904: the judge's three commands, then reset, walk, export and the cached demo."""
    targets = makefile_targets()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    def recipe(name: str) -> str:
        return " ".join(targets[name][1])

    assert "$(ENV_FILE)" in targets["demo-data"][0] and "$(ENV_FILE)" in targets["dev-up"][0]
    assert "mkdir -p data artifacts serving" in recipe("demo-data")
    assert "$(COMPOSE) run --rm --build pipeline" in recipe("demo-data")
    assert "SEED=$(SEED) SCALE=$(SCALE)" in recipe("demo-data")
    assert "test -f serving/manifest.json" in recipe("dev-up")
    assert "$(MAKE) demo-data" in recipe("dev-up")
    assert "$(COMPOSE) --profile dev up -d --build" in recipe("dev-up")
    assert recipe("reset") == "$(COMPOSE) --profile dev run --rm reset"
    assert recipe("gpu-reset") == "$(GPU) run --rm reset"
    assert recipe("demo-reset") == "$(DEMO) run --rm reset"
    assert "python -m scripts.demo_walk --base-url $(API_URL)" in recipe("demo-walk")
    assert re.search(r"^API_URL \?= http://127\.0\.0\.1:8000$", makefile, re.M)
    assert recipe("batch-generate") == "$(GPU) run --rm batch-generate"
    export = recipe("demo-export")
    assert "python3 -m scripts.demo_export build --serving serving --bench bench/results" in export
    assert "tar -czf $(DEMO_DIR).tar.gz $(DEMO_DIR)" in export
    # Rules 11 and 13: the hardware has no default, the drafts must be GEMMA_MODEL's (.env), and
    # a rehearsal is declared (scripts/demo_export.py refuses anything else).
    assert re.search(r"^DEMO_GPU \?=[ \t]*$", makefile, re.M), "DEMO_GPU must have no default"
    assert "$(ENV_FILE)" in targets["demo-export"][0] and "$(LOAD_ENV)" in export
    assert '--generated-on "$(DEMO_GPU)" --model "$$GEMMA_MODEL"' in export
    assert "$(if $(REHEARSAL),--rehearsal)" in export
    assert "$(if $(ALLOW_EMPTY_CACHE),--allow-empty-cache)" in export
    assert "python3 -m scripts.demo_export verify $(DEMO_DIR)" in recipe("demo-verify")
    assert "demo-verify" in targets["demo-up"][0] and "demo-verify" in targets["demo-local"][0]
    assert "$(DEMO) $(PUBLIC) up -d --build" in recipe("demo-up")
    assert "$(DEMO) up -d --build" in recipe("demo-local")
    assert "$(GPU) $(PUBLIC) up -d --build" in recipe("gpu-up")
    assert re.search(r"^DEMO_DIR = demo-cache$", makefile, re.M)
    phony = re.search(r"^\.PHONY:(.*)$", makefile.replace("\\\n", " "), re.M)
    assert phony is not None
    for target in (
        "demo-data",
        "dev-up",
        "reset",
        "demo-walk",
        "batch-generate",
        "demo-export",
        "demo-import",
        "demo-verify",
        "demo-up",
        "demo-local",
        "demo-reset",
        "demo-down",
        "admin-password",
    ):
        assert target in targets, target
        assert re.search(rf"(^|\s){re.escape(target)}(\s|$)", phony.group(1)), target


# ---- Rendered configs (docker compose config) -------------------------------------------------


def render(tmp_path: Path, profile: str, files: list[str], extra_env: str = "") -> dict[str, Any]:
    """The merged config of `files` for the profiles in `profile` (comma separated)."""
    env_file = tmp_path / ".env"
    text = (ROOT / ".env.example").read_text(encoding="utf-8") + extra_env
    env_file.write_text(text, encoding="utf-8")
    cmd = ["docker", "compose", "-f", str(INFRA / "docker-compose.yml")]
    for extra in files:
        cmd += ["-f", str(INFRA / extra)]
    cmd += ["--env-file", str(env_file)]
    for name in profile.split(","):
        cmd += ["--profile", name]
    cmd += ["config", "--format", "json"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    rendered: dict[str, Any] = json.loads(out.stdout)["services"]
    return rendered


# A bcrypt-shaped value for the public override (single quotes: Compose reads it literally),
# and the host .env.example leaves empty.
PUBLIC_HOST_ENV = "\nPUBLIC_HOST=203-0-113-10.sslip.io\n"
PUBLIC_ENV = PUBLIC_HOST_ENV + "ADMIN_PASSWORD_HASH='" + "$2a$10$" + "x" * 53 + "'\n"


@pytest.mark.skipif(not compose_available(), reason="docker compose not installed")
@pytest.mark.parametrize(
    ("profile", "files", "expected"),
    [
        ("dev", [], {"DEVICE": "cpu", "LLM_MODE": "mock", "DEMO_MODE": "live"}),
        ("gpu", ["compose.gpu.yml"], {"DEVICE": "gpu", "LLM_MODE": "vllm"}),
        ("gpu", ["compose.gpu.yml", "compose.public.yml"], {"DEVICE": "gpu", "LLM_MODE": "vllm"}),
        ("demo", ["compose.demo.yml"], {"DEMO_MODE": "cached", "LLM_MODE": "mock"}),
        ("demo", ["compose.demo.yml", "compose.public.yml"], {"DEMO_MODE": "cached"}),
    ],
)
def test_rendered_compose_config(
    profile: str, files: list[str], expected: dict[str, str], tmp_path: Path
) -> None:
    rendered = render(tmp_path, profile, files, PUBLIC_ENV)
    for key, value in expected.items():
        assert rendered["api"]["environment"][key] == value
        assert rendered["reset"]["environment"][key] == value
    assert "ports" not in rendered.get("vllm", {})
    assert "pipeline" not in rendered and "batch-generate" not in rendered  # never started by up
    assert {"postgres", "reset", "api", "ui", "proxy"} <= set(rendered)
    assert ("telemetry-sampler" in rendered) is (profile == "gpu")
    for service_name, service in rendered.items():
        for volume in service.get("volumes", []):
            source = volume.get("source", "")
            assert not source.endswith("/data/truth")
            inside = source.replace(str(ROOT), "")
            if service_name in RUNNING_SERVICES and volume.get("type") == "bind":
                assert not inside.startswith(("/data", "/artifacts")), (service_name, source)
    package = "demo-cache" if profile == "demo" else ""
    sources = {v["target"]: v["source"] for v in rendered["api"]["volumes"]}
    assert sources["/app/serving"] == str((ROOT / package / "serving").resolve())
    assert sources["/app/bench/results"] == str((ROOT / package / "bench" / "results").resolve())
    if profile == "demo":  # the package's MANIFEST.json next to the snapshot, as in demo-cache/
        assert sources["/app/MANIFEST.json"] == str((ROOT / package / "MANIFEST.json").resolve())
    else:
        assert "/app/MANIFEST.json" not in sources
    for name in ("postgres", "api", "ui", "proxy"):
        assert rendered[name]["restart"] == "unless-stopped", name
    assert rendered["reset"]["restart"] == "no"
    ports = {(p.get("host_ip", ""), str(p["published"])) for p in rendered["proxy"]["ports"]}
    public = "compose.public.yml" in files
    assert ("127.0.0.1", "8080") in ports
    if public:
        assert {("", "80"), ("", "443")} <= ports
    else:
        assert len(ports) == 1
    assert rendered["proxy"]["environment"]["ADMIN_AUTH"] == ("on" if public else "off")


@pytest.mark.skipif(not compose_available(), reason="docker compose not installed")
def test_rendered_jobs(tmp_path: Path) -> None:
    """make demo-data and make batch-generate target their service; the job runs as the host
    user (review P5)."""
    rendered = render(tmp_path, "jobs", [])
    assert set(rendered) == {"pipeline"}
    assert rendered["pipeline"]["user"] in {
        f"{env_example()['HOST_UID']}:{env_example()['HOST_GID']}",
        f"{os.getuid()}:{os.getgid()}",  # make exports HOST_UID and HOST_GID
    }
    # make batch-generate: $(GPU) (profile gpu) run --rm batch-generate
    gpu_jobs = render(tmp_path, "gpu,gpu-jobs", ["compose.gpu.yml"])
    assert gpu_jobs["batch-generate"]["environment"]["LLM_MODE"] == "vllm"
    assert gpu_jobs["batch-generate"]["depends_on"]["vllm"]["condition"] == "service_healthy"


def _public_config(tmp_path: Path, extra_env: str) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / ".env"
    text = (ROOT / ".env.example").read_text(encoding="utf-8") + extra_env
    env_file.write_text(text, encoding="utf-8")
    cmd = ["docker", "compose", "-f", str(INFRA / "docker-compose.yml")]
    cmd += ["-f", str(INFRA / "compose.demo.yml"), "-f", str(INFRA / "compose.public.yml")]
    cmd += ["--env-file", str(env_file), "--profile", "demo", "config"]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@pytest.mark.skipif(not compose_available(), reason="docker compose not installed")
def test_a_public_server_without_a_password_hash_does_not_start(tmp_path: Path) -> None:
    out = _public_config(tmp_path, PUBLIC_HOST_ENV)
    assert out.returncode != 0 and "ADMIN_PASSWORD_HASH" in out.stderr


@pytest.mark.skipif(not compose_available(), reason="docker compose not installed")
def test_a_public_server_without_a_host_does_not_start(tmp_path: Path) -> None:
    """.env.example leaves PUBLIC_HOST empty, so the :? guard of compose.public.yml fires."""
    out = _public_config(tmp_path, PUBLIC_ENV.replace(PUBLIC_HOST_ENV, "\n"))
    assert out.returncode != 0 and "PUBLIC_HOST" in out.stderr
    for profile, files in (("dev", []), ("demo", ["compose.demo.yml"])):
        render(tmp_path, profile, files)  # the laptop profiles need no host
