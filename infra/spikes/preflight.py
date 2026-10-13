"""Spike step 0 on the droplet host: check .env and the host, record versions for the results.

Standard library only (host python3). Run from the repo root: make spike-preflight.
Hard failures (exit 1): an image in .env not pinned by digest or different from .env.example,
GEMMA_REVISION not a commit sha, AMD_PYPI_INDEX_URL empty, VLLM_API_KEY still change-me, no
/dev/kfd, no docker compose, less than 250 GB free for Docker (three images of 7-12 GB compressed,
several times that unpacked, plus 62.6 GB of weights).
Everything else is recorded in artifacts/spikes/host.json for docs/spike-results.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

PINNED = ("VLLM_IMAGE", "DGL_IMAGE", "ROCM_BASE_IMAGE")
MIN_FREE_GB = 250
# amd-smi and rocminfo live in /opt/rocm/bin, which is often not on the host PATH.
os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + "/opt/rocm/bin"
HOST_COMMANDS = {
    "kernel": ["uname", "-r"],
    "os": ["sh", "-c", ". /etc/os-release && echo $PRETTY_NAME"],
    "nproc": ["nproc"],
    "memory": ["free", "-g"],
    "amdgpu_module": ["cat", "/sys/module/amdgpu/version"],
    "host_rocm": ["cat", "/opt/rocm/.info/version"],
    "amd_smi_version": ["amd-smi", "version"],
    "amd_smi_processes": ["amd-smi", "process"],
    "driver_packages": ["sh", "-c", "dpkg -l | grep -E 'amdgpu-dkms|rocm-core' || true"],
    "docker_server": ["docker", "version", "--format", "{{.Server.Version}}"],
    "docker_compose": ["docker", "compose", "version", "--short"],
    "docker_root": ["docker", "info", "--format", "{{.DockerRootDir}}"],
    "containers": ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Ports}}"],
    "listeners": ["sh", "-c", "ss -ltnp | grep -E ':(80|443|8000|8001|8080|8888)\\b' || true"],
}


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def run(cmd: list[str]) -> str:
    if shutil.which(cmd[0]) is None:
        return f"not found: {cmd[0]}"
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return "timeout"
    return (out.stdout + out.stderr).strip()


def check_env(env: dict[str, str], example: dict[str, str]) -> list[str]:
    errors = []
    for key in PINNED:
        value = env.get(key, "")
        if "@sha256:" not in value or value.split("@")[0].endswith(":latest"):
            errors.append(f"{key} must be tag@sha256:digest, got {value!r}")
        elif value != example.get(key):
            errors.append(f"{key} differs from .env.example ({value!r}): copy the pinned value")
    if not env.get("AMD_PYPI_INDEX_URL", "").startswith("https://pypi.amd.com/"):
        errors.append("AMD_PYPI_INDEX_URL must be the AMD index from .env.example")
    if env.get("VLLM_API_KEY", "change-me") in ("", "change-me"):
        errors.append("VLLM_API_KEY is empty or change-me: openssl rand -hex 24")
    if not env.get("GEMMA_MODEL"):
        errors.append("GEMMA_MODEL is empty")
    if not re.fullmatch(r"[0-9a-f]{40}", env.get("GEMMA_REVISION", "")):
        errors.append("GEMMA_REVISION must be the 40-character commit sha from .env.example")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/spikes/host.json"))
    args = parser.parse_args()

    errors: list[str] = []
    if not args.env_file.exists():
        errors.append(f"{args.env_file} missing: cp .env.example .env")
        env: dict[str, str] = {}
    else:
        env = read_env(args.env_file)
        errors += check_env(env, read_env(Path(".env.example")))

    host: dict[str, Any] = {name: run(cmd) for name, cmd in HOST_COMMANDS.items()}
    host["dev_kfd"] = Path("/dev/kfd").exists()
    host["dev_dri"] = (
        sorted(p.name for p in Path("/dev/dri").glob("*")) if Path("/dev/dri").exists() else []
    )
    root = host["docker_root"] if host["docker_root"].startswith("/") else "/"
    try:
        host["docker_root_free_gb"] = round(shutil.disk_usage(root).free / 1e9)
    except OSError:
        host["docker_root_free_gb"] = round(shutil.disk_usage("/").free / 1e9)
    if not host["dev_kfd"]:
        errors.append("/dev/kfd missing: no AMD GPU driver on this host")
    if not host["docker_server"][:1].isdigit():
        errors.append("Docker daemon not reachable (docker version shows no server)")
    if not host["docker_compose"][:1].isdigit():
        errors.append("docker compose v2 missing: apt-get install -y docker-compose-v2")
    if host["docker_root_free_gb"] < MIN_FREE_GB:
        errors.append(
            f"only {host['docker_root_free_gb']} GB free for Docker; need about {MIN_FREE_GB}"
        )

    report = {
        "env": {
            k: env.get(k) for k in (*PINNED, "AMD_PYPI_INDEX_URL", "GEMMA_MODEL", "GEMMA_REVISION")
        },
        "host": host,
        "errors": errors,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name in ("os", "kernel", "amdgpu_module", "host_rocm", "docker_server", "docker_compose"):
        print(f"{name:16} {host[name].splitlines()[0] if host[name] else ''}")
    print(f"{'docker free GB':16} {host['docker_root_free_gb']}")
    print(f"{'containers':16} {host['containers'] or '(none)'}")
    for error in errors:
        print(f"ERROR: {error}")
    print(f"wrote {args.out}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
