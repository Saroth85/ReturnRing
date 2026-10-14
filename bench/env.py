"""Hardware and software of the machine a benchmark part runs on (US-902: versions, data,
configuration). Every probe is best effort: a missing tool gives None, never an exception.

The image references (VLLM_IMAGE, DGL_IMAGE, ROCM_BASE_IMAGE: tag@sha256 from .env) and the
model revision (GEMMA_REVISION) come from the environment the compose file passes in; the git
commit from BENCH_GIT_COMMIT (containers have no .git) or `git rev-parse`, dirty when any file is
changed or untracked. Because a dirty tree is not a version, every part also records a content
hash of each code folder it can see (code_bench, code_ml, ...; review G8): two runs with the same
hashes ran the same code, committed or not.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import platform
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

IMAGE_VARIABLES = ("VLLM_IMAGE", "DGL_IMAGE", "ROCM_BASE_IMAGE")
MODEL_VARIABLES = ("GEMMA_MODEL", "GEMMA_REVISION")
ROCM_VERSION_FILES = (Path("/opt/rocm/.info/version"), Path("/opt/rocm/.info/version-dev"))
ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = ("bench", "ml", "pipeline", "contracts", "llm", "policy")
NOT_CODE = ("__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache")


def _run(command: Sequence[str], timeout_s: float = 20.0) -> str | None:
    try:
        done = subprocess.run(
            list(command), capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine()


def memory_gib() -> float | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            return round(int(line.split()[1]) / 2**20, 1)
    return None


def rocm_version() -> str | None:
    for path in ROCM_VERSION_FILES:
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    return None


def gpu_static(command: Sequence[str] = ("amd-smi", "static", "--json")) -> dict[str, Any] | None:
    """Name, architecture and VRAM of the GPUs from `amd-smi static --json`, or None."""
    out = _run(command)
    if out is None:
        return None
    try:
        start = min(i for i in (out.find("["), out.find("{")) if i >= 0)
        data = json.loads(out[start:])
    except ValueError:
        return None
    gpus = data.get("gpu_data", data) if isinstance(data, Mapping) else data
    if isinstance(gpus, Mapping):
        gpus = [gpus]
    found = []
    for gpu in gpus if isinstance(gpus, list) else []:
        if not isinstance(gpu, Mapping):
            continue
        asic = gpu.get("asic", {}) if isinstance(gpu.get("asic"), Mapping) else {}
        vram = gpu.get("vram", {}) if isinstance(gpu.get("vram"), Mapping) else {}
        size = vram.get("size")
        found.append(
            {
                "gpu": gpu.get("gpu"),
                "market_name": asic.get("market_name"),
                "target_graphics_version": asic.get("target_graphics_version"),
                "vram_size": size.get("value") if isinstance(size, Mapping) else size,
                "vram_unit": size.get("unit") if isinstance(size, Mapping) else None,
            }
        )
    return {"gpus": found} if found else None


def git_info(root: Path | None = None) -> dict[str, Any]:
    commit = os.environ.get("BENCH_GIT_COMMIT")
    dirty_env = os.environ.get("BENCH_GIT_DIRTY")
    if commit:
        return {"commit": commit, "dirty": dirty_env == "1" if dirty_env is not None else None}
    cwd = str(root) if root else None
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=cwd, check=False
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )
    except OSError:
        return {"commit": None, "dirty": None}
    if head.returncode != 0:
        return {"commit": None, "dirty": None}
    return {"commit": head.stdout.strip(), "dirty": bool(status.stdout.strip())}


def code_hashes(root: Path = ROOT, dirs: Sequence[str] = CODE_DIRS) -> dict[str, str]:
    """sha256 (first 16 hex digits) of every file under each code folder present: relative
    paths and contents, in sorted order; caches, compiled files and bench/results left out."""
    out: dict[str, str] = {}
    for name in dirs:
        folder = root / name
        if not folder.is_dir():
            continue
        digest = hashlib.sha256()
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(root)
            if (
                not path.is_file()
                or path.suffix == ".pyc"
                or any(part in NOT_CODE for part in relative.parts)
                or relative.parts[:2] == ("bench", "results")
            ):
                continue
            digest.update(relative.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
        out[f"code_{name}"] = "sha256:" + digest.hexdigest()[:16]
    return out


def hardware(*, gpu: bool) -> dict[str, Any]:
    info: dict[str, Any] = {
        "cpu": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "memory_gib": memory_gib(),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "in_container": Path("/.dockerenv").exists(),
    }
    if gpu:
        info["gpu"] = gpu_static()
    return info


def module_version(name: str) -> str | None:
    try:
        module = importlib.import_module(name)
    except Exception:  # noqa: BLE001 - an import may fail in many ways in a foreign image
        return None
    version = getattr(module, "__version__", None)
    return str(version) if version is not None else None


def xgboost_build() -> dict[str, Any]:
    """Version and build of the xgboost module: amd_xgboost on ROCm has USE_HIP."""
    try:
        import xgboost
    except ImportError:
        return {"xgboost": None}
    info = xgboost.build_info()
    return {
        "xgboost": str(xgboost.__version__),
        "xgboost_use_hip": bool(info.get("USE_HIP", False)),
        "xgboost_use_cuda": bool(info.get("USE_CUDA", False)),
    }


def software(modules: Sequence[str] = ()) -> dict[str, str]:
    """Python, ROCm, image references, model revision, the given modules' versions and the
    content hashes of the code folders."""
    out: dict[str, str] = {"python": platform.python_version()}
    rocm = rocm_version()
    if rocm:
        out["rocm"] = rocm
    for name in modules:
        version = module_version(name)
        if version:
            out[name] = version
    for variable in (*IMAGE_VARIABLES, *MODEL_VARIABLES):
        value = os.environ.get(variable)
        if value:
            out[variable.lower()] = value
    out.update(code_hashes())
    return out
