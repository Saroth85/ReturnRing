"""Guards on the compose profiles, images and GPU spike setup (US-001, spec sections 9-11, 13).

Pure YAML and text parsing, so no Docker daemon is needed. The last test renders the real merged
config with `docker compose config` when the Compose CLI is installed (it is on GitHub's
ubuntu-24.04 runners) and is skipped otherwise.
"""

from __future__ import annotations

import json
import os
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


# Offline jobs may read the ground truth (rule 12 as rewritten in ADR-13); only the generator
# writes data/. Every other service (postgres, api, ui, proxy, vllm, telemetry, spike-client)
# never sees data/truth.
JOB_SERVICES = frozenset({"pipeline", "trainer-gbm", "trainer-gnn"})
DATA_WRITERS = frozenset({"pipeline"})


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


@pytest.mark.xfail(
    strict=True,
    reason="infra follow-up of review F2: the api and telemetry mount the whole artifacts/ "
    "folder; narrow them (api: artifacts/risk/models read-only) so no running service sees "
    "artifacts/eval, the offline evaluation outputs. infra/ is changed in its own session.",
)
def test_f2_no_running_service_mounts_the_offline_evaluation_outputs() -> None:
    evaluation = ROOT / "artifacts" / "eval"
    for name in COMPOSE_FILES:
        for service_name, service in services(name).items():
            if service_name in JOB_SERVICES:
                continue
            for source, _, _ in bind_mounts(service):
                sees = source == evaluation or source in evaluation.parents
                assert not sees, (name, service_name, source)


def test_running_services_never_see_data() -> None:
    data = ROOT / "data"
    for name in COMPOSE_FILES:
        for service_name in ("api", "ui", "proxy", "postgres", "vllm"):
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
    # The other services keep the image's own user.
    for service_name, service in services("docker-compose.yml").items():
        if service_name != "pipeline":
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
    for target in ("graph", "features", "anomalies", "risk", "score", "results", "propensity"):
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
    if "pipeline" in rendered:  # dev and gpu profiles (review P5)
        assert rendered["pipeline"]["user"] in {
            f"{env_example()['HOST_UID']}:{env_example()['HOST_GID']}",
            f"{os.getuid()}:{os.getgid()}",  # make exports HOST_UID and HOST_GID
        }
    for service in rendered.values():
        for volume in service.get("volumes", []):
            assert not volume.get("source", "").endswith("/data/truth")
