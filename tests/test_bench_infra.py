"""US-902 infra: the bench image, its compose file, the make targets and the GPU runbook.

Pure YAML and text parsing; the merged compose config is rendered when the Compose CLI is
installed (skipped otherwise), as in tests/test_infra.py."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_infra import (
    COMPOSE_FILES,
    INFRA,
    ROOT,
    bind_mounts,
    compose_available,
    makefile_targets,
    services,
)

BENCH_FILE = "compose.bench.yml"
CODE = ("bench", "ml", "pipeline", "contracts", "llm", "policy")


def test_the_bench_job_mounts_code_read_only_and_never_the_ground_truth() -> None:
    bench = services(BENCH_FILE)["bench"]
    mounts = {target: (source, ro) for source, target, ro in bind_mounts(bench)}
    for name in CODE:
        assert mounts[f"/app/{name}"] == ((ROOT / name).resolve(), True), name
    writable = {t for t, (_, ro) in mounts.items() if not ro}
    assert writable == {"/app/bench/results", "/app/artifacts", "/app/bench-work"}
    assert mounts["/app/data/raw"] == ((ROOT / "data" / "raw").resolve(), True)
    truth = ROOT / "data" / "truth"
    for source, _ in mounts.values():
        assert source != ROOT, "mounts the whole repo"
        assert source != truth and truth not in source.parents and source not in truth.parents
    assert "ports" not in bench and bench["profiles"] == ["bench"]
    assert bench["environment"]["VLLM_BASE_URL"] == "http://vllm:8001/v1"  # internal (NFR-11)
    assert bench["environment"]["LLM_MODE"] == "vllm" and bench["devices"] == [
        "/dev/kfd",
        "/dev/dri",
    ]
    args = bench["build"]["args"]
    assert args["ROCM_BASE_IMAGE"].startswith("${ROCM_BASE_IMAGE:?")
    assert args["AMD_PYPI_INDEX_URL"].startswith("${AMD_PYPI_INDEX_URL:?")
    gnn = services(BENCH_FILE)["trainer-gnn"]
    assert ((ROOT / "bench").resolve(), "/app/bench", True) in bind_mounts(gnn)


def test_no_running_service_sees_the_bench_work_folder() -> None:
    """B4 writes a generated store, truth included until it ends, under bench-work/: only the
    bench job mounts it (rule 12)."""
    work = ROOT / "bench-work"
    for name in (*COMPOSE_FILES, BENCH_FILE):
        for service_name, service in services(name).items():
            for source, _, _ in bind_mounts(service):
                if source == work or work in source.parents or source in work.parents:
                    assert service_name == "bench" or source == ROOT.resolve(), (name, service_name)
    assert "bench-work/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_bench_image_takes_amd_xgboost_from_the_amd_index_only() -> None:
    dockerfile = (INFRA / "docker" / "bench.Dockerfile").read_text(encoding="utf-8")
    assert '--index-url "$AMD_PYPI_INDEX_URL"' in dockerfile
    assert "--require-hashes" in dockerfile and "requirements-gbm-amd.txt" in dockerfile
    assert "--index-url https://pypi.org/simple" in dockerfile
    assert "requirements-bench.txt" in dockerfile and "pip check" in dockerfile
    assert "USE_HIP" in dockerfile and "--extra-index-url" not in dockerfile
    assert "FROM ${ROCM_BASE_IMAGE}" in dockerfile


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-r"):
            name, _, version = line.partition("==")
            pins[name.lower().replace("_", "-")] = version
    return pins


def test_bench_requirements_are_pinned_like_the_product() -> None:
    bench = _pins(INFRA / "docker" / "requirements-bench.txt")
    base = _pins(ROOT / "requirements" / "base.txt")
    assert bench and all(bench.values()), "every package is pinned"
    assert not set(bench) & {"xgboost", "xgboost-cpu", "amd-xgboost"}
    for name in set(bench) & set(base):
        assert bench[name] == base[name], name
    for needed in ("polars", "pyarrow", "scikit-learn", "networkx", "pydantic", "pandas"):
        assert needed in bench, needed


def test_make_bench_targets() -> None:
    targets = makefile_targets()
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert targets["bench"][0] == "bench-$(DEVICE)"
    assert re.search(r"^DEVICE \?= cpu$", makefile, re.M)
    assert targets["bench-gpu"][0].split() == [
        "bench-build",
        "bench-b1",
        "bench-b2",
        "bench-vllm",
        "bench-b4",
        "bench-b3",
        "bench-merge",
    ]
    cpu = " ".join(targets["bench-cpu"][1])
    assert "python -m bench run --device cpu --run-id $(BENCH_RUN)" in cpu
    assert "python -m bench report" in cpu and "$(BENCH_DOC)" in cpu
    for target in ("bench-b1", "bench-b2"):
        assert targets[target][1][0] == "@$(VLLM_DOWN)", target  # alone on the GPU and CPU
    assert "trainer-gnn python -m bench run --device gpu --parts B2" in " ".join(
        targets["bench-b2"][1]
    )
    b4 = " ".join(targets["bench-b4"][1])
    assert "python3 -m bench.amdsmi --out $(BENCH_DIR)/amdsmi.jsonl" in b4
    assert "--b4-samples" in b4 and "--b4-sampler inprocess" in b4
    assert re.search(r"^BENCH_COMPOSE = .*-f infra/compose\.bench\.yml", makefile, re.M)
    assert "implemented in session" not in " ".join(targets["bench"][1])
    phony = re.search(r"^\.PHONY:(.*)$", makefile.replace("\\\n", " "), re.M)
    assert phony is not None
    for target in (
        "bench",
        "bench-cpu",
        "bench-gpu",
        "bench-b1",
        "bench-b2",
        "bench-b3",
        "bench-b4",
        "bench-vllm",
        "bench-vllm-start",
        "bench-merge",
        "bench-report",
        "bench-build",
    ):
        assert re.search(rf"(^|\s){re.escape(target)}(\s|$)", phony.group(1)), target


def test_the_gpu_runbook_has_the_benchmark_session() -> None:
    runbook = (INFRA / "spikes" / "README.md").read_text(encoding="utf-8")
    spikes_end = runbook.index("### 15. Record")
    section = runbook.index("## Benchmarks B1-B4")
    assert spikes_end < section < runbook.index("## Files")
    text = runbook[section:]
    for needle in (
        "make bench DEVICE=gpu",
        "bench/results",
        "artifacts/bench/",
        "bench-report",
        "BENCH_RUN",
        "data/raw/asos_",
    ):
        assert needle in text, needle


@pytest.mark.skipif(not compose_available(), reason="docker compose not installed")
def test_rendered_bench_compose_config(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    cmd = ["docker", "compose"]
    for name in ("docker-compose.yml", "compose.gpu.yml", BENCH_FILE):
        cmd += ["-f", str(INFRA / name)]
    cmd += [
        "--env-file",
        str(env_file),
        "--profile",
        "gpu",
        "--profile",
        "bench",
        "config",
        "--format",
        "json",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    rendered = json.loads(out.stdout)["services"]
    assert "ports" not in rendered["bench"] and "ports" not in rendered["vllm"]
    assert rendered["bench"]["environment"]["DEVICE"] == "gpu"
    assert rendered["trainer-gnn"]["environment"]["DEVICE"] == "gpu"
    for volume in rendered["bench"]["volumes"]:
        assert "/data/truth" not in volume["source"]
